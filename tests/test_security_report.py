"""보안 탐지 리포트 검증 — porcelain 파싱 + 실제 git 저장소 대상 collect/build

실행: python3 tests/test_security_report.py
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import security_report as sr  # noqa: E402


def run(repo, *args):
    subprocess.run(args, cwd=repo, check=True, capture_output=True)


def make_repo(path):
    run(path, "git", "init", "-q", "-b", "main")
    run(path, "git", "config", "user.email", "t@t")
    run(path, "git", "config", "user.name", "t")
    for name in ("keep.py", "edit.py", "gone.py", "old.py"):
        with open(os.path.join(path, name), "w") as f:
            f.write("a\nb\nc\n")
    run(path, "git", "add", "-A")
    run(path, "git", "commit", "-qm", "init")


def test_parse_status_rename():
    """R 항목은 새경로 뒤에 옛경로가 한 필드 더 붙는다 — 순서가 뒤집히면 안 된다"""
    raw = "R  new.py\0old.py\0 M edit.py\0?? fresh.py\0"
    assert sr.parse_status(raw) == [
        ("R ", "old.py → new.py"),
        (" M", "edit.py"),
        ("??", "fresh.py"),
    ]


def test_parse_status_empty():
    assert sr.parse_status("") == []


def test_status_label():
    assert sr.status_label("??") == "미추적 (신규)"
    assert sr.status_label(" M") == "수정"
    assert sr.status_label("MM") == "수정"          # staged+unstaged 중복 제거
    assert sr.status_label("MD") == "수정 / 삭제"
    assert sr.status_label("A ") == "추가"


def test_parse_numstat():
    assert sr.parse_numstat("3\t1\ta.py\n-\t-\tb.png\n") == {
        "a.py": (3, 1),
        "b.png": (0, 0),
    }


def test_collect_and_build():
    with tempfile.TemporaryDirectory() as repo:
        make_repo(repo)

        with open(os.path.join(repo, "edit.py"), "a") as f:
            f.write("d\ne\n")
        os.remove(os.path.join(repo, "gone.py"))
        os.rename(os.path.join(repo, "old.py"), os.path.join(repo, "new.py"))
        with open(os.path.join(repo, "fresh.py"), "w") as f:
            f.write("x\n")
        run(repo, "git", "add", "new.py", "old.py")

        info = sr.collect(sr.Path(repo))
        by_path = dict((p, c) for c, p in info["changes"])

        assert info["branch"] == "main"
        assert "init" in info["head"]
        assert by_path["edit.py"].strip() == "M"
        assert by_path["gone.py"].strip() == "D"
        assert by_path["fresh.py"] == "??"
        assert "old.py → new.py" in by_path
        assert "keep.py" not in by_path          # 변경 없는 파일은 안 나온다

        assert sr.churn_text("edit.py", info["stat"]) == "+2 / -0"
        assert sr.churn_text("fresh.py", info["stat"]) == "-"  # 미추적은 diff 대상 아님

        blocks = sr.build_blocks(info, datetime.now(sr.KST))
        table = [b for b in blocks if b["type"] == "data_table"][0]
        assert len(table["rows"]) == len(info["changes"]) + 1   # 헤더 1줄


def test_build_blocks_clean():
    with tempfile.TemporaryDirectory() as repo:
        make_repo(repo)
        info = sr.collect(sr.Path(repo))
        assert info["changes"] == []
        blocks = sr.build_blocks(info, datetime.now(sr.KST))
        assert len(blocks) == 1
        assert "변경사항이 없습니다" in blocks[0]["text"]["text"]
        assert not any(b["type"] == "data_table" for b in blocks)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}개 통과")
