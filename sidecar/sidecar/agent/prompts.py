"""M4: 回答生成 prompt 模板（F6, F7）。

- 第一人称口语化 + 禁用书面连接词
- STAR（经历/行为题）与「结论→原理→实例」（技术题）分支
- 三档字数区间（AC8）
- 事实边界：仅用 <profile> 素材，无素材输出固定话术（AC7）
- 输出格式：3–5 条要点骨架 + 空行 + 完整回答（N1 两级展示）
"""
from __future__ import annotations

STYLES: dict[str, dict] = {
    "concise": {"range": "60–90", "hint": "只说结论 + 一个亮点"},
    "standard": {"range": "150–220", "hint": "完整 STAR 结构"},
    "detailed": {"range": "300–400", "hint": "STAR + 一个延伸细节或教训"},
}

NO_MATERIAL_PHRASE = "这个问题我档案里没有对应素材，建议方向是："

SYSTEM_PROMPT = """## 角色
你在为一场真实面试中的候选人生成参考回答。候选人将参考你的回答现场组织语言。

## 硬性约束
- 始终使用第一人称（"我"），口语化，像说话而不是写文章。
- 禁用书面语连接词："此外""综上所述""值得注意的是""赋能""抓手"。
- 经历/行为类问题使用 STAR 结构：情境(1句) → 任务(1句) → 行动(2–3句) → 结果(1句，必须含数字)。
- 技术类问题使用：结论 → 原理 → 项目实例 结构。
- 当前长度档位：{style}，完整回答正文 {word_range} 字（{style_hint}）。

## 输出格式（严格遵守）
先输出 3–5 条要点骨架，每条一行、以"- "开头、每条不超过 15 字；
然后空一行；
然后输出完整回答正文。

## 事实边界
只允许使用 <profile> 标签内的经历、数字、公司名、技术栈。
<profile> 标签外的信息视为不存在。
如果问题涉及的经历档案中没有，完整回答部分只输出：
"{no_material}" 加一句话思路。
绝不编造公司名、数字、职位、项目名。

<profile>
{profile_context}
</profile>

<jd>
{jd_digest}
</jd>"""


def render_system_prompt(
    style: str,
    profile_context: str,
    jd_digest: str,
) -> str:
    if style not in STYLES:
        raise ValueError(f"未知档位: {style}，可选 {list(STYLES)}")
    return SYSTEM_PROMPT.format(
        style=style,
        word_range=STYLES[style]["range"],
        style_hint=STYLES[style]["hint"],
        no_material=NO_MATERIAL_PHRASE,
        profile_context=profile_context,
        jd_digest=jd_digest,
    )


def build_user_prompt(question: str, history_summary: str = "") -> str:
    parts = []
    if history_summary:
        parts.append(f"本场面试已有上下文：{history_summary}\n")
    parts.append(f"面试官的问题：{question}")
    return "\n".join(parts)


def parse_skeleton_and_full(text: str) -> tuple[list[str], str]:
    """把模型输出拆成骨架要点 + 完整回答。"""
    lines = text.splitlines()
    skeleton: list[str] = []
    full_lines: list[str] = []
    in_skeleton = True
    for line in lines:
        stripped = line.strip()
        if in_skeleton and stripped.startswith("- "):
            skeleton.append(stripped[2:].strip())
        elif in_skeleton and not stripped:
            if skeleton:
                in_skeleton = False
        else:
            in_skeleton = False
            full_lines.append(line)
    return skeleton, "\n".join(full_lines).strip()
