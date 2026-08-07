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


def test_ignored_paths_baseline():
    """`*` + 화이트리스트 gitignore 에서 숨은 파일을 기준선 대비 신규로 잡는다"""
    with tempfile.TemporaryDirectory() as repo:
        # 운영과 같은 형태 — 전부 무시하고 몇 개만 되살린다
        with open(os.path.join(repo, ".gitignore"), "w") as f:
            f.write("*\n!*/\n/logs/\n!.gitignore\n!scripts/*.py\n")
        os.makedirs(os.path.join(repo, "scripts"))
        os.makedirs(os.path.join(repo, "logs", "security"))
        with open(os.path.join(repo, "scripts", "ok.py"), "w") as f:
            f.write("a\n")
        with open(os.path.join(repo, "legit.dat"), "w") as f:
            f.write("a\n")            # 기준선에 흡수될 기존 파일
        run(repo, "git", "init", "-q", "-b", "main")
        run(repo, "git", "config", "user.email", "t@t")
        run(repo, "git", "config", "user.name", "t")
        run(repo, "git", "add", "-A")
        run(repo, "git", "commit", "-qm", "init")

        orig_dir, orig_base = sr.HERMES_DIR, sr.BASELINE_FILE
        sr.HERMES_DIR = sr.Path(repo)
        sr.BASELINE_FILE = sr.Path(repo) / "logs" / "security" / ".known_ignored"
        try:
            assert "legit.dat" in sr.scan_ignored(sr.Path(repo))

            assert sr.new_ignored(sr.Path(repo)) == []      # 첫 실행은 기준선만 만든다
            assert "legit.dat" in sr.read_baseline()

            with open(os.path.join(repo, "test.txt"), "w") as f:
                f.write("payload\n")
            with open(os.path.join(repo, "scripts", "evil.sh"), "w") as f:
                f.write("payload\n")   # .py 만 화이트리스트라 이것도 숨는다

            assert sr.new_ignored(sr.Path(repo)) == ["scripts/evil.sh", "test.txt"]

            changes = sr.collect(sr.Path(repo))["changes"]
            assert ("!!", "test.txt") in changes
            assert sr.status_label("!!") == "화이트리스트 밖 (신규)"

            sr.write_baseline(sr.scan_ignored(sr.Path(repo)))   # 일일 리포트가 하는 일
            assert sr.new_ignored(sr.Path(repo)) == []          # 흡수 후엔 조용해진다
        finally:
            sr.HERMES_DIR, sr.BASELINE_FILE = orig_dir, orig_base


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}개 통과")
