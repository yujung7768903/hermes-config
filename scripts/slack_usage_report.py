#!/usr/bin/env python3
"""사용 현황 리포트 — 사용 원장을 Block Kit 으로 관리자 채널에 보낸다.

읽는 것   `~/.hermes/logs/usage/usage.db` (hooks/usage_log.py 가 쌓는 원장)
보내는 곳 SLACK_ADMIN_CHANNEL (기본 C0BPH28DLDN)
주기      크론에서 **인자 없이** 실행된다 (docs/cron-jobs.md). 기본 창은 최근 7일.
          `--days`·`--channel`·`--db`·`--dry-run` 은 수동 확인용이다.

메시지를 두 개로 나누는 이유
  Slack 은 **메시지당 data_visualization 블록 2개**까지만 받는다. 차트가 셋이라
  첫 메시지에 둘(사용자별 요청 수·사용자별 토큰), 그 스레드에 나머지 하나
  (일자별 토큰 추이)와 상세 표를 붙인다. 스레드로 내리면 채널 목록이 리포트
  한 줄로 유지된다.

Block Kit 제약 (docs.slack.dev/reference/block-kit/blocks/data-visualization-block)
  title 50자 · series/segments 최대 12 · series 당 data point 최대 20 ·
  series name 과 data point label 20자 · categories 20자
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERMES_HOME = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
DB_PATH = HERMES_HOME / "logs" / "usage" / "usage.db"
ENV_FILE = HERMES_HOME / ".env"
NAME_CACHE = HERMES_HOME / "logs" / "usage" / "users.json"

DEFAULT_CHANNEL = "C0BPH28DLDN"      # 관리자 채널
DEFAULT_DAYS = 7
TOP_USERS = 10                        # 차트 카테고리 상한(20)과 가독성 사이

# 원장에는 prompt-gate 판정을 **가공 없이** 넣는다. 묶는 기준은 여기 하나뿐이라,
# 카테고리가 늘거나 정의가 바뀌면 원장이 아니라 이 표만 고치면 된다.
# 어느 그룹에도 없는 값은 '기타' 로 모인다 — 새 카테고리가 조용히 사라지지 않는다.
GROUPS = {
    "블로그 설명": ("service_explain", "code_locate_impact", "service_access_info"),
    "데이터 조회": ("service_data_query",),
    "오류 분석":   ("incident_analysis",),
    "운영·정책":   ("project_docs_qa", "deploy_history_query", "db_schema_query"),
    "에이전트":    ("agent_usage_query", "chitchat"),
    "변경 요구":   ("mutate_code_config_data", "deploy_restart_kill", "skill_add",
                    "batch_schedule_add", "script_add", "development_request",
                    "agent_restart"),
    "공격 시도":   ("prompt_injection",),
    "미분류":      ("unknown", "out_of_scope", "credential_instance_access",
                    "harness_self_modify"),
}
GROUP_OF = {cat: name for name, cats in GROUPS.items() for cat in cats}

FORM_LABEL = {"initial": "요청", "followup": "재질문", "challenge": "반문"}


# ── .env ──────────────────────────────────────────────────────────────────
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ── 원장 읽기 ─────────────────────────────────────────────────────────────
def fetch_rows(db: Path, since_epoch: float) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM turns WHERE ts_epoch >= ? ORDER BY ts_epoch",
            (since_epoch,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ── 사용자 이름 ───────────────────────────────────────────────────────────
def resolve_names(token: str, user_ids: list[str]) -> dict:
    """Slack user id → 표시 이름. 조회 결과는 파일에 캐시한다.

    이름은 원장이 아니라 여기서 붙인다. 게이트웨이 핫패스에서 users.info 를 부르면
    대화 응답이 그만큼 늦어지고, 이름은 바뀔 수 있어 리포트 시점 값이 더 맞다.
    """
    try:
        cache = json.loads(NAME_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    for uid in user_ids:
        if not uid or uid in cache:
            continue
        # 실패는 빈 문자열로 캐시한다. uid 를 넣으면 원장에 실려 온 이름
        # (user_name)보다 우선해 버려서, 조회 권한이 없을 때 표에 ID 만 찍힌다.
        # 빈 값이면 display_name 이 원장 이름 → uid 순으로 흘러간다.
        try:
            req = urllib.request.Request(
                f"https://slack.com/api/users.info?user={uid}",
                headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            prof = (data.get("user") or {}).get("profile") or {}
            cache[uid] = ((prof.get("display_name") or prof.get("real_name")
                           or (data.get("user") or {}).get("name") or "")
                          if data.get("ok") else "")
        except Exception:
            cache[uid] = ""        # 조회 실패해도 리포트는 계속 나간다

    try:
        NAME_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NAME_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return cache


# ── 집계 ──────────────────────────────────────────────────────────────────
def summarize(rows: list[dict], days: int, now: datetime) -> dict:
    per_user = defaultdict(lambda: {"dm": 0, "mention": 0, "other": 0, "tokens": 0,
                                    "cost": 0.0, "initial": 0, "followup": 0,
                                    "challenge": 0, "improve": 0, "name": None})
    per_day = {(now.date() - timedelta(days=i)).strftime("%m-%d"): 0
               for i in range(days - 1, -1, -1)}
    per_group = defaultdict(lambda: {"count": 0, "tokens": 0, "blocked": 0})
    totals = {"requests": len(rows), "tokens": 0, "cost": 0.0, "unanswered": 0,
              "blocked": 0, "observed": 0, "latency": [],
              "modes": defaultdict(int)}

    for r in rows:
        uid = r.get("user_id") or "unknown"
        u = per_user[uid]
        if r.get("user_name") and not u["name"]:
            u["name"] = r["user_name"]

        mode = r.get("mode") or "unknown"
        u["dm" if mode == "dm" else "mention" if mode == "mention" else "other"] += 1
        totals["modes"][mode] += 1

        form = r.get("form") if r.get("form") in FORM_LABEL else "initial"
        u[form] += 1
        if r.get("improve"):
            u["improve"] += 1

        tokens = r.get("tokens_total") or 0
        cost = r.get("cost_usd") or 0.0
        u["tokens"] += tokens
        u["cost"] += cost
        totals["tokens"] += tokens
        totals["cost"] += cost

        verdict = r.get("verdict")
        if verdict in ("block", "admin_block"):
            totals["blocked"] += 1
        elif verdict == "observe":
            totals["observed"] += 1
        if r.get("response") is None:
            totals["unanswered"] += 1
        if r.get("latency_ms"):
            totals["latency"].append(r["latency_ms"])

        day = datetime.fromtimestamp(r["ts_epoch"], KST).strftime("%m-%d")
        if day in per_day:
            per_day[day] += tokens

        g = per_group[GROUP_OF.get(r.get("category"), "기타")]
        g["count"] += 1
        g["tokens"] += tokens
        if verdict in ("block", "admin_block"):
            g["blocked"] += 1

    order = sorted(per_user, key=lambda x: -(per_user[x]["dm"]
                                             + per_user[x]["mention"]
                                             + per_user[x]["other"]))
    return {"per_user": per_user, "order": order[:TOP_USERS], "per_day": per_day,
            "per_group": per_group, "totals": totals}


# ── Block Kit ─────────────────────────────────────────────────────────────
def clip(text: str, limit: int) -> str:
    text = (text or "").strip() or "-"
    return text if len(text) <= limit else text[:limit - 1] + "…"


def viz(title: str, chart_type: str, categories: list[str],
        series: list[tuple[str, list]], x_label: str = "",
        y_label: str = "") -> dict:
    """data_visualization 블록. 카테고리·시리즈는 Slack 상한에 맞춰 자른다."""
    cats = [clip(c, 20) for c in categories[:20]]
    built = [{"name": clip(name, 20),
              "data": [{"label": c, "value": v} for c, v in zip(cats, values)]}
             for name, values in series[:12]]
    block = {"type": "data_visualization", "title": clip(title, 50),
             "chart": {"type": chart_type, "series": built,
                       "axis_config": {"categories": cats}}}
    if x_label:
        block["chart"]["axis_config"]["x_label"] = clip(x_label, 50)
    if y_label:
        block["chart"]["axis_config"]["y_label"] = clip(y_label, 50)
    return block


def table(caption: str, header: list[str], rows: list[list[str]]) -> dict:
    """data_table 블록. 첫 행은 raw_text 만 받는다 (rich_text 를 넣으면 400)."""
    head = [{"type": "raw_text", "text": clip(h, 60)} for h in header]
    body = [[{"type": "rich_text", "elements": [
        {"type": "rich_text_section",
         "elements": [{"type": "text", "text": str(c) if str(c).strip() else "-"}]}]}
        for c in row] for row in rows]
    return {"type": "data_table", "caption": caption, "rows": [head] + body}


def display_name(uid: str, bucket: dict, names: dict) -> str:
    return names.get(uid) or bucket.get("name") or uid


def build_main(agg: dict, names: dict, days: int, now: datetime) -> list[dict]:
    t, per_user, order = agg["totals"], agg["per_user"], agg["order"]
    avg = sum(t["latency"]) / len(t["latency"]) / 1000 if t["latency"] else 0
    start = (now - timedelta(days=days - 1)).strftime("%m-%d")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"Hermes 사용 현황 (최근 {days}일)"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*{start} ~ {now.strftime('%m-%d')}*  ·  요청 *{t['requests']}건*  ·  "
            f"사용자 *{len(per_user)}명*  ·  토큰 *{t['tokens']:,}*  ·  "
            f"추정 *${t['cost']:.4f}*")}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": (
            f"DM {t['modes'].get('dm', 0)}건 · 채널 {t['modes'].get('mention', 0)}건 · "
            f"평균 응답 {avg:.1f}초 · 차단 {t['blocked']}건 · "
            f"관측차단 {t['observed']}건 · 무응답 {t['unanswered']}건")}]},
    ]

    if not order:
        blocks.append({"type": "section", "text": {
            "type": "mrkdwn", "text": "이 기간에 기록된 대화가 없습니다."}})
        return blocks

    labels = [clip(display_name(u, per_user[u], names), 20) for u in order]
    blocks.append(viz("사용자별 요청 수", "bar", labels,
                      [("DM", [per_user[u]["dm"] for u in order]),
                       ("채널", [per_user[u]["mention"] for u in order])],
                      y_label="요청 수"))
    blocks.append(viz("사용자별 토큰", "bar", labels,
                      [("토큰", [per_user[u]["tokens"] for u in order])],
                      y_label="토큰"))
    return blocks


def build_detail(agg: dict, names: dict) -> list[dict]:
    per_user, order, per_day = agg["per_user"], agg["order"], agg["per_day"]
    blocks = [viz("일자별 토큰 추이", "line", list(per_day),
                  [("토큰", list(per_day.values()))], x_label="날짜", y_label="토큰")]

    groups = sorted(agg["per_group"].items(), key=lambda kv: -kv[1]["count"])
    if groups:
        blocks.append(table(
            "요청 종류별 사용량", ["요청 종류", "건수", "토큰", "차단"],
            [[name, str(v["count"]), f"{v['tokens']:,}", str(v["blocked"])]
             for name, v in groups]))

    if order:
        blocks.append(table(
            "사용자별 요청 유형",
            ["사용자", "요청", "재질문", "반문", "개선제안", "토큰", "비용"],
            [[clip(display_name(u, per_user[u], names), 30),
              str(per_user[u]["initial"]), str(per_user[u]["followup"]),
              str(per_user[u]["challenge"]), str(per_user[u]["improve"]),
              f"{per_user[u]['tokens']:,}", f"${per_user[u]['cost']:.4f}"]
             for u in order]))
    return blocks


# ── 전송 ──────────────────────────────────────────────────────────────────
def post(token: str, channel: str, blocks: list[dict], text: str,
         thread_ts: str | None = None) -> str | None:
    payload = {"channel": channel, "blocks": blocks, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"[ERROR] Slack HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[ERROR] Slack 전송 실패: {exc}", file=sys.stderr)
        return None
    if not result.get("ok"):
        print(f"[ERROR] Slack API: {result.get('error')} "
              f"{result.get('response_metadata', '')}", file=sys.stderr)
        return None
    return result.get("ts")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--channel", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="전송하지 않고 블록 JSON 만 출력")
    args = ap.parse_args()

    load_env(ENV_FILE)
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = args.channel or os.environ.get("SLACK_ADMIN_CHANNEL") or DEFAULT_CHANNEL
    db = Path(args.db) if args.db else DB_PATH

    now = datetime.now(KST)
    rows = fetch_rows(db, (now - timedelta(days=args.days)).timestamp())
    agg = summarize(rows, args.days, now)
    names = ({} if args.dry_run or not token
             else resolve_names(token, list(agg["order"])))

    main_blocks = build_main(agg, names, args.days, now)
    detail_blocks = build_detail(agg, names) if agg["order"] else []

    if args.dry_run:
        print(json.dumps({"main": main_blocks, "detail": detail_blocks},
                         ensure_ascii=False, indent=2))
        return
    if not token:
        print("[ERROR] SLACK_BOT_TOKEN 이 없습니다.", file=sys.stderr)
        sys.exit(1)

    ts = post(token, channel, main_blocks, "Hermes 사용 현황 리포트")
    if ts is None:
        sys.exit(1)
    if detail_blocks:
        post(token, channel, detail_blocks, "사용 현황 상세", thread_ts=ts)
    print(f"[OK] 사용 현황 리포트 전송 — {len(rows)}건 / {args.days}일 / {channel}")


if __name__ == "__main__":
    main()
