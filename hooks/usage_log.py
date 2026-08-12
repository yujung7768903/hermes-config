"""usage_log.py — 사용 원장. 요청 한 건이 한 행이다.

쓰는 곳
  - prompt-gate 플러그인 (`decide()`)         : 요청·게이트 판정 기록
  - usage-log 플러그인                         : 응답·토큰·보조분류 기록
  - scripts/slack_usage_report.py              : 읽기 (리포트)

`security_log.py` 와 같은 자리에 두는 이유 — 훅과 플러그인이 둘 다
`sys.path.insert(0, HERMES_HOME/"hooks")` 로 import 하는 경로다. 같은 게이트웨이
프로세스에서 같은 모듈 객체가 되므로 토큰 기준선 같은 메모리 상태도 공유된다.

원장 위치: ~/.hermes/logs/usage/usage.db  (SQLite, WAL)
보관 기간: 90일 (기록할 때마다 오래된 행 삭제)

**기록 실패는 대화를 막지 않는다.** 모든 공개 함수가 예외를 삼키고, 실패는 로그
한 줄로 끝난다. 모니터링이 서비스를 멈추게 하는 것이 유일한 실패 모드다.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
HERMES_HOME = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))

# logs/ 아래에 둔다. LK(systemd ReadOnlyPaths) 동결 제외 대상이라 쓰기가 되고,
# .gitignore 가 통째로 무시해서 security_watch 의 신규 파일 탐지에 매번 걸리지도
# 않는다 — docs/cron-jobs.md 의 `.last_alert` 와 같은 판단이다.
DB_PATH = HERMES_HOME / "logs" / "usage" / "usage.db"

# 코어 SessionDB. 토큰·비용 누계를 여기서 읽는다 (skills/token-usage/SKILL.md 가
# 쓰는 그 테이블). **읽기 전용으로만** 연다.
STATE_DB = HERMES_HOME / "state.db"

KEEP_DAYS = 90
REQUEST_LIMIT = 4000
RESPONSE_LIMIT = 8000

# 응답을 어느 요청에 붙일지 판단하는 창. 이 시간을 넘긴 요청에는 붙이지 않는다 —
# 크론 리포트처럼 사용자 요청 없이 나가는 메시지가 옛 행을 덮어쓰는 것을 막는다.
ATTACH_WINDOW_SEC = 900

# 스트리밍이 켜지면 최종 답변이 chat_update 로 여러 번 온다. 첫 응답 이후 이
# 시간 안의 갱신은 같은 턴의 갱신으로 보고 덮어쓴다 (최종본이 남는다).
UPDATE_WINDOW_SEC = 180


_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    ts_epoch      REAL    NOT NULL,
    platform      TEXT,
    user_id       TEXT,
    user_name     TEXT,
    mode          TEXT,
    channel_id    TEXT,
    chat_id       TEXT,
    message_id    TEXT,
    request       TEXT,
    response      TEXT,
    responded_at  TEXT,
    latency_ms    INTEGER,
    category      TEXT,
    verdict       TEXT,
    via           TEXT,
    form          TEXT,
    improve       INTEGER,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    cache_read    INTEGER,
    cache_write   INTEGER,
    tokens_total  INTEGER,
    cost_usd      REAL,
    session_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_turns_chat ON turns(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_turns_msg ON turns(message_id);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _rotate(con: sqlite3.Connection) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    con.execute("DELETE FROM turns WHERE ts_epoch < ?", (cutoff,))


# ── 대화 방식 ─────────────────────────────────────────────────────────────
def mode_and_channel(platform: str, chat_id: str) -> tuple[str, str | None]:
    """(대화 방식, 채널 ID) — 채널 대화가 아니면 채널 ID 는 None.

    슬랙 ID 접두어로 판정한다. D=DM, C=공개/비공개 채널, G=레거시 그룹.
    헤르메스는 채널에서 멘션으로만 불리므로 채널 대화 = 멘션이다.
    다른 플랫폼은 접두어 규칙이 달라 판정하지 않는다.
    """
    cid = (chat_id or "").strip()
    if platform == "slack" and cid:
        if cid.startswith("D"):
            return "dm", None
        if cid.startswith(("C", "G")):
            return "mention", cid
    if not cid:
        return "unknown", None
    return "other", cid


# ── 토큰 ──────────────────────────────────────────────────────────────────
# chat_id → (row_id, 요청 시점 세션 스냅샷, user_id). 응답이 올 때 차분을 낸다.
# 프로세스 메모리에만 있고 재기동하면 사라진다 — 그 경우 토큰만 비고 행은 남는다.
_BASELINE: dict = {}


def session_snapshot(limit: int = 50) -> dict:
    """{세션id: (source, in, out, cache_read, cache_write, cost)} — 최근 세션만.

    스키마가 없거나 파일이 없으면 빈 dict. 토큰만 비고 나머지 기록은 그대로 남는다.
    """
    if not STATE_DB.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "SELECT id, source, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens, estimated_cost_usd "
                "FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            con.close()
        return {r[0]: (r[1] or "", *(v or 0 for v in r[2:6]), r[6] or 0.0)
                for r in rows}
    except Exception as exc:
        logger.debug("[usage-log] 세션 스냅샷 실패: %s", exc)
        return {}


def token_delta(before: dict, after: dict, chat_id: str, user_id: str) -> dict | None:
    """스냅샷 두 장의 차분에서 이번 턴의 토큰을 고른다.

    코어는 토큰을 **세션 누계**로만 들고 있어 턴 단위 값이 없다. 그래서 요청
    시점과 응답 시점의 차분을 쓴다. 어느 세션의 차분인지는 이 순서로 고른다.

      1) source 에 이 대화의 chat_id 나 user_id 가 들어 있는 세션
      2) (1) 이 없으면, 차분이 생긴 세션이 **정확히 하나**일 때 그 세션
      3) 둘 다 아니면 포기 (NULL)

    ponytail: 동시 대화가 겹치고 source 매칭도 실패하면 토큰이 빈다. 사용자 수가
    늘어 이게 문제가 되면 코어에 턴 단위 usage 훅이 필요하다 — 여기서 더 정교하게
    추정할 수는 없다.
    """
    changed = {}
    for sid, aft in after.items():
        bef = before.get(sid)
        base = bef[1:] if bef else (0, 0, 0, 0, 0.0)
        diff = tuple(a - b for a, b in zip(aft[1:], base))
        if any(d > 0 for d in diff[:4]):
            changed[sid] = (aft[0], diff)
    if not changed:
        return None

    pick = None
    for sid, (source, _diff) in changed.items():
        if (chat_id and chat_id in source) or (user_id and user_id in source):
            pick = sid
            break
    if pick is None and len(changed) == 1:
        pick = next(iter(changed))
    if pick is None:
        return None

    ti, to, cr, cw, cost = changed[pick][1]
    return {"tokens_in": ti, "tokens_out": to, "cache_read": cr, "cache_write": cw,
            "tokens_total": ti + to + cr + cw, "cost_usd": round(cost, 6),
            "session_id": pick}


# ── 기록 ──────────────────────────────────────────────────────────────────
def record(*, platform: str = "", user_id: str = "", user_name: str = "",
           chat_id: str = "", message_id: str = "", request: str = "",
           category: str = "", verdict: str = "", via: str = "") -> int | None:
    """요청 한 건을 원장에 넣고 행 id 를 돌려준다. 실패하면 None.

    같은 message_id 가 이미 있으면 넣지 않는다 — 큐·펜딩 재디스패치로 같은
    메시지가 두 번 들어오는 경로가 있다 (prompt-gate 의 `seen` 캐시와 같은 이유).

    토큰 기준선도 여기서 잡는다. 게이트 분류기 호출이 **끝난 뒤**라, 게이트가
    쓴 토큰은 기준선 이전으로 빠져 이번 턴 사용량에 섞이지 않는다.
    """
    try:
        mode, channel_id = mode_and_channel(platform, chat_id)
        now = datetime.now(KST)
        with _conn() as con:
            if message_id:
                dup = con.execute("SELECT id FROM turns WHERE message_id=?",
                                  (message_id,)).fetchone()
                if dup is not None:
                    return dup["id"]
            cur = con.execute(
                "INSERT INTO turns (ts, ts_epoch, platform, user_id, user_name, "
                "mode, channel_id, chat_id, message_id, request, category, "
                "verdict, via) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(timespec="seconds"), now.timestamp(),
                 platform or None, user_id or None, user_name or None,
                 mode, channel_id, chat_id or None, message_id or None,
                 (request or "")[:REQUEST_LIMIT], category or None,
                 verdict or None, via or None))
            _rotate(con)
            row_id = cur.lastrowid
        if chat_id:
            _BASELINE[chat_id] = (row_id, session_snapshot(), user_id)
            while len(_BASELINE) > 64:
                _BASELINE.pop(next(iter(_BASELINE)))
        return row_id
    except Exception as exc:
        logger.warning("[usage-log] 요청 기록 실패: %s", exc)
        return None


def attach_response(chat_id: str, text: str) -> bool:
    """가장 최근 열린 요청에 응답과 토큰을 붙인다. 붙였으면 True.

    붙일 대상 판정
      - 같은 chat_id 의 가장 최근 행
      - 요청이 ATTACH_WINDOW_SEC 안에 들어온 것
      - 아직 응답이 없거나(첫 응답), 응답 직후 UPDATE_WINDOW_SEC 안(스트리밍 갱신)
    셋 다 만족하지 않으면 아무것도 하지 않는다 — 사용자 요청 없이 나가는 메시지
    (크론 리포트·차단 안내)가 남의 턴을 덮어쓰지 않게 하는 조건이다.
    """
    try:
        if not chat_id or not (text or "").strip():
            return False
        now = time.time()
        with _conn() as con:
            row = con.execute(
                "SELECT id, ts_epoch, responded_at, tokens_total FROM turns "
                "WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
            if row is None or now - row["ts_epoch"] > ATTACH_WINDOW_SEC:
                return False
            if row["responded_at"] is not None:
                try:
                    first = datetime.fromisoformat(row["responded_at"]).timestamp()
                except ValueError:
                    return False
                if now - first > UPDATE_WINDOW_SEC:
                    return False
                stamp = row["responded_at"]
            else:
                stamp = datetime.now(KST).isoformat(timespec="seconds")

            fields = {"response": (text or "")[:RESPONSE_LIMIT],
                      "responded_at": stamp,
                      "latency_ms": int((now - row["ts_epoch"]) * 1000)}
            # 토큰은 처음 붙일 때만 잰다. 스트리밍 갱신마다 다시 재면 같은 턴을
            # 여러 번 세게 된다.
            if row["tokens_total"] is None:
                entry = _BASELINE.get(chat_id)
                before, user_id = (entry[1], entry[2]) if entry else ({}, "")
                usage = token_delta(before, session_snapshot(), chat_id, user_id)
                if usage:
                    fields.update(usage)
            sets = ", ".join(f"{k}=:{k}" for k in fields)
            con.execute(f"UPDATE turns SET {sets} WHERE id=:id",
                        {**fields, "id": row["id"]})
        return True
    except Exception as exc:
        logger.warning("[usage-log] 응답 기록 실패: %s", exc)
        return False


def set_form(*, message_id: str = "", chat_id: str = "", request: str = "",
             form: str = "", improve: bool = False) -> bool:
    """보조 분류(유형·개선제안)를 행에 채운다. 못 찾으면 False.

    **이번 턴의 행이 확실할 때만 쓴다.** message_id 가 있으면 그것으로만 찾고,
    없으면 같은 대화에서 아직 유형이 안 채워졌고 원문이 일치하는 최근 행을 찾는다.
    "그 대화의 가장 최근 행" 으로 대충 붙이면, 게이트가 아직 이번 행을 쓰기 전일 때
    **직전 턴**에 값을 덮어쓴다 (훅 등록 순서는 보장되지 않는다).

    이 값들은 게이트 판정에 전혀 쓰이지 않는다 — 틀려도 리포트만 틀린다.
    """
    try:
        with _conn() as con:
            if message_id:
                row = con.execute("SELECT id FROM turns WHERE message_id=?",
                                  (message_id,)).fetchone()
            elif chat_id:
                row = con.execute(
                    "SELECT id, request FROM turns WHERE chat_id=? AND form IS NULL "
                    "ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
                if row is not None and request:
                    stored = (row["request"] or "")
                    if not (stored.startswith(request) or request.startswith(stored)):
                        row = None
            else:
                row = None
            if row is None:
                return False
            con.execute("UPDATE turns SET form=?, improve=? WHERE id=?",
                        (form or None, 1 if improve else 0, row["id"]))
        return True
    except Exception as exc:
        logger.warning("[usage-log] 보조 분류 기록 실패: %s", exc)
        return False
