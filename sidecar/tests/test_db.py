"""T3 验证：建表与五表增删查、级联删除（AC12）。"""
from sidecar.storage.db import DB


def make_db(tmp_path):
    return DB(tmp_path / "test.db")


def test_profile_crud(tmp_path):
    db = make_db(tmp_path)
    p = db.create_profile("字节-后端", "简历文本", "JD文本")
    assert p["name"] == "字节-后端"
    assert db.get_profile(p["profile_id"])["jd_text"] == "JD文本"
    assert len(db.list_profiles()) == 1
    db.update_profile(p["profile_id"], name="阿里-后端")
    assert db.get_profile(p["profile_id"])["name"] == "阿里-后端"
    db.delete_profile(p["profile_id"])
    assert db.get_profile(p["profile_id"]) is None
    db.close()


def test_cards_replace_and_list(tmp_path):
    db = make_db(tmp_path)
    p = db.create_profile("p", "r", "j")
    db.replace_cards(p["profile_id"], [
        {"title": "Kafka 项目", "org": "XX科技", "tech_stack": ["Kafka"],
         "achievements": ["延迟降低 30%"], "keywords": ["消息队列"]},
        {"title": "网关重构", "org": "YY公司"},
    ])
    cards = db.list_cards(p["profile_id"])
    assert len(cards) == 2
    assert cards[0]["tech_stack"] == ["Kafka"]
    db.replace_cards(p["profile_id"], [{"title": "新项目"}])
    assert len(db.list_cards(p["profile_id"])) == 1
    db.close()


def test_session_turn_segment_flow_and_cascade(tmp_path):
    db = make_db(tmp_path)
    p = db.create_profile("p", "r", "j")
    s = db.create_session(p["profile_id"])
    assert s["status"] == "active"

    db.insert_segment(s["session_id"], "interviewer", "介绍一下你自己", 0, 1500)
    db.insert_segment(s["session_id"], "me", "我是……", 2000, 5000)
    tid = db.insert_turn(s["session_id"], "介绍一下你自己",
                         question_type="self_intro",
                         answer_skeleton=["要点1", "要点2"],
                         answer_full="完整回答",
                         fact_violations=["不存在的公司"],
                         trigger="auto", latency_ms={"total": 2800})

    db.end_session(s["session_id"])
    assert db.get_session(s["session_id"])["status"] == "ended"
    assert len(db.list_segments(s["session_id"])) == 2
    turns = db.list_turns(s["session_id"])
    assert turns[0]["turn_id"] == tid
    assert turns[0]["answer_skeleton"] == ["要点1", "要点2"]
    assert turns[0]["latency_ms"]["total"] == 2800

    # AC12：删除 session 后 turns/segments 级联清空
    db.delete_session(s["session_id"])
    assert db.list_segments(s["session_id"]) == []
    assert db.list_turns(s["session_id"]) == []
    assert db.get_session(s["session_id"]) is None
    db.close()
