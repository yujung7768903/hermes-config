"""배치(크론 잡) 등록 차단 검증 — security_guard 훅(규칙 5) + approvals.deny

스크립트를 ~/.hermes/scripts 밖(~/work 등)에 두고 등록하는 우회도 같이 막히는지 본다.
차단 지점이 스크립트 위치가 아니라 등록 행위이므로, 경로를 바꿔도 결과는 같아야 한다.

실행: python3 tests/test_cron_block.py
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
JOBS = os.path.join(os.path.expanduser("~"), ".hermes", "cron", "jobs.json")

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
    print(f"  {mark}  {desc:48s}  {'차단' if got else '통과'}")


print("── 크론 등록 명령 차단 ──")
REGISTER_CMDS = [
    ("hermes cron create (스크립트 잡)",
     "hermes cron create --name daily-farewell --script security_report.py --schedule '30 8 * * *'"),
    ("hermes cron create (에이전트 잡, 스크립트 없음)",
     "hermes cron create --name daily-farewell --prompt '퇴근 인사 보내줘' --schedule '30 8 * * *'"),
    ("스크립트를 ~/work 에 두고 등록 (우회 경로)",
     "hermes cron create --name farewell --script /home/hermes/work/daily_farewell.py --schedule '30 8 * * *'"),
    ("hermes cron add", "hermes cron add --name x --schedule '* * * * *'"),
    ("hermes cron update", "hermes cron update 76aa450c9931 --schedule '0 9 * * *'"),
    ("hermes cron enable", "hermes cron enable 76aa450c9931"),
    ("hermes cron disable", "hermes cron disable 76aa450c9931"),
    ("hermes cron delete", "hermes cron delete 76aa450c9931"),
    ("hermes cron remove", "hermes cron remove 76aa450c9931"),
    ("시스템 crontab 등록", "crontab -l | { cat; echo '30 8 * * * /home/hermes/work/f.py'; } | crontab -"),
    ("systemd-run 타이머", "systemd-run --user --on-calendar='*-*-* 08:30:00' /home/hermes/work/f.py"),
    ("systemctl 타이머 기동", "systemctl --user start farewell.timer"),
    ("at 예약 (시각)", "at 17:30 -f /home/hermes/work/daily_farewell.py"),
    ("at 예약 (now)", "echo /home/hermes/work/f.py | at now + 1 hour"),
]
for desc, cmd in REGISTER_CMDS:
    check(desc, guard_blocks("terminal", {"command": cmd}), True)

print("\n── jobs.json 직접 편집 차단 ──")
JOBS_CMDS = [
    ("리다이렉션 덮어쓰기", f"echo '{{}}' > {JOBS}"),
    ("리다이렉션 추가", f"echo x >> {JOBS}"),
    ("tee", f"cat /tmp/new.json | tee {JOBS}"),
    ("sed -i", f"sed -i 's/false/true/' {JOBS}"),
    ("cp 로 덮어쓰기", f"cp /tmp/jobs.json {JOBS}"),
    ("python open(w)", f"python3 -c \"open('{JOBS}','w').write('[]')\""),
]
for desc, cmd in JOBS_CMDS:
    check(desc, guard_blocks("terminal", {"command": cmd}), True)

check("write_file 로 jobs.json 쓰기", guard_blocks("write_file", {"path": JOBS}), True)
check("write_file 상대경로 .hermes/cron/jobs.json",
      guard_blocks("write_file", {"path": ".hermes/cron/jobs.json"}), True)
check("patch Update File: jobs.json",
      guard_blocks("patch", {"patch": f"*** Update File: {JOBS}\n@@\n-a\n+b\n"}), True)

print("\n── 조회·무관 명령은 통과 ──")
READ_CMDS = [
    ("hermes cron list", "hermes cron list"),
    ("hermes cron show", "hermes cron show 76aa450c9931"),
    ("jobs.json 읽기", f"cat {JOBS}"),
    ("jobs.json grep", f"grep -n schedule {JOBS}"),
    ("크론 문서 읽기", "cat /home/hermes/.hermes/docs/cron-jobs.md"),
    ("로그에서 cron 검색", "grep -i cron /home/hermes/.hermes/logs/agent.log"),
    ("at 이 단어로 들어간 명령", "grep -n 'look at the config' /home/hermes/work/README.md"),
    ("systemctl list-timers (조회)", "systemctl --user list-timers"),
]
for desc, cmd in READ_CMDS:
    check(desc, guard_blocks("terminal", {"command": cmd}), False)

print("\n── L1 approvals.deny (훅 fail-open 대비) ──")
for desc, cmd in REGISTER_CMDS + JOBS_CMDS:
    check(f"deny: {desc}", deny_match(cmd) is not None, True)

print("\n── L1 오탐 체크 ──")
for desc, cmd in READ_CMDS:
    p = deny_match(cmd)
    got = p is not None
    mark = "PASS" if not got else "FAIL"
    if mark == "PASS":
        ok += 1
    else:
        fail += 1
    print(f"  {mark}  deny 미매칭: {desc:36s}  {'패턴=' + str(p) if got else '통과'}")

print(f"\n총 {ok + fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
