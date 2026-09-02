"""T10 验证：双通道编排——channel 标签无串扰（AC1 雏形）、final 落库。"""
import json
import pytest

from sidecar.asr.providers import ReplayASRProvider
from sidecar.asr.stream import ASROrchestrator
from sidecar.storage.db import DB

INTERVIEWER = [
    {"text": "你好请先自我介绍一下", "start_ms": 0, "end_ms": 2000},
    {"text": "聊聊你做过的项目", "start_ms": 8000, "end_ms": 10000},
]
ME = [
    {"text": "好的我叫小明", "start_ms": 2500, "end_ms": 4000},
    {"text": "我做过一个 Kafka 项目", "start_ms": 10500, "end_ms": 13000},
]


@pytest.fixture
def transcripts(tmp_path):
    a = tmp_path / "interviewer.json"
    b = tmp_path / "me.json"
    a.write_text(json.dumps(INTERVIEWER, ensure_ascii=False))
    b.write_text(json.dumps(ME, ensure_ascii=False))
    return a, b


@pytest.mark.asyncio
async def test_dual_channel_no_crosstalk_and_persist(transcripts, tmp_path):
    a, b = transcripts
    db = DB(tmp_path / "t.db")
    prof = db.create_profile("p", "r", "j")
    session = db.create_session(prof["profile_id"])

    orch = ASROrchestrator(
        providers={
            "interviewer": ReplayASRProvider(a, speed=100, emit_interim=True),
            "me": ReplayASRProvider(b, speed=100, emit_interim=True),
        },
        db=db, session_id=session["session_id"],
    )
    segs = [s async for s in orch.run()]

    finals = [s for s in segs if s.is_final]
    assert len(finals) == 4
    # 通道标签与文本一一对应，无串扰
    by_text = {s.text: s.channel for s in finals}
    assert by_text["你好请先自我介绍一下"] == "interviewer"
    assert by_text["好的我叫小明"] == "me"
    assert by_text["我做过一个 Kafka 项目"] == "me"
    # 落库仅 final，且带 channel
    rows = db.list_segments(session["session_id"])
    assert len(rows) == 4
    assert {r["channel"] for r in rows} == {"interviewer", "me"}
    db.close()


@pytest.mark.asyncio
async def test_orchestrator_without_db(transcripts):
    a, b = transcripts
    orch = ASROrchestrator(
        providers={
            "interviewer": ReplayASRProvider(a, speed=100, emit_interim=False),
            "me": ReplayASRProvider(b, speed=100, emit_interim=False),
        },
        db=None, session_id="noop",
    )
    segs = [s async for s in orch.run()]
    assert len(segs) == 4
