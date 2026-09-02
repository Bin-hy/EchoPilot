"""M6: API Key 存储——macOS 钥匙串，进程内缓存，不明文落盘（N6）。"""
from __future__ import annotations

import keyring

SERVICE = "EchoPilot"
_cache: dict[str, str] = {}


def set_key(provider: str, secret: str) -> None:
    keyring.set_password(SERVICE, provider, secret)
    _cache[provider] = secret


def get_key(provider: str) -> str | None:
    if provider in _cache:
        return _cache[provider]
    value = keyring.get_password(SERVICE, provider)
    if value:
        _cache[provider] = value
    return value


def delete_key(provider: str) -> None:
    _cache.pop(provider, None)
    try:
        keyring.delete_password(SERVICE, provider)
    except keyring.errors.PasswordDeleteError:
        pass
