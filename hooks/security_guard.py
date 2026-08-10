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
    """현재 어떤 규칙도 이 함수에 의존하지 않는다 — 판정을 신뢰할 수 없기 때문이다.

    gateway 는 platform 을 contextvars 로 바인딩하는데 훅은 별도 subprocess 라
    HERMES_SESSION_PLATFORM 을 상속받지 못하고, session_id 도 실제로는
    "20260805_060149_1a94bc" 형식이라 파싱도 실패한다. 관측된 로그의 platform=
    은 전부 빈 값이었다.

    채널별 정책을 다시 넣으려면 먼저 platform 전달 경로부터 고쳐야 한다.
    그때까지 이 함수는 쓰지 않는다. 감사 로그에는 platform 값을 그대로 남겨서
    (block() 참조) 판정이 복구됐는지 확인할 수 있게 해둔다.
    """
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
# [규칙 1] 파일 삭제 차단 (모든 플랫폼)
# terminal 삭제 명령과 patch Delete File 지시어를 전 플랫폼(CLI 포함) 차단한다.
#   플랫폼 한정으로 두면 platform 감지 실패(HERMES_SESSION_PLATFORM 미전달,
#   session_id 형식 불일치) 시 차단이 통째로 무력화되므로 판정에 의존하지 않는다.
#   patch Delete File 은 원래 is_remote() 안에 있었고 그래서 한 번도 발동한 적이
#   없었다. terminal rm 과 같은 이유로 밖으로 꺼냈다.
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

# patch: Delete File 지시어 차단
if tool_name == "patch":
    patch_content = str(tool_input.get("patch", ""))
    if "*** Delete File:" in patch_content:
        block(
            f"[보안 정책] 파일 삭제는 허용되지 않습니다.\n"
            f"(patch Delete File 지시어 차단)\n"
            f"삭제가 필요하면 사용자가 직접 실행해야 합니다."
        )

# write_file 로 빈 내용을 덮어쓰는 것은 삭제가 아니므로 허용한다.
# 스킬을 통한 삭제도 결국 terminal·patch 를 호출하므로 위 두 차단으로 커버된다.


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
    # AWS CLI 실행 자체. config.yaml 의 '* aws *' 를 대체한다 —
    # fnmatch 는 명령 경계를 못 봐서 `grep aws x`, `git commit -m "fix aws 문서"`,
    # 히어독 문서 작성까지 차단했다. 여기서는 명령이 시작될 수 있는 위치
    # (줄 시작 · ; · | · & · ( · 백틱 · 개행) 뒤의 aws 만 잡고, sudo·env·VAR=x
    # 프리픽스는 통과시키지 않는다.
    # 남는 오탐: 히어독 본문에서 줄이 "aws " 로 시작하는 경우. 개행 뒤를 안 보면
    # `cmd1\naws s3 ls` 가 그대로 통과하므로, 이쪽을 택했다.
    (r"(?:^|[;|&(`\n])\s*(?:sudo\s+|env\s+|\w+=\S+\s+)*aws\s",
                                          "AWS CLI 실행"),
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

# 대상 파일을 건드리는 쓰기 수단. 읽기 전용 명령(cat/grep/diff)은 통과한다.
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

def has_write_indicator(command):
    for pat in WRITE_INDICATORS:
        if re.search(pat, command, re.IGNORECASE | re.DOTALL):
            return True
    return False

if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    if re.search(r"\bSOUL\.md\b", command, re.IGNORECASE) and has_write_indicator(command):
        block(f"{PROTECTED_MSG}\n차단된 명령: {command[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# [규칙 5] 스케줄러 등록·변경 차단 (모든 플랫폼)
# SOUL.md "하지 않는 일 — 배치 추가". LK(systemd ReadOnlyPaths) 는 cron/jobs.json 을
# 동결할 수 없다 — 스케줄러가 next_run_at·last_run_at 을 상시 기록해야 하기 때문이다.
# 그래서 파일 권한이 아니라 도구·명령 단계에서 막는다.
#
# 스크립트를 어디에 두든(~/.hermes/scripts 가 읽기전용이면 ~/work 로 우회) 실행되려면
# 스케줄러에 등록돼야 하므로, 차단 지점은 스크립트 위치가 아니라 등록 행위다.
# no_agent 스크립트 잡과 프롬프트 기반 에이전트 잡 모두 같은 등록 경로를 쓴다.
#
# 읽기는 허용한다 — `hermes cron list`, `cat cron/jobs.json` 은 통과한다.
# 관리자가 ssh 로 직접 실행하는 등록은 훅 밖이라 영향받지 않는다.
# ─────────────────────────────────────────────────────────────────────────────
CRON_STORE = os.path.realpath(os.path.join(HOME, ".hermes", "cron", "jobs.json"))

SCHEDULER_MSG = (
    "[보안 정책] 배치(크론 잡) 등록·변경은 허용되지 않습니다.\n"
    "스크립트를 다른 경로에 두고 등록하는 것도 같은 차단 대상입니다.\n"
    "배치가 필요하면 관리자에게 요청하세요."
)

SCHEDULER_CMD_PATTERNS = [
    # hermes 내장 크론. 변경 계열 서브커맨드만 잡는다 (list·show 는 통과)
    (r"\bcron\b\s+(create|add|new|update|edit|set|enable|disable|delete|remove|rm)\b",
     "hermes cron 잡 등록·변경"),
    (r"\bcrontab\b",                       "시스템 crontab 등록·변경"),
    (r"\bsystemd-run\b",                   "systemd 트랜지언트 타이머·서비스 등록"),
    (r"\bsystemctl\b[^\n]*\.timer\b",      "systemd 타이머 조작"),
    (r"\bat\s+(now\b|\d{1,2}:\d{2}\b|\d{1,2}\s*(am|pm)\b)", "at 예약 실행"),
]
SCHEDULER_CMD_COMPILED = [(re.compile(p, re.IGNORECASE), d) for p, d in SCHEDULER_CMD_PATTERNS]

def is_cron_store(path_str):
    if not path_str:
        return False
    try:
        if os.path.isabs(path_str):
            candidate = os.path.realpath(path_str)
        else:
            candidate = os.path.realpath(os.path.join(HOME, path_str))
    except Exception:
        return False
    return candidate == CRON_STORE

if tool_name == "terminal":
    command = str(tool_input.get("command", ""))
    for pat, desc in SCHEDULER_CMD_COMPILED:
        if pat.search(command):
            block(f"{SCHEDULER_MSG}\n사유: {desc}\n차단된 명령: {command[:200]}")
    # 잡 저장소 직접 편집 (리다이렉션·tee·sed -i·인터프리터 경유)
    if re.search(r"\bjobs\.json\b", command, re.IGNORECASE) and has_write_indicator(command):
        block(f"{SCHEDULER_MSG}\n사유: cron/jobs.json 직접 편집\n차단된 명령: {command[:200]}")

if tool_name == "write_file":
    if is_cron_store(str(tool_input.get("path", ""))):
        block(f"{SCHEDULER_MSG}\n사유: cron/jobs.json 직접 편집")

if tool_name == "patch":
    for line in str(tool_input.get("patch", "")).splitlines():
        if line.startswith(("*** Update File:", "*** Create File:", "*** Delete File:")):
            if is_cron_store(line.split(":", 1)[-1].strip()):
                block(f"{SCHEDULER_MSG}\n사유: cron/jobs.json 직접 편집\n대상: {line.strip()}")

sys.exit(0)
