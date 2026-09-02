"""T11 验证：30 条标注样本规则分类全部符合预期。"""
import pytest

from sidecar.detect.rules import classify_rule

# (文本, 期望)
SAMPLES = [
    # ── 疑问句 hit（10）
    ("你为什么想离开现在的公司？", "hit"),
    ("你是怎么做性能优化的？", "hit"),
    ("能介绍一下你的项目吗？", "hit"),
    ("有没有遇到过线上故障？", "hit"),
    ("你们的 QPS 峰值是多少？", "hit"),
    ("这个方案有什么缺点？", "hit"),
    ("如何平衡业务和技术的投入", "hit"),
    ("你觉得团队协作中最重要的是什么", "hit"),
    ("能不能举个例子说明一下", "hit"),
    ("介绍一下你最满意的一个项目", "hit"),
    # ── 祈使句 hit（8）
    ("说说你在上一家公司的主要职责", "hit"),
    ("讲讲你们的分区策略", "hit"),
    ("聊聊你做过的最有挑战的项目", "hit"),
    ("谈谈你对微服务的理解", "hit"),
    ("请描述一下当时的场景", "hit"),
    ("分享一下你带团队的经验", "hit"),
    ("解释一下什么是最终一致性", "hit"),
    ("展开说说这个架构的取舍", "hit"),
    # ── 带寒暄前缀的指令 hit（2，真实面试高频）
    ("你好，请先做一个自我介绍", "hit"),
    ("那接下来请你介绍一下你的项目", "hit"),
    # ── 附和 reject（8）
    ("嗯", "reject"),
    ("嗯嗯", "reject"),
    ("对", "reject"),
    ("好的", "reject"),
    ("然后呢", "reject"),
    ("没错", "reject"),
    ("哦", "reject"),
    ("确实", "reject"),
    # ── 碎片/陈述 reject 或 unsure（4）
    ("好的那我", "reject"),          # 过短碎片
    ("这个项目", "reject"),          # 过短碎片
    ("我们今天先到这里", "unsure"),   # 陈述句，交给 LLM 兜底
    ("你们团队氛围看起来不错", "unsure"),
]


@pytest.mark.parametrize("text,expected", SAMPLES)
def test_rule_classification(text, expected):
    assert classify_rule(text) == expected


def test_sample_count():
    assert len(SAMPLES) == 32
