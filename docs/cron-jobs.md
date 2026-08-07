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

| id | name | script | schedule (UTC) | 전달 |
| --- | --- | --- | --- | --- |
| `2a7912f3e65b` | slack-improvement-report | `~/.hermes/scripts/slack_improvement_report.py` | `0 6 * * 1-5` | local |
| `595d73863896` | slack-security-report | `security_report.py` | `0 0 * * *` | local |

둘 다 `no_agent: true` — 모델을 거치지 않고 스크립트만 실행함.

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

## 남은 위험

추적 중일 때는 `pull` · `reset --hard` 가 에이전트가 임의로 등록한 잡을 되돌렸음
(2026-08-07 `daily-greeting` 사례). 이건 설계된 방어가 아니라 부수 효과였고, `pull`
시점에만 걸려서 그 사이 잡은 실제로 실행 예약까지 됐음. 추적 해제로 이 부수 효과도
사라지므로, 에이전트의 크론 등록 차단이 별도로 필요함.

`cron/jobs.json` 은 스케줄러가 상시 기록해야 해서 LK 동결 대상에서 제외돼 있음
(`hermes-security-policy.md` 6-2-1 참조). 차단은 파일 권한이 아니라 도구·명령
단계에서 해야 함.
