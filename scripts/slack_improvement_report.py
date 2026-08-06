#!/usr/bin/env python3
"""
슬랙 자가 개선 리포트 스크립트 (--no-agent 모드용)
- 마지막 슬랙 전송 이후 쌓인 히스토리를 Slack Block Kit data_table 로 전송
- 새 기록 없으면 "새롭게 개선된 내용이 없습니다" 메시지 전송
- ~/.hermes/.env 에서 SLACK_BOT_TOKEN / SLACK_HOME_CHANNEL 읽음
- 마지막 전송 시각은 ~/.hermes/history/.last_slack_sent 에 ISO 형식으로 저장
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict

KST = timezone(timedelta(hours=9))
HISTORY_DIR = Path(os.path.expanduser("~/.hermes/history"))
LAST_SENT_FILE = HISTORY_DIR / ".last_slack_sent"
ENV_FILE = Path(os.path.expanduser("~/.hermes/.env"))


# ─── .env 로더 ────────────────────────────────────────────────────────────────

def load_env(path: Path):
    """~/.hermes/.env 를 파싱해서 os.environ 에 주입 (이미 설정된 값은 덮지 않음)"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ─── 마지막 전송 시각 관리 ──────────────────────────────────────────────────────

def read_last_sent() -> Optional[datetime]:
    if not LAST_SENT_FILE.exists():
        return None
    try:
        raw = LAST_SENT_FILE.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def write_last_sent(dt: datetime):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SENT_FILE.write_text(dt.isoformat(), encoding="utf-8")


# ─── 히스토리 파싱 ─────────────────────────────────────────────────────────────

def collect_new_records(since: Optional[datetime]) -> List[Dict]:
    records = []
    if not HISTORY_DIR.exists():
        return records

    for md_file in sorted(HISTORY_DIR.glob("????-??.md")):
        content = md_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6:
                continue
            ts_str = parts[1]
            # 헤더·구분선 스킵
            if ts_str in ("시간 (KST)", "---", "") or ts_str.startswith("-"):
                continue
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
            except ValueError:
                continue
            if since is not None and ts <= since:
                continue
            records.append({
                "timestamp": ts,
                "title":  parts[2] if len(parts) > 2 else "-",
                "reason": parts[3] if len(parts) > 3 else "-",
                "basis":  parts[4] if len(parts) > 4 else "-",
                "files":  parts[5] if len(parts) > 5 else "-",
            })

    records.sort(key=lambda r: r["timestamp"])
    return records


# ─── Block Kit 빌더 ────────────────────────────────────────────────────────────

def raw_cell(text: str) -> Dict:
    """헤더 전용 raw_text 셀"""
    return {"type": "raw_text", "text": text or "-"}


def rich_cell(*lines: tuple) -> Dict:
    """
    rich_text 셀 — 말줄임 없이 줄바꿈으로 전체 텍스트 표시
    lines: (text, style_dict_or_None) 튜플 목록
    예) rich_cell(("본문", None), ("부제", {"italic": True}))
    """
    elements = []
    for i, (txt, style) in enumerate(lines):
        if not txt:
            continue
        elem: Dict = {"type": "text", "text": txt}
        if style:
            elem["style"] = style
        elements.append(elem)
        # 줄 사이에 줄바꿈 삽입 (마지막 줄 제외)
        if i < len(lines) - 1:
            elements.append({"type": "text", "text": "\n"})

    return {
        "type": "rich_text",
        "elements": [
            {"type": "rich_text_section", "elements": elements}
        ],
    }


def build_blocks(records: List[Dict], now: datetime) -> List[Dict]:
    """Slack Block Kit blocks 반환"""

    blocks: List[Dict] = []

    if not records:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *Hermes 자가 개선 리포트* "
                    f"({now.strftime('%Y-%m-%d %H:%M KST')})\n"
                    "이번 주기에 새롭게 개선된 내용이 없습니다."
                )
            }
        })
        return blocks

    # 헤더 섹션
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f":rocket: *Hermes 자가 개선 리포트* "
                f"({now.strftime('%Y-%m-%d %H:%M KST')})\n"
                f"총 *{len(records)}건* 의 개선이 있었습니다."
            )
        }
    })

    blocks.append({"type": "divider"})

    # data_table — 4컬럼 (시간 / 개선 내용+근거 / 이유 / 수정 파일)
    # 컬럼을 줄여서 셀당 너비를 확보. 근거는 개선 내용 아래 italic 으로 표시.
    header_row = [
        raw_cell("시간 (KST)"),
        raw_cell("개선 내용 / 근거"),
        raw_cell("이유"),
        raw_cell("수정 파일"),
    ]

    data_rows: List[List[Dict]] = []
    for r in records:
        ts_str = r["timestamp"].strftime("%Y-%m-%d\n%H:%M")
        # 수정 파일: <br> → 줄바꿈
        files_text = r["files"].replace("<br>", "\n")
        # 근거가 있으면 개선 내용 아래에 italic 으로 붙임
        basis = r["basis"].strip()
        title_lines = [
            (r["title"], {"bold": True}),
        ]
        if basis and basis != "-":
            title_lines.append((basis, {"italic": True}))

        data_rows.append([
            rich_cell((ts_str, None)),
            rich_cell(*title_lines),
            rich_cell((r["reason"], None)),
            rich_cell((files_text, {"code": True}) if files_text != "-" else (files_text, None)),
        ])

    blocks.append({
        "type": "data_table",
        "caption": f"자가 개선 내역 ({now.strftime('%Y-%m-%d')})",
        "rows": [header_row] + data_rows,
    })

    return blocks


# ─── Slack API 전송 ────────────────────────────────────────────────────────────

def post_to_slack(token: str, channel: str, blocks: List[Dict]) -> bool:
    payload = json.dumps({
        "channel": channel,
        "blocks": blocks,
        "text": "Hermes 자가 개선 리포트",  # 알림 fallback 텍스트
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Slack HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] Slack 전송 실패: {e}", file=sys.stderr)
        return False

    if not result.get("ok"):
        print(f"[ERROR] Slack API error: {result.get('error')}", file=sys.stderr)
        return False

    return True


# ─── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    load_env(ENV_FILE)

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_HOME_CHANNEL", "C0BGUPUJFAS")  # #hermes-test

    if not token:
        print("[ERROR] SLACK_BOT_TOKEN 이 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(KST)
    since = read_last_sent()
    records = collect_new_records(since)
    blocks = build_blocks(records, now)

    ok = post_to_slack(token, channel, blocks)
    if ok:
        write_last_sent(now)
        print(f"[OK] 슬랙 전송 완료 — {len(records)}건 ({now.strftime('%Y-%m-%d %H:%M KST')})")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
