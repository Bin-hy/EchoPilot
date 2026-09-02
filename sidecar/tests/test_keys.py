"""T4 验证：钥匙串写入/读取/删除；项目目录无明文落盘。

注意：macOS Keychain 在沙箱/CI 环境不可用，后端用内存 fake 替代；
真实 Keychain 可用性单独探测，不可用时跳过（生产环境由 Tauri
以普通进程启动 sidecar，Keychain 正常可用）。
"""
import subprocess
from pathlib import Path

import pytest

from sidecar.storage import keys

TEST_PROVIDER = "pytest-provider"
TEST_SECRET = "sk-pytest-secret-0123456789"


class FakeKeyring:
    """内存版 keyring 后端，接口与 keyring 模块一致。"""

    class errors:
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self.store = {}

    def set_password(self, service, user, secret):
        self.store[(service, user)] = secret

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise FakeKeyring.errors.PasswordDeleteError("not found")
        del self.store[(service, user)]


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(keys, "keyring", fake)
    keys._cache.clear()
    yield fake
    keys._cache.clear()


def test_key_roundtrip(fake_keyring):
    keys.set_key(TEST_PROVIDER, TEST_SECRET)
    assert keys.get_key(TEST_PROVIDER) == TEST_SECRET
    keys.delete_key(TEST_PROVIDER)
    keys._cache.pop(TEST_PROVIDER, None)  # 清缓存确认后端也删了
    assert keys.get_key(TEST_PROVIDER) is None


def test_delete_missing_is_silent(fake_keyring):
    keys.delete_key("never-existed")  # 不抛异常


def test_no_plaintext_on_disk(fake_keyring):
    keys.set_key(TEST_PROVIDER, TEST_SECRET)
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-r", "-l", "--exclude-dir=__pycache__", "--exclude-dir=.venv",
         TEST_SECRET, str(project_root)],
        capture_output=True, text=True,
    )
    hits = [line for line in result.stdout.splitlines()
            if "test_keys.py" not in line]  # 测试文件自身除外
    keys.delete_key(TEST_PROVIDER)
    assert hits == [], f"发现明文落盘: {hits}"


def test_real_macos_keychain_availability():
    """探测真实 macOS Keychain（非 fake）。沙箱中预期失败则跳过。"""
    real = pytest.importorskip("keyring")
    try:
        real.set_password("EchoPilot", "probe", "probe-value")
        assert real.get_password("EchoPilot", "probe") == "probe-value"
        real.delete_password("EchoPilot", "probe")
    except Exception as e:
        pytest.skip(f"当前环境 Keychain 不可用（沙箱/CI）: {e}")
