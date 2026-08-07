"""읽기 전용 에이전트 검증 — disabled_toolsets(terminal) + security_guard 규칙 5

두 계층을 함께 본다.
  L1 config.yaml : terminal 툴셋 제거로 셸·프로세스 도구 자체가 사라짐
  L2 훅 규칙 5   : 남은 쓰기 수단 write_file·patch 차단
읽기 도구(read_file·search_files)는 통과해야 한다 — 막히면 분석 업무가 죽는다.

실행: python3 tests/test_readonly_agent.py
"""
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")
GUARD = os.path.join(ROOT, "hooks", "security_guard.py")

CFG = yaml.safe_load(open(CONFIG))
DISABLED = CFG["agent"]["disabled_toolsets"]


def guard_blocks(tool_name, tool_input, platform="slack"):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": f"agent:main:{platform}:dm:U123",
    })
    out = subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True,
        env=dict(os.environ, HERMES_SESSION_PLATFORM=platform),
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
    print(f"  {mark}  {desc:46s}  {'차단' if got else '통과'}")


print("── L1 disabled_toolsets ──")
# terminal 을 빼면 terminal·process 가, cronjob 을 빼면 cronjob 도구가 사라진다.
# code_execution 은 execute_code, skills 는 skill_manage 를 없애 우회 경로를 닫는다.
for ts in ["terminal", "cronjob", "code_execution", "skills", "delegation"]:
    got = ts in DISABLED
    mark = "PASS" if got else "FAIL"
    if got:
        ok += 1
    else:
        fail += 1
    print(f"  {mark}  disabled_toolsets 에 {ts:16s}  {'있음' if got else '없음'}")

# read_file·search_files 를 죽이는 설정이 들어오면 분석 자체가 불가능해진다.
got = "file" in DISABLED
mark = "PASS" if not got else "FAIL"
if not got:
    ok += 1
else:
    fail += 1
print(f"  {mark}  file 툴셋은 살아 있어야 함{'':18s}  {'죽음' if got else '살아있음'}")

print("\n── L2 쓰기 차단 (규칙 5) ──")
WRITE_CASES = [
    ("write_file 임의 경로", "write_file", {"path": "/home/hk/work/App.java", "content": "x"}),
    ("write_file 홈 하위", "write_file", {"path": "/home/hermes/note.md", "content": "x"}),
    ("write_file /tmp", "write_file", {"path": "/tmp/scratch.txt", "content": "x"}),
    ("patch Update File", "patch", {"patch": "*** Update File: /home/hk/work/App.java\n@@\n-a\n+b\n"}),
    ("patch Create File", "patch", {"patch": "*** Create File: /home/hermes/new.py\n+print(1)\n"}),
    ("patch Delete File", "patch", {"patch": "*** Delete File: /tmp/x\n"}),
]
for desc, tool, args in WRITE_CASES:
    check(desc, guard_blocks(tool, args), True)

print("\n── CLI 세션도 동일하게 차단 ──")
for desc, tool, args in WRITE_CASES[:2]:
    check(f"{desc} (cli)", guard_blocks(tool, args, platform="cli"), True)

print("\n── 읽기는 통과해야 함 ──")
READ_CASES = [
    ("read_file 서비스 코드", "read_file", {"path": "/home/hk/work/App.java"}),
    ("read_file 로그", "read_file", {"path": "/var/log/blog/app.log"}),
    ("search_files 코드 검색", "search_files", {"path": "/home/hk/work", "query": "NullPointer"}),
]
for desc, tool, args in READ_CASES:
    check(desc, guard_blocks(tool, args), False)

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
