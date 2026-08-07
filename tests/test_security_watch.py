"""10분 주기 감시의 전송 판단 검증 — 없으면 안 보내고, 같으면 다시 안 보낸다

실행: python3 tests/test_security_watch.py
"""
import os
import sys

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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}개 통과")
