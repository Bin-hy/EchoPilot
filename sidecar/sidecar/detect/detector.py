"""M3: 分层问题检测器（图外常驻，F4/F5）。

漏斗：通道过滤 → 静音阈值 → 规则快筛 → 廉价 LLM 兜底。
判定目标是「期望候选人接话的完整问题」，而非「疑问句」。

- feed() 由 ASR 编排层驱动（每条 segment 一次）
- evaluate() 由会话循环每 300ms 调用一次；静音时长由 silence_ms_fn
  注入（生产：VAD；回放测试：按时间轴计算）
- 触发：on_question(question_text, question_end_ms) 回调
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sidecar.asr.providers import ASRSegment
from sidecar.detect.rules import classify_rule

log = logging.getLogger(__name__)

LLM_LABELS = {"complete_question", "incomplete", "statement", "backchannel"}

LLM_FALLBACK_PROMPT = """判断下面这段面试对话中面试官的话属于哪一类：
- complete_question：说完了一个期望候选人回答的完整问题或指令
- incomplete：问题还没说完，还有下文
- statement：陈述/评论，不期望候选人回答
- backchannel：附和、过渡语

只输出类别标签，不要输出其他内容。

面试官的话：{text}"""


class QuestionDetector:
    def __init__(
        self,
        silence_ms_fn: Callable[[], int],
        on_question: Callable[[str, int], None],
        *,
        silence_threshold_ms: int = 800,
        llm_fallback: Callable[[str], Awaitable[str]] | None = None,
        context_segments: int = 3,
    ):
        self.silence_ms_fn = silence_ms_fn
        self.on_question = on_question
        self.silence_threshold_ms = silence_threshold_ms
        self.llm_fallback = llm_fallback
        self.context_segments = context_segments
        self._buffer: list[ASRSegment] = []
        self._triggered = False  # 当前缓冲已触发过，避免重复

    def feed(self, segment: ASRSegment) -> None:
        if segment.channel != "interviewer" or not segment.is_final:
            return
        self._buffer.append(segment)
        self._triggered = False

    def _joined(self) -> str:
        return "".join(s.text for s in self._buffer).strip()

    async def evaluate(self) -> bool:
        """评估一次当前缓冲是否构成完整问题。触发返回 True。"""
        if not self._buffer or self._triggered:
            return False
        if self.silence_ms_fn() < self.silence_threshold_ms:
            return False

        text = self._joined()
        verdict = classify_rule(text)

        if verdict == "reject":
            self._buffer.clear()
            return False

        if verdict == "unsure":
            if not self.llm_fallback:
                return False  # 无兜底则继续等更多上下文
            try:
                label = (await self.llm_fallback(text)).strip()
            except Exception as e:  # LLM 不可用时静默等待（N8 由健康通道上报）
                log.warning("llm_fallback failed: %s", e)
                return False
            if label == "incomplete":
                return False  # 等下文
            if label != "complete_question":
                self._buffer.clear()
                return False

        self._emit()
        return True

    def _emit(self) -> None:
        text = self._joined()
        end_ms = self._buffer[-1].end_ms
        self._buffer.clear()
        self._triggered = True
        self.on_question(text, end_ms)

    def flush_manual(self) -> str | None:
        """F5 手动触发：无条件把当前缓冲作为问题发出。"""
        if not self._buffer:
            return None
        text = self._joined()
        self._emit()
        return text

    def pending_text(self) -> str:
        return self._joined()
