# Hermes 팀 에이전트 보안 정책

작성일: 2026-07-29
상태: 진행 중 (일부 미결정)

> **[2026-08-09 정정]** 이 문서는 도입 전 합의문이고, 그 뒤 코드 대조와 운영에서
> 전제 세 가지가 뒤집혔다. 원문은 남기고 해당 절마다 정정을 달았다.
>
> 1. **"슬랙 파일 변경 불가"는 툴셋 차원에서 구현된 적이 없다** — 아래 "슬랙 파일
>    변경 제한" 절 참조.
> 2. **에이전트의 개발·배포 자동화 목표는 철회됐다** (커밋 `5473ac6`) — 아래 "배경"
>    과 "Git / 개발 작업" 절 참조.
> 3. **`aws` CLI 전체 차단은 구현됐다** — 아래 "접근 제어" 절 참조.
>
> 구현 상태의 정본은 `hermes-security-policy.md` 다. 이 문서는 조직 차원의
> 운영 정책만 다룬다.

---

## 배경

개발 부문 팀 에이전트 도입을 위한 보안 정책 문서.
에이전트 역할:

1. 업무 자동화 (장애 감지 -> 분석 -> 개발 -> 배포 -> Jira 업데이트)
2. 슬랙/팀즈 문의 응답 및 개발 업무 지원

> **[2026-08-09 정정]** 1번의 "개발 -> 배포" 는 철회됐다. 08-07 커밋 `5473ac6`
> ("개발하는 일 하지 않도록 설정 추가") 이후 SOUL.md 는 코드·설정 변경, 배포·재시작,
> 개발을 전부 "하지 않는 일" 로 못박는다. 현재 역할은 2번 — 읽기·분석·답변뿐이다.
> 검증 결과에 따라 1번을 다시 열지는 아직 정해지지 않았다.

---

## 결정된 정책

### OS 격리

- hermes 전용 OS 유저 운영 (/home/hermes)
- SSH 로그인 비허용 (nologin)
- sudo 권한 없음
- ec2-user는 관리자 전용으로 유지, hermes 유저와 완전 분리
- systemd 서비스로 실행 (hermes-gateway.service)
  - 서비스 파일: /etc/systemd/system/hermes-gateway.service
  - 재시작: ec2-user가 직접 서버 접근하여 수행 (추후 슬랙 연동 고려)

### 파일시스템 접근

- hermes 유저 접근 허용 범위:
  - /home/hermes/.hermes/ (읽기/쓰기, 런타임 필수)
  - /home/hermes/work/ (읽기/쓰기, 개발 작업)
- 그 외 전체 차단 (ec2-user 홈, 시스템 파일 등)
- .env 파일 권한: 600 (hermes 유저만 읽기 가능)

### Git / 개발 작업

> **[2026-08-09 정정]** 이 절 전체가 현재는 적용되지 않는다. 에이전트가 개발·배포를
> 하지 않기로 했기 때문이다 (위 "배경" 정정 참조). git pull 만 허용되고, 그것도
> 하네스 갱신 경로로만 쓴다. 개발 자동화를 다시 검토할 때 이 절이 출발점이 된다.

- 개발 작업은 /home/hermes/work/ 에서만 수행
- GitHub organization 단위로 git pull 허용
  - 허용 org 목록은 설정 파일로 관리, 관리자만 수정 가능
- force push 금지, 브랜치 기반 작업 강제
- 배포는 GitHub Actions 트리거만 허용, 에이전트는 트리거 역할만 수행
- PR 머지는 사람이 직접 승인

### 접근 제어

- 슬랙/팀즈를 통해 들어온 요청 + 허용 유저 리스트 둘 다 충족해야 접근 가능
- 슬랙 워크스페이스 멤버라도 허용 리스트에 없으면 접근 불가
- 허용 유저 관리:
  - Slack Slash Command -> Signing Secret 검증 -> 관리자 확인 -> 스크립트 직접 실행
  - 에이전트를 거치지 않고 서버 스크립트가 직접 수행
  - 관리자만 유저 추가/제거 가능

### 명령 차단 (security_guard.py 훅)

현재 적용 중:

- 파일 삭제 명령(rm 계열) 전 플랫폼 차단 — CLI 포함. approvals.deny 와 이중 차단
- SOUL.md 수정 차단 — 읽기만 허용. 갱신은 관리자 로컬 수정 → git push → pull 만
- ~/private 디렉토리 접근 차단
- EC2 메타데이터(169.254.169.254) 직접 접근 차단
- AWS STS, IAM 조회 차단
- AWS 자격증명 직접 출력 차단

결정됨 (미구현):

- aws CLI 전체 차단 (모든 서브커맨드)

