"""deploy_from_git.sh 검증 — author 필터와 재시작 판정

임시 origin/서버 저장소를 만들고 스크립트를 실제로 실행한다.
sudo·systemctl 은 환경변수로 대체한다 (HERMES_GIT_AS 를 비우면 현재 유저로 git 실행).

실행: python3 tests/test_deploy_from_git.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "deploy_from_git.sh")

MINE = ("yu-june0422", "yu-jung0422@hankookilbo.com")
MINE_ALT = ("yujung7768903", "yu-jung31476@naver.com")
HERMES = ("hermes", "hermes@ai-agent.local")

tmp = tempfile.mkdtemp(prefix="hermes-deploy-test-")
ORIGIN = os.path.join(tmp, "origin.git")
SERVER = os.path.join(tmp, "server")
WORK = os.path.join(tmp, "work")
LOG = os.path.join(tmp, "deploy.log")
RESTART_MARK = os.path.join(tmp, "restarted")


def git(repo, *args, author=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if author:
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = author
        env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = author
    r = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, env=env
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout.strip()


def commit(path, content, author, message):
    """work 클론에 커밋하고 origin 에 올린다"""
    full = os.path.join(WORK, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    git(WORK, "add", path)
    git(WORK, "-c", "commit.gpgsign=false", "commit", "-m", message, author=author)
    git(WORK, "push", "--quiet", "origin", "main")


def deploy():
    env = dict(
        os.environ,
        HERMES_REPO=SERVER,
        HERMES_DEPLOY_LOG=LOG,
        HERMES_DEPLOY_LOCK=os.path.join(tmp, "lock"),
        HERMES_GIT_AS="",
        HERMES_RESTART_CMD=f"touch {RESTART_MARK}",
    )
    return subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=env)


def last_log():
    if not os.path.exists(LOG):
        return ""
    with open(LOG) as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def log_tail(n=2):
    if not os.path.exists(LOG):
        return []
    with open(LOG) as f:
        return [ln for ln in f.read().splitlines() if ln.strip()][-n:]


def head(repo):
    return git(repo, "rev-parse", "HEAD")


# ── 준비 ────────────────────────────────────────────────────────────────────
subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", ORIGIN], check=True)
subprocess.run(["git", "clone", "--quiet", ORIGIN, WORK], check=True)
git(WORK, "config", "user.name", MINE[0])
git(WORK, "config", "user.email", MINE[1])
commit("README.md", "init\n", MINE, "init")
subprocess.run(["git", "clone", "--quiet", ORIGIN, SERVER], check=True)
git(SERVER, "config", "user.name", "server")
git(SERVER, "config", "user.email", "server@local")

ok = fail = 0


def check(desc, got, want):
    global ok, fail
    mark = "PASS" if got == want else "FAIL"
    if mark == "PASS":
        ok += 1
    else:
        fail += 1
    print(f"  {mark}  {desc}")
    if mark == "FAIL":
        print(f"        기대: {want!r}")
        print(f"        실제: {got!r}")


print("── 변경 없음 ──")
before = head(SERVER)
deploy()
check("HEAD 그대로", head(SERVER), before)
check("로그 없음", last_log(), "")

print("\n── 내 커밋 · 재시작 불필요 경로 ──")
commit("docs/note.md", "hello\n", MINE, "docs: 메모")
deploy()
check("반영됨", head(SERVER), git(WORK, "rev-parse", "HEAD"))
check("DEPLOYED 기록", "DEPLOYED" in log_tail()[0], True)
check("재시작 안 함", os.path.exists(RESTART_MARK), False)
check("NO-RESTART 기록", "NO-RESTART" in last_log(), True)

print("\n── 내 커밋(다른 이메일) · config.yaml 변경 ──")
commit("config.yaml", "mode: enforce\n", MINE_ALT, "feat: 설정 변경")
deploy()
check("반영됨", head(SERVER), git(WORK, "rev-parse", "HEAD"))
check("재시작 실행됨", os.path.exists(RESTART_MARK), True)
check("RESTART 기록", "RESTART" in last_log(), True)

print("\n── 허용 밖 author 커밋 ──")
frozen = head(SERVER)
commit("SOUL.md", "self-modified\n", HERMES, "feat: 에이전트가 올린 커밋")
deploy()
check("반영 안 됨", head(SERVER), frozen)
check("HOLD 기록", "HOLD" in last_log(), True)
check("author 가 로그에 남음", HERMES[1] in last_log(), True)

print("\n── 허용 밖 커밋 위에 내 커밋을 얹은 경우 ──")
commit("docs/note.md", "innocent\n", MINE, "docs: 정상 커밋")
deploy()
check("여전히 반영 안 됨", head(SERVER), frozen)
check("HOLD 유지", "HOLD" in last_log(), True)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(0 if fail == 0 else 1)
