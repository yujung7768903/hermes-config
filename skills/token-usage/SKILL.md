---
name: token-usage
description: "Check token consumption, estimated cost, and elapsed time for the current or recent Hermes sessions using the SessionDB."
version: 1.1.0
author: local
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Tokens, Cost, Usage, Analytics, Productivity]
---

# Token Usage

세션별 토큰 사용량, 예상 비용, 소요 시간을 조회한다. Hermes의 내부 SessionDB(SQLite)에서 실시간으로 읽어온다.

## 언제 사용하나

- 사용자가 "토큰 얼마나 썼어?", "비용이 얼마야?", "이번 대화에서 토큰 사용량 알려줘" 같이 물을 때
- 소요 시간, API 호출 횟수, 캐시 히트율, 모델별 비용을 확인할 때
- `hermes insights`보다 현재 진행 중인 세션의 실시간 수치가 필요할 때

## 빠른 참조

### 현재 세션 토큰 사용량 조회

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import sys, time
from datetime import datetime, timezone
sys.path.insert(0, '/home/ec2-user/.hermes/hermes-agent')
from hermes_state import SessionDB

db = SessionDB()
row = dict(db._conn.execute('''
    SELECT id, source, started_at, ended_at,
           input_tokens, output_tokens,
           cache_read_tokens, cache_write_tokens, reasoning_tokens,
           api_call_count, tool_call_count,
           estimated_cost_usd, cost_status, model, billing_provider
    FROM sessions
    ORDER BY started_at DESC
    LIMIT 1
''').fetchone())
db.close()

total = (row['input_tokens'] or 0) + (row['output_tokens'] or 0) + \
        (row['cache_read_tokens'] or 0) + (row['cache_write_tokens'] or 0)
cache_read = row['cache_read_tokens'] or 0
cache_write = row['cache_write_tokens'] or 0
cache_hit_pct = (cache_read / (cache_read + cache_write) * 100) if (cache_read + cache_write) > 0 else 0
cost = row['estimated_cost_usd']

