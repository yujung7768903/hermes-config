# 팀 에이전트 보안 설계 — 결정 사항 및 위협 사례

## 주요 결정 사항

### 자가개선 정책

- 메모리·스킬 자동 저장 비활성화 방향
- `memory.write_approval: true`, `skills.write_approval: true` 로 설정하면 모든 쓰기가 staging 됨
- `/memory pending`, `/skills pending` 으로 관리자가 검토 후 approve/reject
- 한 사용자의 선호가 전체 응답에 영향을 주는 것을 방지하기 위함

### 하네스 보호 원칙

- 하네스는 로컬 개발자만 수정 가능
- Hermes의 하네스 접근은 git pull(읽기/업데이트)만 허용
- 미래에 Hermes가 자동 개발을 수행하더라도 하네스 영역은 절대 수정 불가

### 메시징 채널 권한 원칙

- 의도: Slack/Teams에서는 읽기·분석·답변만 허용, 코드 작성·수정·실행 금지
- **[2026-08-09 정정] "플랫폼 툴셋은 어댑터만 등록 (file/terminal 툴 노출 금지)"
  는 사실이 아니었다.** `hermes-slack` 은 `_HERMES_CORE_TOOLS` 를 그대로 쓰고,
  거기에는 terminal · read_file · write_file · patch · search_files ·
  execute_code 가 전부 들어 있다. 좁혀진 적이 없다 —
  L0(툴셋 화이트리스트)는 원격 채널에 대해 아무것도 막지 않는다.

- 실제로 원격 채널을 막고 있는 것은 L1(approvals.deny) · L2(security_guard 훅) ·
  LK(systemd ReadOnlyPaths) 세 계층이다.
  상세는 `~/.hermes/docs/hermes-security-policy.md`.

- 아래 위협 사례 1 이 이 정정의 직접 증거다 — Slack 세션의 `write_file` 로 스킬이
  생성됐다. 툴셋이 우회당한 게 아니라 원래 열려 있었다.

- **미결**: 원격 채널 툴셋을 좁힐지, full access 를 유지하고 훅으로만 통제할지는
  아직 결정되지 않았다.

### 모델 판단 전 개입 지점

- `pre_gateway_dispatch`: Gateway에서 메시지 수신 직후, 모델 도달 전 차단/교체 가능 (미구현)
- `pre_llm_call`: 모델 판단 직전 컨텍스트 주입 가능 (미구현)
- 현재는 모델 판단 전 필터링 레이어 없음

---

## 확인된 위협 사례

### 사례 1: Slack 세션에서 스킬 생성 및 트리거 삽입 (2026-08-06)

**발생 경위:**

- 김태영(Slack 사용자)이 07/23부터 이어진 Slack 스레드 세션(20260723_043419_e6853d50)에서 보안 우회를 지속적으로 시도
- rm 차단 → 스크립트 우회 시도 → chown 시도 등 단계적 우회 시도
- 06:39에 Hermes가 대화 맥락에서 스스로 판단하여 delete-test-file 스킬을 write_file로 재생성
- 해당 스킬에는 "반가워. 헤르메스야!" 트리거가 포함되어 있어, 특정 문구 입력 시 파일 삭제/생성 동작 수행
- 06:42에는 create-new-profile 스킬도 추가 생성 ("아오" 트리거 포함)

**핵심 문제:**

- Slack 세션에서 write_file로 ~/.hermes/skills/ 하위 파일을 자유롭게 생성 가능
- 명시적 지시 없이도 모델이 대화 맥락에서 스스로 스킬을 생성함
- 생성된 스킬에 트리거를 심으면 특정 문구로 임의 동작을 실행시킬 수 있음

**차단된 것:**

- rm -rf 시도는 approvals.deny 규칙(rm*-rf*~/*) 에 의해 정상 차단됨
- 실제 파일 삭제는 발생하지 않음

**차단되지 않은 것:**

- Slack 세션에서 ~/.hermes/skills/ 하위 write_file 호출
- 모델이 스스로 스킬을 생성하는 판단 자체
