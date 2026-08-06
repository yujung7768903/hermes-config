"""
security_log.py — 보안 이벤트 감사 로그 유틸리티

사용처:
  - security_guard.py    (pre_tool_call hook, 차단 이벤트)
  - security-filter 플러그인  (transform_tool_result, 마스킹 이벤트)

로그 위치: ~/.hermes/logs/security/YYYY-MM-DD.log
보관 기간: 14일 (기록할 때마다 오래된 파일 자동 삭제)

포맷 (한 줄):
  2026-08-04 10:30:15 | BLOCKED  | platform=slack | tool=terminal | session=agent:main:slack:... | rule=Slack/Teams 파일 삭제 차단 | detail=rm -rf /home/...
"""

from __future__ import annotations

import os
import glob
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────
_HOME         = Path.home()
_LOG_DIR      = _HOME / ".hermes" / "logs" / "security"
_KEEP_DAYS    = 14
_DETAIL_LIMIT = 300   # detail 필드 최대 문자 수 (너무 길면 잘라냄)


def _log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _today_file() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _log_dir() / f"{date_str}.log"


def _rotate() -> None:
    """14일 초과 로그 파일 삭제."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_KEEP_DAYS)
    for path in glob.glob(str(_log_dir() / "*.log")):
        fname = os.path.basename(path)              # 예: 2026-07-01.log
        date_part = fname.replace(".log", "")
        try:
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# 공개 인터페이스
# ─────────────────────────────────────────────────────────────────────────────
def write(
    event_type: str,          # "BLOCKED" | "MASKED" 등
    *,
    tool: str    = "",
    platform: str = "",
    session: str = "",
    rule: str    = "",
    detail: str  = "",
) -> None:
    """
    보안 이벤트를 오늘자 로그 파일에 한 줄로 기록한다.
    예외가 발생해도 조용히 무시 (로그 실패가 보안 차단을 방해해선 안 됨).
    """
    try:
        # detail 길이 제한
        if len(detail) > _DETAIL_LIMIT:
            detail = detail[:_DETAIL_LIMIT] + "...(생략)"

        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"{ts} | {event_type:<8} | platform={platform} | tool={tool} | "
            f"session={session} | rule={rule} | detail={detail}\n"
        )

        with open(_today_file(), "a", encoding="utf-8") as f:
            f.write(line)

        # rotation은 기록 성공 후 실행 (비용 적음, 날짜 바뀌는 시점에 자동 정리)
        _rotate()

    except Exception:
        pass  # 로그 오류는 무시 — 에이전트 동작에 영향 없어야 함
