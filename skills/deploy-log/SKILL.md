---
name: deploy-log
description: "배포 기록 조회 — /배포 커맨드로 저장된 배포 이력을 검색하고 답변에 활용한다."
version: 1.0.0
author: local
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [배포, Deploy, Slack, 이력, 공지]
---

# 배포 기록 (deploy-log)

슬랙 /배포 커맨드로 기록된 모든 배포 이력이 `~/.hermes/deploy-log/deploys.db` SQLite에 저장된다.
사용자가 배포 관련 질문을 하면 이 DB를 조회해서 답변한다.

## 언제 사용하나

- "이 기능 언제 배포됐어?", "HERB 마지막 배포가 언제야?" 같은 질문
- 특정 서비스의 배포 이력 조회
- 롤백 이력 확인
- 예정 배포 목록 확인

## 배포 기록 검색

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import os, sys
sys.path.insert(0, os.path.expanduser('~/.hermes/plugins/deploy-log'))
from db import search_deploys, recent_deploys

# 키워드 검색 (서비스명, 내용, 유형 모두 검색)
keyword = "HERB"  # ← 검색어로 교체
results = search_deploys(keyword)
for r in results:
    print(f"[{r['id']}] {r['deploy_date']} {r['deploy_time']} | {r['type']:4s} | {r['service']:8s} | {r['content'][:60]}")
PYEOF
```

## 최근 배포 N건 조회

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import os, sys
sys.path.insert(0, os.path.expanduser('~/.hermes/plugins/deploy-log'))
from db import recent_deploys

rows = recent_deploys(limit=10)
if not rows:
    print("배포 기록 없음")
else:
    print(f"{'ID':>4} {'날짜':>12} {'시간':>6} {'구분':>4} {'서비스':>8}  내용")
    print("-" * 80)
    for r in rows:
        print(f"{r['id']:>4} {r['deploy_date']:>12} {r['deploy_time']:>6} {r['type']:>4} {r['service']:>8}  {r['content'][:40]}")
PYEOF
```

## 서비스별 최근 배포 조회

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import os, sys, sqlite3
sys.path.insert(0, os.path.expanduser('~/.hermes/plugins/deploy-log'))
from db import DB_PATH

service = "AMS"  # ← 서비스명으로 교체
con = sqlite3.connect(str(DB_PATH))
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT * FROM deploys WHERE service = ? ORDER BY deploy_date DESC, deploy_time DESC LIMIT 10",
    (service,)
).fetchall()
con.close()
for r in rows:
    print(f"{r['deploy_date']} {r['deploy_time']} | {r['type']} | {r['content']}")
PYEOF
```

## 예정 배포 목록

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import os, sys, sqlite3
sys.path.insert(0, os.path.expanduser('~/.hermes/plugins/deploy-log'))
from db import DB_PATH

con = sqlite3.connect(str(DB_PATH))
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT * FROM deploys WHERE type = '예정' ORDER BY deploy_date ASC, deploy_time ASC"
).fetchall()
con.close()
if not rows:
    print("예정된 배포 없음")
for r in rows:
    print(f"* {r['deploy_date']}  * {r['deploy_time']}  {r['service']}  {r['content']}")
PYEOF
```

## DB 위치

`~/.hermes/deploy-log/deploys.db`

## 컬럼 설명

| 컬럼 | 설명 |
|------|------|
| id | 자동 증가 PK |
| type | 예정 / 완료 / 롤백 |
| service | HERB / AMS / HOMEPAGE / INFRA / LIBRARY / VMS |
| deploy_date | 배포 날짜 (YYYY-MM-DD) |
| deploy_time | 배포 시간 (HH:MM) |
| content | 배포 내용 |
| notified_by | 공지한 Slack user_id |
| channel_ts | 슬랙 메시지 ts |
| created_at | 레코드 생성 시각 (UTC ISO) |
