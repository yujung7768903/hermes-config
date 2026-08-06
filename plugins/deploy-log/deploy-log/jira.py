"""
deploy-log Jira 연동 모듈

환경변수:
  JIRA_URL          Jira 베이스 URL  (예: https://yourcompany.atlassian.net)
  JIRA_USER         Jira 계정 이메일 (예: dev@yourcompany.com)
  JIRA_API_TOKEN    Jira API 토큰    (Atlassian 계정 → 보안 → API 토큰에서 발급)

트랜지션 이름 매핑 (Jira 워크플로우마다 다름 — 필요시 .env에 추가):
  JIRA_TRANSITION_COMPLETE    배포 완료 시    (기본: "완료")
  JIRA_TRANSITION_CANCEL      배포 취소 시    (기본: "취소")
  JIRA_TRANSITION_ROLLBACK    롤백 시         (기본: "진행 중")
  JIRA_TRANSITION_QA_DONE     QA 완료 시      (기본: "완료")

Jira REST API v3 사용 (Cloud / Server 8.4+ 지원)
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── 설정 헬퍼 ──────────────────────────────────────────────────────────────

def _cfg() -> dict:
    """환경변수에서 Jira 접속 정보 읽기."""
    return {
        "url":   (os.environ.get("JIRA_URL") or "").rstrip("/"),
        "user":  os.environ.get("JIRA_USER") or "",
        "token": os.environ.get("JIRA_API_TOKEN") or "",
    }


def is_configured() -> bool:
    """Jira 연동에 필요한 환경변수가 모두 설정되어 있는지 확인."""
    c = _cfg()
    return bool(c["url"] and c["user"] and c["token"])


# 배포 상태 → Jira 트랜지션 이름 매핑
TRANSITION_MAP: dict[str, str] = {
    "완료":   "JIRA_TRANSITION_COMPLETE",
    "취소":   "JIRA_TRANSITION_CANCEL",
    "롤백":   "JIRA_TRANSITION_ROLLBACK",
    "QA완료": "JIRA_TRANSITION_QA_DONE",
}

DEFAULT_TRANSITIONS: dict[str, str] = {
    "완료":   "완료",
    "취소":   "취소",
    "롤백":   "진행 중",
    "QA완료": "완료",
}


def _get_transition_name(deploy_status: str) -> str:
    env_key = TRANSITION_MAP.get(deploy_status)
    if env_key:
        override = os.environ.get(env_key)
        if override:
            return override
    return DEFAULT_TRANSITIONS.get(deploy_status, "완료")


# ── 이슈 키 추출 ───────────────────────────────────────────────────────────

def extract_issue_key(jira_url: str) -> Optional[str]:
    """
    Jira 이슈 URL에서 이슈 키를 추출한다.

    지원 형식:
      https://company.atlassian.net/browse/PROJ-1234
      https://jira.company.com/browse/PROJ-1234
      https://company.atlassian.net/jira/software/projects/PROJ/issues/PROJ-1234
      PROJ-1234  (키 직접 입력도 허용)
    """
    if not jira_url:
        return None
    # 이미 이슈 키 형식인 경우 (예: PROJ-1234)
    direct = re.match(r'^([A-Z][A-Z0-9_]+-\d+)$', jira_url.strip())
    if direct:
        return direct.group(1)
    # URL에서 추출
    key_pattern = re.search(r'/([A-Z][A-Z0-9_]+-\d+)(?:[/?#]|$)', jira_url)
    if key_pattern:
        return key_pattern.group(1)
    logger.warning("[deploy-log/jira] 이슈 키를 파싱할 수 없음: %s", jira_url)
    return None


# ── REST API 호출 ──────────────────────────────────────────────────────────

def _auth_headers(cfg: dict) -> dict:
    token = base64.b64encode(f"{cfg['user']}:{cfg['token']}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _get_transitions(cfg: dict, issue_key: str) -> list[dict]:
    """이슈에서 사용 가능한 트랜지션 목록 조회."""
    import aiohttp
    url = f"{cfg['url']}/rest/api/3/issue/{issue_key}/transitions"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_auth_headers(cfg)) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GET transitions 실패 ({resp.status}): {text[:200]}")
            data = await resp.json()
            return data.get("transitions", [])


async def _do_transition(cfg: dict, issue_key: str, transition_id: str) -> None:
    """트랜지션 실행."""
    import aiohttp
    url = f"{cfg['url']}/rest/api/3/issue/{issue_key}/transitions"
    payload = {"transition": {"id": transition_id}}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=_auth_headers(cfg), json=payload) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise RuntimeError(f"POST transition 실패 ({resp.status}): {text[:200]}")


# ── 퍼블릭 API ─────────────────────────────────────────────────────────────

async def transition_issue(jira_url: str, deploy_status: str) -> tuple[bool, str]:
    """
    Jira 이슈를 배포 상태에 맞는 트랜지션으로 전환한다.

    Returns:
        (success: bool, message: str)
          success=True  → 전환 성공, message는 전환된 트랜지션 이름
          success=False → 실패 또는 스킵, message는 사유
    """
    if not is_configured():
        return False, "JIRA_URL / JIRA_USER / JIRA_API_TOKEN 미설정 — 연동 스킵"

    issue_key = extract_issue_key(jira_url)
    if not issue_key:
        return False, f"이슈 키 파싱 실패: {jira_url}"

    target_name = _get_transition_name(deploy_status)
    cfg = _cfg()

    try:
        transitions = await _get_transitions(cfg, issue_key)
    except Exception as e:
        return False, f"트랜지션 목록 조회 실패: {e}"

    # 이름 대소문자 무시하고 매칭
    matched = next(
        (t for t in transitions if t.get("name", "").strip().lower() == target_name.strip().lower()),
        None,
    )
    if not matched:
        available = [t.get("name") for t in transitions]
        return False, (
            f"'{target_name}' 트랜지션을 찾을 수 없음 "
            f"(사용 가능: {available}). "
            f"env JIRA_TRANSITION_{deploy_status.upper()} 에서 이름을 조정하세요."
        )

    try:
        await _do_transition(cfg, issue_key, matched["id"])
        return True, f"{issue_key} → '{matched['name']}' 전환 완료"
    except Exception as e:
        return False, f"트랜지션 실행 실패: {e}"


async def get_issue_status(jira_url: str) -> Optional[str]:
    """이슈의 현재 상태명 반환 (실패 시 None)."""
    if not is_configured():
        return None
    issue_key = extract_issue_key(jira_url)
    if not issue_key:
        return None
    cfg = _cfg()
    try:
        import aiohttp
        url = f"{cfg['url']}/rest/api/3/issue/{issue_key}?fields=status"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_auth_headers(cfg)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (
                    data.get("fields", {})
                    .get("status", {})
                    .get("name")
                )
    except Exception as e:
        logger.warning("[deploy-log/jira] get_issue_status 실패: %s", e)
        return None
