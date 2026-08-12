# 크론 잡

`~/.hermes/cron/jobs.json` 은 헤르메스 내장 크론 저장소임 (`hermes-agent/cron/jobs.py`).
경로 고정이고 변경 설정 없음.

## 추적하지 않는 이유

정의와 런타임 상태가 한 파일에 섞여 있음.

| 성격 | 필드 |
| --- | --- |
| 정의 | `id`, `name`, `script`, `schedule`, `enabled`, `deliver`, `no_agent`, `created_at` |
| 런타임 상태 | `next_run_at`, `last_run_at`, `last_status`, `last_error`, `last_delivery_error`, `repeat.completed`, `state`, `fire_claim`, 최상위 `updated_at` |

잡이 실행될 때마다 상태 필드가 바뀌므로, 추적하면 서버 `git pull` 마다 로컬 변경과
충돌하거나 스케줄러 상태가 커밋본으로 덮임. 2026-08-07 05:51 `pull` + `reset --hard`
에서 실제로 덮인 사례 있음.

## 등록된 잡

서버 타임존은 UTC 이고 `config.yaml` 에 timezone 설정이 없음. cron 식은 UTC 로 해석됨.

| name | script | schedule (UTC) | KST | 전달 |
| --- | --- | --- | --- | --- |
| slack-improvement-report | `slack_improvement_report.py` | `0 6 * * 1-5` | 평일 15:00 | local |
| slack-security-report | `security_report.py` | `0 1 * * *` | 매일 10:00 | local |
| slack-security-watch | `security_watch.py` | `*/10 * * * *` | 10분마다 | local |
| slack-usage-report | `slack_usage_report.py` | `0 0 * * 1` | 월 09:00 | local |

전부 `no_agent: true` — 모델을 거치지 않고 스크립트만 실행함.

## 사용 현황 리포트 (slack-usage-report)

관리자 채널(`C0BPH28DLDN`)로 최근 7일 사용 현황을 보냄. 원장 하나만 읽음.

| 항목 | 값 |
| --- | --- |
| 원장 | `logs/usage/usage.db` (`hooks/usage_log.py`). 보관 90일 |
| 기록 시점 | 요청·게이트 판정 = prompt-gate `decide()` / 응답·토큰 = usage-log 플러그인 |
| 보내는 것 | 본문(차트 2개) + 스레드(차트 1개 + 표 2개) |
| 수동 확인 | `--days N` · `--channel` · `--db` · `--dry-run` |

메시지를 둘로 나누는 이유 — Slack 은 **메시지당 `data_visualization` 블록 2개**까지만
받음. 차트가 셋이라 나머지 하나를 스레드로 내림.

원장에는 prompt-gate 카테고리를 **가공 없이** 그대로 넣음. 묶는 기준은 리포트
스크립트의 `GROUPS` 하나뿐이라, 카테고리가 늘거나 정의가 바뀌면 그 표만 고치면 됨.
게이트 분류기의 프롬프트·출력 스키마는 건드리지 않음 — 모델이 읽고 쓰는 것이 그대로여야
인젝션 표면이 그대로임. 요청 유형(요청·재질문·반문)과 개선 제안 여부만 원장 전용 보조
분류기가 채우고, 그 값은 게이트 판정에 쓰이지 않음.

등록 (에이전트는 크론 등록이 차단돼 있으므로 관리자가 ssh 로 실행):

```bash
sudo -u hermes -H bash -lc "cd ~ && hermes cron create '0 0 * * 1' \
  --name slack-usage-report --script slack_usage_report.py \
  --no-agent --deliver local"
```

인자 형태를 틀리기 쉬운 곳 세 군데 —

- 스케줄은 **위치 인자**임. `--schedule` 플래그는 없음
- `--script` 는 `~/.hermes/scripts/` 기준 **파일명만**. 경로를 붙이면 못 찾음
- `-H` 로 `HOME` 을 hermes 로 맞춰야 함. 안 그러면 호출자의 홈을 봄

