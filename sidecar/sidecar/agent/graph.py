"""M4: LangGraph 图定义与运行入口。

图：classify → [smalltalk? 跳过] → retrieve → generate → fact_guard → persist
run_turn() 对外产出 skeleton / delta / done 事件流（经 WebSocket 推浮窗）。
"""
from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from langgraph.graph import END, START, StateGraph

from sidecar.agent import nodes
from sidecar.agent.nodes import AgentDeps, TurnState


def _route_after_classify(state: TurnState) -> str:
    # 闲聊类问题无需检索档案，省 150-300ms
    return "generate" if state.get("question_type") == "smalltalk" else "retrieve"


def build_graph(deps: AgentDeps):
    async def classify(s): return await nodes.classify(s, deps)
    async def retrieve(s): return await nodes.retrieve(s, deps)
    async def generate(s): return await nodes.generate(s, deps)
    async def fact_guard(s): return await nodes.fact_guard(s, deps)
    async def persist(s): return await nodes.persist(s, deps)

    g = StateGraph(TurnState)
    g.add_node("classify", classify)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_node("fact_guard", fact_guard)
    g.add_node("persist", persist)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", _route_after_classify,
                            {"retrieve": "retrieve", "generate": "generate"})
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "fact_guard")
    g.add_edge("fact_guard", "persist")
    g.add_edge("persist", END)
    return g.compile()


async def run_turn(
    deps: AgentDeps,
    session_id: str,
    question: str,
    question_end_ms: int,
    *,
    style: str = "standard",
    trigger: str = "auto",
) -> AsyncIterator[dict]:
    """运行一轮问答，产出事件流：
    {"type":"skeleton","turn_id","items"} /
    {"type":"delta","turn_id","text"} /
    {"type":"done","turn_id","fact_violations","latency_ms"}
    """
    graph = build_graph(deps)
    turn_id = uuid.uuid4().hex
    t0 = time.monotonic()
    state: TurnState = {
        "session_id": session_id,
        "turn_id": turn_id,
        "detected_question": question,
        "question_end_ms": question_end_ms,
        "answer_style": style,
        "trigger": trigger,
        "latency_ms": {},
    }
    final: TurnState = {}
    async for mode, chunk in graph.astream(
            state, stream_mode=["custom", "values"]):
        if mode == "custom":
            chunk["turn_id"] = turn_id
            yield chunk
        else:
            final = chunk
    latency = dict(final.get("latency_ms", {}))
    latency["total"] = int((time.monotonic() - t0) * 1000)
    # persist 节点在图内执行时 total 尚未产生，图结束后回填（AC8 统计）
    deps.db.update_turn_latency(turn_id, latency)
    yield {
        "type": "done",
        "turn_id": turn_id,
        "fact_violations": final.get("fact_violations", []),
        "latency_ms": latency,
    }
