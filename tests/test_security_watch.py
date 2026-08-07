"""10분 주기 감시의 전송 판단 검증 — 없으면 안 보내고, 같으면 다시 안 보낸다

실행: python3 tests/test_security_watch.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import security_watch as sw  # noqa: E402


CHANGES = [(" M", "config.yaml"), ("??", "hooks/evil.py")]


def test_no_changes_never_sends():
    send, sig = sw.decide([], "")
    assert send is False
    assert sig == ""


def test_first_detection_sends():
    send, sig = sw.decide(CHANGES, "")
    assert send is True
    assert sig


def test_same_changes_do_not_resend():
    _, sig = sw.decide(CHANGES, "")
    send, again = sw.decide(CHANGES, sig)
    assert send is False
    assert again == sig          # 지문은 그대로 유지된다


def test_changed_set_resends():
    _, sig = sw.decide(CHANGES, "")
    more = CHANGES + [("??", "scripts/evil.py")]
    send, new_sig = sw.decide(more, sig)
    assert send is True
    assert new_sig != sig


def test_resolved_then_reappears_sends_again():
    """커밋으로 해소되면 상태가 비고, 같은 변경이 다시 생기면 새 시도로 본다"""
    _, sig = sw.decide(CHANGES, "")
    send, cleared = sw.decide([], sig)
    assert send is False and cleared == ""
    send, _ = sw.decide(CHANGES, cleared)
    assert send is True


def test_order_matters_not_but_content_does():
    """같은 파일이라도 상태 코드가 바뀌면 다른 지문 — 재알림 대상"""
    _, sig = sw.decide([(" M", "config.yaml")], "")
    send, _ = sw.decide([("MD", "config.yaml")], sig)
    assert send is True


def test_state_file_is_gitignored():
    """상태 파일이 추적 대상이면 자기 자신을 탐지해 10분마다 알림이 나간다"""
    rel = str(sw.STATE_FILE).split(".hermes/")[-1]
    assert rel == "logs/security/.last_alert"
    proc = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", rel])
    assert proc.returncode == 0, f".gitignore 가 {rel} 를 제외하지 않는다"


def test_end_to_end_only_new_findings_are_sent():
    """실제 git 저장소 + 상태 파일로 6틱을 돌려 전송 횟수를 센다"""
    with tempfile.TemporaryDirectory() as repo:
        def run(*a):
            subprocess.run(a, cwd=repo, check=True, capture_output=True)

        Path(repo, "a.py").write_text("x\n")
        Path(repo, ".gitignore").write_text("/logs/\n")   # 운영과 동일하게 상태파일 제외
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "init")

        sent = []
        orig_dir, orig_env, orig_state, orig_post = (
            sw.sr.HERMES_DIR, sw.sr.ENV_FILE, sw.STATE_FILE, sw.post_to_slack)
        sw.sr.HERMES_DIR = Path(repo)
        sw.sr.ENV_FILE = Path(repo) / ".env"
        sw.STATE_FILE = Path(repo) / "logs" / "security" / ".last_alert"
        sw.post_to_slack = lambda token, ch, blocks: sent.append(blocks) or True
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
        try:
            sw.main()                                        # 1) 깨끗함
            assert len(sent) == 0

            Path(repo, "evil.py").write_text("bad\n")
            sw.main()                                        # 2) 미추적 파일 등장
            assert len(sent) == 1

            sw.main()                                        # 3) 같은 상태 — 재알림 없음
            assert len(sent) == 1

            Path(repo, "a.py").write_text("x\ny\n")
            sw.main()                                        # 4) 변경 추가 — 새 시도
            assert len(sent) == 2

            os.remove(Path(repo, "evil.py"))
            Path(repo, "a.py").write_text("x\n")
            sw.main()                                        # 5) 해소 — 조용히 상태만 비움
            assert len(sent) == 2
            assert sw.STATE_FILE.read_text() == ""

            Path(repo, "evil.py").write_text("bad\n")
            sw.main()                                        # 6) 같은 변경 재발 — 다시 알림
            assert len(sent) == 3

            assert "10분 주기" in sent[0][0]["text"]["text"]
        finally:
            sw.sr.HERMES_DIR, sw.sr.ENV_FILE = orig_dir, orig_env
            sw.STATE_FILE, sw.post_to_slack = orig_state, orig_post


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}개 통과")
