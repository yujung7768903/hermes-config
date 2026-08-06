#!/usr/bin/env python3
"""
자가 개선 히스토리 기록 스크립트
사용법: python3 log_self_improvement.py \
            --title "개선 제목" \
            --reason "개선 이유" \
            --basis "근거" \
            --files "file1.py,file2.yaml"

이 스크립트는 ~/.hermes/history/YYYY-MM.md 파일에 표 형식으로 기록을 추가합니다.
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

# KST = UTC+9
KST = timezone(timedelta(hours=9))

HISTORY_DIR = os.path.expanduser("~/.hermes/history")


def load_existing(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def append_record(title: str, reason: str, basis: str, files: list[str]) -> str:
    now = datetime.now(KST)
    month_str = now.strftime("%Y-%m")
    filename = f"{month_str}.md"
    path = os.path.join(HISTORY_DIR, filename)

    os.makedirs(HISTORY_DIR, exist_ok=True)
    existing = load_existing(path)

    # 파일이 새로 생성되는 경우 헤더 삽입
    if not existing:
        header = (
            f"# Hermes 자가 개선 히스토리 — {month_str}\n\n"
            "| 시간 (KST) | 개선 내용 | 이유 | 근거 | 수정 파일 |\n"
            "|-----------|----------|------|------|----------|\n"
        )
        existing = header

    timestamp = now.strftime("%Y-%m-%d %H:%M")
    files_str = "<br>".join(files) if files else "-"

    # 파이프 문자 이스케이프 (표 셀 안에서 |가 깨지지 않도록)
    def esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ")

    row = (
        f"| {timestamp} "
        f"| {esc(title)} "
        f"| {esc(reason)} "
        f"| {esc(basis)} "
        f"| {files_str} |\n"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(existing + row)

    print(f"[log_self_improvement] 기록 완료 → {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="자가 개선 히스토리 기록")
    parser.add_argument("--title", required=True, help="개선 내용 요약")
    parser.add_argument("--reason", required=True, help="개선 이유")
    parser.add_argument("--basis", required=True, help="근거 (커밋, 이슈, 관찰 등)")
    parser.add_argument("--files", default="", help="수정된 파일 목록 (쉼표 구분)")
    args = parser.parse_args()

    files = [f.strip() for f in args.files.split(",") if f.strip()]
    append_record(args.title, args.reason, args.basis, files)


if __name__ == "__main__":
    main()
