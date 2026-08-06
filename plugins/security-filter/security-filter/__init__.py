"""
security-filter plugin — 민감 정보 결과 마스킹 (transform_tool_result)

목적:
  pre_tool_call hook(security_guard.py)은 위험한 명령 실행 자체를 막지만,
  명령이 이미 실행된 후 결과물에 민감 정보가 섞여 나오는 경우는 커버하지 못한다.

  이 플러그인은 transform_tool_result 훅으로 도구 결과가 모델에게 전달되기
  직전에 아래 패턴을 마스킹한다:

  [AWS]
    - AWS 액세스 키 ID    (AKIA/ASIA/AROA/AIDA... 접두사)
    - AWS 시크릿 키       (변수명 컨텍스트 + 40자 base64)
    - AWS 세션 토큰       (FwoGZX 접두사 / AWS_SESSION_TOKEN= 컨텍스트)

  [플랫폼 토큰]
    - Slack 토큰          (xoxb- / xoxp- / xoxa- / xoxr- / xoxs- / xapp-)
    - Discord 봇 토큰     (base64.base64.base64 — 3파트 dot 구분)
    - Telegram 봇 토큰    (숫자:35자 alphanum)
    - GitHub PAT          (ghp_ / gho_ / ghu_ / ghs_ / ghr_ / github_pat_)
    - Stripe 키           (sk_live_ / sk_test_ / rk_live_ / pk_live_ 등)
    - OpenAI API 키       (sk-... 48자 이상 / sk-proj-...)
    - Generic 서비스 키   (sk- / pk- 접두사 + 24자 이상)

  [범용 형식]
    - JWT 토큰            (eyJ 로 시작하는 3파트 base64url)
    - Bearer 토큰         (Authorization: Bearer <token>)
    - Generic API 키      (api_key / secret_key / access_token = 형태)
    - Private Key 블록    (-----BEGIN ... PRIVATE KEY-----)

  [인프라]
    - EC2 인스턴스 ID     (i-xxxxxxxxxxxxxxxx)
    - AMI ID              (ami-xxxxxxxxxxxxxxxx)
    - EC2 IMDS 주소       (169.254.169.254)
    - 사설 IP 대역        (10.x / 172.16-31.x / 192.168.x)

마스킹이 발생하면 agent.log에 WARNING으로 기록한다.
플러그인이 크래시해도 훅 예외는 무시되므로 에이전트 동작에 영향 없음.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("plugins.security-filter")


# ─────────────────────────────────────────────────────────────────────────────
# 마스킹 패턴 정의
# (패턴, 설명, 치환 문자열)
# ─────────────────────────────────────────────────────────────────────────────
_RULES: list[tuple[re.Pattern, str, str]] = []

def _add(pattern: str, desc: str, replacement: str = "[REDACTED]", flags: int = re.IGNORECASE) -> None:
    _RULES.append((re.compile(pattern, flags), desc, replacement))


# ── AWS 자격증명 ──────────────────────────────────────────────────────────────

# AWS 액세스 키 ID (고정 접두사 + 대문자+숫자 16자)
_add(
    r"(?<![A-Z0-9])(AKIA|ASIA|AROA|AIDA|ANPA|ANVA|APKA)[A-Z0-9]{16}(?![A-Z0-9])",
    "AWS 액세스 키 ID",
    "[AWS_KEY_REDACTED]",
    re.ASCII,
)

# AWS 시크릿 키: 변수명 컨텍스트가 있을 때만 32자 이상 base64 마스킹 (오탐 방지)
_add(
    r"(?i)(AWS_SECRET_ACCESS_KEY|aws_secret|secret_access_key)\s*[=:]\s*[\"']?([A-Za-z0-9/+]{32,})[\"']?",
    "AWS 시크릿 액세스 키 (변수명 컨텍스트)",
    r"\1=[AWS_SECRET_REDACTED]",
)

# AWS 세션 토큰: FwoGZX 접두사 (실제 STS 토큰 패턴)
_add(
    r"FwoGZX[A-Za-z0-9/+]{50,}={0,2}",
    "AWS 세션 토큰 (FwoGZX 접두사)",
    "[AWS_SESSION_TOKEN_REDACTED]",
    re.ASCII,
)

# AWS 세션 토큰: 변수명 컨텍스트
_add(
    r"(?i)(AWS_SESSION_TOKEN|aws_security_token)\s*[=:]\s*[\"']?([A-Za-z0-9/+]{40,}={0,2})[\"']?",
    "AWS 세션 토큰 (변수명 컨텍스트)",
    r"\1=[AWS_SESSION_TOKEN_REDACTED]",
)


# ── 플랫폼 토큰 ───────────────────────────────────────────────────────────────

# Slack: xox[bpars]-숫자-숫자-alphanum  /  xapp-숫자-alphanum-숫자-alphanum
_add(
    r"xox[bpars]-[0-9]+-[0-9A-Za-z-]+",
    "Slack 토큰 (xoxb/xoxp/xoxa/xoxr/xoxs)",
    "[SLACK_TOKEN_REDACTED]",
    re.ASCII,
)
_add(
    r"xapp-[0-9]-[A-Z0-9]+-[0-9]+-[0-9a-f]+",
    "Slack 앱 레벨 토큰 (xapp-)",
    "[SLACK_TOKEN_REDACTED]",
    re.ASCII,
)

# Discord 봇 토큰: base64(snowflake).base64(timestamp).base64(hmac)
# 첫 파트는 18~28자 base64url, 두 번째 6~7자, 세 번째 27자 이상
_add(
    r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}",
    "Discord 봇 토큰",
    "[DISCORD_TOKEN_REDACTED]",
    re.ASCII,
)

# Telegram 봇 토큰: {8-10자리 숫자}:{35자 이상 alphanum+_-}
_add(
    r"\d{8,10}:[A-Za-z0-9_-]{35,}",
    "Telegram 봇 토큰",
    "[TELEGRAM_TOKEN_REDACTED]",
    re.ASCII,
)

# GitHub 토큰 (classic PAT)
_add(
    r"gh[pousr]_[A-Za-z0-9]{36}",
    "GitHub 토큰 (ghp/gho/ghu/ghs/ghr)",
    "[GITHUB_TOKEN_REDACTED]",
    re.ASCII,
)
# GitHub fine-grained PAT
_add(
    r"github_pat_[A-Za-z0-9_]{82}",
    "GitHub fine-grained PAT",
    "[GITHUB_TOKEN_REDACTED]",
    re.ASCII,
)

# Stripe 키: sk_live_ / sk_test_ / rk_live_ / pk_live_ / pk_test_
_add(
    r"(sk|rk|pk)_(live|test)_[A-Za-z0-9]{24,}",
    "Stripe API 키",
    "[STRIPE_KEY_REDACTED]",
    re.ASCII,
)

# OpenAI API 키: sk-로 시작 48자+ (classic) 또는 sk-proj- (신형, _ 포함)
_add(
    r"sk-proj-[A-Za-z0-9_-]{80,}",
    "OpenAI API 키 (sk-proj-)",
    "[OPENAI_KEY_REDACTED]",
    re.ASCII,
)
_add(
    r"sk-[A-Za-z0-9_-]{48,}",
    "OpenAI API 키 (sk-...48자+)",
    "[OPENAI_KEY_REDACTED]",
    re.ASCII,
)

# Generic service key: sk- / pk- + 24자 이상 (위 Stripe/OpenAI 이후 잔여 처리)
_add(
    r"(?<![A-Za-z0-9])(sk|pk)-[A-Za-z0-9]{24,}(?![A-Za-z0-9])",
    "Generic 서비스 키 (sk-/pk- 접두사)",
    "[SERVICE_KEY_REDACTED]",
    re.ASCII,
)


# ── 범용 형식 ─────────────────────────────────────────────────────────────────

# JWT: eyJ로 시작하는 3파트 base64url (header.payload.signature)
_add(
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    "JWT 토큰 (eyJ 접두사)",
    "[JWT_REDACTED]",
    re.ASCII,
)

# Bearer 토큰 (Authorization 헤더)
_add(
    r"(?i)(Authorization\s*:\s*Bearer\s+)([A-Za-z0-9\-_.~+/]+=*)",
    "Bearer 토큰",
    r"\1[BEARER_TOKEN_REDACTED]",
)

# Generic API 키 (변수명 = 값 형태)
_add(
    r"""(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)\s*[=:]\s*["']?([A-Za-z0-9\-_./+]{16,})["']?""",
    "Generic API 키/토큰 (변수명 컨텍스트)",
    r"\1=[REDACTED]",
)

