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

전부 `no_agent: true` — 모델을 거치지 않고 스크립트만 실행함.

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

### 탐지 범위의 한계

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
삭제됨. slack-improvement-report·slack-security-report 두 잡이 소실됐고, 08:23 에
`daily-farewell` (`30 8 * * *`, DM 전송) 잡 하나만 남은 상태로 재생성돼 있었음.
등록자 불명 — 관리자가 만든 잡이 아님.

세 잡은 `hermes cron create` 로 재등록함. 추적 해제 상태이므로 앞으로 `jobs.json` 은
git 이 건드리지 않고, 이 사고는 반복되지 않음.

## 남은 위험

추적 중일 때는 `pull` · `reset --hard` 가 에이전트가 임의로 등록한 잡을 되돌렸음
(2026-08-07 `daily-greeting` 사례). 이건 설계된 방어가 아니라 부수 효과였고, `pull`
시점에만 걸려서 그 사이 잡은 실제로 실행 예약까지 됐음. 추적 해제로 이 부수 효과도
사라지므로, 에이전트의 크론 등록 차단이 별도로 필요함.

`cron/jobs.json` 은 스케줄러가 상시 기록해야 해서 LK 동결 대상에서 제외돼 있음
(`hermes-security-policy.md` 6-2-1 참조). 차단은 파일 권한이 아니라 도구·명령
단계에서 해야 함.
