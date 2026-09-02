"""T16 验证：图事件序 skeleton→delta*→done、smalltalk 跳检索、
latency 埋点、fact_guard 违规检出、turn 落库。"""
import json
import pytest

from sidecar.agent.graph import run_turn
from sidecar.agent.nodes import AgentDeps
from sidecar.profile.store import ProfileStore
from sidecar.storage.db import DB

ANSWER = "- 主导 Kafka 改造\n- 延迟降低 37%\n- 灰度上线无事故\n\n我在星图科技主导了 Kafka 消息平台改造，端到端延迟降低 37%，支撑 200万 QPS。"

RESUME = "星图科技 后端工程师 2021-2023，主导 Kafka 消息平台改造，延迟降低 37%，支撑 200万 QPS。"

CARDS = [
    {"title": "Kafka 消息平台改造", "org": "星图科技", "role": "后端工程师",
     "period": "2021-2023", "tech_stack": ["Kafka"],
     "achievements": ["延迟降低 37%", "支撑 200万 QPS"],
     "keywords": ["Kafka"], "raw_excerpt": ""},
]


def make_deps(tmp_path, qtype="project", answer=ANSWER):
    db = DB(tmp_path / "t.db")
    prof = db.create_profile("p", RESUME, "j")
    db.replace_cards(prof["profile_id"], CARDS)
    session = db.create_session(prof["profile_id"])

    async def cheap_chat(messages):
        return json.dumps({"question_type": qtype,
                           "topic_tags": ["Kafka"]}, ensure_ascii=False)

    async def flagship_stream(messages):
        # 模拟逐段流式输出
        for piece in [answer[:20], answer[20:45], answer[45:60], answer[60:]]:
            yield piece

    deps = AgentDeps(
        cheap_chat=cheap_chat,
        flagship_stream=flagship_stream,
        store=ProfileStore(db),
        db=db,
        profile_id=prof["profile_id"],
        jd_digest="熟悉消息队列",
        resume_text=RESUME,
    )
    return deps, session, db


@pytest.mark.asyncio
async def test_event_sequence_and_content(tmp_path):
    deps, session, db = make_deps(tmp_path)
    events = [e async for e in run_turn(
        deps, session["session_id"], "聊聊你做过的 Kafka 项目", 5000)]

    types = [e["type"] for e in events]
    assert types[0] == "skeleton"
    assert types[-1] == "done"
    assert "delta" in types  # 骨架之后有流式增量
    # skeleton 在第一个 delta 之前（N1/N2 时序）
    assert types.index("skeleton") < types.index("delta")

    skeleton = events[0]["items"]
    assert skeleton == ["主导 Kafka 改造", "延迟降低 37%", "灰度上线无事故"]

    done = events[-1]
    assert done["fact_violations"] == []  # 全部实体锚定档案
    for key in ("classify", "retrieve", "generate", "fact_guard", "total"):
        assert key in done["latency_ms"], f"缺少埋点: {key}"

    # turn 落库
    turns = db.list_turns(session["session_id"])
    assert len(turns) == 1
    assert turns[0]["answer_skeleton"] == skeleton
    assert "星图科技" in turns[0]["answer_full"]
    db.close()


@pytest.mark.asyncio
async def test_smalltalk_skips_retrieve(tmp_path):
    deps, session, db = make_deps(tmp_path, qtype="smalltalk")
    events = [e async for e in run_turn(
        deps, session["session_id"], "今天天气不错对吧", 1000)]
    done = events[-1]
    # smalltalk 不走 retrieve 节点 → 无 retrieve 埋点
    assert "retrieve" not in done["latency_ms"]
    assert "classify" in done["latency_ms"]
    db.close()


@pytest.mark.asyncio
async def test_fabricated_entity_flagged(tmp_path):
    fake_answer = "- 负责跨境项目\n\n我在蓝鲸集团负责跨境电商，GMV 提升 55%。"
    deps, session, db = make_deps(tmp_path, answer=fake_answer)
    events = [e async for e in run_turn(
        deps, session["session_id"], "聊聊你的项目", 1000)]
    violations = events[-1]["fact_violations"]
    assert "蓝鲸集团" in violations
    assert any("55%" in v for v in violations)
    db.close()
