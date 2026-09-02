"""T12 验证：分层检测器——召回、附和零触发、祈使句、LLM 兜底、手动 flush、延迟。"""
import pytest

from sidecar.asr.providers import ASRSegment
from sidecar.detect.detector import QuestionDetector


def seg(text, end_ms, channel="interviewer"):
    return ASRSegment(f"s-{end_ms}", channel, text, end_ms - 1000, end_ms, True)


class FakeClock:
    """模拟静音时长：由测试直接控制。"""

    def __init__(self):
        self.silence_ms = 0

    def __call__(self):
        return self.silence_ms


def make_detector(collected, **kw):
    clock = FakeClock()
    det = QuestionDetector(
        silence_ms_fn=clock,
        on_question=lambda text, end_ms: collected.append((text, end_ms)),
        **kw,
    )
    return det, clock


@pytest.mark.asyncio
async def test_question_detected_after_silence():
    got = []
    det, clock = make_detector(got)
    det.feed(seg("你为什么想离开现在的公司？", 2000))
    assert await det.evaluate() is False  # 静音不足
    clock.silence_ms = 900
    assert await det.evaluate() is True
    assert got == [("你为什么想离开现在的公司？", 2000)]
    # 不重复触发
    assert await det.evaluate() is False


@pytest.mark.asyncio
async def test_backchannel_never_triggers():
    got = []
    det, clock = make_detector(got)
    clock.silence_ms = 5000
    for text in ["嗯", "对", "好的"]:
        det.feed(seg(text, 1000))
        assert await det.evaluate() is False
    assert got == []


@pytest.mark.asyncio
async def test_imperative_triggers_without_llm():
    got = []
    det, clock = make_detector(got)
    clock.silence_ms = 1000
    det.feed(seg("聊聊你做过的项目", 3000))
    assert await det.evaluate() is True
    assert got[0][0] == "聊聊你做过的项目"


@pytest.mark.asyncio
async def test_statement_filtered_by_llm_fallback():
    got = []

    async def fake_llm(text):
        return "statement"

    det, clock = make_detector(got, llm_fallback=fake_llm)
    clock.silence_ms = 1000
    det.feed(seg("我们今天先到这里", 4000))  # 规则 unsure → LLM 判陈述
    assert await det.evaluate() is False
    assert got == []
    assert det.pending_text() == ""  # 缓冲已清


@pytest.mark.asyncio
async def test_incomplete_waits_for_more():
    got = []
    calls = []

    async def fake_llm(text):
        calls.append(text)
        return "incomplete" if len(calls) == 1 else "complete_question"

    det, clock = make_detector(got, llm_fallback=fake_llm)
    clock.silence_ms = 1000
    det.feed(seg("你们在做的就是那个", 1000))  # unsure → incomplete，等待
    assert await det.evaluate() is False
    det.feed(seg("基于 Kafka 的延迟优化对吧", 2500))
    # 新片段到达后重新评估 → complete
    assert await det.evaluate() is True
    assert "基于 Kafka" in got[0][0]


@pytest.mark.asyncio
async def test_me_channel_ignored():
    got = []
    det, clock = make_detector(got)
    clock.silence_ms = 1000
    det.feed(seg("我回答一下这个问题", 1000, channel="me"))
    det.feed(seg("嗯好的", 1500, channel="me"))
    assert await det.evaluate() is False
    assert got == []


@pytest.mark.asyncio
async def test_manual_flush():
    got = []
    det, clock = make_detector(got)  # 静音不足也可手动触发
    det.feed(seg("你平时怎么学习的", 1000))
    text = det.flush_manual()
    assert text == "你平时怎么学习的"
    assert got[0][0] == "你平时怎么学习的"
    assert det.flush_manual() is None  # 缓冲已空
