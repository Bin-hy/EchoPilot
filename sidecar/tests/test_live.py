"""LivePipeline 集成验证：采集(fake) → VAD(真实) → 云端 ASR(mock WS) 全链。"""
import asyncio
import base64
import json

import pytest
import websockets

from sidecar.asr.providers import CloudASRConfig
from sidecar.audio.capture import DualChannelCapture
from sidecar.live import LivePipeline


class FakeSource:
    def check(self):
        return True, None

    def start(self, on_frame):
        self.on_frame = on_frame

    def stop(self):
        pass

    def emit(self, pcm: bytes):
        self.on_frame(pcm)


async def mock_handler(ws):
    count = 0
    try:
        async for raw in ws:
            data = json.loads(raw)
            if data["type"] == "input_audio_buffer.append":
                count += 1
                if count == 2:
                    await ws.send(json.dumps({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "聊聊你的项目"}))
    except websockets.ConnectionClosed:
        pass


@pytest.mark.asyncio
async def test_live_pipeline_capture_to_asr():
    async with websockets.serve(mock_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        mic, system = FakeSource(), FakeSource()
        capture = DualChannelCapture(mic=mic, system=system)
        pipeline = LivePipeline(
            CloudASRConfig(ws_url=f"ws://127.0.0.1:{port}", api_key="k"),
            capture=capture)

        assert pipeline.check_channels()["system"] is True

        await pipeline.start()

        async def collect():
            async for seg in pipeline.providers["interviewer"].open_stream(
                    "interviewer"):
                if seg.is_final:
                    return seg

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.3)
        # 模拟面试官通道出声（静音 PCM 喂 VAD + 发 ASR）
        for _ in range(3):
            system.emit(b"\x00\x00" * 320)  # 20ms 静音帧
            await asyncio.sleep(0.05)

        seg = await asyncio.wait_for(task, timeout=5)
        assert seg.text == "聊聊你的项目"
        assert seg.channel == "interviewer"

        # VAD 静音信号：喂的是静音帧 → silence_ms 应累计
        await asyncio.sleep(0.5)
        assert pipeline.silence_ms_fn() > 0

        await pipeline.stop()