elapsed_sec = (row['ended_at'] or time.time()) - row['started_at']
m, s = divmod(int(elapsed_sec), 60)
h, m = divmod(m, 60)
elapsed_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
status = "진행 중" if row['ended_at'] is None else "완료"
started = datetime.fromtimestamp(row['started_at'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

print(f"Session ID    : {row['id']}")
print(f"Model         : {row['model']} ({row['billing_provider']})")
print(f"Started       : {started}")
print(f"Status        : {status}  ({elapsed_str})")
print()
print(f"--- Token Breakdown ---")
print(f"Input         : {row['input_tokens'] or 0:>12,}")
print(f"Output        : {row['output_tokens'] or 0:>12,}")
print(f"Cache write   : {cache_write:>12,}")
print(f"Cache read    : {cache_read:>12,}")
print(f"Reasoning     : {row['reasoning_tokens'] or 0:>12,}")
print(f"Total         : {total:>12,}")
print(f"Cache hit rate: {cache_hit_pct:>11.1f}%")
print()
print(f"--- API / Tools ---")
print(f"API calls     : {row['api_call_count'] or 0:>12,}")
print(f"Tool calls    : {row['tool_call_count'] or 0:>12,}")
print()
print(f"--- Cost ({row['cost_status'] or 'unknown'}) ---")
if cost is not None:
    print(f"Estimated USD : ${cost:.6f}  (~₩{cost*1350:,.0f} KRW)")
else:
    print(f"Estimated USD : N/A")
PYEOF
```

### 최근 N개 세션 요약

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import sys, time
from datetime import datetime, timezone
sys.path.insert(0, '/home/ec2-user/.hermes/hermes-agent')
from hermes_state import SessionDB

N = 5
db = SessionDB()
rows = [dict(r) for r in db._conn.execute(f'''
    SELECT id, started_at, ended_at,
           input_tokens, output_tokens,
           cache_read_tokens, cache_write_tokens,
           api_call_count, tool_call_count,
           estimated_cost_usd, model
    FROM sessions
    WHERE input_tokens > 0 OR output_tokens > 0
    ORDER BY started_at DESC
    LIMIT {N}
''').fetchall()]
db.close()

print(f"{'Session ID':<26} {'Model':<30} {'Duration':>9} {'Tokens':>10} {'Cost USD':>9} {'API':>4}")
print("-" * 95)
total_cost = 0
for r in rows:
    total_t = (r['input_tokens'] or 0) + (r['output_tokens'] or 0) + \
              (r['cache_read_tokens'] or 0) + (r['cache_write_tokens'] or 0)
    cost = r['estimated_cost_usd'] or 0
    total_cost += cost
    elapsed = (r['ended_at'] or time.time()) - r['started_at']
    m, s = divmod(int(elapsed), 60)
    h, m = divmod(m, 60)
    dur = f"{h}h{m}m{s}s" if h else f"{m}m{s}s"
    model_short = (r['model'] or '')[-30:]
    print(f"{r['id']:<26} {model_short:<30} {dur:>9} {total_t:>10,} {cost:>9.4f} {r['api_call_count'] or 0:>4}")
print("-" * 95)
print(f"{'Total':>76} {total_cost:>9.4f}")
PYEOF
```

### 특정 세션 ID로 상세 조회

```bash
SESSION_ID="20260714_020513_7105b8"
~/.hermes/hermes-agent/venv/bin/python3 << PYEOF
import sys, time
from datetime import datetime, timezone
sys.path.insert(0, '/home/ec2-user/.hermes/hermes-agent')
from hermes_state import SessionDB
db = SessionDB()
row = db._conn.execute("SELECT * FROM sessions WHERE id = ?", ("$SESSION_ID",)).fetchone()
if row:
    for k in row.keys():
        print(f"{k:<30}: {row[k]}")
db.close()
PYEOF
```

### 오늘 전체 비용 합산

```bash
~/.hermes/hermes-agent/venv/bin/python3 << 'PYEOF'
import sys, time
sys.path.insert(0, '/home/ec2-user/.hermes/hermes-agent')
from hermes_state import SessionDB

today_start = time.mktime(time.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d'))
db = SessionDB()
rows = [dict(r) for r in db._conn.execute('''
    SELECT started_at, ended_at,
           api_call_count, tool_call_count,
           input_tokens, output_tokens,
           cache_read_tokens, cache_write_tokens,
           estimated_cost_usd
    FROM sessions WHERE started_at >= ?
''', (today_start,)).fetchall()]
db.close()

total_elapsed = sum((r['ended_at'] or time.time()) - r['started_at'] for r in rows)
total_cost = sum(r['estimated_cost_usd'] or 0 for r in rows)
total_tokens = sum(
    (r['input_tokens'] or 0) + (r['output_tokens'] or 0) +
    (r['cache_read_tokens'] or 0) + (r['cache_write_tokens'] or 0)
    for r in rows
)
m, s = divmod(int(total_elapsed), 60)
h, m = divmod(m, 60)
elapsed_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

print(f"Today's Usage Summary")
print(f"  Sessions      : {len(rows)}")
print(f"  Total time    : {elapsed_str}")
print(f"  API calls     : {sum(r['api_call_count'] or 0 for r in rows):,}")
print(f"  Tool calls    : {sum(r['tool_call_count'] or 0 for r in rows):,}")
print(f"  Total tokens  : {total_tokens:,}")
print(f"  Input         : {sum(r['input_tokens'] or 0 for r in rows):,}")
print(f"  Output        : {sum(r['output_tokens'] or 0 for r in rows):,}")
print(f"  Cache read    : {sum(r['cache_read_tokens'] or 0 for r in rows):,}")
print(f"  Cache write   : {sum(r['cache_write_tokens'] or 0 for r in rows):,}")
print(f"  Estimated cost: ${total_cost:.6f}  (~₩{total_cost*1350:,.0f} KRW)")
PYEOF
```

## 주의사항

- `estimated_cost_usd`는 Hermes 내부 pricing 테이블 기준 추정치 (bedrock은 실제 청구액과 다를 수 있음)
- `ended_at IS NULL`인 세션은 진행 중 — `time.time()`으로 현재까지 소요 시간 계산
- 캐시 히트율이 높을수록 비용 효율이 좋음 (cache_read 단가는 일반 input의 약 10%)
- `hermes insights` 명령으로 더 시각적인 통계를 볼 수 있음
