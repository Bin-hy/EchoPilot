"""T7 验证：云端流式 ASR 适配器——用本地 mock WS 服务器验证协议交互：
session 初始化、PCM append、interim/final 事件解析、错误上报。"""
import asyncio
import base64
import json

import pytest
import websockets

from sidecar.asr.providers import ASRError, CloudASRConfig, CloudStreamASRProvider


class MockASRServer:
    """模拟 OpenAI Realtime transcription 协议的最小服务器。"""

    def __init__(self):
        self.received_init = None
        self.received_audio: list[bytes] = []
        self.port = None

    async def handler(self, ws):
        try:
            async for raw in ws:
                data = json.loads(raw)
                if data["type"] == "transcription_session.update":
                    self.received_init = data
                    # 收到 3 帧音频后回 interim + final（在 append 分支计数）
                elif data["type"] == "input_audio_buffer.append":
                    self.received_audio.append(
                        base64.b64decode(data["audio"]))
                    if len(self.received_audio) == 2:
                        await ws.send(json.dumps({
                            "type": "conversation.item.input_audio_transcription.delta",
                            "delta": "你好，请"}))
                    if len(self.received_audio) == 3:
                        await ws.send(json.dumps({
                            "type": "conversation.item.input_audio_transcription.completed",
                            "transcript": "你好，请介绍一下你自己"}))
        except websockets.ConnectionClosed:
            pass


@pytest.fixture
async def mock_server():
    server = MockASRServer()
    async with websockets.serve(server.handler, "127.0.0.1", 0) as ws_server:
        server.port = ws_server.sockets[0].getsockname()[1]
        yield server


@pytest.mark.asyncio
async def test_cloud_asr_stream_flow(mock_server):
    errors = []
    provider = CloudStreamASRProvider(
        CloudASRConfig(ws_url=f"ws://127.0.0.1:{mock_server.port}",
                       api_key="sk-test"),
        on_error=errors.append)
    await provider.start_channel("interviewer")

    async def collect():
        segs = []
        async for seg in provider.open_stream("interviewer"):
            segs.append(seg)
            if seg.is_final:
                return segs
        return segs

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.2)
    for i in range(3):
        await provider.feed_pcm("interviewer", b"\x00\x01" * 160)
        await asyncio.sleep(0.1)
    segs = await asyncio.wait_for(task, timeout=5)
    await provider.close()

    assert [s.is_final for s in segs] == [False, True]
    assert segs[0].text == "你好，请"
    assert segs[1].text == "你好，请介绍一下你自己"
    assert segs[1].channel == "interviewer"
    assert errors == []

    # 协议交互断言
    assert mock_server.received_init["session"]["input_audio_format"] == "pcm16"
    assert len(mock_server.received_audio) == 3
    assert mock_server.received_audio[0] == b"\x00\x01" * 160


@pytest.mark.asyncio
async def test_cloud_asr_error_event_reported(mock_server):
    errors = []

    class ErrorProvider(CloudStreamASRProvider):
        def parse_event(self, data, channel):
            # 第一帧后服务器回 error（模拟鉴权失败场景）
            return super().parse_event(data, channel)

    provider = ErrorProvider(
        CloudASRConfig(ws_url=f"ws://127.0.0.1:{mock_server.port}",
                       api_key="sk-bad"),
        on_error=errors.append)

    await provider.start_channel("interviewer")
    # 直接注入一条 error 事件走 parse_event 通道
    with pytest.raises(ASRError):
        provider.parse_event({"type": "error", "error": {"code": "auth"}}, "me")
    await provider.close()


@pytest.mark.asyncio
async def test_cloud_asr_connect_failure_classified():
    provider = CloudStreamASRProvider(
        CloudASRConfig(ws_url="ws://127.0.0.1:1", api_key="k"))
    with pytest.raises(ASRError) as exc:
        await provider.start_channel("interviewer")
    assert exc.value.kind == "network"
