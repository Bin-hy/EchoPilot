"""M1: 双通道音频采集（F1, F3）。

- 面试官通道：ScreenCaptureKit 系统音频（PyObjC，macOS 13+）
- 用户通道：sounddevice 麦克风
- 两路独立环形缓冲，输出 16kHz mono int16 20ms 帧
- check_channels() 自检：权限/电平异常给出明确原因（不静默失败）

音频源可注入（MicSource/SystemSource），测试用 fake 替代真实设备。
"""
from __future__ import annotations

import asyncio
import queue
import threading
from typing import AsyncIterator, Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000      # 320
FRAME_BYTES = FRAME_SAMPLES * 2                      # int16


class MicSource(Protocol):
    def start(self, on_frame) -> None: ...
    def stop(self) -> None: ...
    def check(self) -> tuple[bool, str | None]: ...


class SystemSource(Protocol):
    def start(self, on_frame) -> None: ...
    def stop(self) -> None: ...
    def check(self) -> tuple[bool, str | None]: ...


# ── 真实麦克风源 ─────────────────────────────────────────────
class SounddeviceMic:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._stream = None

    def check(self) -> tuple[bool, str | None]:
        try:
            import sounddevice as sd
            sd.check_input_settings(channels=1, samplerate=self.sample_rate)
            # 读 200ms 电平，确认不是完全无声的设备（如未插麦）
            data = sd.rec(int(0.2 * self.sample_rate),
                          samplerate=self.sample_rate, channels=1,
                          dtype="int16", blocking=True)
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            if rms < 1.0:
                return False, "麦克风无输入信号（未连接或被系统静音）"
            return True, None
        except Exception as e:
            return False, f"麦克风不可用: {e}（检查系统设置→隐私→麦克风权限）"

    def start(self, on_frame) -> None:
        import sounddevice as sd

        def callback(indata, frames, time_info, status):
            on_frame(indata[:, 0].astype(np.int16).tobytes())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, callback=callback)
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


# ── 真实系统音频源（ScreenCaptureKit）────────────────────────
class ScreenCaptureKitSystem:
    """PyObjC 调 ScreenCaptureKit，回调线程驱动 NSRunLoop。"""

    def check(self) -> tuple[bool, str | None]:
        try:
            import ScreenCaptureKit  # noqa: F401
            # TCC 探测：请求可捕获内容，拒绝时抛错（错误码 -3801）
            import Foundation  # noqa: F401
            # 轻量探测：仅尝试获取 shareable content
            ok, err = _probe_shareable_content()
            if not ok:
                return False, (
                    f"系统音频捕获未授权: {err}（系统设置→隐私与安全性→"
                    "屏幕录制 中授予本应用权限）")
            return True, None
        except ImportError as e:
            return False, f"PyObjC ScreenCaptureKit 不可用: {e}"

    def start(self, on_frame) -> None:
        self._runner = _SCKRunner(on_frame)
        self._runner.start()

    def stop(self) -> None:
        if getattr(self, "_runner", None):
            self._runner.stop()
            self._runner = None


def _probe_shareable_content() -> tuple[bool, str | None]:
    import threading
    import ScreenCaptureKit
    from Foundation import NSDate, NSRunLoop

    done = threading.Event()
    holder: dict = {}

    def on_content(content, error):
        holder["content"], holder["error"] = content, error
        done.set()

    ScreenCaptureKit.SCShareableContent \
        .getShareableContentWithCompletionHandler_(on_content)
    deadline = __import__("time").time() + 3
    while not done.is_set() and __import__("time").time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.05))
    if holder.get("error"):
        return False, str(holder["error"])
    return (holder.get("content") is not None), None


class _SCKRunner:
    """在专用线程里跑 ScreenCaptureKit 捕获，float32 转 int16 输出。"""

    def __init__(self, on_frame):
        self.on_frame = on_frame
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        import ctypes

        import CoreMedia
        import ScreenCaptureKit
        from Foundation import NSDate, NSObject, NSRunLoop

        on_frame = self.on_frame

        class StreamOutput(NSObject):
            def stream_didOutputSampleBuffer_ofType_(
                    self, stream, sample_buffer, output_type):
                if output_type != ScreenCaptureKit.SCStreamOutputTypeAudio:
                    return
                status, block, abl, _, _ = (
                    CoreMedia
                    .CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
                        sample_buffer, None, None, 0, None, None, 0, None))
                if status != 0:
                    return
                buf = abl.mBuffers[0]
                addr = buf.mData if isinstance(buf.mData, int) else int(
                    ctypes.cast(buf.mData, ctypes.c_void_p).value)
                f32 = np.frombuffer(
                    ctypes.string_at(addr, buf.mDataByteSize),
                    dtype=np.float32)
                i16 = np.clip(f32 * 32768, -32768, 32767).astype(np.int16)
                on_frame(i16.tobytes())

        content, err = _probe_shareable_content()
        if not content:
            return
        display = content.displays()[0]
        content_filter = ScreenCaptureKit.SCContentFilter.alloc() \
            .initWithDisplay_excludingWindows_(display, [])
        config = ScreenCaptureKit.SCStreamConfiguration()
        config.setCapturesAudio_(True)
        config.setExcludesCurrentProcessAudio_(True)
        config.setSampleRate_(SAMPLE_RATE)
        config.setChannelCount_(1)
        config.setWidth_(2)
        config.setHeight_(2)
        stream = ScreenCaptureKit.SCStream.alloc() \
            .initWithFilter_configuration_delegate_(content_filter, config, None)
        output = StreamOutput.alloc().init()
        stream.addStreamOutput_type_sampleHandlerQueue_error_(
            output, ScreenCaptureKit.SCStreamOutputTypeAudio, None, None)

        started = threading.Event()
        stream.startCaptureWithCompletionHandler_(lambda e: started.set())
        while not started.is_set() and not self._stop.is_set():
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.05))
        while not self._stop.is_set():
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.1))
        stream.stopCaptureWithCompletionHandler_(lambda e: None)


# ── 双通道采集器 ─────────────────────────────────────────────
class DualChannelCapture:
    def __init__(
        self,
        mic: MicSource | None = None,
        system: SystemSource | None = None,
    ):
        self.mic = mic or SounddeviceMic()
        self.system = system or ScreenCaptureKitSystem()
        self._buffers: dict[str, queue.Queue[bytes]] = {
            "interviewer": queue.Queue(maxsize=500),
            "me": queue.Queue(maxsize=500),
        }
        self._running = False

    def check_channels(self) -> dict:
        """F3 自检：两路分别报告，附修复指引。"""
        mic_ok, mic_err = self.mic.check()
        sys_ok, sys_err = self.system.check()
        return {
            "system": sys_ok, "mic": mic_ok,
            "error": "; ".join(e for e in (sys_err, mic_err) if e) or None,
        }

    def start(self) -> None:
        self._running = True
        self.system.start(self._make_handler("interviewer"))
        self.mic.start(self._make_handler("me"))

    def stop(self) -> None:
        self._running = False
        self.system.stop()
        self.mic.stop()

    def _make_handler(self, channel: str):
        q = self._buffers[channel]

        def handle(pcm: bytes) -> None:
            # 按 20ms 帧切分入队（源可能给任意长度）
            for off in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                try:
                    q.put_nowait(pcm[off:off + FRAME_BYTES])
                except queue.Full:
                    pass  # 下游消费不过来时丢帧保实时
        return handle

    async def frames(self, channel: str) -> AsyncIterator[bytes]:
        q = self._buffers[channel]
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                yield await loop.run_in_executor(
                    None, lambda: q.get(timeout=0.5))
            except queue.Empty:
                continue
