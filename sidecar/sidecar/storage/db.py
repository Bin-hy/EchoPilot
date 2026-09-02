"""M6: SQLite 本地存储（profiles / profile_cards / sessions / turns / segments）。

对应 plan.md 数据结构；删除 session 级联清理 turns/segments（AC12）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.environ.get("ECHOPILOT_DB_PATH",
                   str(Path.home() / ".echopilot" / "echopilot.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resume_text TEXT NOT NULL,
    jd_text TEXT NOT NULL,
    jd_digest TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_cards (
    card_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    org TEXT, role TEXT, period TEXT,
    tech_stack TEXT, achievements TEXT, keywords TEXT,
    raw_excerpt TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    question_text TEXT NOT NULL,
    question_type TEXT,
    answer_skeleton TEXT,
    answer_full TEXT,
    fact_violations TEXT,
    trigger TEXT NOT NULL DEFAULT 'auto',
    latency_ms TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
"""


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class DB:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ── profiles ──────────────────────────────────────────────
    def create_profile(self, name: str, resume_text: str, jd_text: str) -> dict:
        pid = _new_id()
        now = _now()
        self.conn.execute(
            "INSERT INTO profiles VALUES (?,?,?,?,?,?,?)",
            (pid, name, resume_text, jd_text, None, now, now),
        )
        self.conn.commit()
        return self.get_profile(pid)

    def get_profile(self, profile_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_profiles(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT profile_id, name, created_at, updated_at FROM profiles "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_profile(self, profile_id: str, **fields) -> dict | None:
        allowed = {"name", "resume_text", "jd_text", "jd_digest"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get_profile(profile_id)
        sets["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in sets)
        self.conn.execute(
            f"UPDATE profiles SET {cols} WHERE profile_id=?",
            (*sets.values(), profile_id),
        )
        self.conn.commit()
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        self.conn.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))
        self.conn.commit()

    # ── profile cards ─────────────────────────────────────────
    def replace_cards(self, profile_id: str, cards: list[dict]) -> None:
        self.conn.execute(
            "DELETE FROM profile_cards WHERE profile_id=?", (profile_id,)
        )
        for c in cards:
            self.conn.execute(
                "INSERT INTO profile_cards VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    c.get("card_id") or _new_id(), profile_id, c["title"],
                    c.get("org"), c.get("role"), c.get("period"),
                    json.dumps(c.get("tech_stack", []), ensure_ascii=False),
                    json.dumps(c.get("achievements", []), ensure_ascii=False),
                    json.dumps(c.get("keywords", []), ensure_ascii=False),
                    c.get("raw_excerpt", ""),
                ),
            )
        self.conn.commit()

    def list_cards(self, profile_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM profile_cards WHERE profile_id=?", (profile_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("tech_stack", "achievements", "keywords"):
                d[k] = json.loads(d[k] or "[]")
            out.append(d)
        return out

    # ── sessions ──────────────────────────────────────────────
    def create_session(self, profile_id: str) -> dict:
        sid = _new_id()
        self.conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?)",
            (sid, profile_id, _now(), None, "active"),
        )
        self.conn.commit()
        return self.get_session(sid)

    def get_session(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def end_session(self, session_id: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, status='ended' WHERE session_id=?",
            (_now(), session_id),
        )
        self.conn.commit()

    def list_sessions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        # 依赖外键 ON DELETE CASCADE 清理 turns/segments（AC12 无残留）
        self.conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        self.conn.commit()

    # ── turns ─────────────────────────────────────────────────
    def insert_turn(self, session_id: str, question_text: str,
                    turn_id: str | None = None, **fields) -> str:
        tid = turn_id or _new_id()
        self.conn.execute(
            "INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                tid, session_id, question_text,
                fields.get("question_type"),
                json.dumps(fields.get("answer_skeleton", []), ensure_ascii=False),
                fields.get("answer_full", ""),
                json.dumps(fields.get("fact_violations", []), ensure_ascii=False),
                fields.get("trigger", "auto"),
                json.dumps(fields.get("latency_ms", {}), ensure_ascii=False),
                _now(),
            ),
        )
        self.conn.commit()
        return tid

    def update_turn_latency(self, turn_id: str, latency_ms: dict) -> None:
        self.conn.execute(
            "UPDATE turns SET latency_ms=? WHERE turn_id=?",
            (json.dumps(latency_ms, ensure_ascii=False), turn_id),
        )
        self.conn.commit()

    def list_turns(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("answer_skeleton", "fact_violations", "latency_ms"):
                d[k] = json.loads(d[k] or ("[]" if k != "latency_ms" else "{}"))
            out.append(d)
        return out

    # ── segments ──────────────────────────────────────────────
    def insert_segment(self, session_id: str, channel: str, text: str,
                       start_ms: int, end_ms: int) -> None:
        self.conn.execute(
            "INSERT INTO segments (session_id, channel, text, start_ms, end_ms) "
            "VALUES (?,?,?,?,?)",
            (session_id, channel, text, start_ms, end_ms),
        )
        self.conn.commit()

    def list_segments(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT channel, text, start_ms, end_ms FROM segments "
            "WHERE session_id=? ORDER BY start_ms",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
