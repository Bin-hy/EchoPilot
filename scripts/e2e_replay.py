"""T22 端到端联调脚本：真实 App + 回放链路 + fake LLM。

前提：App 以 ECHOPILOT_FAKE_LLM=1 运行（guardian 已拉起 sidecar）。
流程：建档案 → 开始回放会话 → WS 收集事件 → 断言 6 类消息 →
      停止 → 导出历史 → 删除。退出码 0 = 全链路通过。
"""
import asyncio
import json
import sys

import httpx
import websockets

API = "http://127.0.0.1:18321"
WS = "ws://127.0.0.1:18321/ws"
REPLAY = "/Users/binhy/Binhy-Projects/EchoPilot/samples/interview_replay.json"
RESUME = "星图科技 后端工程师 2021-2023，主导 Kafka 消息平台改造，延迟降低 37%，支撑 200万 QPS。"


async def main() -> int:
    async with httpx.AsyncClient(base_url=API, timeout=10) as client:
        health = (await client.get("/health")).json()
        assert health["ok"], "sidecar 不健康"

        profile = (await client.post("/profiles", json={
            "name": "e2e-回放测试", "resume_text": RESUME,
            "jd_text": "熟悉消息队列"})).json()
        pid = profile["profile_id"]

        received: list[dict] = []

        async def listen():
            async with websockets.connect(WS) as ws:
                async for raw in ws:
                    received.append(json.loads(raw))
                    if len(received) > 500:
                        return

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.5)

        session = (await client.post("/sessions", json={
            "profile_id": pid, "replay_path": REPLAY,
            "replay_speed": 20.0})).json()
        sid = session["session_id"]
        print(f"会话开始: {sid}")

        # 回放 4 分钟 / 20 倍速 ≈ 12s + 尾部检测时间。
        # 先等全部 8 段转写到达，再等最后一题的生成完成。
        deadline = asyncio.get_event_loop().time() + 60
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            finals = [m for m in received if m["type"] == "asr.segment"
                      and m.get("is_final")]
            dones = [m for m in received if m["type"] == "answer.done"]
            if len(finals) >= 8:
                # 全部段落到齐后再给尾部检测留出时间
                if len(dones) >= 3:
                    await asyncio.sleep(3)
                    break

        await client.post(f"/sessions/{sid}/stop")
        listener.cancel()

        types = {m["type"] for m in received}
        questions = [m for m in received if m["type"] == "question.detected"]
        skeletons = [m for m in received if m["type"] == "answer.skeleton"]
        dones = [m for m in received if m["type"] == "answer.done"]

        print(f"收到消息 {len(received)} 条: " +
              ", ".join(f"{t}×{sum(1 for m in received if m['type'] == t)}"
                        for t in sorted(types)))

        # ── 断言 ──
        assert "asr.segment" in types, "缺 asr.segment"
        assert len(questions) >= 3, f"自动检测问题数不足: {len(questions)}"
        assert len(skeletons) >= 3, f"提纲数不足: {len(skeletons)}"
        assert "answer.delta" in types, "缺 answer.delta"
        assert len(dones) >= 3, f"answer.done 不足: {len(dones)}"

        # 附和不触发：样本中 "好的"/"嗯" 不应成为问题
        qtexts = [q["text"] for q in questions]
        assert not any(t in ("好的", "嗯") for t in qtexts), f"附和被误触发: {qtexts}"
        # 祈使句触发
        assert any("自我介绍" in t for t in qtexts), f"未检出自我介绍: {qtexts}"
        assert any("最有挑战" in t for t in qtexts), f"未检出祈使句: {qtexts}"

        # skeleton 先于 delta（每个 turn 内时序）
        first_skeleton_idx = next(
            i for i, m in enumerate(received) if m["type"] == "answer.skeleton")
        first_delta_idx = next(
            i for i, m in enumerate(received) if m["type"] == "answer.delta")
        assert first_skeleton_idx < first_delta_idx, "提纲未先于完整稿"

        # 落库回看
        exported = (await client.get(f"/sessions/{sid}/export")).json()
        assert len(exported["segments"]) == 8, \
            f"转写段数: {len(exported['segments'])}"
        assert len(exported["turns"]) >= 3
        for turn in exported["turns"]:
            assert turn["answer_skeleton"], "提纲未落库"
            assert turn["latency_ms"].get("total") is not None

        # 删除无残留
        await client.delete(f"/sessions/{sid}")
        assert (await client.get(f"/sessions/{sid}/export")).status_code == 404
        await client.delete(f"/profiles/{pid}")

        print("T22 端到端联调：PASS（回放 → 检测 → 生成 → 落库 → 删除 全链路）")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
