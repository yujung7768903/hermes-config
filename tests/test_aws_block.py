"""AWS CLI 차단 검증 — config.yaml deny(L1) + security_guard 훅 규칙 3(L2)

배경: 원래 L1 에 'aws *' 와 '* aws *' 두 패턴이 있었다. fnmatch 는 명령 경계를
못 보기 때문에 '* aws *' 가 `grep aws config.yaml`, `git commit -m "fix aws 문서"`,
히어독 문서 작성까지 차단했다 (agent.log 에 실제 사례).

'* aws *' 를 제거하고, 명령 위치 판정을 훅 규칙 3 의 정규식으로 옮겼다.
L1 에 남은 'aws *' 는 훅이 fail-open 될 때의 2차 방어선이다.

실행: python3 tests/test_aws_block.py
"""
import fnmatch
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")
GUARD = os.path.join(ROOT, "hooks", "security_guard.py")

DENY = yaml.safe_load(open(CONFIG))["approvals"]["deny"]


def deny_match(cmd):
    c = cmd.lower().strip()
    for p in DENY:
        if fnmatch.fnmatchcase(c, p.lower()):
            return p
    return None


def guard_blocks(cmd):
    """훅을 실제 subprocess 로 실행. block 이면 True"""
    payload = json.dumps({
        "tool_name": "terminal",
        "tool_input": {"command": cmd},
        "session_id": "20260806_120000_abcdef",
    })
    out = subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True
    ).stdout.strip()
    if not out:
        return False
    return json.loads(out).get("action") == "block"


# (설명, 명령, 차단돼야 하는가)
CASES = [
    # ── AWS CLI 실행은 막힌다 ──
    ("aws 단독",                "aws s3 ls",                              True),
    ("aws sts",                 "aws sts get-caller-identity",            True),
    ("sudo aws",                "sudo aws s3 ls",                         True),
    ("세미콜론 뒤 aws",          "cd /tmp; aws iam list-users",            True),
    ("&& 뒤 aws",               "cd /tmp && aws s3 ls",                   True),
    ("파이프 뒤 aws",            "echo x | aws s3 cp - s3://b/k",          True),
    ("서브셸 안 aws",            "echo $(aws sts get-caller-identity)",    True),
    ("VAR= 프리픽스",            "AWS_PROFILE=p aws s3 ls",                True),
    ("env 프리픽스",             "env AWS_PROFILE=p aws s3 ls",            True),
    ("개행 뒤 aws",              "cd /tmp\naws s3 ls",                     True),
    # ── 오탐 회귀 방지: aws 가 명령이 아닌 경우는 통과해야 한다 ──
    ("grep 검색어",              "grep aws config.yaml",                   False),
    ("커밋 메시지",              'git commit -m "fix aws 관련 문서"',       False),
    ("파일명에 aws",             "cat docs/aws-notes.md",                  False),
    ("디렉터리명에 aws",         "ls -la /home/hermes/aws",                False),
    ("문장 중간의 aws",          "cat <<EOF > doc.md\n비용은 aws 청구서 참조\nEOF", False),
]

ok = fail = 0
print("── AWS CLI 차단 (L1 deny + L2 훅) ──")
for desc, cmd, should_block in CASES:
    p = deny_match(cmd)
    hooked = guard_blocks(cmd)
    blocked = (p is not None) or hooked
    mark = "PASS" if blocked == should_block else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    shown = cmd.replace("\n", "\\n")
    where = ("L1" if p else "") + ("L2" if hooked else "") or "통과"
    print(f"  {mark}  {desc:20s}  {shown:44s}  {where}")

# 과차단 패턴이 되돌아오지 않았는지
print("\n── '* aws *' 재도입 방지 ──")
mark = "PASS" if "* aws *" not in DENY else "FAIL"
ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
print(f"  {mark}  deny 에 '* aws *' 없음")

print("\n[알려진 잔여 오탐] 히어독 본문에서 줄이 'aws ' 로 시작하면 차단된다.")
print("  개행 뒤를 안 보면 `cmd1\\naws s3 ls` 가 통과하므로 이쪽을 택했다.")

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
