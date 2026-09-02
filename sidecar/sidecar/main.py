"""EchoPilot sidecar：FastAPI 入口，WS/REST 路由装配（plan.md 进程间接口）。

- WS  /ws                    推送 6 类消息（asr.segment / question.detected /
                             answer.skeleton / answer.delta / answer.done /
                             status.health）
- REST 按 plan.md 8 端点
deps_factory / providers_factory 可注入，测试用 fake + 回放（N10）。
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from sidecar.agent.nodes import AgentDeps
from sidecar.asr.providers import ASRProvider, ReplayASRProvider
from sidecar.llm.provider import LLMConfig, chat_once, stream_chat
from sidecar.profile.store import ProfileStore
from sidecar.runtime import SessionRuntime
from sidecar.storage import keys
from sidecar.storage.db import DB


# ── 配置 ─────────────────────────────────────────────────────
@dataclass
class Settings:
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_cheap_model: str = "deepseek-chat"
    llm_flagship_model: str = "deepseek-chat"
    llm_provider: str = "default"      # keyring 里的 provider 名
    silence_threshold_ms: int = 800
    default_style: str = "standard"


# ── 请求模型 ─────────────────────────────────────────────────
class ProfileIn(BaseModel):
    name: str
    resume_text: str
    jd_text: str


class SessionIn(BaseModel):
    profile_id: str
    replay_path: str | None = None     # 回放模式（测试链路），空则实时采集
    replay_speed: float = 1.0


class TriggerIn(BaseModel):
    mode: str                          # "manual" | "regen"
    text: str | None = None


class StyleIn(BaseModel):
    style: str                         # "concise" | "standard" | "detailed"


class KeyIn(BaseModel):
    provider: str
    api_key: str


# ── 默认工厂（真实供应商，N10）────────────────────────────────
FAKE_ANSWER = ("- 主导 Kafka 改造\n- 延迟降低 37%\n- 灰度上线无事故\n\n"
               "我在星图科技主导了 Kafka 消息平台改造，"
               "端到端延迟降低 37%，支撑 200万 QPS。")


def fake_deps_factory(db: DB, settings: Settings,
                      profile_id: str) -> AgentDeps:
    """开发/联调模式（ECHOPILOT_FAKE_LLM=1）：不依赖真实 Key，
    验证 app→sidecar→浮窗→落库 全链路（T22）。"""
    profile = db.get_profile(profile_id) or {}

    async def cheap_chat(messages):
        return json.dumps({"question_type": "project",
                           "topic_tags": ["Kafka"]}, ensure_ascii=False)

    async def flagship_stream(messages):
        for piece in [FAKE_ANSWER[i:i + 12]
                      for i in range(0, len(FAKE_ANSWER), 12)]:
            yield piece
            await asyncio.sleep(0.02)  # 模拟流式节奏

    return AgentDeps(
        cheap_chat=cheap_chat,
        flagship_stream=flagship_stream,
        store=ProfileStore(db),
        db=db,
        profile_id=profile_id,
        jd_digest=(profile or {}).get("jd_digest") or "",
        resume_text=(profile or {}).get("resume_text") or "",
    )


def default_deps_factory(db: DB, settings: Settings,
                         profile_id: str) -> AgentDeps:
    import os
    if os.environ.get("ECHOPILOT_FAKE_LLM") == "1":
        return fake_deps_factory(db, settings, profile_id)
    api_key = keys.get_key(settings.llm_provider)
    if not api_key:
        raise HTTPException(400, f"未配置 LLM API Key（provider={settings.llm_provider}）")
    cheap_cfg = LLMConfig(settings.llm_base_url, api_key,
                          settings.llm_cheap_model)
    flagship_cfg = LLMConfig(settings.llm_base_url, api_key,
                             settings.llm_flagship_model)
    profile = db.get_profile(profile_id)

    async def cheap_chat(messages):
        return await chat_once(messages, cheap_cfg, temperature=0)

    def flagship_stream(messages):
        return stream_chat(messages, flagship_cfg, temperature=0.7)

    return AgentDeps(
        cheap_chat=cheap_chat,
        flagship_stream=flagship_stream,
        store=ProfileStore(db),
        db=db,
        profile_id=profile_id,
        jd_digest=(profile or {}).get("jd_digest") or "",
        resume_text=(profile or {}).get("resume_text") or "",
    )


def replay_providers_factory(path: str, speed: float) -> dict[str, ASRProvider]:
    data = json.loads(Path(path).read_text())
    # 支持单文件双通道 {interviewer: [...], me: [...]} 或纯数组（=interviewer）
    if isinstance(data, dict):
        return {
            ch: ReplayASRProvider(_write_tmp(items), speed=speed)
            for ch, items in data.items()
        }
    return {"interviewer": ReplayASRProvider(path, speed=speed)}


def _write_tmp(items) -> str:
    import tempfile
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(items, f, ensure_ascii=False)
    f.close()
    return f.name


# ── 应用工厂 ─────────────────────────────────────────────────
def create_app(
    db: DB | None = None,
    settings: Settings | None = None,
    deps_factory: Callable[..., AgentDeps] = default_deps_factory,
    providers_factory: Callable[..., dict[str, ASRProvider]] | None = None,
) -> FastAPI:
    db = db or DB()
    settings = settings or Settings()
    sessions: dict[str, SessionRuntime] = {}
    clients: set[WebSocket] = set()

    async def broadcast(msg: dict) -> None:
        dead = []
        for ws in clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    app = FastAPI(title="EchoPilot Sidecar")
    app.state.db = db
    app.state.settings = settings

    @app.get("/health")
    def health():
        return {"ok": True}

    # ── WebSocket ────────────────────────────────────────────
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # 客户端心跳/忽略
        except WebSocketDisconnect:
            clients.discard(websocket)

    # ── 档案（F8）────────────────────────────────────────────
    @app.post("/profiles")
    def create_profile(body: ProfileIn):
        return db.create_profile(body.name, body.resume_text, body.jd_text)

    @app.get("/profiles")
    def list_profiles():
        return db.list_profiles()

    @app.get("/profiles/{pid}")
    def get_profile(pid: str):
        p = db.get_profile(pid)
        if not p:
            raise HTTPException(404, "档案不存在")
        return p

    @app.put("/profiles/{pid}")
    def update_profile(pid: str, body: ProfileIn):
        p = db.update_profile(pid, name=body.name,
                              resume_text=body.resume_text, jd_text=body.jd_text)
        if not p:
            raise HTTPException(404, "档案不存在")
        return p

    @app.delete("/profiles/{pid}")
    def delete_profile(pid: str):
        db.delete_profile(pid)
        return {"ok": True}

    @app.post("/profiles/{pid}/cardify")
    async def cardify(pid: str):
        deps = deps_factory(db, settings, pid)
        if not hasattr(deps.store, "llm_chat") or deps.store.llm_chat is None:
            # 用 deps 的 cheap_chat 作为卡片化 LLM
            deps.store.llm_chat = lambda prompt: deps.cheap_chat(
                [{"role": "user", "content": prompt}])
        return await deps.store.cardify(pid)

    # ── 会话（F1–F10）────────────────────────────────────────
    @app.post("/sessions")
    async def start_session(body: SessionIn):
        if not db.get_profile(body.profile_id):
            raise HTTPException(404, "档案不存在")
        deps = deps_factory(db, settings, body.profile_id)

        if providers_factory:
            providers = providers_factory(body)
        elif body.replay_path:
            providers = replay_providers_factory(
                body.replay_path, body.replay_speed)
        else:
            raise HTTPException(
                400, "实时采集模式尚未启用（T7/T9 待权限与 ASR 供应商），"
                     "请使用 replay_path 回放模式")

        session = db.create_session(body.profile_id)
        last_final_t = [time.monotonic()]

        def silence_ms_fn() -> int:
            return int((time.monotonic() - last_final_t[0]) * 1000)

        runtime = SessionRuntime(
            session["session_id"], providers, silence_ms_fn,
            deps, db, broadcast, style=settings.default_style,
            silence_threshold_ms=settings.silence_threshold_ms)
        # 更新静音计时：面试官 final 到达时重置
        orig_feed = runtime.detector.feed

        def feed_and_reset(seg):
            if seg.channel == "interviewer" and seg.is_final:
                last_final_t[0] = time.monotonic()
            orig_feed(seg)

        runtime.detector.feed = feed_and_reset
        sessions[session["session_id"]] = runtime
        await runtime.start()
        return session

    @app.post("/sessions/{sid}/stop")
    async def stop_session(sid: str):
        runtime = sessions.pop(sid, None)
        if runtime:
            await runtime.stop()
        db.end_session(sid)
        return {"ok": True}

    @app.post("/sessions/{sid}/trigger")
    async def trigger(sid: str, body: TriggerIn):
        runtime = sessions.get(sid)
        if not runtime:
            raise HTTPException(404, "会话不在运行")
        return await runtime.trigger(body.mode, body.text)

    @app.post("/sessions/{sid}/style")
    def set_style(sid: str, body: StyleIn):
        runtime = sessions.get(sid)
        if not runtime:
            raise HTTPException(404, "会话不在运行")
        try:
            runtime.set_style(body.style)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "style": body.style}

    @app.get("/sessions")
    def list_sessions():
        return db.list_sessions()

    @app.get("/sessions/{sid}/export")
    def export_session(sid: str):
        session = db.get_session(sid)
        if not session:
            raise HTTPException(404, "会话不存在")
        return {
            "session": session,
            "segments": db.list_segments(sid),
            "turns": db.list_turns(sid),
        }

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str):
        db.delete_session(sid)
        return {"ok": True}

    # ── 设置（N6）────────────────────────────────────────────
    @app.put("/settings/keys")
    def set_key(body: KeyIn):
        keys.set_key(body.provider, body.api_key)
        return {"ok": True}

    @app.get("/settings")
    def get_settings():
        s = app.state.settings
        return {k: v for k, v in vars(s).items()}

    class SettingsIn(BaseModel):
        llm_base_url: str | None = None
        llm_cheap_model: str | None = None
        llm_flagship_model: str | None = None
        llm_provider: str | None = None
        silence_threshold_ms: int | None = None
        default_style: str | None = None

    @app.put("/settings")
    def update_settings(body: SettingsIn):
        s = app.state.settings
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(s, k, v)
        return {k: v for k, v in vars(s).items()}

    return app


# 启动方式（工厂模式，避免 import 时触碰默认 DB 路径）：
#   uvicorn sidecar.main:create_app --factory --port 18321
