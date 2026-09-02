"""T13 验证：三档模板渲染无残留变量、必备段落齐备、骨架解析正确。"""
import pytest

from sidecar.agent.prompts import (
    NO_MATERIAL_PHRASE, STYLES, build_user_prompt,
    parse_skeleton_and_full, render_system_prompt,
)

PROFILE = "XX科技 后端工程师 2020-2024：Kafka 项目，延迟降低 30%"
JD = "要求：5 年后端经验，熟悉消息队列"


def test_render_all_styles_no_leftover_vars():
    for style in STYLES:
        out = render_system_prompt(style, PROFILE, JD)
        assert "{" not in out.replace("{{", ""), f"{style} 存在未渲染变量"
        assert PROFILE in out and JD in out


def test_render_contains_required_sections():
    out = render_system_prompt("standard", PROFILE, JD)
    for keyword in ["第一人称", "STAR", "要点骨架", "事实边界",
                    NO_MATERIAL_PHRASE, "150–220", "<profile>"]:
        assert keyword in out, f"缺少必备段落: {keyword}"


def test_style_word_ranges():
    assert "60–90" in render_system_prompt("concise", PROFILE, JD)
    assert "300–400" in render_system_prompt("detailed", PROFILE, JD)


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        render_system_prompt("verbose", PROFILE, JD)


def test_parse_skeleton_and_full():
    text = "- 主导 Kafka 改造\n- 延迟降低 30%\n- 灰度上线无事故\n\n我在 XX科技 主导了 Kafka 项目的改造……"
    skeleton, full = parse_skeleton_and_full(text)
    assert skeleton == ["主导 Kafka 改造", "延迟降低 30%", "灰度上线无事故"]
    assert full.startswith("我在 XX科技")


def test_build_user_prompt_with_history():
    out = build_user_prompt("为什么离职？", "已问过自我介绍")
    assert "已问过自我介绍" in out and "为什么离职？" in out
