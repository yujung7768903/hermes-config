"""코어 format_message → 변환까지 실제로 돌려보는 눈확인용 스크립트.

hermes-agent 소스 경로가 있어야 돌아간다(로컬 개발용). CI 대상은 test_slack_table.py.
실행: python3 tests/smoke_slack_table.py [hermes-agent 경로]
"""
import importlib.util
import json
import os
import sys

AGENT = sys.argv[1] if len(sys.argv) > 1 else "/mnt/c/Users/D4006124/hermes-agent"
sys.path.insert(0, AGENT)
from plugins.platforms.slack.adapter import SlackAdapter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "st", os.path.join(ROOT, "plugins", "slack-table", "__init__.py"))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

# 2026-08-10 13:31 DM 에서 실제로 깨진 본문 (일부)
RAW = """도메인 엔티티 5개를 직접 읽었습니다. 정리해 드립니다.

---

테이블 구조

users
| 컬럼 | 타입 | 제약 |
|---|---|---|
| id | BIGINT | PK, AUTO_INCREMENT |
| email | VARCHAR | UNIQUE, NOT NULL |
| bio | TEXT | nullable |

:page_facing_up: User.java 6~25행

---

comments
| 컬럼 | 타입 | 제약 |
|---|---|---|
| parent_id | BIGINT | FK → comments.id, nullable (대댓글) |
| created_at | DATETIME | - |

parent_id가 있어서 대댓글 구조 지원합니다.
:page_facing_up: Comment.java 8~40행"""

formatted = SlackAdapter.format_message(None, RAW)
print("=== format_message 결과 ===")
print(formatted)

blocks = st.build_blocks(formatted)
print("\n=== blocks ===")
print(json.dumps(blocks, ensure_ascii=False, indent=1))
print("\n블록 수:", len(blocks), "타입:", [b["type"] for b in blocks])
