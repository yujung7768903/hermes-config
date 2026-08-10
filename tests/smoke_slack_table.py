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

RAW = """저는 한국일보 모의 블로그 서비스 분석 전담 에이전트입니다.

## 못 하는 일

| :x: | 이유 |
|---|---|
| 코드·설정 수정 | 읽기 전용 에이전트 |
| 배포·프로세스 재시작 | 운영 권한 없음 |
| 자격증명 조회·전달 | 보안 정책 |

프론트 주소는 [블로그](http://16.184.55.44:4200/) 이고 설정은 `config.yaml` 입니다.
어디서부터 시작할까요?"""

formatted = SlackAdapter.format_message(None, RAW)
print("=== format_message 결과 ===")
print(formatted)

blocks = st.build_blocks(formatted)
print("\n=== blocks ===")
print(json.dumps(blocks, ensure_ascii=False, indent=1))
print("\n블록 수:", len(blocks), "타입:", [b["type"] for b in blocks])
