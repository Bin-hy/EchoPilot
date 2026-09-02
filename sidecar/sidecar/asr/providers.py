"""ASRProvider 适配层（N10）：统一接口 + 回放适配器 + 云端流式适配器。

- ASRSegment：plan.md 核心数据结构（channel 归属即说话人）。
- ReplayASRProvider：从标注 JSON 回放面试音频转写，供 AC4/AC8/AC13 测试
  复用（练习模式延后可替代的测试链路）。
- CloudStreamASRProvider：云端流式 ASR（WebSocket 发 PCM 帧），
  V0.1 以 OpenAI 兼容 realtime 接口为例，可换供应商。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import AsyncIterator


@dataclass
class ASRSegment:
    segment_id: str
    channel: str          # "interviewer" | "me"
    text: str
    start_ms: int
    end_ms: int
    is_final: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class ASRProvider(ABC):
    @abstractmethod
    def open_stream(self, channel: str) -> AsyncIterator[ASRSegment]:
        """开启一条识别流，按时间产出 segment（interim 与 final）。"""
        ...


@dataclass
class ReplayItem:
    text: str
    start_ms: int
    end_ms: int


class ReplayASRProvider(ASRProvider):
    """回放适配器：读标注 JSON [{text,start_ms,end_ms}, ...]，
    按时间轴产出 final segment；speed > 1 可加速回放。"""

    def __init__(self, transcript_path: str | Path, speed: float = 1.0,
                 emit_interim: bool = True):
        self.items = [
            ReplayItem(**it) for it in json.loads(Path(transcript_path).read_text())
        ]
        self.speed = speed
        self.emit_interim = emit_interim

    async def open_stream(self, channel: str) -> AsyncIterator[ASRSegment]:
        t0 = time.monotonic()
        for i, item in enumerate(self.items):
            target = item.start_ms / 1000 / self.speed
            delay = t0 + target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            seg_id = f"replay-{i}"
            if self.emit_interim:
                half = max(1, len(item.text) // 2)
                yield ASRSegment(seg_id, channel, item.text[:half],
                                 item.start_ms, item.start_ms + 1, False)
            yield ASRSegment(seg_id, channel, item.text,
                             item.start_ms, item.end_ms, True)


class CloudStreamASRProvider(ASRProvider):
    """云端流式 ASR 适配器（OpenAI 兼容 realtime 风格 WS 协议）。

    子类或配置可覆盖 build_url / 消息格式以适配不同供应商。
    断线抛出 ASRError(kind="network") 供 status.health 上报（N8）。
    """

    def __init__(self, base_ws_url: str, api_key: str, model: str,
                 sample_rate: int = 16000):
        self.base_ws_url = base_ws_url
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self._queues: dict[str, asyncio.Queue] = {}
        self._seq = 0

    async def feed_pcm(self, channel: str, pcm_bytes: bytes) -> None:
        """由采集层回调喂入 20ms PCM 帧。"""
        # V0.1：真实实现按供应商协议封帧发送；此处保留接口形状。
        raise NotImplementedError("待 T7 按选定供应商协议实现")

    async def open_stream(self, channel: str) -> AsyncIterator[ASRSegment]:
        q: asyncio.Queue = self._queues.setdefault(channel, asyncio.Queue())
        while True:
            seg = await q.get()
            if seg is None:  # 流结束信号
                return
            yield seg


class ASRError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind  # "auth" | "quota" | "network" | "unknown"
        self.detail = detail
