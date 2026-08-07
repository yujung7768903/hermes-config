#!/usr/bin/env python3
"""
슬랙 보안 탐지 리포트 (--no-agent 모드용)

hermes 는 파일 수정·권한 변경 권한이 없다. 따라서 ~/.hermes 에 커밋되지 않은
변경사항이 남아 있다는 것 자체가 점검 대상이다. 매일 관리자 채널로 그 목록을 보낸다.

- 대상: ~/.hermes 의 `git status` (추적 파일 수정 + .gitignore 화이트리스트 신규 파일)
- 채널: SLACK_ADMIN_CHANNEL (기본 C0BPH28DLDN)
- 슬랙 전송·env 로더는 slack_improvement_report 모듈을 재사용

~/.hermes/.gitignore 는 `*` 후 화이트리스트 방식이라, 화이트리스트 밖에 생성된
파일(루트의 test.txt, hooks/evil.sh, skills/ 하위 새 스킬 등)은 `git status` 에
아예 나오지 않는다. 그래서 두 번째 축으로 무시된 경로 목록을 기준선과 비교한다.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

# 크론이 이 파일을 어떤 방식으로 실행하든 같은 디렉터리의 모듈을 찾게 한다
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slack_improvement_report import load_env, post_to_slack, raw_cell, rich_cell  # noqa: E402

KST = timezone(timedelta(hours=9))
HERMES_DIR = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
ENV_FILE = HERMES_DIR / ".env"
DEFAULT_CHANNEL = "C0BPH28DLDN"  # 관리자 채널

# 무시된 경로 스캔에서 뺄 곳. 런타임이 파일을 계속 만들고 지워서 경로 목록이
# 안 고정된다. git 이 통째로 무시하는 디렉터리(logs/, node/ 등)는 항목 하나로
# 접혀 나오므로 따로 뺄 필요가 없다.
IGNORED_EXCLUDES = (":(exclude)cron", ":(exclude)**/__pycache__")
BASELINE_FILE = HERMES_DIR / "logs" / "security" / ".known_ignored"

CODE_LABEL = {
    "M": "수정",
    "A": "추가",
    "D": "삭제",
    "R": "이름변경",
    "C": "복사",
    "T": "타입변경",
    "U": "충돌",
}


# ─── git 실행 ─────────────────────────────────────────────────────────────────

def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc.stdout


# ─── 파싱 ─────────────────────────────────────────────────────────────────────

def parse_status(raw: str) -> List[Tuple[str, str]]:
    """`git status --porcelain=v1 -z` 출력 → [(코드, 경로)]

    -z 를 쓰는 이유: 기본 출력은 특수문자 경로를 C 스타일로 따옴표 인용해서
    경로가 깨진다. -z 는 NUL 구분이라 인용이 없다.
    이름변경(R)·복사(C) 는 새 경로 뒤에 옛 경로가 한 필드 더 붙는다.
    """
    parts = raw.split("\0")
    items: List[Tuple[str, str]] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in ("R", "C") and i < len(parts):
            old = parts[i]
            i += 1
            path = f"{old} → {path}"
        items.append((code, path))
    return items


def status_label(code: str) -> str:
    if code == "??":
        return "미추적 (신규)"
    if code == "!!":
        return "화이트리스트 밖 (신규)"
    marks = []
    for c in code:
        name = CODE_LABEL.get(c)
        if name and name not in marks:
            marks.append(name)
    return " / ".join(marks) or code


def parse_numstat(raw: str) -> Dict[str, Tuple[int, int]]:
    """`git diff --numstat` 출력 → {경로: (추가, 삭제)}. 바이너리는 (0, 0)."""
    stat: Dict[str, Tuple[int, int]] = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        added, deleted, path = fields[0], fields[1], fields[-1]
        stat[path] = (
            int(added) if added.isdigit() else 0,
            int(deleted) if deleted.isdigit() else 0,
        )
    return stat


def churn_text(path: str, stat: Dict[str, Tuple[int, int]]) -> str:
    added, deleted = stat.get(path, (0, 0))
    if not added and not deleted:
        return "-"
    return f"+{added} / -{deleted}"


# ─── 수집 ─────────────────────────────────────────────────────────────────────

def scan_ignored(repo: Path) -> List[str]:
    """.gitignore 로 무시되고 있는 경로 전체. 화이트리스트 밖 파일이 여기 잡힌다."""
    raw = git(repo, "status", "--porcelain=v1", "-z", "--ignored=matching",
              "--untracked-files=all", "--", ".", *IGNORED_EXCLUDES)
    return sorted(path for code, path in parse_status(raw) if code == "!!")


def read_baseline() -> List[str]:
    try:
        return BASELINE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def write_baseline(paths: List[str]) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text("\n".join(paths), encoding="utf-8")


def new_ignored(repo: Path) -> List[str]:
    """기준선에 없던 무시 경로만. 기준선이 없으면 지금 상태를 기준선으로 삼고 비운다.

    무시 경로는 정상 파일만 569개(번들 스킬·캐시·.env 등)라 전량 보고는 의미가 없다.
    기준선 갱신은 일일 리포트만 한다 — 10분 감시가 갱신해버리면 하루 한 번 눈으로
    확인할 기회 없이 신규 파일이 그대로 정상 취급된다.
    """
    current = scan_ignored(repo)
    if not BASELINE_FILE.exists():
        write_baseline(current)
        return []
    known = set(read_baseline())
    return [p for p in current if p not in known]


def collect(repo: Path) -> Dict:
    changes = parse_status(git(repo, "status", "--porcelain=v1", "-z"))
    changes += [("!!", path) for path in new_ignored(repo)]

    stat = parse_numstat(git(repo, "diff", "--numstat"))
    stat.update(parse_numstat(git(repo, "diff", "--cached", "--numstat")))

    return {"changes": changes, "stat": stat}


# ─── Block Kit ────────────────────────────────────────────────────────────────

DEFAULT_TITLE = "Hermes 보안 탐지 리포트"


def build_blocks(info: Dict, now: datetime, title: str = DEFAULT_TITLE) -> List[Dict]:
    ts = now.strftime("%Y-%m-%d %H:%M KST")
    changes = info["changes"]

    if not changes:
        return [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f":white_check_mark: *{title}* ({ts})\n"
                f"`~/.hermes` 에 커밋되지 않은 변경사항이 없습니다."
            )},
        }]

    blocks: List[Dict] = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": (
            f":rotating_light: *{title}* ({ts})\n"
            f"`~/.hermes` 에 커밋되지 않은 변경사항 *{len(changes)}건* 이 있습니다."
        )},
    }, {"type": "divider"}]

    header = [raw_cell("상태"), raw_cell("파일"), raw_cell("변경량")]
    rows = [[
        rich_cell((status_label(code), None)),
        rich_cell((path, {"code": True})),
        rich_cell((churn_text(path, info["stat"]), None)),
    ] for code, path in changes]

    blocks.append({
        "type": "data_table",
        "caption": f"커밋되지 않은 변경사항 ({now.strftime('%Y-%m-%d')})",
        "rows": [header] + rows,
    })
    return blocks


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    load_env(ENV_FILE)

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_ADMIN_CHANNEL", DEFAULT_CHANNEL)
    if not token:
        print("[ERROR] SLACK_BOT_TOKEN 이 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(KST)
    info = collect(HERMES_DIR)
    blocks = build_blocks(info, now)

    if not post_to_slack(token, channel, blocks):
        sys.exit(1)

    # 보고한 신규 무시 경로를 기준선에 흡수한다. 하루 한 번 보고된 뒤에야
    # 정상 취급되므로, 10분 감시와 이 리포트 중 최소 한 번은 반드시 눈에 띈다.
    write_baseline(scan_ignored(HERMES_DIR))
    print(f"[OK] 슬랙 전송 완료 — 변경 {len(info['changes'])}건 ({now.strftime('%Y-%m-%d %H:%M KST')})")


if __name__ == "__main__":
    main()
