"""approvals.deny 신규 패턴 매칭 검증 — fnmatch 직접 사용"""
import fnmatch, sys

NEW_PATTERNS = [
    # A
    "*chmod*777* /*",
    "*chmod*-r*777* /*",
    "*chown*-r* /*",
    # B
    "*rm*-rf*/etc*",
    "*rm*-rf*/usr*",
    "*rm*-rf*/bin*",
    "*rm*-rf*/lib*",
    "*rm*-rf*/boot*",
    "*rm*-rf*/var*",
    # C
    "rm -rf ~",
    "* rm -rf ~",
    "rm*-rf*~/*",
    "*rm*-rf*~/*",
    "*rm*-rf*/home/hermes*",
    # D
    "*rm*~/.ssh*",
    "*rm*-rf*~/.ssh*",
    "*rm*~/.hermes/.env*",
]

def match(cmd):
    c = cmd.lower().strip()
    for p in NEW_PATTERNS:
        if fnmatch.fnmatchcase(c, p.lower()):
            return p
    return None

tests = [
    # (설명, 명령, 잡혀야 하는가)

    # ── A ──
    ("chmod 777 /etc",                "chmod 777 /etc",                True),
    ("chmod 777 공백 여러개",          "chmod   777   /etc",            True),
    ("chmod -R 777 /",                "chmod -R 777 /",                True),
    ("chmod -r 777 소문자",            "chmod -r 777 /var",             True),
    ("chown -R root /",               "chown -R root /",               True),
    ("파이프 뒤 chmod",               "echo x | chmod 777 /usr",       True),
    ("chmod 755 ./mydir 오탐아님",    "chmod 755 ./mydir",             False),
    ("chmod 777 상대경로 오탐아님",   "chmod 777 myproject/",          False),

    # ── B ──
    ("rm -rf /etc",                   "rm -rf /etc",                   True),
    ("rm -rf /usr/local",             "rm -rf /usr/local",             True),
    ("rm -rf /bin",                   "rm -rf /bin",                   True),
    ("rm -rf /var/log",               "rm -rf /var/log",               True),
    ("공백 여러개 rm",                "rm  -rf  /etc/passwd",          True),
    ("파이프 뒤 rm /lib",             "echo foo | rm -rf /lib",        True),
    ("세미콜론 뒤 rm /boot",          "cd /tmp; rm -rf /boot",         True),

    # ── C ──
    ("rm -rf ~",                      "rm -rf ~",                      True),
    ("세미콜론 뒤 rm -rf ~",          "cd /tmp; rm -rf ~",             True),
    ("rm -rf ~/",                     "rm -rf ~/",                     True),
    ("rm -rf ~/documents",            "rm -rf ~/documents",            True),
    ("파이프 뒤 rm ~/",               "echo x | rm -rf ~/",            True),
    ("rm -rf /home/hermes",           "rm -rf /home/hermes",           True),
    ("rm -f ~/file.txt 오탐아님",     "rm -f ~/file.txt",              False),

    # ── D ──
    ("rm -rf ~/.ssh",                 "rm -rf ~/.ssh",                 True),
    ("rm ~/.ssh/id_rsa",              "rm ~/.ssh/id_rsa",              True),
    ("파이프 뒤 rm .ssh",             "ls | rm -rf ~/.ssh",            True),
    ("rm ~/.hermes/.env",             "rm ~/.hermes/.env",             True),
    ("rm -f ~/.hermes/.env",          "rm -f ~/.hermes/.env",          True),
]

ok = fail = 0
for desc, cmd, should_match in tests:
    p = match(cmd)
    caught = p is not None
    if caught == should_match:
        mark = "PASS"
        ok += 1
    else:
        mark = "FAIL"
        fail += 1
    detail = f"패턴={p}" if caught else "미매칭"
    print(f"  {mark}  {desc:35s}  {detail}")

print(f"\n총 {ok+fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
