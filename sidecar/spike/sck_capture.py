"""T1 spike 步骤 1: ScreenCaptureKit 系统音频捕获验证

目标：证明 PyObjC 调用 ScreenCaptureKit 能稳定拿到系统音频 PCM 流。
做法：捕获 8 秒系统音频，期间用 `say` 播放语音，统计采集到的
sample buffer 数量、格式与能量（验证非静音），原始数据落盘供分析。
"""
import ctypes
import threading
import time
from Foundation import NSObject, NSRunLoop, NSDate
import ScreenCaptureKit
import CoreMedia

CAPTURE_SECONDS = 8
OUT_RAW = "spike_system_audio.raw"

collected = []
format_reported = []


class StreamOutput(NSObject):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if output_type != ScreenCaptureKit.SCStreamOutputTypeAudio:
            return
        if not CoreMedia.CMSampleBufferIsValid(sample_buffer):
            return
        if not format_reported:
            desc = CoreMedia.CMSampleBufferGetFormatDescription(sample_buffer)
            asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(desc)
            format_reported.append({
                "sample_rate": asbd.mSampleRate,
                "channels": asbd.mChannelsPerFrame,
                "bits": asbd.mBitsPerChannel,
                "format": asbd.mFormatID,
                "frames": CoreMedia.CMSampleBufferGetNumSamples(sample_buffer),
            })
        status, block_buffer, abl, _, _ = \
            CoreMedia.CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
                sample_buffer, None, None, 0, None, None, 0, None)
        if status != 0:
            return
        buf = abl.mBuffers[0]
        addr = buf.mData if isinstance(buf.mData, int) else int(ctypes.cast(
            buf.mData, ctypes.c_void_p).value)
        data = ctypes.string_at(addr, buf.mDataByteSize)
        collected.append(data)


def run_with_runloop(seconds):
    """让 NSRunLoop 运转指定秒数（ScreenCaptureKit 回调依赖它）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.05))


def main():
    done = threading.Event()
    holder = {}

    def on_content(content, error):
        holder["content"] = content
        holder["error"] = error
        done.set()

    ScreenCaptureKit.SCShareableContent \
        .getShareableContentWithCompletionHandler_(on_content)
    while not done.is_set():
        run_with_runloop(0.1)

    if holder.get("error") or not holder.get("content"):
        print(f"FAIL: 无法获取可捕获内容: {holder.get('error')}")
        raise SystemExit(1)

    display = holder["content"].displays()[0]
    content_filter = ScreenCaptureKit.SCContentFilter.alloc() \
        .initWithDisplay_excludingWindows_(display, [])

    config = ScreenCaptureKit.SCStreamConfiguration()
    config.setCapturesAudio_(True)
    config.setExcludesCurrentProcessAudio_(True)
    config.setSampleRate_(16000)
    config.setChannelCount_(1)
    # 视频帧压到最小——我们只关心音频
    config.setWidth_(2)
    config.setHeight_(2)
    config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

    stream = ScreenCaptureKit.SCStream.alloc() \
        .initWithFilter_configuration_delegate_(content_filter, config, None)
    output = StreamOutput.alloc().init()
    err = stream.addStreamOutput_type_sampleHandlerQueue_error_(
        output, ScreenCaptureKit.SCStreamOutputTypeAudio, None, None)
    print(f"addStreamOutput: {err}")

    started = threading.Event()
    start_err = {}

    def on_start(e):
        start_err["e"] = e
        started.set()

    stream.startCaptureWithCompletionHandler_(on_start)
    while not started.is_set():
        run_with_runloop(0.1)
    if start_err.get("e"):
        print(f"FAIL: 启动捕获失败: {start_err['e']}")
        print("提示：多半是天授予屏幕录制权限（系统设置 → 隐私与安全性 → 屏幕录制）")
        raise SystemExit(1)
    print("捕获已启动，8 秒内播放测试语音...")

    import subprocess
    time.sleep(1)
    subprocess.Popen(["say", "面试官你好，请介绍一下你自己，聊聊你做过的项目"])
    run_with_runloop(CAPTURE_SECONDS)

    stopped = threading.Event()
    stream.stopCaptureWithCompletionHandler_(lambda e: stopped.set())
    while not stopped.is_set():
        run_with_runloop(0.1)

    total = sum(len(d) for d in collected)
    with open(OUT_RAW, "wb") as f:
        f.write(b"".join(collected))

    print(f"格式: {format_reported}")
    print(f"采集: {len(collected)} 个 buffer, 共 {total} 字节 -> {OUT_RAW}")

    # 能量分析（假设 float32 PCM）
    if collected:
        import array
        samples = array.array("f")
        samples.frombytes(b"".join(collected)[:(total // 4) * 4])
        peak = max((abs(s) for s in samples), default=0.0)
        print(f"峰值振幅: {peak:.4f} ({'非静音 OK' if peak > 0.01 else '疑似静音 FAIL'})")
    print("PASS" if collected and total > 16000 else "FAIL: 未采到足够音频")


if __name__ == "__main__":
    main()
