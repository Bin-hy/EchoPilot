"""T14 验证：档案外实体被检出；来自问题/档案的实体不误报。"""
from sidecar.agent.factcheck import EntityWhitelist, check_answer, extract_entities

CARDS = [
    {"title": "Kafka 消息平台改造", "org": "星图科技", "role": "后端工程师",
     "period": "2021-2023", "tech_stack": ["Kafka", "Flink"],
     "achievements": ["端到端延迟降低 37%", "支撑 200万 QPS"],
     "keywords": ["消息队列"], "raw_excerpt": ""},
]
RESUME = "星图科技 后端工程师 2021-2023，主导 Kafka 消息平台改造，延迟降低 37%，支撑 200万 QPS。熟练使用 Kafka、Flink。"


def make_wl():
    return EntityWhitelist.from_profile(CARDS, jd_text="", resume_text=RESUME)


def test_clean_answer_no_violations():
    wl = make_wl()
    answer = "我在星图科技主导了 Kafka 消息平台改造，端到端延迟降低 37%，支撑 200万 QPS。"
    assert check_answer(answer, "聊聊你的项目", wl) == []


def test_fabricated_org_detected():
    wl = make_wl()
    answer = "我在蓝鲸集团负责跨境电商项目，GMV 提升 55%。"
    violations = check_answer(answer, "聊聊你的项目", wl)
    assert "蓝鲸集团" in violations
    assert "55%" in violations or "55" in " ".join(violations)


def test_fabricated_number_detected():
    wl = make_wl()
    # 把真实的 37% 说成 50% —— 数字幻觉重灾区
    answer = "我把延迟降低了 50%。"
    violations = check_answer(answer, "聊聊优化成果", wl)
    assert any("50%" in v for v in violations)


def test_question_entities_not_flagged():
    wl = make_wl()
    # "蓝鲸集团" 来自面试官提问本身 → 不误报（F7 标红不拦截的边界）
    question = "你了解蓝鲸集团的供应链业务吗？"
    answer = "我对蓝鲸集团的供应链业务有一些了解。"
    assert check_answer(answer, question, wl) == []


def test_year_check():
    wl = make_wl()
    assert check_answer("我在 2022 年完成了改造。", "说说时间线", wl) == []
    violations = check_answer("我在 2018 年就做过类似项目。", "说说时间线", wl)
    assert "2018" in violations


def test_extract_entities():
    ents = extract_entities("延迟降低 37%，2019 年在星图科技用 Kafka")
    assert "37%" in ents["numbers"]
    assert "2019" in ents["years"]
    assert "星图科技" in ents["orgs"]
    assert "Kafka" in ents["orgs"]  # 英文大写词被抽出，靠档案子串放行
