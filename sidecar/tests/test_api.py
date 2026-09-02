"""T17 验证：8 个 REST 端点 + 6 类 WS 消息全链路（回放 + fake LLM）。"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from sidecar.agent.nodes import AgentDeps
from sidecar.main import Settings, create_app
from sidecar.profile.store import ProfileStore
from sidecar.storage import keys
from sidecar.storage.db import DB

RESUME = "星图科技 后端工程师 2021-2023，主导 Kafka 消息平台改造，延迟降低 37%。"

TRANSCRIPT = [
    {"text": "你好，请先做一个自我介绍", "start_ms": 0, "end_ms": 2000},
    # 两题间隔足够大，确保各自独立触发（不触发多问合并）；
    # 间隔需 > 静音阈值 300ms（墙钟 = start_ms/speed）+ 评估周期
    {"text": "聊聊你做过的 Kafka 项目", "start_ms": 40000, "end_ms": 42000},
]

ANSWER = "- 主导 Kafka 改造\n- 延迟降低 37%\n\n我在星图科技主导了 Kafka 改造，延迟降低 37%。"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 沙箱内 keyring 不可用 → 内存替代
    store = {}
    monkeypatch.setattr(keys, "set_key",
                        lambda p, s: store.__setitem__(p, s))
    monkeypatch.setattr(keys, "get_key", lambda p: store.get(p))
    monkeypatch.setattr(keys, "delete_key", lambda p: store.pop(p, None))

    db = DB(tmp_path / "t.db")
    transcript = tmp_path / "interview.json"
    transcript.write_text(json.dumps(TRANSCRIPT, ensure_ascii=False))

    async def cheap_chat(messages):
        return json.dumps({"question_type": "project", "topic_tags": ["Kafka"]},
                          ensure_ascii=False)

    async def flagship_stream(messages):
        for piece in [ANSWER[:25], ANSWER[25:50], ANSWER[50:]]:
            yield piece

    def deps_factory(db, settings, profile_id):
        return AgentDeps(
            cheap_chat=cheap_chat,
            flagship_stream=flagship_stream,
            store=ProfileStore(db),
            db=db,
            profile_id=profile_id,
            jd_digest="熟悉消息队列",
            resume_text=RESUME,
        )

    app = create_app(db=db, settings=Settings(silence_threshold_ms=300),
                     deps_factory=deps_factory)
    with TestClient(app) as c:
        c.replay_path = str(transcript)
        yield c, db


def collect_until(ws, pred, max_msgs=60):
    """收集 WS 消息直到 pred 命中。"""
    msgs = []
    for _ in range(max_msgs):
        msg = ws.receive_json()
        msgs.append(msg)
        if pred(msg):
            return msgs
    raise AssertionError(f"未等到目标消息，已收: {[m['type'] for m in msgs]}")


def test_profiles_crud(client):
    c, _ = client
    p = c.post("/profiles", json={
        "name": "字节-后端", "resume_text": RESUME, "jd_text": "JD"}).json()
    pid = p["profile_id"]
    assert c.get("/profiles").json()[0]["name"] == "字节-后端"
    assert c.get(f"/profiles/{pid}").json()["jd_text"] == "JD"
    c.put(f"/profiles/{pid}", json={
        "name": "阿里-后端", "resume_text": RESUME, "jd_text": "JD2"})
    assert c.get(f"/profiles/{pid}").json()["name"] == "阿里-后端"
    assert c.delete(f"/profiles/{pid}").json()["ok"]
    assert c.get(f"/profiles/{pid}").status_code == 404


def test_settings_keys(client):
    c, _ = client
    assert c.put("/settings/keys", json={
        "provider": "default", "api_key": "sk-test"}).json()["ok"]
    assert keys.get_key("default") == "sk-test"


def test_live_mode_requires_asr_key(client):
    """实时模式无 ASR Key → 400 且报错明确（不静默失败，F3/N8）。"""
    c, _ = client
    pid = c.post("/profiles", json={
        "name": "p", "resume_text": RESUME, "jd_text": "j"}).json()["profile_id"]
    r = c.post("/sessions", json={"profile_id": pid})
    assert r.status_code == 400
    assert "ASR" in r.json()["detail"]


def test_full_replay_session_ws_flow(client):
    c, db = client
    pid = c.post("/profiles", json={
        "name": "p", "resume_text": RESUME, "jd_text": "j"}).json()["profile_id"]

    with c.websocket_connect("/ws") as ws:
        session_holder = {}

        def start():
            r = c.post("/sessions", json={
                "profile_id": pid, "replay_path": c.replay_path,
                "replay_speed": 50.0})
            session_holder.update(r.json())

        t = threading.Thread(target=start)
        t.start()
        # 等 question.detected（覆盖 asr.segment 消息）
        msgs = collect_until(ws, lambda m: m["type"] == "question.detected")
        types = [m["type"] for m in msgs]
        assert "asr.segment" in types
        seg = next(m for m in msgs if m["type"] == "asr.segment"
                   and m["channel"] == "interviewer" and m["is_final"])
        assert "自我介绍" in seg["text"]

        q = msgs[-1]
        assert "自我介绍" in q["text"]
        assert q["question_end_ms"] == 2000

        # 等 answer.done（覆盖 skeleton/delta）
        msgs2 = collect_until(ws, lambda m: m["type"] == "answer.done")
        types2 = [m["type"] for m in msgs2]
        assert types2[0] == "answer.skeleton"
        assert "answer.delta" in types2
        skeleton = msgs2[0]["items"]
        assert skeleton == ["主导 Kafka 改造", "延迟降低 37%"]
        assert "latency_ms" in msgs2[-1]

        t.join(10)
        sid = session_holder["session_id"]

        # 手动重新生成（F5）
        regen = c.post(f"/sessions/{sid}/trigger", json={"mode": "regen"})
        assert regen.json()["ok"] is True
        collect_until(ws, lambda m: m["type"] == "answer.done")

        # 切换档位
        assert c.post(f"/sessions/{sid}/style",
                      json={"style": "concise"}).json()["ok"]
        assert c.post(f"/sessions/{sid}/style",
                      json={"style": "bogus"}).status_code == 400

        # 等第二题的 final segment 到达（回放时间轴 800ms 墙钟处）
        collect_until(ws, lambda m: m["type"] == "asr.segment"
                      and m.get("is_final") and "Kafka 项目" in m.get("text", ""))
        assert c.post(f"/sessions/{sid}/stop").json()["ok"]

    # 落库回看（F10）
    exported = c.get(f"/sessions/{sid}/export").json()
    assert exported["session"]["status"] == "ended"
    assert len(exported["segments"]) == 2
    assert len(exported["turns"]) >= 2  # 自动 1 轮 + regen 1 轮

    # 删除无残留（AC12）
    assert c.delete(f"/sessions/{sid}").json()["ok"]
    assert c.get(f"/sessions/{sid}/export").status_code == 404
