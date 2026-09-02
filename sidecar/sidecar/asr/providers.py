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
from typing import AsyncIterator, Callable


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


@dataclass
class CloudASRConfig:
    ws_url: str                      # 如 wss://api.openai.com/v1/realtime?intent=transcription
    api_key: str
    model: str = "gpt-4o-transcribe"
    extra_headers: dict = field(default_factory=dict)


class CloudStreamASRProvider(ASRProvider):
    """云端流式 ASR 适配器（OpenAI Realtime transcription 协议，N10）。

    供应商差异收敛在三个协议钩子上：
    session_init_msg() / append_msg() / parse_event() ——
    换 Deepgram/阿里云时子类覆盖这三个方法即可。
    """

    def __init__(self, config: CloudASRConfig,
                 on_error: Callable[["ASRError"], None] | None = None):
        self.config = config
        self.on_error = on_error
        self._queues: dict[str, asyncio.Queue] = {}
        self._senders: dict[str, Callable[[bytes], None]] = {}
        self._tasks: list[asyncio.Task] = []
        self._t0: dict[str, float] = {}
        self._seq = 0

    # ── 协议钩子 ──────────────────────────────────────────────
    def session_init_msg(self) -> dict | None:
        return {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": self.config.model},
                "turn_detection": {"type": "server_vad", "threshold": 0.5,
                                   "silence_duration_ms": 500},
            },
        }

    def append_msg(self, pcm: bytes) -> dict:
        return {"type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode()}

    def parse_event(self, data: dict, channel: str) -> ASRSegment | None:
        etype = data.get("type", "")
        if etype == "conversation.item.input_audio_transcription.delta":
            return self._make(channel, data.get("delta", ""), False)
        if etype == "conversation.item.input_audio_transcription.completed":
            return self._make(channel, data.get("transcript", ""), True)
        if etype == "error":
            raise ASRError("unknown", json.dumps(data)[:200])
        return None

    # ── 生命周期 ──────────────────────────────────────────────
    def _make(self, channel: str, text: str, is_final: bool) -> ASRSegment:
        self._seq += 1
        now_ms = int((time.monotonic() - self._t0.get(channel, time.monotonic()))
                     * 1000)
        return ASRSegment(f"cloud-{channel}-{self._seq}", channel, text,
                          max(0, now_ms - 1000), now_ms, is_final)

    async def start_channel(self, channel: str) -> None:
        import websockets
        self._queues[channel] = asyncio.Queue()
        self._t0[channel] = time.monotonic()
        headers = {"Authorization": f"Bearer {self.config.api_key}",
                   "OpenAI-Beta": "realtime=v1",
                   **self.config.extra_headers}
        try:
            ws = await websockets.connect(
                self.config.ws_url, additional_headers=headers)
        except Exception as e:
            raise ASRError("network", f"WS 连接失败: {e}") from e

        init = self.session_init_msg()
        if init:
            await ws.send(json.dumps(init))

        self._senders[channel] = lambda pcm: asyncio.create_task(
            ws.send(json.dumps(self.append_msg(pcm))))
        self._tasks.append(asyncio.create_task(
            self._reader(ws, channel)))

    async def _reader(self, ws, channel: str) -> None:
        q = self._queues[channel]
        try:
            async for raw in ws:
                try:
                    seg = self.parse_event(json.loads(raw), channel)
                except ASRError as e:
                    if self.on_error:
                        self.on_error(e)
                    continue
                if seg and seg.text:
                    await q.put(seg)
        except Exception as e:
            if self.on_error:
                self.on_error(ASRError("network", str(e)))
        finally:
            await q.put(None)

    async def feed_pcm(self, channel: str, pcm_bytes: bytes) -> None:
        sender = self._senders.get(channel)
        if sender:
            sender(pcm_bytes)

    async def open_stream(self, channel: str) -> AsyncIterator[ASRSegment]:
        q = self._queues.setdefault(channel, asyncio.Queue())
        while True:
            seg = await q.get()
            if seg is None:  # 流结束信号
                return
            yield seg

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []


class ASRError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind  # "auth" | "quota" | "network" | "unknown"
        self.detail = detail
