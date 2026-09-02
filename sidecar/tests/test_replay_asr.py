"""T6 验证：回放适配器按时间轴产出 interim/final segment，字段与标注一致。"""
import json
import pytest

from sidecar.asr.providers import ReplayASRProvider


SAMPLE = [
    {"text": "你好，请先做一个简单的自我介绍", "start_ms": 100, "end_ms": 2500},
    {"text": "你在上一家公司主要负责什么", "start_ms": 5000, "end_ms": 7200},
]


@pytest.fixture
def transcript(tmp_path):
    p = tmp_path / "transcript.json"
    p.write_text(json.dumps(SAMPLE, ensure_ascii=False))
    return p


@pytest.mark.asyncio
async def test_replay_segments_match_annotation(transcript):
    provider = ReplayASRProvider(transcript, speed=100.0, emit_interim=False)
    segs = [s async for s in provider.open_stream("interviewer")]
    assert len(segs) == 2
    assert all(s.is_final for s in segs)
    assert segs[0].text == SAMPLE[0]["text"]
    assert segs[0].channel == "interviewer"
    assert segs[0].start_ms == 100 and segs[0].end_ms == 2500
    assert segs[1].start_ms == 5000


@pytest.mark.asyncio
async def test_replay_emits_interim_then_final(transcript):
    provider = ReplayASRProvider(transcript, speed=100.0, emit_interim=True)
    segs = [s async for s in provider.open_stream("interviewer")]
    assert len(segs) == 4
    assert [s.is_final for s in segs] == [False, True, False, True]
    # interim 是 final 的前缀
    assert segs[1].text.startswith(segs[0].text)
    assert segs[0].segment_id == segs[1].segment_id


@pytest.mark.asyncio
async def test_replay_respects_timeline(transcript):
    import time
    provider = ReplayASRProvider(transcript, speed=10.0, emit_interim=False)
    t0 = time.monotonic()
    segs = [s async for s in provider.open_stream("interviewer")]
    elapsed = time.monotonic() - t0
    # 第二条 start_ms=5000，10 倍速下应在 0.5s 左右到达
    assert 0.4 < elapsed < 1.5
