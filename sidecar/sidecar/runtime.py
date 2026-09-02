"""会话运行时：把一个面试会话的各模块串成闭环。

采集/回放 → ASR 编排 → 检测器 → LangGraph 轮次 → WS 广播。
追问合并：新问题到达时取消仍在生成的旧轮（M4 同时只跑一轮）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from sidecar.agent.graph import run_turn
from sidecar.agent.nodes import AgentDeps
from sidecar.asr.providers import ASRProvider
from sidecar.asr.stream import ASROrchestrator
from sidecar.detect.detector import QuestionDetector
from sidecar.storage.db import DB

log = logging.getLogger(__name__)

Broadcast = Callable[[dict], Awaitable[None]]


class SessionRuntime:
    def __init__(
        self,
        session_id: str,
        providers: dict[str, ASRProvider],
        silence_ms_fn: Callable[[], int],
        deps: AgentDeps,
        db: DB,
        broadcast: Broadcast,
        *,
        style: str = "standard",
        eval_interval_ms: int = 300,
        silence_threshold_ms: int = 800,
    ):
        self.session_id = session_id
        self.deps = deps
        self.db = db
        self.broadcast = broadcast
        self.style = style
        self.eval_interval_ms = eval_interval_ms
        self.orchestrator = ASROrchestrator(providers, db, session_id)
        self.detector = QuestionDetector(
            silence_ms_fn=silence_ms_fn,
            on_question=self._on_question_sync,
            silence_threshold_ms=silence_threshold_ms,
        )
        self._tasks: list[asyncio.Task] = []
        self._turn_task: asyncio.Task | None = None
        self._last_question: tuple[str, int] | None = None
        self._running = False

    # ── 生命周期 ──────────────────────────────────────────────
    async def start(self) -> None:
        self._running = True
        self._tasks.append(asyncio.create_task(self._consume_segments()))
        self._tasks.append(asyncio.create_task(self._eval_loop()))

    async def stop(self) -> None:
        self._running = False
        if self._turn_task:
            self._turn_task.cancel()
        await self.orchestrator.stop()
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    # ── 内部任务 ──────────────────────────────────────────────
    async def _consume_segments(self) -> None:
        try:
            async for seg in self.orchestrator.run():
                self.detector.feed(seg)
                await self.broadcast({"type": "asr.segment", **seg.to_dict()})
        except asyncio.CancelledError:
            pass

    async def _eval_loop(self) -> None:
        try:
            while self._running:
                await self.detector.evaluate()
                await asyncio.sleep(self.eval_interval_ms / 1000)
        except asyncio.CancelledError:
            pass

    def _on_question_sync(self, text: str, end_ms: int) -> None:
        """检测器回调（同步上下文）：广播并启动生成任务。"""
        self._last_question = (text, end_ms)
        asyncio.get_event_loop().create_task(
            self.broadcast(
                {"type": "question.detected", "text": text,
                 "question_end_ms": end_ms}))
        self._start_turn(text, end_ms, trigger="auto")

    def _start_turn(self, text: str, end_ms: int, trigger: str) -> None:
        # 追问合并：取消仍在生成的旧轮（同时只跑一轮）
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = asyncio.create_task(
            self._run_and_broadcast(text, end_ms, trigger))

    async def _run_and_broadcast(self, text: str, end_ms: int,
                                 trigger: str) -> None:
        try:
            async for event in run_turn(
                    self.deps, self.session_id, text, end_ms,
                    style=self.style, trigger=trigger):
                await self.broadcast({
                    "type": f"answer.{event['type']}",
                    **{k: v for k, v in event.items() if k != "type"}})
        except asyncio.CancelledError:
            pass
        except Exception as e:  # N8：生成失败要可感知
            log.exception("turn failed")
            await self.broadcast({
                "type": "status.health", "channel_ok": True, "asr_ok": True,
                "llm_ok": False, "detail": str(e)})

    # ── 手动控制（F5, F7）────────────────────────────────────
    async def trigger(self, mode: str, text: str | None = None) -> dict:
        if mode == "manual":
            question = text or self.detector.flush_manual()
            if not question:
                return {"ok": False, "error": "暂无可触发的转写内容"}
            self._last_question = (question, int(time.time() * 1000))
            self._start_turn(question, self._last_question[1], trigger="manual")
            return {"ok": True, "question": question}
        if mode == "regen":
            if not self._last_question:
                return {"ok": False, "error": "尚无问题可重新生成"}
            q, end_ms = self._last_question
            self._start_turn(q, end_ms, trigger="manual")
            return {"ok": True, "question": q}
        return {"ok": False, "error": f"未知 mode: {mode}"}

    def set_style(self, style: str) -> None:
        if style not in ("concise", "standard", "detailed"):
            raise ValueError(f"未知档位: {style}")
        self.style = style
