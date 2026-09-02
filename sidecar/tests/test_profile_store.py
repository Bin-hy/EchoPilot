"""T15 验证：卡片化入库、JD 摘要、向量检索 top-k、profile_context 渲染。"""
import json
import math

import pytest

from sidecar.profile.store import ProfileStore
from sidecar.storage.db import DB

RESUME = "星图科技 后端工程师 2021-2023：主导 Kafka 消息平台改造，延迟降低 37%。"

CARDS_JSON = json.dumps([
    {"title": "Kafka 消息平台改造", "org": "星图科技", "role": "后端工程师",
     "period": "2021-2023", "tech_stack": ["Kafka"],
     "achievements": ["延迟降低 37%"], "keywords": ["消息队列", "Kafka"],
     "raw_excerpt": "主导 Kafka 消息平台改造"},
    {"title": "API 网关重构", "org": "星图科技", "role": "后端工程师",
     "period": "2022", "tech_stack": ["Go"],
     "achievements": ["QPS 提升 3倍"], "keywords": ["网关"],
     "raw_excerpt": "重构 API 网关"},
], ensure_ascii=False)

DIGEST_JSON = json.dumps(
    {"requirements": ["5 年后端", "熟悉消息队列"], "keywords": ["Kafka"],
     "seniority": "senior"}, ensure_ascii=False)


async def fake_embed(texts):
    """确定性假 embedding：含 "Kafka" 的文本向量指向第 0 维。"""
    vecs = []
    for t in texts:
        v = [0.0] * 8
        v[0] = 1.0 if "Kafka" in t or "消息队列" in t else 0.0
        v[1] = 1.0 if "网关" in t else 0.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs.append([x / norm for x in v])
    return vecs


async def fake_llm(prompt: str) -> str:
    if "简历" in prompt:
        return f"```json\n{CARDS_JSON}\n```"
    return DIGEST_JSON


@pytest.mark.asyncio
async def test_cardify_and_search(tmp_path):
    db = DB(tmp_path / "t.db")
    store = ProfileStore(db, llm_chat=fake_llm, embed=fake_embed, embed_dim=8)
    p = db.create_profile("字节", RESUME, "JD：熟悉消息队列")

    result = await store.cardify(p["profile_id"])
    assert result["cards"] == 2
    assert result["jd_digest"]["seniority"] == "senior"

    cards = db.list_cards(p["profile_id"])
    assert len(cards) == 2
    assert cards[0]["achievements"] == ["延迟降低 37%"]
    digest = json.loads(db.get_profile(p["profile_id"])["jd_digest"])
    assert "Kafka" in digest["keywords"]

    # 用「Kafka 项目」查询应命中 Kafka 卡片而非网关卡片（AC10 雏形）
    top = await store.search_cards(p["profile_id"], "Kafka 项目", k=1)
    assert len(top) == 1
    assert top[0]["title"] == "Kafka 消息平台改造"
    db.close()


@pytest.mark.asyncio
async def test_search_without_embed_falls_back(tmp_path):
    db = DB(tmp_path / "t.db")
    store = ProfileStore(db, llm_chat=fake_llm, embed=None)
    p = db.create_profile("p", RESUME, "j")
    await store.cardify(p["profile_id"])
    cards = await store.search_cards(p["profile_id"], "任意", k=1)
    assert len(cards) == 1  # 退化为全量截断
    db.close()


def test_profile_context_render(tmp_path):
    db = DB(tmp_path / "t.db")
    store = ProfileStore(db)
    p = db.create_profile("p", RESUME, "j")
    db.replace_cards(p["profile_id"], json.loads(CARDS_JSON))
    ctx = store.profile_context(db.list_cards(p["profile_id"]))
    assert "Kafka 消息平台改造" in ctx
    assert "延迟降低 37%" in ctx
    assert "星图科技" in ctx
    db.close()
