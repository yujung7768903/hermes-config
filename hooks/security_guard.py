#!/usr/bin/env python3
"""
Hermes Security Guard Hook - pre_tool_call

목적:
  1. Slack/Teams 세션에서 파일 삭제 차단
     - terminal 유무와 무관하게, 스킬 경유 포함 모든 도구에서 삭제 차단
  2. ~/private 디렉토리 접근 차단
  3. 민감 정보(인스턴스 ID, IP, AMI ID, IAM role, 토큰/키) 노출 차단

platform 감지 방법:
  - HERMES_SESSION_PLATFORM 환경변수 (gateway가 contextvars로 설정)
  - session_id 문자열 파싱 ("agent:main:slack:..." 형태)
"""

import json
import os
import re
import sys

# ─────────────────────────────────────────────────────────────────────────────
# STDIN 파싱
# ─────────────────────────────────────────────────────────────────────────────
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name  = payload.get("tool_name", "")
tool_input = payload.get("tool_input") or payload.get("args") or {}
session_id = str(payload.get("session_id", "")).lower()

# platform 감지: 환경변수 우선, 없으면 session_id에서 파싱
platform = os.getenv("HERMES_SESSION_PLATFORM", "").lower().strip()
if not platform:
    # session_id 예: "agent:main:slack:dm:XXX" or "agent:main:teams:channel:XXX"
    parts = session_id.split(":")
    for part in parts:
        if part in {"slack", "teams", "telegram", "discord", "whatsapp", "cli"}:
            platform = part
            break

REMOTE_PLATFORMS = {"slack", "teams"}

def is_remote():
    return platform in REMOTE_PLATFORMS

def block(message):
    print(json.dumps({"action": "block", "message": message}))
    # 감사 로그
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from security_log import write as _log_write
        # message 첫 줄에서 사유 추출, 두 번째 줄부터 detail
        lines = message.strip().splitlines()
        rule   = lines[0].replace("[보안 정책]", "").strip() if lines else ""
        detail = " | ".join(lines[1:]) if len(lines) > 1 else ""
        _log_write(
            "BLOCKED",
            tool=tool_name,
            platform=platform,
            session=session_id,
            rule=rule,
            detail=detail,
        )
    except Exception:
        pass
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# [규칙 1] 파일 삭제 차단
# terminal 삭제 명령은 전 플랫폼(CLI 포함) 차단.
#   플랫폼 한정으로 두면 platform 감지 실패(HERMES_SESSION_PLATFORM 미전달,
#   session_id 형식 불일치) 시 차단이 통째로 무력화되므로 판정에 의존하지 않는다.
# patch Delete File 지시어는 Slack/Teams 한정 유지.
# ─────────────────────────────────────────────────────────────────────────────

# terminal: rm, rmdir, shred, unlink, truncate, find -delete 등
if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    DELETE_PATTERNS = [
        r"\brm\b",
        r"\brmdir\b",
        r"\bshred\b",
        r"\bunlink\b",
        r"\btruncate\b",
        r"\bfind\b.*-delete\b",
        r"\bfind\b.*-exec\s+rm\b",
    ]
    for pat in DELETE_PATTERNS:
        if re.search(pat, command, re.IGNORECASE | re.DOTALL):
            block(
                f"[보안 정책] 파일 삭제 명령은 허용되지 않습니다.\n"
                f"차단된 명령: {command[:200]}\n"
                f"삭제가 필요하면 사용자가 직접 실행해야 합니다."
            )

if is_remote():

    # patch: Delete File 지시어 차단
    if tool_name == "patch":
        patch_content = str(tool_input.get("patch", ""))
        if "*** Delete File:" in patch_content:
            block(
                f"[보안 정책] {platform.upper()} 세션에서는 파일 삭제가 허용되지 않습니다.\n"
                f"(patch Delete File 지시어 차단)\n"
                f"파일 삭제가 필요하다면 CLI 세션에서 직접 실행해 주세요."
            )

    # write_file: 파일을 빈 내용으로 덮어쓰는 삭제 패턴은 허용
    # (write_file 자체는 삭제가 아니므로 허용)

    # 스킬을 통한 삭제도 위 terminal/patch 차단으로 커버됨
    # (스킬은 결국 terminal이나 patch 도구를 호출하므로)


