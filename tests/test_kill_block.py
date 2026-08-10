"""프로세스 종료 차단 검증 — config.yaml deny 패턴(L1)

배경: kill/pkill/killall 은 L1 에만 규칙이 있다 (security_guard 훅에는 없음).
개행 뒤 형태는 '* kill *' 로 안 잡힌다 — 개행 앞에 공백이 없기 때문이다.
그래서 "*\\nkill *" 류 패턴이 따로 필요한데, 이게 YAML 멀티라인 폴딩으로 적혀
있어서 pkill 은 뒤 * 가, killall 은 앞 * 가 빠진 채 방치돼 있었다.
아래 "개행 뒤" 3케이스가 그 회귀를 잡는다.

실행: python3 tests/test_kill_block.py
"""
import fnmatch
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")

DENY = yaml.safe_load(open(CONFIG))["approvals"]["deny"]


def deny_match(cmd):
    c = cmd.lower().strip()
    for p in DENY:
        if fnmatch.fnmatchcase(c, p.lower()):
            return p
    return None


# (설명, 명령, 차단돼야 하는가)
CASES = [
    ("kill 단독",                  "kill 1234",                    True),
    ("sudo kill",                  "sudo kill -9 1234",            True),
    ("세미콜론 뒤 kill",           "cd /tmp; kill 1234",           True),
    ("pkill 단독",                 "pkill -9 java",                True),
    ("xargs pkill",                "pgrep -af java | xargs pkill", True),
    ("killall 단독",               "killall java",                 True),
    ("세미콜론 뒤 killall",        "cd /tmp; killall java",        True),
    # ── 개행 뒤 (수정 전에는 pkill·killall 이 통과했다) ──
    ("개행 뒤 kill",               "cd /tmp\nkill 1234",           True),
    ("개행 뒤 pkill",              "cd /tmp\npkill -9 java",       True),
    ("개행 뒤 killall",            "cd /tmp\nkillall java",        True),
    # ── 오탐 체크 ──
    ("ls 통과",                    "ls -la /tmp",                  False),
    ("npm build 통과",             "npm run build",                False),
    ("killer 는 kill 아님",        'echo "killer feature"',        False),
]

ok = fail = 0
print("── kill 계열 L1 차단 ──")
for desc, cmd, should_block in CASES:
    p = deny_match(cmd)
    blocked = p is not None
    mark = "PASS" if blocked == should_block else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    shown = cmd.replace("\n", "\\n")
    detail = f"차단(패턴={p!r})" if blocked else "통과"
    print(f"  {mark}  {desc:26s}  {shown:34s}  {detail}")

# 9개 패턴이 전부 대칭인지 — 개행 패턴에 앞뒤 * 가 다 있는지 직접 검사
print("\n── 개행 패턴 대칭성 ──")
for name in ("kill", "pkill", "killall"):
    pats = [p for p in DENY if isinstance(p, str) and "\n" in p and name in p]
    # kill 은 pkill/killall 의 부분문자열이므로 정확히 걸러낸다
    pats = [p for p in pats if p.split("\n")[1].split()[0].rstrip("*") == name]
    sym = [p for p in pats if p.startswith("*") and p.endswith("*")]
    mark = "PASS" if sym else "FAIL"
    ok, fail = (ok + 1, fail) if mark == "PASS" else (ok, fail + 1)
    shown_pats = [p.replace(chr(10), chr(92) + "n") for p in pats]
    print(f"  {mark}  {name:8s}  개행패턴={shown_pats}")

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
