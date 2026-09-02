"""T5 验证：流式解析 SSE、错误分类（auth/quota/network）。真实 API 验证待用户提供 Key。"""
import json
import pytest
import httpx

from sidecar.llm.provider import LLMConfig, LLMError, stream_chat, chat_once


def sse_body(deltas: list[str]) -> bytes:
    lines = []
    for d in deltas:
        chunk = {"choices": [{"delta": {"content": d}}]}
        lines.append(f"data: {json.dumps(chunk)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def make_transport(deltas=None, status=200, body=b""):
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 200:
            return httpx.Response(200, content=sse_body(deltas or []))
        return httpx.Response(status, content=body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_stream_yields_deltas(monkeypatch):
    cfg = LLMConfig("http://mock/v1", "k", "m")
    transport = make_transport(["你好", "，世界", "！"])
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: orig(transport=transport, **kw))
    parts = [d async for d in stream_chat([{"role": "user", "content": "hi"}], cfg)]
    assert parts == ["你好", "，世界", "！"]
    assert await chat_once([], cfg) == "你好，世界！"


@pytest.mark.asyncio
async def test_error_classification(monkeypatch):
    cfg = LLMConfig("http://mock/v1", "k", "m")
    orig = httpx.AsyncClient
    for status, kind in [(401, "auth"), (403, "auth"), (429, "quota"), (500, "unknown")]:
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: orig(transport=make_transport(status=status, body=b"err"), **kw))
        with pytest.raises(LLMError) as exc:
            async for _ in stream_chat([], cfg):
                pass
        assert exc.value.kind == kind


@pytest.mark.asyncio
async def test_network_error(monkeypatch):
    cfg = LLMConfig("http://127.0.0.1:1/v1", "k", "m", timeout_s=1.0)
    with pytest.raises(LLMError) as exc:
        async for _ in stream_chat([], cfg):
            pass
    assert exc.value.kind == "network"
