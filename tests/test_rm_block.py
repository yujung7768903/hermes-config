"""rm 전체 차단 검증 — config.yaml deny 패턴(L1) + security_guard 훅(L2)

실행: python3 tests/test_rm_block.py
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


def guard_blocks(cmd, platform=""):
    """훅을 실제 subprocess 로 실행. block 이면 True"""
    env = dict(os.environ, HERMES_SESSION_PLATFORM=platform)
    payload = json.dumps({
        "tool_name": "terminal",
        "tool_input": {"command": cmd},
        "session_id": "20260806_120000_abcdef",
    })
    out = subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True, env=env
    ).stdout.strip()
    if not out:
        return False
    return json.loads(out).get("action") == "block"


# (설명, 명령, 차단돼야 하는가)
CASES = [
    # 사용자가 실제로 통과시킨 케이스
    ("rm 절대경로 (기존 우회 케이스)", "rm /home/hermes/.hermes/.env",     True),
    ("rm -f 단일 파일",               "rm -f /tmp/a.log",                True),
    ("rm 상대경로",                   "rm build/out.txt",                True),
    ("rm 여러 파일",                  "rm a.txt b.txt",                  True),
    ("세미콜론 뒤 rm",                "cd /tmp; rm x",                   True),
    ("세미콜론 직후 rm (공백없음)",   "cd /tmp;rm x",                    True),
    ("&& 뒤 rm",                     "cd /tmp && rm x",                 True),
    ("파이프 xargs rm",              "find . -name '*.tmp' | xargs rm",  True),
    ("sudo rm",                      "sudo rm /var/log/x",              True),
    ("개행 뒤 rm",                   "cd /tmp\nrm x",                   True),
    ("rm -rf 기존 케이스",           "rm -rf /etc",                     True),
    # 삭제가 아닌 명령은 통과해야 함
    ("ls 통과",                      "ls -la /tmp",                     False),
    ("git status 통과",              "git status --short",              False),
]

ok = fail = 0
print("── L1 approvals.deny (fnmatch) ──")
for desc, cmd, expect in CASES:
    p = deny_match(cmd)
    caught = p is not None
    mark = "PASS" if caught == expect else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    print(f"  {mark}  {desc:30s}  {'패턴=' + str(p) if caught else '미매칭'}")

print("\n── L2 security_guard 훅 (platform 감지 실패 상태) ──")
for desc, cmd, expect in CASES:
    caught = guard_blocks(cmd, platform="")
    mark = "PASS" if caught == expect else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    print(f"  {mark}  {desc:30s}  {'차단' if caught else '통과'}")

print("\n── L2 삭제 예외 경로 없음 ──")
# 예전에 delete_test 트리거용으로 뚫려 있던 예외 경로가 되살아나지 않았는지 확인
allowed_cmd = "rm -rf /home/hermes/.hermes/skills/delete_test"
caught = guard_blocks(allowed_cmd, platform="")
mark = "PASS" if caught else "FAIL"
ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
print(f"  {mark}  delete_test 경로도 차단          {'차단' if caught else '통과'}")


# ─────────────────────────────────────────────────────────────────────────────
# 포섭 검증 — 제거한 경로별 rm 패턴 17개가 잡던 명령을 현재 deny 가 전부 잡는가
# (이 검증이 실패하면 rm 차단 범위가 좁아진 것이다)
# ─────────────────────────────────────────────────────────────────────────────
REMOVED_LEGACY = [
    "rm -rf /home/*", "rm -rf ~/hermes-workspace", "rm -rf ~/.hermes",
    "*rm*-rf*/etc*", "*rm*-rf*/usr*", "*rm*-rf*/bin*",
    "*rm*-rf*/lib*", "*rm*-rf*/boot*", "*rm*-rf*/var*",
    "rm -rf ~", "* rm -rf ~", "rm*-rf*~/*", "*rm*-rf*~/*",
    "*rm*-rf*/home/hermes*",
    "*rm*~/.ssh*", "*rm*-rf*~/.ssh*", "*rm*~/.hermes/.env*",
]
PREFIX = ["", "sudo ", "cd /tmp; ", "cd /tmp;", "x && ", "x&&", "x | ", "x|",
          "echo $(", "echo `", 'sh -c "', "nohup ", "cd /tmp\n", "env A=1 ",
          "xargs ", "time ", "( ", "{ "]
OPT = ["", "-f ", "-rf ", "-r -f ", "-fr "]
PATHS = ["/etc/x", "/usr/x", "/bin/x", "/lib/x", "/boot/x", "/var/x", "~", "~/",
         "~/docs", "~/.ssh", "~/.ssh/id_rsa", "~/.hermes", "~/.hermes/.env",
         "/home/hermes", "/home/hermes/.hermes/.env", "/home/x",
         "~/hermes-workspace", "build/out.txt", "a.txt"]


def legacy_match(cmd):
    c = cmd.lower().strip()
    return any(fnmatch.fnmatchcase(c, p.lower()) for p in REMOVED_LEGACY)


leaks = []
total = 0
for pre in PREFIX:
    for opt in OPT:
        for path in PATHS:
            cmd = f"{pre}rm {opt}{path}"
            total += 1
            if legacy_match(cmd) and deny_match(cmd) is None:
                leaks.append(cmd)

print(f"\n── 포섭 검증 (조합 {total}건) ──")
mark = "PASS" if not leaks else "FAIL"
ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
print(f"  {mark}  제거한 옛 패턴 17개의 커버리지 유지  누락 {len(leaks)}건")
for c in leaks[:10]:
    print(f"        누락: {c!r}")

# 오탐 — rm 이 아닌 정상 명령은 통과해야 함
print("\n── 오탐 체크 (L1) ──")
BENIGN = ["npm run build", "git status --short", "docker rmi foo", "pytest -q",
          "confirm the change", "warm up cache", "terraform apply", "ls -la"]
for cmd in BENIGN:
    p = deny_match(cmd)
    mark = "PASS" if p is None else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    print(f"  {mark}  {cmd:30s}  {'차단(패턴=' + str(p) + ')' if p else '통과'}")

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
