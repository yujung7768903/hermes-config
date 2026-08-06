
## 응답 스타일
- 도구 실행 중간 결과(빈 결과, 막힌 시도 등)는 설명하지 말 것
- 도구는 조용히 실행하고, 최종 결과와 핵심 요약만 간결하게 남길 것
- "확인해봤더니 없었고, 다른 방법으로 찾아봤더니..." 같은 과정 서술 불필요

## 프로세스 검색 규칙
- Linux 커널은 comm 필드(프로세스 이름)를 15자로 잘라 저장 (TASK_COMM_LEN=16, null 포함)
- pgrep 기본 동작은 comm 필드 기준 매칭이므로 15자 초과 프로세스명은 검색 실패
- 예: gnome-keyring-daemon(20자) → comm에는 "gnome-keyring-d"로 잘려 저장
- 프로세스 검색 시 항상 pgrep -af (전체 cmdline 기준 매칭) 를 우선 사용할 것
- ps 조회도 ps aux | grep <name> 대신 ps -ef | grep <name> 또는 pgrep -af <name> 사용

## Slack 링크 포맷 규칙
- Slack에서 URL을 줄 때 **절대 특수문자(*, _, ~ 등)를 URL 안에 포함하지 말 것**
- 마크다운 강조 기호가 URL에 붙으면 Slack이 특수문자까지 URL로 인식해버림
- 올바른 예: https://api.slack.com/apps
- 잘못된 예: *https://api.slack.com/apps* (Slack이 * 포함해서 링크로 만들어버림)