### 놓친 실행의 뒤늦은 수행

서버가 멈춰 예정 시각을 넘긴 잡은 다음 기동 때 **유예 2시간(`grace=7200s`) 안이면 즉시
실행**되고 다음 차례를 다시 잡음. 로그에 `missed its scheduled time ... Running now` 로
남음. 스케줄과 무관한 시각에 결과가 도착하면 이걸 먼저 의심함.

### 일일 리포트와 10분 감시의 차이

| | `security_report.py` | `security_watch.py` |
| --- | --- | --- |
| 주기 | 매일 10:00 KST | 10분 |
| 탐지 대상 | 동일 (`~/.hermes` 의 커밋되지 않은 변경) | 동일 |
| 전송 조건 | 항상. 변경 없으면 "없음" 으로 보냄 | 변경이 있고 직전 전송분과 목록이 다를 때만 |
| 상태 파일 | 없음 | `logs/security/.last_alert` (변경 목록 sha256) |

같은 변경이 커밋될 때까지 10분마다 재알림되는 걸 막으려고 지문을 비교함. 목록이
바뀌면(파일 추가·삭제·상태코드 변경) 새 시도로 보고 다시 보냄. 커밋 등으로 해소되면
지문이 비고, 같은 변경이 나중에 다시 생기면 또 보냄.

상태 파일을 `logs/` 에 두는 이유 — LK 동결 제외 대상이라 쓰기가 되고, `.gitignore`
대상이라 이 파일 자체가 다음 탐지에 잡히지 않음.

크론의 `script` 필드는 인자를 받지 않음(`_run_job_script` 가 경로 하나만 실행). 그래서
일일/주기 모드를 플래그가 아니라 스크립트 두 개로 나눔.

### 탐지 축 2 — 화이트리스트 밖 파일

`.gitignore` 가 `*` + 화이트리스트 방식이라 `git status` 만으로는 **화이트리스트에
없는 경로가 통째로 안 보임**. 루트에 `test.txt` 를 만들어도, `hooks/evil.sh` 를 넣어도
(`!hooks/*.py` 만 열려 있음), `skills/` 하위에 새 스킬을 만들어도 잡히지 않았음.

그래서 `--ignored=matching -uall` 로 무시된 경로 전체를 스캔하고 **기준선 대비 신규
경로**만 보고함. 전량 보고는 무의미함 — 정상 파일만 569개(번들 스킬·캐시·`.env` 등).

| 항목 | 값 |
| --- | --- |
| 기준선 | `logs/security/.known_ignored` |
| 스캔 제외 | `cron/`, `**/__pycache__` — 런타임이 경로를 계속 만들고 지움 |
| 기준선 갱신 | **일일 리포트만** 함. 10분 감시는 읽기만 함 |
| 첫 실행 | 현재 상태를 기준선으로 삼고 조용히 넘어감 |
| 표기 | 상태 컬럼에 `화이트리스트 밖 (신규)` |

갱신을 일일 리포트로 몰아둔 이유 — 10분 감시가 갱신해버리면 하루 한 번 눈으로 확인할
기회 없이 신규 파일이 정상 취급됨. 지금 구조에서는 신규 경로가 최소 한 번은 10분 감시
알림이나 다음 10:00 리포트에 반드시 나타남.

### 탐지 범위의 한계

무시된 경로는 **목록만** 비교함. 기준선에 이미 있는 파일의 내용이 바뀌는 건 못 잡음
(`.env` 값 변조 등). 통째로 무시되는 디렉터리(`logs/`, `node/`, `hermes-agent/`)는
git 이 항목 하나로 접어서 내보내므로 그 안의 신규 파일도 못 잡음.

