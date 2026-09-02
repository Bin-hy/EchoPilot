"""M5: 档案管理 + LLM 预处理（F6, F8）。

保存档案时：
1. LLM 把简历压缩为 ProfileCard[]，JD 压缩为 JDDigest（JSON 模式）
2. 卡片文本 embedding 写入 sqlite-vec 本地向量索引
检索：search_cards() top-k，供 agent retrieve 节点使用。

LLM 与 embedding 函数均注入，便于测试与供应商替换（N10）。
"""
from __future__ import annotations

import json
import struct
from typing import Awaitable, Callable

import sqlite_vec

from sidecar.storage.db import DB

EMBED_DIM = 1024  # 常见中文 embedding 维度，按实际模型配置

CARDIFY_PROMPT = """把下面的简历压缩成若干张「经历卡片」。每段公司/项目经历一张卡。
输出 JSON 数组，每个元素字段：
title(项目/经历名), org(公司/组织), role(角色), period(时间段),
tech_stack(技术栈数组), achievements(成果数组，必须保留原文数字),
keywords(检索关键词数组), raw_excerpt(简历原文摘录)
只输出 JSON，不要其他内容。

简历：
{resume}"""

JD_DIGEST_PROMPT = """把下面的岗位 JD 压缩为要求摘要（300 token 内）。
输出 JSON：requirements(要求要点数组), keywords(关键词数组), seniority(级别)
只输出 JSON。

JD：
{jd}"""


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class ProfileStore:
    def __init__(
        self,
        db: DB,
        llm_chat: Callable[[str], Awaitable[str]] | None = None,
        embed: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
        embed_dim: int = EMBED_DIM,
    ):
        self.db = db
        self.llm_chat = llm_chat
        self.embed = embed
        self.embed_dim = embed_dim
        self.db.conn.enable_load_extension(True)
        sqlite_vec.load(self.db.conn)
        self.db.conn.enable_load_extension(False)
        self.db.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS cards_vec USING vec0("
            f"card_id TEXT PRIMARY KEY, embedding float[{embed_dim}])")
        self.db.conn.commit()

    async def cardify(self, profile_id: str) -> dict:
        """LLM 预处理：简历→卡片，JD→摘要，入库 + 建索引。"""
        if not self.llm_chat:
            raise RuntimeError("未配置 LLM，无法卡片化")
        profile = self.db.get_profile(profile_id)
        if not profile:
            raise KeyError(f"档案不存在: {profile_id}")

        cards_raw = await self.llm_chat(
            CARDIFY_PROMPT.format(resume=profile["resume_text"]))
        cards = json.loads(_strip_code_fence(cards_raw))
        digest_raw = await self.llm_chat(
            JD_DIGEST_PROMPT.format(jd=profile["jd_text"]))
        digest = json.loads(_strip_code_fence(digest_raw))

        self.db.replace_cards(profile_id, cards)
        self.db.update_profile(profile_id, jd_digest=json.dumps(
            digest, ensure_ascii=False))
        await self._reindex(profile_id)
        return {"cards": len(cards), "jd_digest": digest}

    async def _reindex(self, profile_id: str) -> None:
        if not self.embed:
            return
        cards = self.db.list_cards(profile_id)
        self.db.conn.execute(
            "DELETE FROM cards_vec WHERE card_id IN "
            "(SELECT card_id FROM profile_cards WHERE profile_id=?)",
            (profile_id,))
        if not cards:
            self.db.conn.commit()
            return
        texts = [_card_text(c) for c in cards]
        vectors = await self.embed(texts)
        for card, vec in zip(cards, vectors):
            self.db.conn.execute(
                "INSERT INTO cards_vec (card_id, embedding) VALUES (?, ?)",
                (card["card_id"], _serialize_f32(vec)))
        self.db.conn.commit()

    async def search_cards(self, profile_id: str, query: str,
                           k: int = 5) -> list[dict]:
        """向量 top-k 检索；无 embedding 时退化为全量返回（小档案够用）。"""
        cards = self.db.list_cards(profile_id)
        if not self.embed or not cards:
            return cards[:k]
        [qvec] = await self.embed([query])
        rows = self.db.conn.execute(
            "SELECT card_id, distance FROM cards_vec "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_serialize_f32(qvec), k)).fetchall()
        by_id = {c["card_id"]: c for c in cards}
        return [by_id[r["card_id"]] for r in rows if r["card_id"] in by_id]

    def profile_context(self, cards: list[dict]) -> str:
        """渲染进 system prompt 的 <profile> 内容。"""
        blocks = []
        for c in cards:
            lines = [f"【{c['title']}】{c.get('org') or ''} {c.get('role') or ''} "
                     f"{c.get('period') or ''}".strip()]
            if c.get("tech_stack"):
                lines.append("技术栈：" + "、".join(c["tech_stack"]))
            for a in c.get("achievements", []):
                lines.append(f"成果：{a}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


def _card_text(card: dict) -> str:
    parts = [card["title"], card.get("org") or "", card.get("role") or ""]
    parts += card.get("tech_stack", []) + card.get("keywords", [])
    parts += card.get("achievements", [])
    return " ".join(p for p in parts if p)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
