"""M4: LangGraph 单轮问答节点（F6, F7）。

节点：classify → retrieve → generate → fact_guard → persist
依赖全部注入（AgentDeps），LLM 可换供应商（N10），测试用 fake。
流式：generate 节点通过 LangGraph stream writer 产出
skeleton / delta 自定义事件（N1 两级展示）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, TypedDict

from langgraph.config import get_stream_writer

from sidecar.agent.factcheck import EntityWhitelist, check_answer
from sidecar.agent.prompts import (
    build_user_prompt, parse_skeleton_and_full, render_system_prompt,
)
from sidecar.profile.store import ProfileStore
from sidecar.storage.db import DB

QUESTION_TYPES = [
    "self_intro", "project", "behavioral", "technical",
    "salary", "smalltalk", "other",
]

CLASSIFY_PROMPT = """把面试官的问题分类，输出 JSON：
question_type（{types} 之一）, topic_tags（2-4 个主题关键词数组）
只输出 JSON。

问题：{question}"""


class TurnState(TypedDict, total=False):
    session_id: str
    turn_id: str
    detected_question: str
    question_end_ms: int
    question_type: str
    topic_tags: list[str]
    retrieved_cards: list[dict]
    answer_style: str
    answer_skeleton: list[str]
    answer_full: str
    fact_violations: list[str]
    latency_ms: dict
    trigger: str  # "auto" | "manual"


@dataclass
class AgentDeps:
    cheap_chat: Callable[[list[dict]], Awaitable[str]]
    flagship_stream: Callable[[list[dict]], AsyncIterator[str]]
    store: ProfileStore
    db: DB
    profile_id: str
    jd_digest: str = ""
    resume_text: str = ""


def _mark(state: TurnState, node: str) -> None:
    state.setdefault("latency_ms", {})[f"{node}_start"] = time.monotonic()


def _elapsed(state: TurnState, node: str) -> None:
    start = state["latency_ms"].pop(f"{node}_start", None)
    if start is not None:
        state["latency_ms"][node] = int((time.monotonic() - start) * 1000)


async def classify(state: TurnState, deps: AgentDeps) -> dict:
    _mark(state, "classify")
    raw = await deps.cheap_chat([
        {"role": "user", "content": CLASSIFY_PROMPT.format(
            types="/".join(QUESTION_TYPES),
            question=state["detected_question"])}])
    try:
        parsed = json.loads(raw.strip().strip("`").removeprefix("json").strip())
        qtype = parsed.get("question_type", "other")
        if qtype not in QUESTION_TYPES:
            qtype = "other"
        tags = list(parsed.get("topic_tags", []))[:4]
    except (json.JSONDecodeError, AttributeError):
        qtype, tags = "other", []
    _elapsed(state, "classify")
    return {"question_type": qtype, "topic_tags": tags,
            "latency_ms": state["latency_ms"]}


async def retrieve(state: TurnState, deps: AgentDeps) -> dict:
    _mark(state, "retrieve")
    cards = await deps.store.search_cards(
        deps.profile_id, state["detected_question"], k=5)
    _elapsed(state, "retrieve")
    return {"retrieved_cards": cards, "latency_ms": state["latency_ms"]}


async def generate(state: TurnState, deps: AgentDeps) -> dict:
    _mark(state, "generate")
    writer = get_stream_writer()
    cards = state.get("retrieved_cards", [])
    system = render_system_prompt(
        state.get("answer_style", "standard"),
        deps.store.profile_context(cards),
        deps.jd_digest,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": build_user_prompt(
            state["detected_question"])},
    ]

    buffer = ""
    skeleton: list[str] = []
    skeleton_sent = False
    async for delta in deps.flagship_stream(messages):
        buffer += delta
        if not skeleton_sent:
            maybe, _ = parse_skeleton_and_full(buffer)
            # 骨架完成的信号：出现 "- "行后的空行
            if maybe and "\n\n" in buffer:
                skeleton = maybe
                writer({"type": "skeleton", "items": skeleton})
                skeleton_sent = True
        else:
            writer({"type": "delta", "text": delta})

    final_skeleton, full = parse_skeleton_and_full(buffer)
    if not skeleton_sent and final_skeleton:
        skeleton = final_skeleton
        writer({"type": "skeleton", "items": skeleton})
    _elapsed(state, "generate")
    return {"answer_skeleton": skeleton, "answer_full": full,
            "latency_ms": state["latency_ms"]}


async def fact_guard(state: TurnState, deps: AgentDeps) -> dict:
    _mark(state, "fact_guard")
    wl = EntityWhitelist.from_profile(
        state.get("retrieved_cards", []), resume_text=deps.resume_text)
    violations = check_answer(
        state.get("answer_full", ""), state["detected_question"], wl)
    _elapsed(state, "fact_guard")
    return {"fact_violations": violations, "latency_ms": state["latency_ms"]}


async def persist(state: TurnState, deps: AgentDeps) -> dict:
    latency = dict(state.get("latency_ms", {}))
    deps.db.insert_turn(
        state["session_id"], state["detected_question"],
        turn_id=state.get("turn_id"),
        question_type=state.get("question_type"),
        answer_skeleton=state.get("answer_skeleton", []),
        answer_full=state.get("answer_full", ""),
        fact_violations=state.get("fact_violations", []),
        trigger=state.get("trigger", "auto"),
        latency_ms=latency,
    )
    return {}
