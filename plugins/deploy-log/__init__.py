"""
deploy-log plugin — ⚡ 배포 공지 shortcut

흐름:
  1. 슬랙 ⚡ 메뉴에서 "배포 공지" 선택
     → shortcut handler가 즉시 views_open (Block Kit 모달)
  2. 모달 제출 (deploy_log_submit view_submission)
     → 배포 채널에 포맷된 공지 전송 + DB 저장
  3. 공지 메시지의 액션 버튼 클릭
     → 상태 변경 + 메시지 업데이트 + (완료 시) QA 스레드 전송
     → Jira 이슈 상태 자동 전환 (JIRA_URL/JIRA_USER/JIRA_API_TOKEN 설정 시)

Slack 앱 설정:
  - Features → Interactivity & Shortcuts → Shortcuts → Create New Shortcut
    - Name: 배포 공지
    - Short description: 배포 예정/완료/롤백 공지
    - Callback ID: deploy_log
    - Type: Global  (어느 채널에서든 ⚡ 메뉴로 접근)
  - Interactivity ON 필수

Jira 연동 설정 (.env):
  JIRA_URL           https://yourcompany.atlassian.net
  JIRA_USER          dev@yourcompany.com
  JIRA_API_TOKEN     <Atlassian 계정 → 보안 → API 토큰>

  트랜지션 이름 오버라이드 (선택, Jira 워크플로우 이름과 맞춰야 함):
  JIRA_TRANSITION_COMPLETE   배포 완료 시  (기본: 완료)
  JIRA_TRANSITION_CANCEL     배포 취소 시  (기본: 취소)
  JIRA_TRANSITION_ROLLBACK   롤백 시       (기본: 진행 중)
  JIRA_TRANSITION_QA_DONE    QA 완료 시    (기본: 완료)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEPLOY_TYPES = ["예정", "완료", "롤백"]
SERVICES = ["HERB", "AMS", "HOMEPAGE", "INFRA", "LIBRARY", "VMS", "MEMBERSHIP"]

# Slack manifest metadata — consumed by `hermes slack manifest` to include
# this shortcut in the generated app manifest automatically.
SHORTCUTS = [
    {
        "callback_id": "deploy_log",
        "name": "배포 공지",
        "description": "배포 예정/완료/롤백 공지 등록",
        "type": "global",
    }
]


# ── Block Kit 모달 ─────────────────────────────────────────────────────────

def _build_modal() -> dict:
    return {
        "type": "modal",
        "callback_id": "deploy_log_submit",
        "title": {"type": "plain_text", "text": "배포 공지"},
        "submit": {"type": "plain_text", "text": "공지하기"},
        "close":  {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "input",
                "block_id": "block_type",
                "label": {"type": "plain_text", "text": "배포 구분"},
                "element": {
                    "type": "static_select",
                    "action_id": "deploy_type",
                    "placeholder": {"type": "plain_text", "text": "선택하세요"},
                    "options": [
                        {"text": {"type": "plain_text", "text": t}, "value": t}
                        for t in DEPLOY_TYPES
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "block_service",
                "label": {"type": "plain_text", "text": "서비스 구분"},
                "element": {
                    "type": "static_select",
                    "action_id": "deploy_service",
                    "placeholder": {"type": "plain_text", "text": "선택하세요"},
                    "options": [
                        {"text": {"type": "plain_text", "text": s}, "value": s}
                        for s in SERVICES
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "block_date",
                "label": {"type": "plain_text", "text": "날짜"},
                "element": {
                    "type": "datepicker",
                    "action_id": "deploy_date",
                    "placeholder": {"type": "plain_text", "text": "날짜 선택"},
                },
            },
            {
                "type": "input",
                "block_id": "block_time",
                "label": {"type": "plain_text", "text": "시간"},
                "element": {
                    "type": "timepicker",
                    "action_id": "deploy_time",
                    "placeholder": {"type": "plain_text", "text": "시간 선택"},
                },
            },
            {
                "type": "input",
                "block_id": "block_content",
                "label": {"type": "plain_text", "text": "배포 내용"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "deploy_content",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "배포 내용을 입력해 주세요"},
                },
            },
            {
                "type": "input",
                "block_id": "block_pr",
                "label": {"type": "plain_text", "text": "PR 링크"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "deploy_pr",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "한 줄에 PR 링크 하나씩 입력\nhttps://github.com/...\nhttps://github.com/..."},
                },
            },
            {
                "type": "input",
                "block_id": "block_jira",
                "label": {"type": "plain_text", "text": "JIRA"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "deploy_jira",
                    "placeholder": {"type": "plain_text", "text": "https://jira.example.com/browse/PROJ-1234"},
                },
            },
            {
                "type": "input",
                "block_id": "block_assignees",
                "label": {"type": "plain_text", "text": "담당자"},
                "optional": True,
                "element": {
                    "type": "multi_users_select",
                    "action_id": "deploy_assignees",
                    "placeholder": {"type": "plain_text", "text": "담당자를 선택하세요"},
                },
            },
            {
                "type": "input",
                "block_id": "block_qa",
                "label": {"type": "plain_text", "text": "QA 항목"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "deploy_qa",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "- 로그인 기능 확인\n- 결제 플로우 확인\n- 메인 페이지 노출 확인"},
                },
            },
        ],
    }


# ── URL에서 프로젝트명 추출 ────────────────────────────────────────────────

def _extract_project_name(url: str) -> str:
    """
    GitLab: https://gitlab.example.com/group/project/-/merge_requests/3 → project
    GitHub: https://github.com/org/repo/pull/123 → repo
    파싱 실패 시 URL 그대로 반환
    """
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        # GitLab: /-/ 앞 마지막 세그먼트
        if "/-/" in path:
            return path.split("/-/")[0].rstrip("/").split("/")[-1]
        # GitHub /pull/ or /merge_requests/ 패턴
        for marker in ["/pull/", "/merge_requests/"]:
            if marker in path:
                return path.split(marker)[0].rstrip("/").split("/")[-1]
        # 그 외: 마지막 세그먼트
        return path.split("/")[-1] or url
    except Exception:
        return url


# ── QA 항목 파싱 ──────────────────────────────────────────────────────────

def _parse_qa_items(qa_text: str) -> list[str]:
    """'- 항목' 형식 멀티라인 텍스트 → 항목 리스트"""
    items = []
    for line in qa_text.splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            items.append(line)
    return items


# ── 공지 Block Kit 포맷 ────────────────────────────────────────────────────

def _build_announce_blocks(
    deploy_type: str, service: str,
    deploy_date: str, deploy_time: str,
    content: str, user_id: str,
    pr_link: str = "", jira: str = "",
    status: str = "",
    deploy_id: int = 0,
) -> list:
    pr_links = [l.strip() for l in pr_link.splitlines() if l.strip()] if pr_link else []
    date_label = "예정 날짜" if deploy_type == "예정" else "날짜"
    time_label = "예정 시간" if deploy_type == "예정" else "시간"
    current_status = status or deploy_type

    # 헤더 텍스트
    if current_status == "롤백":
        header_text = "🔴롤백 공지"
    elif current_status == "취소":
        header_text = "❌취소 공지"
    elif current_status == "QA완료":
        header_text = "✅배포 공지"
    else:
        header_text = "📍배포 공지"

    # 날짜/시간/JIRA 불릿 리스트 아이템
    bullet_items = [
        {
            "type": "rich_text_section",
            "elements": [
                {"type": "text", "text": f"{date_label}: "},
                {"type": "text", "text": deploy_date},
            ],
        },
        {
            "type": "rich_text_section",
            "elements": [
                {"type": "text", "text": f"{time_label}: "},
                {"type": "text", "text": deploy_time},
            ],
        },
    ]
    if jira:
        bullet_items.append({
            "type": "rich_text_section",
            "elements": [
                {"type": "text", "text": "JIRA: "},
                {"type": "link", "url": jira, "text": jira, "style": {"bold": True}},
            ],
        })

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True},
            "level": 1,
        },
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": content}],
                },
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 0,
                    "elements": bullet_items,
                },
            ],
        },
    ]

    # PR별 섹션
    for url in pr_links:
        project_name = _extract_project_name(url)
        blocks += [
            {"type": "divider"},
            {
                "type": "header",
                "text": {"type": "plain_text", "text": project_name, "emoji": True},
            },
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_list",
                        "style": "bullet",
                        "indent": 0,
                        "elements": [
                            {
                                "type": "rich_text_section",
                                "elements": [
                                    {"type": "text", "text": "PR: "},
                                    {"type": "link", "url": url, "text": url, "style": {"bold": True}},
                                ],
                            }
                        ],
                    }
                ],
            },
        ]

    blocks += [
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn",
                "text": f"공지: <@{user_id}> | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            }],
        },
    ]

    # 액션 버튼 (상태별)
    if deploy_id:
        action_buttons = _build_action_buttons(current_status, deploy_id)
        if action_buttons:
            blocks.append(action_buttons)

    return blocks


def _build_action_buttons(status: str, deploy_id: int) -> dict | None:
    """상태에 따른 액션 버튼 블록 반환. 버튼 없으면 None."""
    if status == "예정":
        return {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "완료로 변경"},
                    "style": "primary",
                    "action_id": "deploy_action_complete",
                    "value": str(deploy_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "취소"},
                    "style": "danger",
                    "action_id": "deploy_action_cancel",
                    "value": str(deploy_id),
                    "confirm": {
                        "title": {"type": "plain_text", "text": "배포 취소"},
                        "text": {"type": "plain_text", "text": "배포를 취소하시겠습니까?"},
                        "confirm": {"type": "plain_text", "text": "취소"},
                        "deny": {"type": "plain_text", "text": "돌아가기"},
                    },
                },
            ],
        }
    if status == "QA중":
        return {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "QA 완료"},
                    "style": "primary",
                    "action_id": "deploy_action_qa_done",
                    "value": str(deploy_id),
                },
            ],
        }
    return None


# ── QA 스레드 블록 ─────────────────────────────────────────────────────────

def _build_qa_thread_blocks(
    assignees: str, qa_items: str, deploy_id: int, status: str = "QA중"
) -> list:
    """완료 공지 스레드에 전송할 QA 체크리스트 블록."""
    assignee_ids = [a.strip() for a in assignees.split(",") if a.strip()] if assignees else []
    mentions = " ".join(f"<@{uid}>" for uid in assignee_ids)
    items = _parse_qa_items(qa_items) if qa_items else []

    checklist_elements = []
    for item in items:
        checklist_elements.append({
            "type": "rich_text_section",
            "elements": [{"type": "text", "text": f"☐ {item}"}],
        })

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{mentions} QA 부탁드립니다 🙏" if mentions else "QA 부탁드립니다 🙏",
            },
        },
    ]

    if checklist_elements:
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 0,
                    "elements": checklist_elements,
                }
            ],
        })

    if status == "QA중":
        # QA중: QA완료 버튼 표시
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "QA 완료"},
                    "style": "primary",
                    "action_id": "deploy_action_qa_done",
                    "value": str(deploy_id),
                },
            ],
        })
    elif status == "QA완료":
        # QA완료: 버튼 대신 완료 텍스트 표시 (더 이상 누를 수 없음)
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "✅ *QA 완료되었습니다.*"},
            ],
        })

    return blocks


# ── 플러그인 진입점 ────────────────────────────────────────────────────────

def register(ctx) -> None:
    # 1) ⚡ shortcut → 즉시 모달 오픈
    async def _on_shortcut(ack, shortcut, client):
        await ack()
        try:
            await client.views_open(
                trigger_id=shortcut["trigger_id"],
                view=_build_modal(),
            )
        except Exception as e:
            logger.error("[deploy-log] views_open 실패: %s", e, exc_info=True)

    ctx.register_slack_shortcut_handler("deploy_log", _on_shortcut)

    # 2) 모달 submit → 채널 공지 + DB 저장
    async def _on_submit(ack, body):
        await ack()
        await _handle_submit(body)

    ctx.register_slack_view_handler("deploy_log_submit", _on_submit)

    # 3) 액션 버튼 핸들러
    for action_id in [
        "deploy_action_complete",
        "deploy_action_cancel",
        "deploy_action_qa_done",
    ]:
        async def _on_action(ack, body, action_id=action_id):
            await ack()
            await _handle_action(body, action_id)

        ctx.register_slack_action_handler(action_id, _on_action)

    logger.info("[deploy-log] 플러그인 등록 완료 (shortcut: deploy_log)")


async def _handle_submit(body: dict) -> None:
    values  = (body.get("view") or {}).get("state", {}).get("values", {})
    user_id = (body.get("user") or {}).get("id", "unknown")

    def _pick(block, action, key):
        v = values.get(block, {}).get(action, {})
        if key == "option":
            return (v.get("selected_option") or {}).get("value", "")
        if key == "users":
            return v.get("selected_users", [])
        return v.get(key, "")

    deploy_type  = _pick("block_type",      "deploy_type",      "option")
    service      = _pick("block_service",   "deploy_service",   "option")
    deploy_date  = _pick("block_date",      "deploy_date",      "selected_date")
    deploy_time  = _pick("block_time",      "deploy_time",      "selected_time")
    content      = _pick("block_content",   "deploy_content",   "value")
    pr_link      = _pick("block_pr",        "deploy_pr",        "value")
    jira         = _pick("block_jira",      "deploy_jira",      "value")
    assignee_ids = _pick("block_assignees", "deploy_assignees", "users")  # list
    qa_items     = _pick("block_qa",        "deploy_qa",        "value")

    if not all([deploy_type, service, deploy_date, deploy_time, content]):
        logger.warning("[deploy-log] 모달 제출 값 누락: %s", values)
        return

    assignees_str = ",".join(assignee_ids) if assignee_ids else ""

    # DB 저장
    row_id = None
    try:
        from . import db as _db
        row_id = _db.save_deploy(
            type_=deploy_type, service=service,
            deploy_date=deploy_date, deploy_time=deploy_time,
            content=content, pr_link=pr_link or "", jira=jira or "",
            notified_by=user_id,
            assignees=assignees_str,
            qa_items=qa_items or "",
        )
    except Exception as e:
        logger.error("[deploy-log] DB 저장 실패: %s", e, exc_info=True)

    # 채널 공지
    channel = os.environ.get("DEPLOY_ANNOUNCE_CHANNEL") or os.environ.get("SLACK_HOME_CHANNEL", "")
    if not channel:
        logger.warning("[deploy-log] DEPLOY_ANNOUNCE_CHANNEL 미설정")
        return

    blocks = _build_announce_blocks(
        deploy_type, service, deploy_date, deploy_time,
        content, user_id, pr_link=pr_link or "", jira=jira or "",
        status=deploy_type, deploy_id=row_id or 0,
    )

    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))
        resp = await client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=f"[{service}] 배포 {deploy_type}",
        )
        ts = resp.get("ts") or ""

        if row_id and ts:
            from . import db as _db
            _db.update_channel_ts(row_id, ts)

        # 완료인 경우 즉시 QA 스레드 전송
        if deploy_type == "완료" and row_id and ts and assignees_str and qa_items:
            await _post_qa_thread(client, channel, ts, row_id, assignees_str, qa_items)

    except Exception as e:
        logger.error("[deploy-log] 채널 공지 실패: %s", e, exc_info=True)


async def _post_qa_thread(client, channel: str, ts: str, deploy_id: int, assignees: str, qa_items: str) -> None:
    """완료 공지 스레드에 QA 체크리스트 전송."""
    try:
        blocks = _build_qa_thread_blocks(assignees, qa_items, deploy_id, status="QA중")
        resp = await client.chat_postMessage(
            channel=channel,
            thread_ts=ts,
            blocks=blocks,
            text="QA 체크리스트",
        )
        qa_ts = resp.get("ts") or ""
        if qa_ts:
            from . import db as _db
            _db.update_status(deploy_id, "QA중", qa_thread_ts=qa_ts)
    except Exception as e:
        logger.error("[deploy-log] QA 스레드 전송 실패: %s", e, exc_info=True)


async def _handle_action(body: dict, action_id: str) -> None:
    """액션 버튼 처리."""
    try:
        from . import db as _db
        from slack_sdk.web.async_client import AsyncWebClient

        actions = body.get("actions", [])
        if not actions:
            return
        deploy_id = int(actions[0].get("value", 0))
        channel = (body.get("channel") or {}).get("id", "")
        message_ts = (body.get("message") or {}).get("ts", "")
        user_id = (body.get("user") or {}).get("id", "unknown")

        record = _db.get_deploy(deploy_id)
        if not record:
            logger.warning("[deploy-log] action: deploy_id=%s 레코드 없음", deploy_id)
            return

        client = AsyncWebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))
        jira_url = record.get("jira") or ""

        if action_id == "deploy_action_cancel":
            new_status = "취소"
            _db.update_status(deploy_id, new_status)
            blocks = _build_announce_blocks(
                record["type"], record["service"],
                record["deploy_date"], record["deploy_time"],
                record["content"], record["notified_by"],
                pr_link=record.get("pr_link") or "",
                jira=jira_url,
                status=new_status, deploy_id=deploy_id,
            )
            await client.chat_update(channel=channel, ts=message_ts, blocks=blocks, text=f"[{record['service']}] 배포 취소")
            await _sync_jira(client, channel, message_ts, jira_url, new_status)

        elif action_id == "deploy_action_complete":
            new_status = "완료"
            _db.update_status(deploy_id, new_status)
            blocks = _build_announce_blocks(
                record["type"], record["service"],
                record["deploy_date"], record["deploy_time"],
                record["content"], record["notified_by"],
                pr_link=record.get("pr_link") or "",
                jira=jira_url,
                status=new_status, deploy_id=deploy_id,
            )
            await client.chat_update(channel=channel, ts=message_ts, blocks=blocks, text=f"[{record['service']}] 배포 완료")
            await _sync_jira(client, channel, message_ts, jira_url, new_status)

            # QA 스레드 전송 (담당자 + QA 항목 있을 때)
            assignees = record.get("assignees") or ""
            qa_items  = record.get("qa_items") or ""
            if assignees and qa_items:
                await _post_qa_thread(client, channel, message_ts, deploy_id, assignees, qa_items)

        elif action_id == "deploy_action_qa_done":
            new_status = "QA완료"
            _db.update_status(deploy_id, new_status)

            # 원본 공지 메시지 ts (QA완료 버튼은 스레드에 있으므로 message_ts는 스레드 ts)
            orig_ts = record.get("channel_ts") or message_ts

            # 원본 공지 메시지에 ✅ 리액션
            if orig_ts:
                try:
                    await client.reactions_add(channel=channel, timestamp=orig_ts, name="white_check_mark")
                except Exception as re:
                    logger.warning("[deploy-log] reactions_add 실패: %s", re)

            # 원본 메시지 헤더 업데이트 (✅배포 공지)
            blocks = _build_announce_blocks(
                record["type"], record["service"],
                record["deploy_date"], record["deploy_time"],
                record["content"], record["notified_by"],
                pr_link=record.get("pr_link") or "",
                jira=jira_url,
                status=new_status, deploy_id=deploy_id,
            )
            # QA완료 버튼이 있는 스레드 메시지 업데이트 → 버튼 제거 + 완료 텍스트 표시
            qa_thread_ts = record.get("qa_thread_ts") or ""
            if qa_thread_ts:
                done_blocks = _build_qa_thread_blocks(
                    record.get("assignees") or "",
                    record.get("qa_items") or "",
                    deploy_id, status="QA완료",
                )
                await client.chat_update(channel=channel, ts=qa_thread_ts, blocks=done_blocks, text="QA 완료 ✅")

            # 원본 공지 메시지도 업데이트
            if orig_ts:
                await client.chat_update(channel=channel, ts=orig_ts, blocks=blocks, text=f"[{record['service']}] 배포 QA완료")

            # Jira 전환 — QA 스레드가 있으면 거기에, 없으면 원본에 노티
            notify_ts = qa_thread_ts or orig_ts
            await _sync_jira(client, channel, notify_ts, jira_url, new_status)

    except Exception as e:
        logger.error("[deploy-log] action 처리 실패 (%s): %s", action_id, e, exc_info=True)


async def _sync_jira(client, channel: str, thread_ts: str, jira_url: str, deploy_status: str) -> None:
    """Jira 이슈 상태를 전환하고 결과를 슬랙 스레드에 노티한다."""
    from . import jira as _jira

    if not jira_url:
        return  # Jira URL이 없으면 조용히 스킵

    if not _jira.is_configured():
        logger.info("[deploy-log/jira] 환경변수 미설정 — Jira 연동 스킵")
        return

    success, message = await _jira.transition_issue(jira_url, deploy_status)

    if success:
        logger.info("[deploy-log/jira] %s", message)
        text = f"✅ Jira 상태 업데이트: {message}"
    else:
        logger.warning("[deploy-log/jira] %s", message)
        text = f"⚠️ Jira 상태 업데이트 실패: {message}"

    # 스레드에 결과 노티 (실패해도 전체 플로우는 중단 안 함)
    if channel and thread_ts:
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception as e:
            logger.warning("[deploy-log/jira] 슬랙 노티 전송 실패: %s", e)

