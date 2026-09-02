"""T9 验证：双通道采集——自检错误指引、帧切分、通道隔离、启停。
真实设备（麦克风/系统音频）测试在沙箱中跳过，权限授予后手动复验。"""
import asyncio
import pytest

from sidecar.audio.capture import DualChannelCapture, FRAME_BYTES


class FakeSource:
    def __init__(self, ok=True, err=None, payload=b"\x01\x02" * 640):
        self.ok, self.err = ok, err
        self.payload = payload  # 640 样本 = 2 帧 20ms
        self.started = False
        self.on_frame = None

    def check(self):
        return self.ok, self.err

    def start(self, on_frame):
        self.started = True
        self.on_frame = on_frame

    def stop(self):
        self.started = False

    def emit(self):
        self.on_frame(self.payload)


def test_check_channels_all_ok():
    cap = DualChannelCapture(mic=FakeSource(), system=FakeSource())
    result = cap.check_channels()
    assert result == {"system": True, "mic": True, "error": None}


def test_check_channels_reports_both_with_guidance():
    cap = DualChannelCapture(
        mic=FakeSource(ok=False, err="麦克风不可用: 权限拒绝"),
        system=FakeSource(ok=False, err="系统音频捕获未授权: TCC -3801"),
    )
    result = cap.check_channels()
    assert result["system"] is False and result["mic"] is False
    # 两路错误都汇总上报，不静默、不覆盖
    assert "TCC" in result["error"] and "麦克风" in result["error"]


@pytest.mark.asyncio
async def test_frames_split_and_channel_isolation():
    mic, system = FakeSource(), FakeSource()
    cap = DualChannelCapture(mic=mic, system=system)
    cap.start()
    system.emit()  # 640 样本 → 2 个 20ms 帧，进 interviewer 通道
    mic.emit()

    interviewer_frames = []
    me_frames = []

    async def collect(channel, out):
        async for frame in cap.frames(channel):
            out.append(frame)
            if len(out) >= 2:
                return

    await asyncio.wait_for(
        asyncio.gather(collect("interviewer", interviewer_frames),
                       collect("me", me_frames)),
        timeout=3)
    cap.stop()

    assert all(len(f) == FRAME_BYTES for f in interviewer_frames)
    assert len(interviewer_frames) == 2 and len(me_frames) == 2
    # 通道内容来自各自源（fake payload 相同但队列独立）
    assert interviewer_frames[0] is not me_frames[0]


@pytest.mark.asyncio
async def test_stop_halts_streams():
    mic, system = FakeSource(), FakeSource()
    cap = DualChannelCapture(mic=mic, system=system)
    cap.start()
    assert mic.started and system.started
    cap.stop()
    assert not mic.started and not system.started