# Private Key 블록
_add(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    "Private Key 블록",
    "[PRIVATE_KEY_REDACTED]",
    re.DOTALL | re.IGNORECASE,
)


# ── 인프라 식별자 ─────────────────────────────────────────────────────────────

# EC2 인스턴스 ID / AMI ID
_add(
    r"\bi-[0-9a-f]{8,17}\b",
    "EC2 인스턴스 ID",
    "[INSTANCE_ID_REDACTED]",
    re.ASCII,
)
_add(
    r"\bami-[0-9a-f]{8,17}\b",
    "AMI ID",
    "[AMI_ID_REDACTED]",
    re.ASCII,
)

# EC2 IMDS URL
_add(
    r"169\.254\.169\.254",
    "EC2 IMDS 주소 노출",
    "[IMDS_REDACTED]",
)

# 사설 IP 대역 (RFC 1918)
_add(
    r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    "사설 IP (10.x)",
    "[PRIVATE_IP_REDACTED]",
)
_add(
    r"\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b",
    "사설 IP (172.16-31.x)",
    "[PRIVATE_IP_REDACTED]",
)
_add(
    r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    "사설 IP (192.168.x)",
    "[PRIVATE_IP_REDACTED]",
)


# ─────────────────────────────────────────────────────────────────────────────
# transform_tool_result 콜백
# ─────────────────────────────────────────────────────────────────────────────
def _mask_sensitive(tool_name: str, result: str, **kwargs) -> str | None:
    """도구 결과에서 민감 정보를 마스킹한다. 변경 없으면 None 반환."""
    masked = result
    triggered: list[str] = []

    for pattern, desc, replacement in _RULES:
        new = pattern.sub(replacement, masked)
        if new != masked:
            triggered.append(desc)
            masked = new

    if triggered:
        logger.warning(
            "[security-filter] tool=%s 민감 정보 마스킹: %s",
            tool_name,
            ", ".join(triggered),
        )
        # 감사 로그
        try:
            import sys as _sys
            import os as _os
            _hooks_dir = _os.path.join(_os.path.expanduser("~"), ".hermes", "hooks")
            if _hooks_dir not in _sys.path:
                _sys.path.insert(0, _hooks_dir)
            from security_log import write as _log_write
            _log_write(
                "MASKED",
                tool=tool_name,
                platform=kwargs.get("platform", ""),
                session=str(kwargs.get("task_id", "")),
                rule=", ".join(triggered),
                detail=(result[:200] + "...(생략)" if len(result) > 200 else result),
            )
        except Exception:
            pass
        return masked

    return None  # 변경 없음 → Hermes가 원본 결과 그대로 사용


# ─────────────────────────────────────────────────────────────────────────────
# 플러그인 진입점
# ─────────────────────────────────────────────────────────────────────────────
def register(ctx) -> None:
    ctx.register_hook("transform_tool_result", _mask_sensitive)
    logger.info("[security-filter] 민감 정보 마스킹 훅 등록 완료 (%d 패턴)", len(_RULES))
