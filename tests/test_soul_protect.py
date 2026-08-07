"""SOUL.md 자기수정 차단 검증 — security_guard 훅(규칙 4) + approvals.deny

훅은 ~/.hermes/SOUL.md 를 절대경로로 판정하므로, 테스트는 실행 사용자의 HOME 을
기준으로 경로를 만든다(서버에서는 /home/hermes/.hermes/SOUL.md).

실행: python3 tests/test_soul_protect.py
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
SOUL = os.path.join(os.path.expanduser("~"), ".hermes", "SOUL.md")

DENY = yaml.safe_load(open(CONFIG))["approvals"]["deny"]


def deny_match(cmd):
    c = cmd.lower().strip()
    for p in DENY:
        if fnmatch.fnmatchcase(c, p.lower()):
            return p
    return None


def guard_blocks(tool_name, tool_input):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "20260807_120000_abcdef",
    })
    out = subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True,
        env=dict(os.environ, HERMES_SESSION_PLATFORM=""),
    ).stdout.strip()
    return bool(out) and json.loads(out).get("action") == "block"


ok = fail = 0


def check(desc, got, expect):
    global ok, fail
    mark = "PASS" if got == expect else "FAIL"
    if mark == "PASS":
        ok += 1
    else:
        fail += 1
    print(f"  {mark}  {desc:44s}  {'차단' if got else '통과'}")


print("── 도구 호출 차단 (write_file · patch) ──")
check("write_file 로 SOUL.md 덮어쓰기", guard_blocks("write_file", {"path": SOUL}), True)
check("write_file 상대경로 .hermes/SOUL.md",
      guard_blocks("write_file", {"path": ".hermes/SOUL.md"}), True)
check("patch Update File: SOUL.md",
      guard_blocks("patch", {"patch": f"*** Update File: {SOUL}\n@@\n-a\n+b\n"}), True)
check("patch Delete File: SOUL.md",
      guard_blocks("patch", {"patch": f"*** Delete File: {SOUL}\n"}), True)
# 규칙 5(쓰기 전면 차단) 도입 후 SOUL.md 외의 파일도 차단된다.
# 규칙 4 단독 동작은 test_readonly_agent.py 가 아니라 여기서 확인할 수 없으므로
# 기대값만 갱신한다 — SOUL.md 보호는 위 4건으로 충분히 검증된다.
check("write_file 다른 파일도 차단 (규칙 5)",
      guard_blocks("write_file", {"path": "/tmp/other.md"}), True)

print("\n── terminal 쓰기 수단 차단 ──")
WRITE_CMDS = [
    ("리다이렉션 덮어쓰기", f"echo hacked > {SOUL}"),
    ("리다이렉션 추가", f"echo hacked >> {SOUL}"),
    ("tee", f"echo x | tee {SOUL}"),
    ("sed -i", f"sed -i 's/a/b/' {SOUL}"),
    ("cp 로 덮어쓰기", f"cp /tmp/evil.md {SOUL}"),
    ("mv 로 덮어쓰기", f"mv /tmp/evil.md {SOUL}"),
    ("truncate", f"truncate -s 0 {SOUL}"),
    ("chmod 권한 변경", f"chmod 666 {SOUL}"),
    ("chown 소유자 변경", f"chown ec2-user {SOUL}"),
    ("python open(w)", f"python3 -c \"open('{SOUL}','w').write('x')\""),
    ("git checkout 으로 교체", f"git checkout other -- {SOUL}"),
    ("git apply", f"git apply /tmp/soul.patch  # {SOUL}"),
    ("심볼릭 링크 교체", f"ln -sf /tmp/evil.md {SOUL}"),
]
for desc, cmd in WRITE_CMDS:
    check(desc, guard_blocks("terminal", {"command": cmd}), True)

print("\n── 읽기·정상 경로는 통과 ──")
READ_CMDS = [
    ("cat 으로 읽기", f"cat {SOUL}"),
    ("grep 으로 검색", f"grep -n 역할 {SOUL}"),
    ("wc 로 줄 수", f"wc -l {SOUL}"),
    ("git pull (SOUL.md 미언급)", "git -C ~/.hermes pull --ff-only"),
    ("git status", "git -C ~/.hermes status --short"),
]
for desc, cmd in READ_CMDS:
    check(desc, guard_blocks("terminal", {"command": cmd}), False)

print("\n── L1 approvals.deny (훅 fail-open 대비) ──")
for desc, cmd in WRITE_CMDS:
    p = deny_match(cmd)
    check(f"deny: {desc}", p is not None, True)

print("\n── L1 오탐 체크 ──")
for desc, cmd in READ_CMDS:
    p = deny_match(cmd)
    got = p is not None
    mark = "PASS" if not got else "FAIL"
    if mark == "PASS":
        ok += 1
    else:
        fail += 1
    print(f"  {mark}  deny 미매칭: {desc:32s}  {'패턴=' + str(p) if got else '통과'}")

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
