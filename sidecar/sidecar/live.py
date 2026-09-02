"""实时模式管线：采集 → VAD → 云端 ASR（F1–F3）。

把 DualChannelCapture 的两路帧流分别泵入 CloudStreamASRProvider，
面试官通道同时喂 silero VAD 提供静音信号（供检测器）。
与回放模式共用下游（编排器/检测器/Agent），协议一致。
"""
from __future__ import annotations

import asyncio
import logging

from sidecar.asr.providers import (
    ASRError, ASRProvider, CloudASRConfig, CloudStreamASRProvider,
)
from sidecar.audio.capture import DualChannelCapture
from sidecar.audio.vad import ChannelVAD

log = logging.getLogger(__name__)


class LivePipeline:
    def __init__(
        self,
        asr_config: CloudASRConfig,
        capture: DualChannelCapture | None = None,
        on_error=None,
    ):
        self.capture = capture or DualChannelCapture()
        self.asr = CloudStreamASRProvider(asr_config, on_error=on_error)
        self.vad_interviewer = ChannelVAD()
        self._pump_tasks: list[asyncio.Task] = []

    def check_channels(self) -> dict:
        """F3 自检：通道 + ASR 配置。"""
        return self.capture.check_channels()

    async def start(self) -> None:
        self.capture.start()
        for channel in ("interviewer", "me"):
            await self.asr.start_channel(channel)
            self._pump_tasks.append(asyncio.create_task(
                self._pump(channel)))

    async def _pump(self, channel: str) -> None:
        try:
            async for frame in self.capture.frames(channel):
                if channel == "interviewer":
                    self.vad_interviewer.feed(frame)
                await self.asr.feed_pcm(channel, frame)
        except asyncio.CancelledError:
            pass

    @property
    def providers(self) -> dict[str, ASRProvider]:
        return {"interviewer": self.asr, "me": self.asr}

    def silence_ms_fn(self) -> int:
        return self.vad_interviewer.state()["silence_ms"]

    async def stop(self) -> None:
        self.capture.stop()
        for t in self._pump_tasks:
            t.cancel()
        await self.asr.close()