> **[2026-08-09 정정] 구현 완료.** `config.yaml` 의 `approvals.deny` 에 `aws *`,
> `security_guard.py` 규칙 3 에 명령 경계 정규식이 있다. 원래 함께 있던
> `'* aws *'` 는 명령 경계를 못 봐서 `grep aws`·커밋 메시지·히어독 문서 작성까지
> 차단해 제거했다 (`tests/test_aws_block.py` 가 이 오탐을 회귀 방지한다).

### 슬랙 파일 변경 제한

- 슬랙/팀즈를 통한 명령으로 파일 변경 불가 (개발, 배포 등)
- 슬랙/팀즈는 읽기/답변 및 GitHub Actions 트리거 역할만 허용

> **[2026-08-09 정정] 이 서술은 사실이 아니었다.** `hermes-slack` 툴셋은
> `_HERMES_CORE_TOOLS` 를 그대로 쓰고, 거기에는 terminal · read_file ·
> write_file · patch · search_files · execute_code 가 전부 들어 있다.
> 좁혀진 적이 없다 — 원격 채널은 CLI 와 같은 도구 집합을 갖고 있다.
>
> 실제로 파일 변경을 막고 있는 것은 L1(`approvals.deny`) · L2(`security_guard` 훅) ·
> LK(systemd `ReadOnlyPaths` 하네스 동결) 세 계층이고, 이건 채널 구분 없이
> 전 플랫폼에 걸린다.
>
> 근거: 2026-08-06 위협 사례 — Slack 세션의 `write_file` 로 스킬 2개가 생성됐다
> (`memories/team-agent-context.md` 사례 1). 툴셋이 우회당한 게 아니라 원래
> 열려 있었다.
>
> **미결**: 원격 채널 툴셋을 좁힐지, full access 를 유지하고 훅으로만 통제할지.
> 원격 채널을 실사용으로 열기 전에 정해야 한다.

### 자가개선 / 스킬

- 기본 비허용
- 관리자 허가 하에만 스킬 추가/수정 가능
- 커스텀 스킬/플러그인은 git으로 관리 (git pull 허용)

### 프로세스 제어

- 프로세스 종료 금지
- 재시작: ec2-user가 직접 서버 접근하여 수행
  - 추후 슬랙 관리자 명령으로 연동 고려 (현재 미구현)

---

## 미결정 항목

1. **자동화 범위**
   - 코드 작성까지 허용할지, 수정 가능 범위와 규칙 정의 필요
   - work/ 밖 읽기 허용 여부 (현재 .hermes/ 읽기는 허용)

2. **민감 정보 화이트리스트 초기 목록**
   - 기본 차단 후 허용해나가는 방식으로 결정됨
   - 구체적인 초기 허용 목록 미정

3. **관리자 허가 절차**
   - 마지막에 수립 예정

---

## 고려사항 (추후 검토)

### AWS Secrets Manager / SSM Parameter Store 도입

현재 .env 파일에 평문으로 저장된 민감 정보:

- SLACK_BOT_TOKEN, SLACK_APP_TOKEN
- TEAMS_CLIENT_ID, TEAMS_CLIENT_SECRET, TEAMS_TENANT_ID
- JIRA 연동 정보

도입 시 구조:

- hermes 유저 EC2 IAM Role에 secretsmanager:GetSecretValue 권한 부여
- 서비스 시작 시 secrets를 환경변수로 주입
- 에이전트가 직접 aws CLI 호출하지 않고 시작 스크립트가 처리

단, aws CLI 전체 차단 정책과 충돌 가능성 있으므로
별도 IAM Role + 시작 스크립트 방식으로 구현 필요.
비용: SSM Parameter Store 무료 티어 활용 권장.

### 슬랙 관리자 재시작 연동

현재 ec2-user 직접 접근으로만 재시작 가능.
불편할 경우 아래 구조로 구현 가능:

- 슬랙 관리자 명령 -> 경량 데몬(ec2-user 소유)이 감시 -> systemctl restart
- hermes 유저는 파일만 쓰고 실제 재시작은 ec2-user 데몬이 수행
- hermes 유저에게 systemctl 권한 불필요

---

## 현재 서버 구조

```
/home/ec2-user/          관리자 유저 (hermes 접근 불가)
  .hermes/               기존 설정 백업용 (이관 완료 후 보존)

/home/hermes/            hermes 에이전트 유저
  .hermes/               Hermes 런타임, 설정, 스킬, 플러그인
    .env                 민감 정보 (권한 600)
    config.yaml          에이전트 설정
    hooks/               보안 훅 (security_guard.py)
    skills/              스킬 디렉토리
    plugins/             플러그인 디렉토리
    docs/                이 문서 포함 정책 문서
  work/                  개발 작업 디렉토리 (git 관리)

/opt/uv/                 Python 런타임 (공용, hermes 의존성 없음)
/etc/systemd/system/hermes-gateway.service   서비스 파일

```
