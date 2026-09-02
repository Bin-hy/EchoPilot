"""LLMProvider：OpenAI 兼容协议流式客户端（N10）。

两档模型：cheap（分类/检测兜底）与 flagship（回答生成）。
错误分类供 status.health 上报（N8）：auth / quota / network / unknown。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind  # "auth" | "quota" | "network" | "unknown"
        self.detail = detail


@dataclass
class LLMConfig:
    base_url: str          # 如 https://api.deepseek.com/v1
    api_key: str
    model: str
    timeout_s: float = 30.0


def _classify_http_error(status: int, body: str) -> LLMError:
    if status in (401, 403):
        return LLMError("auth", f"HTTP {status}: {body[:200]}")
    if status == 429:
        return LLMError("quota", f"HTTP 429: {body[:200]}")
    return LLMError("unknown", f"HTTP {status}: {body[:200]}")


async def stream_chat(
    messages: list[dict],
    config: LLMConfig,
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> AsyncIterator[str]:
    """OpenAI 兼容 /chat/completions 流式调用，逐 delta 产出文本。"""
    payload: dict = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if response_format:
        payload["response_format"] = response_format

    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.api_key}"}

    try:
        async with httpx.AsyncClient(timeout=config.timeout_s) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise _classify_http_error(resp.status_code, body)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        yield delta
    except httpx.HTTPError as e:
        raise LLMError("network", str(e)) from e


async def chat_once(messages: list[dict], config: LLMConfig, **kwargs) -> str:
    """非流式便捷封装：收集完整回答。"""
    parts = [d async for d in stream_chat(messages, config, **kwargs)]
    return "".join(parts)
