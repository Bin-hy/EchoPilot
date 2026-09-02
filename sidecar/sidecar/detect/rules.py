"""M3 第一层：规则快筛（零成本）。

classify_rule(text) ->
    "hit"    —— 明确是期望候选人接话的问题（疑问句/祈使句）
    "reject" —— 明确不是（附和、过短碎片）
    "unsure" —— 交给廉价 LLM 兜底判定
"""
from __future__ import annotations

import re

# 疑问词：命中即强烈倾向问题
QUESTION_WORDS = [
    "为什么", "如何", "怎么", "怎样", "能不能", "能否", "可以吗", "是不是",
    "有没有", "什么", "哪些", "哪个", "多少", "多久", "何时", "哪里",
    "介绍一下", "介绍下", "自我介绍", "描述一下", "举例", "举个例子",
]

# 祈使句式：面试官常用陈述形式发出回答指令（不要求句首，
# 因为面试官常说"你好，请先……"这类带寒暄前缀的指令）
IMPERATIVE_PATTERNS = [
    r"(请|麻烦|先)?(说|讲|聊|谈|介绍|描述|分享|解释|展开|细说|回忆)(一|下|说|讲|聊|谈|看|些)?",
    r"(说说|讲讲|聊聊|谈谈|介绍一下|描述一下|分享一下|解释一下)",
    r"(tell me|describe|explain|walk me through|talk about)",
]

# 附和/回填词：绝不触发
BACKCHANNEL = {
    "嗯", "嗯嗯", "对", "对对", "好的", "好", "是", "是的", "行", "可以",
    "明白", "了解", "ok", "okay", "yes", "right", "嗯哼", "哦", "噢",
    "然后呢", "继续", "接着说", "没错", "确实", "我懂", "这样啊",
}

QUESTION_MARK = re.compile(r"[?？]")
MIN_LEN = 5  # 短于 5 字大概率不是完整问题


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。、,.!！~～…]+", "", text.strip())


def classify_rule(text: str) -> str:
    norm = _normalize(text)
    if not norm:
        return "reject"

    # 纯附和（整句就是附和林）
    if norm in BACKCHANNEL:
        return "reject"

    # 过短且无疑问标记 → 碎片
    if len(norm) < MIN_LEN and not QUESTION_MARK.search(text):
        return "reject"

    # 疑问词或祈使句式 → 明确命中
    if any(w in norm for w in QUESTION_WORDS):
        return "hit"
    if any(re.search(p, norm, re.IGNORECASE) for p in IMPERATIVE_PATTERNS):
        return "hit"
    if QUESTION_MARK.search(text):
        return "hit"

    return "unsure"
