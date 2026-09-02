"""sidecar 独立二进制入口（PyInstaller 打包用）。

用法：echopilot-sidecar [--port 18321]
开发模式仍可用：uv run uvicorn sidecar.main:create_app --factory
"""
import argparse

import uvicorn

from sidecar.main import create_app


def main():
    parser = argparse.ArgumentParser(description="EchoPilot Sidecar")
    parser.add_argument("--port", type=int, default=18321)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