# ─────────────────────────────────────────────────────────────────────────────
# [규칙 2] ~/private 접근 차단 (모든 플랫폼)
# ─────────────────────────────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
BLOCKED_DIRS = [os.path.realpath(os.path.join(HOME, "private"))]

def is_blocked_path(path_str):
    if not path_str:
        return False
    try:
        if os.path.isabs(path_str):
            candidate = os.path.realpath(path_str)
        else:
            candidate = os.path.realpath(os.path.join(HOME, path_str))
    except Exception:
        return False
    for blocked in BLOCKED_DIRS:
        if candidate == blocked or candidate.startswith(blocked + os.sep):
            return True
    return False

FILE_PATH_TOOLS = {"read_file": ["path"], "write_file": ["path"], "search_files": ["path"]}

for t, keys in FILE_PATH_TOOLS.items():
    if tool_name == t:
        for key in keys:
            val = str(tool_input.get(key, ""))
            if val and is_blocked_path(val):
                block(
                    f"[보안 정책] 접근 금지 디렉토리입니다.\n"
                    f"경로: {val}\n"
                    f"~/private 및 하위 디렉토리는 Hermes 접근이 차단됩니다."
                )

if tool_name == "patch":
    for line in str(tool_input.get("patch", "")).splitlines():
        if line.startswith(("*** Update File:", "*** Create File:", "*** Delete File:")):
            fpath = line.split(":", 1)[-1].strip()
            if is_blocked_path(fpath):
                block(
                    f"[보안 정책] 접근 금지 디렉토리의 파일입니다.\n"
                    f"경로: {fpath}\n"
                    f"~/private 하위 파일은 Hermes 접근이 차단됩니다."
                )

