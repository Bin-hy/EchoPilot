"""M2: 双通道 ASR 编排。

每通道一条 ASR 流（真实采集走 CloudStreamASRProvider，测试走
ReplayASRProvider），合并为统一 segment 流；final segment 异步落库
（F2, F10）；channel 标签即说话人（F1）。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from sidecar.asr.providers import ASRProvider, ASRSegment
from sidecar.storage.db import DB


class ASROrchestrator:
    def __init__(self, providers: dict[str, ASRProvider], db: DB | None,
                 session_id: str):
        """
        providers: {"interviewer": provider, "me": provider}
        db: 为 None 时不落库（纯测试用）
        """
        self.providers = providers
        self.db = db
        self.session_id = session_id
        self._queue: asyncio.Queue[ASRSegment | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def _pump(self, channel: str, provider: ASRProvider) -> None:
        try:
            async for seg in provider.open_stream(channel):
                if not self._running:
                    break
                seg.channel = channel  # 通道归属即说话人标签（防串轨）
                if seg.is_final and self.db:
                    self.db.insert_segment(
                        self.session_id, channel, seg.text,
                        seg.start_ms, seg.end_ms)
                await self._queue.put(seg)
        finally:
            await self._queue.put(None)  # 该通道结束信号

    async def run(self) -> AsyncIterator[ASRSegment]:
        """启动各通道泵，产出合并后的 segment 流。"""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._pump(ch, p))
            for ch, p in self.providers.items()
        ]
        finished = 0
        try:
            while finished < len(self._tasks):
                item = await self._queue.get()
                if item is None:
                    finished += 1
                else:
                    yield item
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
