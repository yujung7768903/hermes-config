import fnmatch

patterns = [
    "*chmod*777* /*",
    "*chmod*-r*777* /*",
    "*chown*-r* /*",
]

tests = [
    ("chmod 777 /etc",          True),
    ("chmod   777   /etc",      True),
    ("chmod -R 777 /",          True),
    ("echo x | chmod 777 /usr", True),
    ("chmod 777 myproject/",    False),
    ("chmod 755 ./mydir",       False),
    ("chown -R root /",         True),
]

print("패턴 후보 테스트:")
for pat in patterns:
    print(f"\n  패턴: {pat}")
    for cmd, expect in tests:
        hit = fnmatch.fnmatchcase(cmd.lower(), pat.lower())
        mark = "PASS" if hit == expect else "FAIL"
        print(f"    {mark}  {cmd!r}  -> {hit}")