이 탐지는 `git status` 기반이라 **쓰기에 성공한 변조**만 잡음. LK(systemd
`ReadOnlyPaths`) 가 하네스 경로를 읽기전용으로 올린 뒤로 대부분의 변조 시도는 EROFS
로 실패하고 git 에 아무 흔적도 남기지 않음. 시도 자체는 `logs/security/*.log` 의
BLOCKED 줄과 `logs/agent.log` 에만 남음. 시도를 알림 대상으로 삼으려면 그 로그를 읽는
탐지가 따로 필요함.

## 서버 반영 절차

추적 해제 커밋을 서버가 받으면 git 이 `cron/jobs.json` 을 **삭제**함. 그런데 이 파일은
서버에서 항상 로컬 수정 상태라 `pull` 이 거부되므로, 아래 순서로 함.

```bash
sudo -u hermes cp /home/hermes/.hermes/cron/jobs.json /tmp/jobs.json.bak
sudo -u hermes git -C /home/hermes/.hermes checkout -- cron/jobs.json
sudo -u hermes git -C /home/hermes/.hermes pull
sudo -u hermes cp /tmp/jobs.json.bak /home/hermes/.hermes/cron/jobs.json
```

이후 `.gitignore` 가 이 파일을 무시하므로 추적되지 않은 채로 남음.

### 2026-08-07 사고 — 이 절차를 안 밟아서 잡이 전부 날아감

추적 해제 커밋(`b447982`)이 서버에 반영될 때 위 백업 절차 없이 진행돼 `jobs.json` 이
삭제됨. slack-improvement-report·slack-security-report 두 잡이 소실됨.

세 잡은 `hermes cron create` 로 재등록함. 추적 해제 상태이므로 앞으로 `jobs.json` 은
git 이 건드리지 않고, 이 사고는 반복되지 않음.

## 에이전트의 잡 등록 차단

추적 중일 때는 `pull` · `reset --hard` 가 에이전트가 임의로 등록한 잡을 되돌렸음
(2026-08-07 `daily-greeting` 사례). 이건 설계된 방어가 아니라 부수 효과였고, `pull`
시점에만 걸려서 그 사이 잡은 실제로 실행 예약까지 됐음. 추적 해제로 이 부수 효과도
사라졌으므로 별도 차단을 넣음.

`cron/jobs.json` 은 스케줄러가 상시 기록해야 해서 LK 동결 대상에서 제외돼 있음
(`hermes-security-policy.md` 6-2-1 참조). 그래서 파일 권한이 아니라 도구·명령
단계에서 막음 — security_guard 훅 규칙 5(1차)와 `approvals.deny`(2차).

### 차단 지점을 등록 행위로 잡은 이유

2026-08-07 `daily-farewell` 사례에서 에이전트는 `~/.hermes/scripts/` 가 읽기전용이라
쓰기에 실패하자 스크립트를 `/home/hermes/work/` 에 두고 등록했고, 그것도 막히자
스크립트 없는 프롬프트 기반 에이전트 잡으로 등록했음. 즉 스크립트 위치나 잡 종류를
막는 건 우회 가능하고, 두 경로가 공유하는 병목은 **스케줄러 등록** 하나뿐임.

| 차단 대상 | 수단 |
| --- | --- |
| `hermes cron` 변경 서브커맨드 (create·add·update·enable·disable·delete 등) | 훅 규칙 5 + deny |
| 시스템 스케줄러 (`crontab`, `systemd-run`, `systemctl *.timer`, `at`) | 훅 규칙 5 + deny |
| `cron/jobs.json` 직접 편집 (`write_file`·`patch`·리다이렉션·`tee`·`sed -i`·인터프리터) | 훅 규칙 5 + deny |

조회는 막지 않음 — `hermes cron list`·`show`, `cat jobs.json` 은 통과.
관리자가 ssh 로 직접 실행하는 등록은 훅 밖이라 영향 없음.

검증: `tests/test_cron_block.py` (57건. 우회 경로 3종과 조회 오탐 포함).
