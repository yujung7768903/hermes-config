"""git push 차단 검증 — config.yaml deny 패턴(L1) + security_guard 훅 규칙 6(L2)

핵심은 오탐이다. 커밋 메시지·문서 grep 에 "git push" 라는 문자열이 들어가는 것은
막아선 안 된다 (규칙 3 의 aws 패턴이 같은 이유로 한 번 되돌려졌다).

실행: python3 tests/test_git_push_block.py
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
    payload = json.dumps({
        "tool_name": "terminal",
        "tool_input": {"command": cmd},
        "session_id": "20260810_120000_abcdef",
    })
    out = subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True
    ).stdout.strip()
    if not out:
        return False
    return json.loads(out).get("action") == "block"


# (설명, 명령, 차단돼야 하는가)
CASES = [
    ("맨몸 push",                  "git push",                                      True),
    ("origin main",               "git push origin main",                          True),
    ("force",                     "git push --force origin main",                  True),
    ("force-with-lease",          "git push --force-with-lease",                    True),
    ("-u 신규 브랜치",             "git push -u origin feature",                    True),
    ("-C 로 저장소 지정",          "git -C /home/hermes/.hermes push",              True),
    ("-c 설정 주입",               "git -c user.email=x@y.z push",                  True),
    ("--git-dir",                 "git --git-dir=/home/hermes/.hermes/.git push",   True),
    ("세미콜론 뒤",                "cd /tmp; git push",                             True),
    ("&& 뒤",                     "cd /tmp && git push origin main",               True),
    ("파이프 뒤",                  "echo y | git push",                             True),
    ("sudo 프리픽스",              "sudo git push",                                 True),
    ("환경변수 프리픽스",           "GIT_SSH_COMMAND=ssh git push",                  True),
    ("개행 뒤",                    "cd /tmp\ngit push",                             True),
    ("send-pack",                 "git send-pack origin main",                     True),
    ("서브셸",                     "(git push)",                                    True),

    # 읽기·로컬 작업은 통과
    ("pull",                      "git pull --ff-only origin main",                False),
    ("fetch",                     "git fetch origin main",                         False),
    ("status",                    "git status --short",                            False),
    ("log",                       "git log --oneline -5",                          False),
    ("diff",                      "git diff HEAD~1",                               False),
    ("clone",                     "git clone https://github.com/x/y.git",          False),
    ("remote -v",                 "git remote -v",                                 False),

    # 오탐 — "git push" 가 산문·데이터로 등장하는 경우
    ("커밋 메시지 안의 문구",       'git commit -m "docs: git push 절차 정리"',        False),
    ("문서 grep",                  'grep -rn "git push" docs/',                     False),
    ("echo 안내",                  'echo "git push 는 차단됩니다"',                  False),
    ("push 라는 단어만",           "git rev-parse HEAD",                            False),
]

ok = fail = 0

print("── L2 훅 규칙 6 ──")
for desc, cmd, want in CASES:
    got = guard_blocks(cmd)
    mark = "PASS" if got == want else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    shown = cmd.replace("\n", "\\n")
    print(f"  {mark}  {desc:22s} {shown[:46]:48s} {'차단' if got else '통과'}")

# L1 은 fail-open 대비 2차라 커버리지가 훅보다 낮다.
# 여기서 검증하는 것은 (a) 대표적인 push 형태가 잡히는가, (b) 읽기 명령을 막지 않는가.
L1_MUST_BLOCK = [
    "git push",
    "git push origin main",
    "cd /tmp && git push",
    "sudo git push",
    "cd /tmp\ngit push",
    "git send-pack origin main",
]
L1_MUST_PASS = [
    "git pull --ff-only origin main",
    "git fetch origin main",
    "git status --short",
    "git log --oneline -5",
    "git clone https://github.com/x/y.git",
    'grep -rn "git push" docs/',
]

print("\n── L1 config deny ──")
for cmd in L1_MUST_BLOCK:
    p = deny_match(cmd)
    mark = "PASS" if p else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    shown = cmd.replace("\n", "\\n")
    print(f"  {mark}  차단 기대  {shown:40s} {p or '통과함'}")

for cmd in L1_MUST_PASS:
    p = deny_match(cmd)
    mark = "PASS" if p is None else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    print(f"  {mark}  통과 기대  {cmd:40s} {'차단(' + str(p) + ')' if p else '통과'}")

print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(0 if fail == 0 else 1)
