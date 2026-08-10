"""deploy_gate.sh 검증 — author 필터와 재시작 판정

임시 저장소를 만들어 스크립트를 실제로 실행한다. 이 게이트가 무엇이 서버에 닿는지
정하므로, before 가 없는 경우(브랜치 생성·force push)까지 포함해 확인한다.

실행: python3 tests/test_deploy_gate.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "deploy_gate.sh")

MINE = ("yu-june0422", "yu-jung0422@hankookilbo.com")
MINE_ALT = ("yujung7768903", "68562176+yujung7768903@users.noreply.github.com")
HERMES = ("hermes", "hermes@ai-agent.local")

tmp = tempfile.mkdtemp(prefix="deploy-gate-test-")
REPO = os.path.join(tmp, "repo")
ZEROS = "0" * 40


def git(*args, author=None):
    env = dict(os.environ)
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author
        env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = author
    r = subprocess.run(
        ["git", "-C", REPO, *args], capture_output=True, text=True, env=env
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout.strip()


def commit(path, content, author, message):
    full = os.path.join(REPO, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    git("add", path)
    git("-c", "commit.gpgsign=false", "commit", "-m", message, author=author)
    return git("rev-parse", "HEAD")


def gate(before, after):
    r = subprocess.run(
        ["bash", SCRIPT, before, after], capture_output=True, text=True, cwd=REPO
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


subprocess.run(["git", "init", "--quiet", "-b", "main", REPO], check=True)
first = commit("README.md", "init\n", MINE, "init")

ok = fail = 0


def check(desc, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  PASS  {desc}")
    else:
        fail += 1
        print(f"  FAIL  {desc}\n        기대: {want!r}\n        실제: {got!r}")


print("── 내 커밋 · 재시작 불필요 경로 ──")
a = commit("docs/note.md", "hi\n", MINE, "docs: 메모")
code, out, err = gate(first, a)
check("통과", code, 0)
check("restart=0", out, "restart=0")

print("\n── 내 커밋(noreply 이메일) · config.yaml ──")
b = commit("config.yaml", "mode: enforce\n", MINE_ALT, "feat: 설정")
code, out, err = gate(a, b)
check("통과", code, 0)
check("restart=1", out, "restart=1")

print("\n── SOUL.md 변경도 재시작 대상 ──")
c = commit("SOUL.md", "soul\n", MINE, "docs: soul")
code, out, _ = gate(b, c)
check("restart=1", out, "restart=1")

print("\n── 허용 밖 author ──")
d = commit("docs/x.md", "x\n", HERMES, "feat: 에이전트 커밋")
code, out, err = gate(c, d)
check("차단", code, 1)
check("이메일이 사유에 남음", HERMES[1] in err, True)
check("restart 출력 없음", out, "")

print("\n── 허용 밖 커밋 위에 내 커밋을 얹은 경우 ──")
e = commit("docs/y.md", "y\n", MINE, "docs: 정상 커밋")
code, out, err = gate(c, e)
check("여전히 차단", code, 1)
check("허용 밖 커밋이 지목됨", d[:8] in err, True)

print("\n── before 가 40개의 0 (브랜치 생성 push) ──")
code, out, err = gate(ZEROS, e)
check("HEAD 한 커밋만 검사해 통과", code, 0)
check("restart=0", out, "restart=0")
check("범위 축소를 알림", "대체" in err or "하나만" in err, True)

print("\n── before 가 이 저장소에 없는 sha ──")
code, out, err = gate("dead" * 10, e)
check("HEAD~1 로 대체해 통과", code, 0)

print("\n── before 가 0 이고 HEAD 가 허용 밖 author ──")
f = commit("docs/z.md", "z\n", HERMES, "feat: 에이전트 커밋 2")
code, out, err = gate(ZEROS, f)
check("차단", code, 1)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(0 if fail == 0 else 1)
