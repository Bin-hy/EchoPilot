"""M4 fact_guard：程序化幻觉校验（零 LLM 成本）。

从回答中抽取实体（公司/组织名、数字+单位、年份），与「档案白名单 ∪
问题文本实体」比对，集合之外的实体输出为 fact_violations（F7 标红，
不拦截——部分实体来自面试官提问本身）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 数字+单位：37%、3倍、200万、5个、10人、2年
NUMBER_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|千|百|亿|个|次|人|天|周|月|年|小时|分钟|秒|"
    r"ms|s|QPS|qps|TPS|tps|GB|MB|TB|元|块)")
# 年份：2019 / 2020年
YEAR = re.compile(r"(?:19|20)\d{2}\s*年?")
# 年份区间：2021-2023 / 2020 至 2022（用于白名单扩展）
YEAR_RANGE = re.compile(r"((?:19|20)\d{2})\s*[-–~—至到]\s*((?:19|20)\d{2})")
# 组织名：XX公司/XX科技/XX集团/XX银行/XX大学
ORG_CN = re.compile(r"[\u4e00-\u9fa5]{2,10}?(?:公司|科技|集团|银行|证券|基金|大学|学院|实验室|团队)")
# 英文实体词：首字母大写且第二个字符是小写或数字（Kafka、DeepSeek、Flink），
# 排除 GMV/DAU/CPU 这类纯大写缩写（行业通用术语，不算个人经历实体）
ORG_EN = re.compile(r"\b[A-Z](?:[a-z0-9])[A-Za-z0-9]*(?:\s[A-Z](?:[a-z0-9])[A-Za-z0-9]*){0,2}\b")

# 中文组织名匹配常见的前导动词/介词（正则从左侧汉字起始会带入）
LEADING_STRIP = "在于和跟与对从到向把被让是有用的了年月日我这你他那"

# 行业通用缩写：出现时不视为「个人经历实体」
COMMON_ACRONYMS = {
    "GMV", "DAU", "MAU", "ROI", "KPI", "OKR", "API", "SDK", "CPU", "GPU",
    "JVM", "HTTP", "HTTPS", "TCP", "UDP", "SQL", "NoSQL", "CI", "CD",
}


def _clean_org(org: str) -> str:
    """剥掉中文组织名匹配带入的前导字。"""
    while org and org[0] in LEADING_STRIP and len(org) > 2:
        org = org[1:]
    return org


@dataclass
class EntityWhitelist:
    """从档案卡片 + JD 构建的实体白名单。"""
    orgs: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    years: set[str] = field(default_factory=set)
    raw_text: str = ""  # 档案全文，用于子串兜底

    @classmethod
    def from_profile(cls, cards: list[dict], jd_text: str = "",
                     resume_text: str = "") -> "EntityWhitelist":
        wl = cls()
        wl.raw_text = (resume_text or "") + "\n" + (jd_text or "")
        sources = [wl.raw_text]
        for c in cards:
            if c.get("org"):
                wl.orgs.add(c["org"])
            sources.append(str(c.get("period", "")))
            sources.extend(str(a) for a in c.get("achievements", []))
            sources.append(str(c.get("title", "")))
        joined = "\n".join(sources)
        wl.numbers.update(NUMBER_UNIT.findall(joined))
        wl.years.update(_norm_year(y) for y in YEAR.findall(joined))
        # 年份区间扩展：2021-2023 → {2021, 2022, 2023}
        for start, end in YEAR_RANGE.findall(joined):
            wl.years.update(str(y) for y in range(int(start), int(end) + 1))
        wl.orgs.update(_clean_org(o) for o in ORG_CN.findall(joined))
        return wl

    def allows(self, entity: str, question: str) -> bool:
        """实体在白名单内、与白名单组织互为子串、或出现在档案/问题原文，则放行。"""
        e = entity.strip()
        if not e:
            return True
        if e in COMMON_ACRONYMS:
            return True
        if e in self.numbers or _norm_year(e) in self.years and _is_year_like(e):
            return True
        if e in self.orgs:
            return True
        # 组织名互为子串（"年在星图科技" 包含 "星图科技"）
        if len(e) >= 2 and any(o and (o in e or e in o) for o in self.orgs):
            return True
        if len(e) >= 2 and e in self.raw_text:  # 档案原文出现过（技术栈、项目名）
            return True
        if e in question:  # 面试官问题里带的实体
            return True
        return False


def _norm_year(y: str) -> str:
    return y.strip().strip("年").strip()


def _is_year_like(e: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}\s*年?", e.strip()))


def extract_entities(text: str) -> dict[str, set[str]]:
    years = {_norm_year(y) for y in YEAR.findall(text)}
    # 年份从数字集合中剔除，避免 "2022 年" 被双重报告
    numbers = {n for n in NUMBER_UNIT.findall(text)
               if not _is_year_like(n)}
    orgs = {_clean_org(o) for o in ORG_CN.findall(text)}
    orgs |= set(ORG_EN.findall(text)) - COMMON_ACRONYMS
    orgs.discard("")
    return {"numbers": numbers, "years": years, "orgs": orgs}


def check_answer(answer: str, question: str, wl: EntityWhitelist) -> list[str]:
    """返回档案外实体清单（fact_violations）。"""
    violations: list[str] = []
    entities = extract_entities(answer)
    question_entities = extract_entities(question)
    # 问题中的数字/年份自动放行（面试官给的上下文）
    allowed_extra = question_entities["numbers"] | question_entities["years"]
    for group in ("numbers", "years", "orgs"):
        for e in sorted(entities[group]):
            if group != "orgs" and e in allowed_extra:
                continue
            if not wl.allows(e, question):
                violations.append(e)
    # 去重保序
    seen: set[str] = set()
    return [v for v in violations if not (v in seen or seen.add(v))]
