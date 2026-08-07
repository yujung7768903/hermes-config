#!/usr/bin/env python3
"""
10분 주기 보안 탐지 (--no-agent 모드용)

security_report.py 의 일일 리포트는 상태와 무관하게 항상 보낸다. 이 스크립트는
그 사이의 변조 시도를 잡는 용도라 반대다 — 탐지된 게 없으면 아무것도 보내지 않는다.

- 탐지 대상: security_report.collect() 와 동일 (`~/.hermes` 의 커밋되지 않은 변경)
- 전송 조건: 변경이 1건 이상이고, 직전 전송분과 변경 목록이 다를 때만
  같은 변경이 커밋될 때까지 10분마다 재알림되는 것을 막는다. 목록이 바뀌면
  (파일 추가·삭제) 새 시도로 보고 다시 보낸다.
- 상태 파일: ~/.hermes/logs/security/.last_alert
  logs/ 는 LK 동결 제외 대상이라 쓰기가 되고, .gitignore 대상이라 이 파일 자체가
  다음 탐지에 잡히지 않는다.

크론의 script 필드는 인자를 받지 않는다. 그래서 일일/주기 모드를 플래그가 아니라
별도 스크립트로 나눈다.
"""

import hashlib
import os
import sys
from datetime import datetime
from typing import List, Tuple

# 크론이 이 파일을 어떤 방식으로 실행하든 같은 디렉터리의 모듈을 찾게 한다
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import security_report as sr  # noqa: E402
from slack_improvement_report import post_to_slack  # noqa: E402

STATE_FILE = sr.HERMES_DIR / "logs" / "security" / ".last_alert"
TITLE = "Hermes 보안 탐지 (10분 주기)"


def signature(changes: List[Tuple[str, str]]) -> str:
    """변경 목록의 지문. 변경이 없으면 빈 문자열."""
    if not changes:
        return ""
    raw = "\n".join(f"{code}\t{path}" for code, path in changes)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decide(changes: List[Tuple[str, str]], prev: str) -> Tuple[bool, str]:
    """(전송할지, 저장할 지문). 지문이 그대로면 전송하지 않는다."""
    sig = signature(changes)
    return (bool(changes) and sig != prev), sig


def read_state() -> str:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_state(sig: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(sig, encoding="utf-8")


def main():
    sr.load_env(sr.ENV_FILE)

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_ADMIN_CHANNEL", sr.DEFAULT_CHANNEL)
    if not token:
        print("[ERROR] SLACK_BOT_TOKEN 이 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    info = sr.collect(sr.HERMES_DIR)
    changes = info["changes"]
    prev = read_state()
    send, sig = decide(changes, prev)

    if sig != prev:
        write_state(sig)

    if not send:
        print(f"[SKIP] 전송 안 함 — 변경 {len(changes)}건, 직전과 동일 여부 {sig == prev}")
        return

    blocks = sr.build_blocks(info, datetime.now(sr.KST), title=TITLE)
    if not post_to_slack(token, channel, blocks):
        sys.exit(1)
    print(f"[OK] 슬랙 전송 완료 — 신규 변경 {len(changes)}건")


if __name__ == "__main__":
    main()