if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    PRIVATE_PATTERNS = [
        r"~/private\b",
        rf"{re.escape(HOME)}/private\b",
        r"\$HOME/private\b",
        r"\$\{HOME\}/private\b",
    ]
    for pat in PRIVATE_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            block(
                f"[보안 정책] ~/private 디렉토리 접근이 차단되었습니다.\n"
                f"차단된 명령: {command[:200]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# [규칙 3] 민감 정보 노출 차단 (모든 플랫폼)
# 인스턴스 ID, IP, AMI ID, hostname, IAM role, 토큰/키
# ─────────────────────────────────────────────────────────────────────────────
SENSITIVE_CMD_PATTERNS = [
    (r"169\.254\.169\.254",               "EC2 인스턴스 메타데이터(IMDS) 직접 접근"),
    (r"X-aws-ec2-metadata-token",         "EC2 IMDSv2 토큰 발급"),
    (r"\baws\s+ec2\s+describe-instances?\b", "EC2 인스턴스 정보 조회 (인스턴스ID/IP/AMI ID 포함)"),
    (r"\baws\s+ec2\s+describe-instance-attribute\b", "EC2 인스턴스 속성 조회"),
    (r"\baws\s+sts\s+get-caller-identity\b", "STS caller-identity (IAM role/계정ID) 조회"),
    (r"\baws\s+iam\b",                    "IAM 직접 조회"),
    (r"\becho\s+\$AWS_SECRET_ACCESS_KEY\b", "AWS 시크릿 키 직접 출력"),
    (r"\becho\s+\$AWS_SESSION_TOKEN\b",   "AWS 세션 토큰 직접 출력"),
    (r"\becho\s+\$AWS_ACCESS_KEY_ID\b",   "AWS 액세스 키 직접 출력"),
    (r"\bprintenv\s+AWS_SECRET_ACCESS_KEY\b", "AWS 시크릿 키 직접 출력"),
    (r"\bprintenv\s+AWS_SESSION_TOKEN\b", "AWS 세션 토큰 직접 출력"),
    (r"\bcat\s+/etc/machine-id\b",        "/etc/machine-id (인스턴스 고유 ID) 조회"),
    (r"\bcat\s+/etc/hostname\b",          "/etc/hostname 직접 읽기"),
    (r"curl\b.*\$AWS_",                   "AWS 자격증명을 curl로 외부 전송 시도"),
    (r"wget\b.*\$AWS_",                   "AWS 자격증명을 wget으로 외부 전송 시도"),
]
SENSITIVE_CMD_COMPILED = [(re.compile(p, re.IGNORECASE | re.DOTALL), d) for p, d in SENSITIVE_CMD_PATTERNS]

if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    for pat, desc in SENSITIVE_CMD_COMPILED:
        if pat.search(command):
            block(
                f"[보안 정책] 민감 정보 접근이 차단되었습니다.\n"
                f"사유: {desc}\n"
                f"차단된 명령: {command[:300]}\n"
                f"인스턴스 ID/IP/AMI ID/IAM role/토큰/키는 Hermes를 통해 조회하거나 외부로 전송할 수 없습니다."
            )

SENSITIVE_READ_PATTERNS = [
    (r"\.aws/credentials",  "AWS 자격증명 파일"),
    (r"\.aws/config",       "AWS 설정 파일"),
    (r"/etc/hostname$",     "/etc/hostname (인스턴스 hostname)"),
    (r"/etc/machine-id$",   "/etc/machine-id (인스턴스 고유 ID)"),
]
SENSITIVE_READ_COMPILED = [(re.compile(p, re.IGNORECASE), d) for p, d in SENSITIVE_READ_PATTERNS]

if tool_name == "read_file":
    path = str(tool_input.get("path", ""))
    for pat, desc in SENSITIVE_READ_COMPILED:
        if pat.search(path):
            block(
                f"[보안 정책] 민감 파일 읽기가 차단되었습니다.\n"
                f"사유: {desc}\n"
                f"차단된 경로: {path}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# [규칙 4] SOUL.md 자기수정 차단 (모든 플랫폼)
# 갱신 경로는 하나뿐이다 — 관리자가 로컬에서 수정해 git 에 올리고 pull 로 받는다.
# 읽기는 허용한다. git pull 은 SOUL.md 를 명시하지 않으므로 걸리지 않는다.
# 파일 권한으로는 막을 수 없다 — ~/.hermes 디렉터리 소유자가 hermes 이므로
# 파일을 지우고 새로 만드는 경로로 우회된다. 도구 호출 자체를 막아야 한다.
# ─────────────────────────────────────────────────────────────────────────────
PROTECTED_FILES = [os.path.realpath(os.path.join(HOME, ".hermes", "SOUL.md"))]

PROTECTED_MSG = (
    "[보안 정책] SOUL.md 는 Hermes 가 수정할 수 없습니다.\n"
    "갱신은 관리자가 로컬에서 수정해 git 에 올린 뒤 pull 로만 반영됩니다."
)

def is_protected_file(path_str):
    if not path_str:
        return False
    try:
        if os.path.isabs(path_str):
            candidate = os.path.realpath(path_str)
        else:
            candidate = os.path.realpath(os.path.join(HOME, path_str))
    except Exception:
        return False
    return candidate in PROTECTED_FILES

if tool_name == "write_file":
    target = str(tool_input.get("path", ""))
    if is_protected_file(target):
        block(f"{PROTECTED_MSG}\n대상: {target}")

if tool_name == "patch":
    for line in str(tool_input.get("patch", "")).splitlines():
        if line.startswith(("*** Update File:", "*** Create File:", "*** Delete File:")):
            if is_protected_file(line.split(":", 1)[-1].strip()):
                block(f"{PROTECTED_MSG}\n대상: {line.strip()}")

if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    if re.search(r"\bSOUL\.md\b", command, re.IGNORECASE):
        # SOUL.md 를 건드리는 쓰기 수단. 읽기 전용 명령(cat/grep/diff)은 통과한다.
        WRITE_INDICATORS = [
            r">",                 # 리다이렉션 (>, >>)
            r"\btee\b",
            r"\bsed\b.*-i",
            r"\bcp\b", r"\bmv\b", r"\bln\b", r"\binstall\b",
            r"\btruncate\b", r"\bdd\b",
            r"\bchmod\b", r"\bchown\b",
            r"\bopen\s*\(", r"\bwrite\b",      # python 한 줄 실행
            r"\bapply\b", r"\bcheckout\b", r"\brestore\b",  # git 경유 되돌리기
        ]
        for pat in WRITE_INDICATORS:
            if re.search(pat, command, re.IGNORECASE | re.DOTALL):
                block(f"{PROTECTED_MSG}\n차단된 명령: {command[:200]}")

sys.exit(0)
