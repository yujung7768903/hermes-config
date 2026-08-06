"""
deploy-log DB helper — SQLite에 배포 기록을 저장/조회한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from hermes_constants import get_hermes_home
except Exception:
    import os

    def get_hermes_home() -> Path:
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


DB_PATH = get_hermes_home() / "deploy-log" / "deploys.db"

# 가능한 전체 상태값
STATUSES = ["예정", "완료", "롤백", "취소", "QA중", "QA완료"]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS deploys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL,
            service     TEXT NOT NULL,
            deploy_date TEXT NOT NULL,
            deploy_time TEXT NOT NULL,
            content     TEXT NOT NULL,
            pr_link     TEXT,
            jira        TEXT,
            notified_by TEXT,
            channel_ts  TEXT,
            created_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT '예정',
            assignees   TEXT,
            qa_items    TEXT,
            qa_thread_ts TEXT
        )
    """)
    # 기존 DB 마이그레이션 (컬럼 없으면 추가)
    existing = {r[1] for r in con.execute("PRAGMA table_info(deploys)").fetchall()}
    migrations = [
        ("pr_link",      "TEXT"),
        ("jira",         "TEXT"),
        ("status",       "TEXT NOT NULL DEFAULT '예정'"),
        ("assignees",    "TEXT"),
        ("qa_items",     "TEXT"),
        ("qa_thread_ts", "TEXT"),
    ]
    for col, typedef in migrations:
        if col not in existing:
            con.execute(f"ALTER TABLE deploys ADD COLUMN {col} {typedef}")
    con.commit()
    return con


def save_deploy(
    *,
    type_: str,
    service: str,
    deploy_date: str,
    deploy_time: str,
    content: str,
    pr_link: str = "",
    jira: str = "",
    notified_by: str = "",
    channel_ts: str = "",
    assignees: str = "",
    qa_items: str = "",
) -> int:
    con = _conn()
    cur = con.execute(
        """
        INSERT INTO deploys (type, service, deploy_date, deploy_time,
                             content, pr_link, jira,
                             notified_by, channel_ts, created_at,
                             status, assignees, qa_items)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            type_, service, deploy_date, deploy_time,
            content, pr_link or None, jira or None,
            notified_by, channel_ts,
            datetime.now(timezone.utc).isoformat(),
            type_,  # 초기 status = type (예정/완료/롤백)
            assignees or None,
            qa_items or None,
        ),
    )
    con.commit()
    row_id = cur.lastrowid
    con.close()
    return row_id


def update_status(deploy_id: int, status: str, qa_thread_ts: str = "") -> None:
    con = _conn()
    if qa_thread_ts:
        con.execute(
            "UPDATE deploys SET status=?, qa_thread_ts=? WHERE id=?",
            (status, qa_thread_ts, deploy_id),
        )
    else:
        con.execute("UPDATE deploys SET status=? WHERE id=?", (status, deploy_id))
    con.commit()
    con.close()


def update_channel_ts(deploy_id: int, channel_ts: str) -> None:
    con = _conn()
    con.execute("UPDATE deploys SET channel_ts=? WHERE id=?", (channel_ts, deploy_id))
    con.commit()
    con.close()


def get_deploy(deploy_id: int) -> Optional[dict]:
    con = _conn()
    row = con.execute("SELECT * FROM deploys WHERE id = ?", (deploy_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def search_deploys(query: str, limit: int = 20) -> list[dict]:
    """키워드로 배포 기록 검색."""
    con = _conn()
    q = f"%{query}%"
    rows = con.execute(
        """
        SELECT * FROM deploys
        WHERE content LIKE ? OR service LIKE ? OR type LIKE ?
        ORDER BY deploy_date DESC, deploy_time DESC
        LIMIT ?
        """,
        (q, q, q, limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def recent_deploys(limit: int = 10) -> list[dict]:
    con = _conn()
    rows = con.execute(
        "SELECT * FROM deploys ORDER BY deploy_date DESC, deploy_time DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]
