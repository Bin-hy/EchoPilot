"""T8 验证：silero VAD 在真实语音（macOS say 生成）上检测说话与静音。"""
import subprocess
import wave
from pathlib import Path

import pytest

from sidecar.audio.vad import ChannelVAD

FRAME_MS = 20
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # int16


@pytest.fixture(scope="module")
def speech_wav(tmp_path_factory):
    """用 say + afconvert 生成 16kHz mono int16 语音，尾部拼 2s 静音。"""
    tmp = tmp_path_factory.mktemp("vad")
    aiff = tmp / "speech.aiff"
    wav = tmp / "speech.wav"
    subprocess.run(
        ["say", "-o", str(aiff),
         "你好，请做一个简单的自我介绍，聊聊你做过的项目"],
        check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
         str(aiff), str(wav)], check=True)
    with wave.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    pcm += b"\x00" * SAMPLE_RATE * 2 * 2  # 追加 2s 静音
    return pcm


def test_vad_detects_speech_then_silence(speech_wav):
    vad = ChannelVAD()
    saw_speaking = False
    silence_timeline = []
    for off in range(0, len(speech_wav) - FRAME_BYTES + 1, FRAME_BYTES):
        vad.feed(speech_wav[off:off + FRAME_BYTES])
        if vad.state()["speaking"]:
            saw_speaking = True
        silence_timeline.append(vad.state()["silence_ms"])

    assert saw_speaking, "VAD 未检测到语音"
    # 尾部 2s 静音后 silence_ms 应累计到 ≥1500ms
    assert silence_timeline[-1] >= 1500, \
        f"静音累计不足: {silence_timeline[-1]}ms"
    # 静音后 speaking 应为 False
    assert vad.state()["speaking"] is False


def test_vad_pure_silence():
    vad = ChannelVAD()
    vad.feed(b"\x00" * FRAME_BYTES * 50)  # 1s 静音
    state = vad.state()
    assert state["speaking"] is False
    assert 700 <= state["silence_ms"] <= 1300  # ≈1000±100ms 允许窗口量化误差
