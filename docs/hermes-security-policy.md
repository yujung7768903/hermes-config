# Hermes 보안 정책 — 고민 · 결정 · 구현 상태 정리

> 원본 세션: `20260724_084228_d291f1` (2026-07-24 ~ 2026-07-28)
> 작성일: 2026-07-31
> 최종 검증: 2026-08-06 (실제 파일 코드 기준으로 전면 재검증 — 미구현 항목 다수 완료 확인)
> 개정: 2026-08-06 2차 (config.yaml·toolsets.py·security_guard.py·로그 대조. 아래 "0. 개정 요약" 참조)
> 개정: 2026-08-10 (운영 서버 직접 확인 — 게이트웨이 프로세스·systemd·config·훅·플러그인·격리 디렉터리)
>
> 팀 공유용 요약: Confluence "Hermes 보안 정책"
> https://hankookilbo.atlassian.net/wiki/spaces/8qbD515ZCKSn/pages/7227080967

---

## 0. 개정 요약 (2026-08-06 2차)

서버의 실제 코드·설정·로그와 대조한 결과, 이 문서의 전제 하나가 사실과 달랐다.

**Slack/Teams는 처음부터 full access였다.**
`hermes-agent/toolsets.py`의 `hermes-slack`은 `_HERMES_CORE_TOOLS`를 그대로 쓴다.
여기에는 terminal, process, read_file, write_file, patch, search_files,
execute_code, delegate_task, browser 12종이 모두 들어 있다.
toolset 설명 문구도 "Slack bot toolset - full access for workspace use"다.

따라서:
  - 2-1의 "어댑터만 있고 file/terminal 둘 다 없었음"은 사실이 아니다
  - 2-2에서 관찰한 "terminal 없이 스킬로 삭제됨"은 toolset 우회가 아니라
    애초에 terminal이 있었기 때문이다
  - L0(Platform Toolsets)은 원격 채널에 대해 아무것도 막지 않는다
  - 5-2의 "Slack/Teams에는 file/terminal 도구 자체가 없음"도 사실이 아니다

추가로 확인된 것:
  - 규칙 1(원격 파일 삭제 차단)이 실제로 발동하는지 미검증 (0-1 참조)
  - 규칙 2의 terminal 패턴에 경로 오류 (`/home/ec2-user/private`)
  - approvals.deny에 죽은 패턴 3개
  - L1 차단은 감사 로그에 기록되지 않음
  - 문서에 없는 방어 장치 2개 존재 (workdir 검증, 내장 security scan)

각 항목은 해당 절에 [2026-08-06 정정] 표시로 반영했다.


### 0-1. 규칙 1 발동 여부 — 미검증

security_guard.py의 원격 판정은 두 경로에 의존한다.

  1. os.getenv("HERMES_SESSION_PLATFORM")
     gateway는 이 값을 contextvars(gateway/session_context)로 바인딩한다.
     hook은 별도 subprocess라 contextvars를 상속받지 못한다.
     shell_hooks.py에도 이 변수를 subprocess로 넘기는 코드가 없다.
  2. session_id 파싱 ("agent:main:slack:..." 형태 기대)
     실제 session_id는 "20260805_060149_1a94bc" 형식이라 파싱에 실패한다.

둘 다 실패하면 platform=""이 되고 is_remote()가 False를 반환한다.
규칙 1은 발동하지 않는다.

관측된 감사 로그 5줄은 모두 platform= 이 빈 값이다.
다만 이 로그는 전부 CLI 세션이고, 원격 채널 발신 이벤트는 0건이다.
따라서 Slack 세션에서의 동작은 확정도 반증도 못 한 상태다.

  확인 방법: Slack에서 rm 명령을 1회 실행한다.
  차단되면 규칙 1이 작동하는 것이고, 파일이 지워지면 원격 채널에
  파일 삭제 방어가 없는 것이다. 이 테스트 전에는 실사용으로 열면 안 된다.


### 0-3. 게이트웨이 2개 동시 구동 [2026-08-09 추가 → 2026-08-10 해소]

**[2026-08-10 정정] 해소됐다.** 서버 프로세스 목록에 `hermes gateway run` 은 hermes
계정 1개뿐이다 (PID 100447). ec2-user 인스턴스는 종료됐고, 아래 "약한 쪽이 강한 쪽의
차단을 우회하는 경로" 위험은 사라졌다. 정책의 정본은 hermes 계정 인스턴스다.

`/home/ec2-user/.hermes` 실체와 `.hermes-migrated` 심볼릭 링크는 그대로 남아 있으나
어떤 프로세스도 쓰지 않는다. 격리 디렉터리는 `/home/hermes/private` 이 생성됐다.
훅 규칙 2 는 대체 경로를 안내하지 않으므로 `hermes-workspace/`·`hermes-readonly/`
부재는 정책상 결함이 아니다 (규칙 2 절 참조).

아래는 해소 전 기록이다.

이 문서의 방어 기술은 전부 **hermes 계정 인스턴스 기준**이다.
같은 서버에 ec2-user 계정 인스턴스가 **동시에 살아 있다**.

  hermes   인스턴스 : PID 1949,  ~/.hermes/config.yaml,
                      approvals.deny 33개, security_log.py 있음,
                      security-filter(L3) 활성
  ec2-user 인스턴스 : PID 47998, ~/.hermes/config.yaml,
                      approvals.deny 17개, security_log.py 없음,
                      L3 마스킹 없음 (plugins.enabled = deploy-log, teams-platform)

/home/ec2-user/.hermes-migrated 가 /home/hermes/.hermes 를 가리키는 심볼릭
링크로 남아 있다. 이관을 시도한 흔적이지만 ec2-user 쪽 .hermes 실체는 그대로
있고, 게이트웨이도 그 실체를 쓰고 있다. **이관이 끝나지 않았다.**

격리 디렉터리(private/ · hermes-workspace/ · hermes-readonly/)도 ec2-user
홈에만 있고 hermes 홈에는 없다.

**먼저 확인할 것**: 두 인스턴스가 같은 Slack/Teams 워크스페이스에 붙어 있는가.
붙어 있다면 같은 메시지에 두 번 응답하거나, **약한 쪽(ec2-user)이 강한 쪽의
차단을 우회하는 경로**가 된다. 양쪽 .env 의 Slack 앱 토큰을 대조하면 된다.

강한 쪽을 아무리 조여도 약한 쪽이 열려 있으면 정책의 정본이 어느 쪽인지
정해지지 않고, 나머지 결정이 전부 무의미해진다. ec2-user 인스턴스 정리가
다른 모든 항목보다 앞선다.

---

### 0-4. L3 마스킹이 로드된 적이 없었다 [2026-08-09 추가]

이 문서가 `[구현 완료]` 로 기술해 온 **L3(security-filter) 마스킹 23종은 한 번도
동작하지 않았다.** 플러그인 코드는 정상인데 `plugin.yaml` 이 없었다.

플러그인 로더는 `<root>/<plugin>/plugin.yaml` 또는
`<root>/<category>/<plugin>/plugin.yaml` 이 있는 디렉터리만 발견한다 (depth 2 캡).
security-filter 는 두 위치 어디에도 매니페스트가 없어서 **디스커버리 단계에서
탈락**했고, `config.yaml` 의 `plugins.enabled` 에 이름이 있어도 로드할 대상이
없었다. `.gitignore` 의 `!plugins/**/*.yaml` 때문에 파일이 있었다면 git 에
추적됐을 것인데, 추적 목록에 deploy-log 것 하나뿐이었다.

결과: AWS 키·Slack 토큰·JWT·PEM 블록·EC2 인스턴스 ID·사설 IP 등이 도구 결과에
섞여 나왔을 때 **마스킹 없이 그대로 모델에 전달돼 왔다.**

`plugins/security-filter/security-filter/plugin.yaml` 추가로 해소. 로컬
hermes-agent v0.18.0 의 실제 로더로 검증했다 —
`security-filter/security-filter enabled=True`, `transform_tool_result` 콜백 등록 확인.
추가 전에는 둘 다 나오지 않았다.

**교훈**: "코드가 있다" 와 "로드된다" 는 다르다. 이 문서의 `[구현 완료]` 표기는
코드 존재만 근거로 삼았고 런타임 등록을 확인하지 않았다. 나머지 계층도 같은
방식으로 재확인이 필요하다.

---

### 0-2. 수정이 필요한 항목

  [완료 2026-08-06 3차] rm 차단을 전 플랫폼으로 이동 — is_remote() 의존 제거.
  [완료 2026-08-10] patch Delete File 차단도 is_remote() 밖으로 나왔다.
         security_guard.py 규칙 1 은 terminal·patch 모두 전 플랫폼 무조건 판정이다.
  [완료 2026-08-09] security_guard.py 의 계정 하드코딩 제거 (PRIVATE_PATTERNS)
  [완료 2026-08-09] approvals.deny 의 비대칭 개행 패턴 2개 수정
  [완료 2026-08-09] 'aws *' 를 L2 정규식으로 이동, '* aws *' 는 deny 에서 제거
  [높음] 원격 채널 toolset을 좁힐지, full access를 유지하고 hook으로만
         통제할지 명시적으로 결정
  [중간] LG(prompt-gate) enforce 전환 — 오차단 0 관측 후
  [낮음] L1 차단도 감사 로그에 기록

---

## 1. 배경 및 문제 인식

이 세션은 Hermes가 Slack/Teams 같은 원격 채널을 통해 명령을 받을 때
"얼마나 위험한 작업까지 허용할 것인가"를 하드하게 제어하려는 목적으로 시작됐다.

처음 요구사항은 세 가지였다:

1. Slack/Teams에서 파일 삭제 명령이 왔을 때 이를 하드하게 차단
2. Hermes가 접근 가능한 파일 영역과 그렇지 않은 영역을 구분
3. 인스턴스 ID, IP, AMI ID, IAM role, 토큰 등 민감 정보를 차단


---

## 2. 고민했던 것들

### 2-1. platform_toolsets 방식 시도 → 롤백

처음에는 config.yaml의 `platform_toolsets`에서 Slack/Teams에
`file` toolset을 추가해서 제어하려 했다.

- 고민: 기존 Slack/Teams 설정은 어댑터만 있고 file/terminal 둘 다 없었음
  (즉 대화만 가능한 구조)
  [2026-08-06 정정] 이 전제는 사실이 아니었다. toolsets.py의 hermes-slack은
  _HERMES_CORE_TOOLS를 그대로 쓰며 terminal/process/read_file/write_file/
  patch/search_files/execute_code/browser 12종을 모두 포함한다.
  slack: [hermes-slack] 은 "어댑터만"이 아니라 "코어 툴 전체"를 뜻한다.
- 시도: `file` toolset 추가 후 hook에서 삭제 패턴만 차단
- 발견한 문제: 오히려 기존보다 권한을 넓히는 셈이 됨
  [2026-08-06 정정] 권한 확대가 아니었다. 이미 열려 있었다.
  file toolset 추가는 중복이었을 뿐이다.
- 결정: platform_toolsets는 원래대로 어댑터만 남기고 롤백
  (slack: [hermes-slack], teams: [hermes-teams])
  [2026-08-06 정정] 롤백 자체는 무해했으나, 롤백 근거가 틀렸다.
  이 결정으로 원격 채널 권한이 좁혀지지 않았다.

핵심 인사이트:
  platform_toolsets는 "이 채널에서 이 도구를 쓸 수 있다"는 화이트리스트다.
  terminal이 없어도 스킬(SKILL.md)이나 write_file/patch(Delete File)를 통해
  파일 삭제가 가능하므로, toolset 제어만으로는 충분하지 않다.

  [2026-08-06 정정] 결론("toolset 제어만으로는 충분하지 않다")은 유효하나
  이유가 다르다. toolset이 좁아서 우회당한 것이 아니라, 애초에 좁혀진 적이
  없다. L0(Platform Toolsets)은 원격 채널에 대해 아무것도 막지 않는다.
  실효 있는 방어는 L1(approvals.deny)과 L2(security_guard hook)뿐이다.


### 2-2. 스킬 경유 삭제 문제 발견

테스트 중 Slack에서 특정 트리거 문구를 입력했을 때 스킬이 실행되며
terminal 도구 없이도 파일이 삭제됨을 직접 확인했다.

  "트리거 감지! 스킬 실행할게요. -f /home/ec2-user/to-do/test.md"

이 경로를 막으려면 pre_tool_call hook으로 terminal + patch 도구를 모두 커버해야 한다는
결론에 도달했다.

  [2026-08-06 정정] "terminal 도구 없이도"가 아니다. 스킬은 결국 terminal이나
  patch를 호출하고, 그 terminal은 Slack에 원래 있었다. security_guard.py의
  주석도 같은 전제를 적고 있다 — "스킬은 결국 terminal이나 patch 도구를
  호출하므로". 관찰된 현상은 toolset 우회가 아니라 정상 동작이었다.
  hook으로 terminal + patch를 모두 커버한다는 결론은 그대로 유효하다.


### 2-3. rm 전체 차단 vs 선택적 차단

마지막 고민: "rm도 kill처럼 approvals.deny로 전부 막으면 어때?"

고민 이유: 스크립트나 기존 프로세스에서 rm을 사용하는 경우에 영향을 줄까봐.

결론 (코드 확인으로 증명):
  - hook은 Hermes AI가 도구를 호출하는 순간에만 개입한다
  - 사용자가 직접 터미널에서 실행하는 rm, cron이 실행하는 스크립트,
    이미 실행 중인 프로세스, 다른 프로세스가 fork한 명령에는 전혀 영향 없음
  - 그러나 rm은 정상 업무(빌드, 임시 파일 정리, 배포)에서도 쓰이므로
    전체 차단보다 "Slack/Teams 세션 한정 차단" 방식이 합리적

최종 설계:
  approvals.deny (전 플랫폼, yolo/mode=off 우회 불가)
    → kill, pkill, killall 등 프로세스 종료 + 복구 불가 경로만
  security_guard hook (pre_tool_call)
    → Slack/Teams 세션 한정으로 rm 계열 전체 차단
    → CLI에서는 위험한 패턴만 기존 approval 흐름대로

  [2026-08-06 3차 변경] 위 "Slack/Teams 한정" 결론을 폐기하고 rm 을 전 플랫폼
  차단으로 바꿨다. 사유 두 가지.
    1. deny 의 rm 패턴이 모두 `-rf` + 특정 경로 조합이라
       `rm /home/hermes/.hermes/.env` 같은 단순 삭제가 그대로 통과했다.
    2. 한정 차단은 is_remote() 에 의존하고, 그 판정은 0-1절대로 항상 False 다.
       즉 rm 차단이 어느 플랫폼에서도 발동하지 않는 상태였다.
  트레이드오프: 빌드·임시파일 정리도 차단된다. 삭제는 사용자가 직접 실행한다.


---

## 3. 최종 결정 및 현재 구현 상태

### [구현 완료] 규칙 1 — 파일 삭제 차단

방식: security_guard.py (pre_tool_call hook) + config.yaml approvals.deny

차단 대상:
  terminal 도구: rm, rmdir, shred, unlink, truncate, find -delete, find -exec rm
    → 전 플랫폼(CLI 포함). is_remote() 판정에 의존하지 않는다.
  patch 도구: *** Delete File: 지시어
    → 전 플랫폼. [2026-08-10 정정] 원래 is_remote() 안에 있어 한 번도 발동하지
      않았다. terminal 삭제 차단과 같은 이유로 밖으로 꺼냈다.
  approvals.deny: rm 을 명령 토큰으로 잡는 패턴 11개 (경로·옵션별 옛 패턴 17개 대체)
    → 훅이 미실행·예외로 fail-open 되는 경우의 2차 방어선

예외: 없음 (전 경로 차단)

적용 파일: /home/hermes/.hermes/hooks/security_guard.py, ~/.hermes/config.yaml
검증: tests/test_rm_block.py (deny 패턴 + 훅 subprocess 실행, 27건)

  [2026-08-06 정정] 구현은 완료됐으나 실제 발동은 미검증이다.
  이 규칙은 is_remote() 판정에 의존하는데, 관측된 모든 로그에서
  platform 값이 비어 있다. 0-1절 참조.

  [2026-08-06 3차] 위 미검증 사유를 해소했다. terminal 삭제 차단을
  is_remote() 밖으로 빼서 platform 값이 비어 있어도 발동한다.

  [2026-08-10] patch Delete File 차단도 is_remote() 밖으로 나왔다. 서버의
  security_guard.py 에서 is_remote() 를 참조하는 규칙은 하나도 없고, 함수
  독스트링이 "현재 어떤 규칙도 이 함수에 의존하지 않는다" 를 명시한다.
  채널별 정책을 다시 넣으려면 platform 전달 경로부터 고쳐야 한다.


### [구현 완료] 규칙 2 — ~/private 접근 차단 (전 플랫폼)

방식: security_guard.py (pre_tool_call hook)

차단 대상:
  read_file  : path가 ~/private 하위인 경우
  write_file : path가 ~/private 하위인 경우
  search_files: path가 ~/private 하위인 경우
  patch      : Update/Create/Delete File 대상 경로가 ~/private 하위인 경우
  terminal   : ~/private, /home/hermes/private, $HOME/private 패턴

  [2026-08-06 정정] 코드의 실제 정규식은 4개이며 경로 하나가 틀렸다.
    ~/private              정상
    /home/ec2-user/private 오류. Hermes는 hermes 계정으로 구동한다
    $HOME/private          정상
    ${HOME}/private        정상 (문서 누락)

  결과: terminal 로 /home/hermes/private 를 직접 지정하면 차단되지 않는다.
  read_file / write_file / search_files / patch 는 realpath 기반 비교라
  정상 동작한다. security_guard.py 의 PRIVATE_PATTERNS 를 수정해야 한다.

  [2026-08-09 정정] 수정 완료. PRIVATE_PATTERNS 는 이제 계정을 하드코딩하지 않고
  rf"{re.escape(HOME)}/private\b" 를 쓴다 (security_guard.py 규칙 2).
  계정이 바뀌어도 따라간다. 위 "오류" 항목은 해소된 상태다.

접근 거부 메시지 (security_guard.py 실제 문구):
  [보안 정책] 접근 금지 디렉토리입니다.
  경로: {대상 경로}
  ~/private 및 하위 디렉토리는 Hermes 접근이 차단됩니다.

  [2026-08-10 정정] 이 자리에 "접근이 필요하다면 ~/hermes-workspace/ 로 복사 후
  이용하세요" 가 적혀 있었다. 훅은 그 문구를 내지 않는다 — 코드에 hermes-workspace
  언급이 없다. 대체 경로를 안내하지 않는 것이 현재 동작이다.

적용 파일: /home/hermes/.hermes/hooks/security_guard.py


### [구현 완료] 규칙 3 — 민감 정보 차단 (전 플랫폼)

3개 계층으로 구성:

계층 A — approvals.deny (config.yaml, yolo/mode=off로도 우회 불가)
  프로세스 종료:
    kill *, * kill *, pkill *, killall *
  AWS 관련:
    aws *, * aws *               AWS CLI 전체 차단
    *169.254.169.254*            EC2 IMDS 직접 접근
    *X-aws-ec2-metadata-token*   IMDSv2 토큰 발급
  시스템 파괴 수준 명령:
    *chmod*777* /*, *chown*-r* /*                시스템 전체 권한/소유권 변경
    *rm*-rf*/etc*, /usr*, /bin*, /lib*, /boot*, /var*  시스템 디렉토리 삭제
    rm -rf ~, *rm*-rf*~/* , *rm*-rf*/home/hermes*     홈 디렉토리 직접 삭제
    *rm*~/.ssh*, *rm*~/.hermes/.env*             SSH 키 · .env 삭제
    rm -rf ~/.hermes, rm -rf ~/hermes-workspace  Hermes 설정 삭제

  [2026-08-06 정정] config.yaml의 실제 항목은 31개다. 문서에 없던 것:
    *chmod*-r*777* /*      재귀 플래그 변형
    * rm -rf ~             앞에 다른 명령이 붙은 형태
    rm*-rf*~/*             와일드카드 변형
    *rm*-rf*~/.ssh*        SSH 키 재귀 삭제
    rm -rf /home/*         (문서 6-3 표에는 있으나 이 절에는 누락)

  죽은 패턴 3개도 있다. YAML 멀티라인 폴딩으로 개행이 들어갔다.
  아래 3개는 실제 명령과 매칭되지 않는다.
    '*\n\n      kill *'
    '*\n\n      pkill'
    '\n\n      killall *'

  [2026-08-09 정정] 셋 다 죽은 게 아니라 2개다. fnmatch 는 * 가 개행에도
  매칭되므로 '*\nkill *' 은 `cd /tmp\nkill 1234` 를 정상적으로 잡는다.
  실제로 무력한 것은 비대칭인 둘이다.
    '*\npkill'      뒤에 * 가 없어 `a\npkill -9` 미차단
    '\nkillall *'   앞에 * 가 없어 `a\nkillall x` 미차단
  개행 패턴이 필요한 이유는 '* kill *' 이 개행 뒤 형태를 못 잡기 때문이다
  (개행 앞에 공백이 없다).

  수정 완료. rm 그룹과 같은 "\n" 표기로 통일했다. 같은 점검에서 명령 끝에
  오는 형태('… | xargs pkill')가 전부 통과하던 것도 발견해 '* kill' ·
  '* pkill' · '* killall' 을 추가했다 — rm 그룹의 '* rm' 과 같은 이유다.
  회귀 방지: tests/test_kill_block.py

  'aws *' / '* aws *' 는 명령 문자열 어디에나 매칭된다. AWS CLI 호출이
  아닌 명령도 차단한다. agent.log에 히어독으로 문서를 쓰는 명령이
  '* aws *' 로 차단된 사례가 있다. 명령 시작 위치로 한정해야 한다.

  [2026-08-09 정정] 수정 완료. fnmatch 로는 명령 경계를 표현할 수 없으므로
  '* aws *' 를 deny 에서 빼고, 판정을 L2 로 옮겼다. security_guard 규칙 3 에
  줄 시작 · ; · | · & · ( · 백틱 · 개행 뒤의 aws 를 잡는 정규식을 넣었고
  sudo·env·VAR= 프리픽스도 포함한다. deny 에 남은 'aws *' 는 훅이 fail-open
  될 때의 2차 방어선이다.
  남는 오탐: 히어독 본문에서 줄이 "aws " 로 시작하는 경우. 개행 뒤를 안 보면
  `cmd1\naws s3 ls` 가 통과하므로 이쪽을 택했다.
  회귀 방지: tests/test_aws_block.py

계층 B — security_guard.py hook (pre_tool_call)
  terminal 도구 차단:
    169.254.169.254              EC2 IMDS 직접 접근
    X-aws-ec2-metadata-token     IMDSv2 토큰 발급
    aws ec2 describe-instances   인스턴스 ID/IP/AMI ID 조회
    aws ec2 describe-instance-attribute
    aws sts get-caller-identity  IAM role/계정 ID 조회
    aws iam *                    IAM 직접 조회
    echo $AWS_SECRET_ACCESS_KEY
    echo $AWS_SESSION_TOKEN
    echo $AWS_ACCESS_KEY_ID
    printenv AWS_SECRET_ACCESS_KEY/SESSION_TOKEN
    cat /etc/machine-id
    cat /etc/hostname
    curl/wget + $AWS_*           자격증명 외부 전송

  read_file 도구 차단:
    .aws/credentials
    .aws/config
    /etc/hostname
    /etc/machine-id

  ※ AWS CLI 전체는 approvals.deny(계층 A)에서 이미 차단되므로
    계층 B의 aws ec2/sts/iam 패턴은 이중 방어 역할을 한다.

  [2026-08-06 정정] 코드의 실제 개수는 terminal 15개, read_file 4개다.
  문서는 curl/wget 을 한 줄로 묶었으나 코드에서는 별도 정규식 2개다.
  read_file 은 .aws/credentials, .aws/config, /etc/hostname,
  /etc/machine-id 4개로 문서와 일치한다.

계층 C — security-filter 플러그인 (transform_tool_result)
  도구 실행 결과가 모델에 전달되기 직전, 민감 정보를 정규식으로 마스킹한다.
  마스킹 커버리지:
    AWS 액세스 키 ID (AKIA/ASIA/AROA/AIDA 접두사)
    AWS 시크릿 키 (변수명 컨텍스트 + 32자 이상 base64)
    AWS 세션 토큰 (FwoGZX 접두사 또는 변수명 컨텍스트)
    Slack 토큰 (xoxb-/xoxp-/xoxa-/xoxr-/xoxs-/xapp-)
    Discord 봇 토큰 (3파트 dot 구분 base64)
    Telegram 봇 토큰 (숫자:35자 alphanum)
    GitHub PAT (ghp_/gho_/ghu_/ghs_/ghr_/github_pat_)
    Stripe 키 (sk_live_/sk_test_/rk_live_/pk_live_ 등)
    OpenAI API 키 (sk-proj-... / sk-...48자+)
    JWT 토큰 (eyJ 접두사 3파트 base64url)
    Bearer 토큰 (Authorization: Bearer ***)
    Generic API 키 (api_key/secret_key/access_token = 형태)
    Private Key 블록 (-----BEGIN ... PRIVATE KEY-----)
    EC2 인스턴스 ID (i-xxxxxxxxxxxxxxxx)
    AMI ID (ami-xxxxxxxxxxxxxxxx)
    EC2 IMDS 주소 (169.254.169.254)
    사설 IP 대역 (10.x / 172.16-31.x / 192.168.x)
  마스킹 발생 시 agent.log에 WARNING 기록 + 감사 로그 기록.

  적용 파일: /home/hermes/.hermes/plugins/security-filter/security-filter/__init__.py
  활성화 위치: config.yaml > plugins.enabled


### [구현 완료] 감사 로그

방식: security_log.py (security_guard.py 및 security-filter 플러그인 공통 사용)

기록 대상:
  BLOCKED             — pre_tool_call hook(L2)에서 차단된 이벤트
  MASKED              — transform_tool_result(L3)에서 마스킹된 이벤트
  BLOCKED_PROMPT      — LG(prompt-gate)에서 실제로 차단한 요청
  WOULD_BLOCK_PROMPT  — LG observe 모드에서 차단 대상으로 분류했으나 통과시킨 요청

로그 위치: ~/.hermes/logs/security/YYYY-MM-DD.log
보관 기간: 14일 자동 로테이션

  [2026-08-06 정정] 계층 A(approvals.deny) 차단은 이 감사 로그에 남지 않는다.
  tools/approval.py 가 처리하고 security_log.py 를 호출하지 않기 때문이다.
  계층 A 차단 기록은 ~/.hermes/logs/agent.log 에만 있다.
  전체 차단 내역을 보려면 두 로그를 함께 봐야 한다.

  로그 축적 현황 (2026-08-06 기준): 총 5줄.
    2026-08-04 3줄 (TEST 1, MASKED 1, BLOCKED 1)
    2026-08-05 2줄 (BLOCKED 2)
  BLOCKED 3건은 전부 IMDS 접근이고 전부 CLI 세션이다.
  원격 채널 발신 이벤트는 0건이다. 14일 로테이션은 운영 3일차라 미검증이다.

로그 포맷 예시:
  2026-08-04 10:30:15 | BLOCKED  | platform=slack | tool=terminal | session=agent:main:slack:... | rule=Slack/Teams 파일 삭제 차단 | detail=rm -rf /home/...

적용 파일: /home/hermes/.hermes/hooks/security_log.py


---

## 4. 현재 구현된 파일 목록

/home/hermes/.hermes/hooks/security_guard.py
  역할: pre_tool_call hook. 규칙 1~3 계층B 전부 담당.

/home/hermes/.hermes/hooks/security_log.py
  역할: 감사 로그 유틸리티. BLOCKED/MASKED 이벤트를 날짜별 파일에 기록.

/home/hermes/.hermes/plugins/security-filter/security-filter/__init__.py
  역할: transform_tool_result 플러그인. 도구 결과 민감 정보 마스킹 (규칙 3 계층C).

/home/hermes/.hermes/plugins/prompt-gate/__init__.py   [2026-08-10 추가]
  역할: pre_gateway_dispatch 플러그인. 인바운드 요청을 21개 카테고리로 분류해
        허용 8종·관리자 전용 2종만 통과시킨다 (LG). 매니페스트는 같은
        디렉터리의 plugin.yaml.
        security-filter 는 한 단계 더 들어간 plugins/security-filter/
        security-filter/ 에 매니페스트가 있다 — 로더가 깊이 2까지 훑기 때문에
        두 배치 모두 발견된다.

/home/hermes/.hermes/config.yaml
  역할: approvals.deny 블록 (규칙 3 계층A), platform_toolsets, plugins.enabled 설정.
        hooks_auto_accept: true, hooks.pre_tool_call 등록도 여기 있다.

/home/hermes/.hermes/hermes-agent/toolsets.py   [2026-08-06 추가]
  역할: 채널별 toolset의 실제 정의. config.yaml의 platform_toolsets는
        여기 정의된 이름(hermes-slack 등)을 참조할 뿐이다.
        원격 채널 권한을 좁히려면 이 파일을 봐야 한다.
        _HERMES_CORE_TOOLS 는 31행부터.

/home/hermes/.hermes/config.yaml.bak.security
  역할: 보안 정책 적용 전 원본 config 백업.


---

## 5. 추후 고려해야 할 항목 (미구현)

다음 항목들은 세션에서 언급되거나 논의됐으나 당장 구현하지 않은 것들이다.


### 5-1. 파일 접근 구역 체계화

현재: ~/private 차단만 구현됨.
논의됐지만 미구현:

  ~/hermes-workspace/   Hermes 읽기/쓰기 허용 영역 (별도 생성 필요)
  ~/hermes-readonly/    읽기만 허용 영역 (hook 로직 추가 필요)
  ~/private/            모든 접근 차단 (완료)

추가 고려사항:
  - HERMES_WRITE_SAFE_ROOT=/home/hermes/hermes-workspace 를 .env에 등록하면
    Hermes의 file_safety.py가 safe_root 밖 쓰기를 이중으로 차단 가능
  - 단순 디렉토리 분리가 아닌 "읽기 전용 경로 화이트리스트" 체계가 필요


### 5-2. Slack/Teams에서 쓰기 허용 범위 결정

현재: Slack/Teams 세션은 어댑터만 있어 file/terminal 도구 자체가 없음.
남은 질문: "Slack/Teams에서 특정 경로의 파일 읽기/쓰기가 필요한가?"

  [2026-08-06 정정] 위 "현재" 서술은 사실이 아니다. hermes-slack은
  _HERMES_CORE_TOOLS 전체를 포함하므로 read_file / write_file / patch /
  search_files / terminal / execute_code 가 모두 이미 열려 있다.
  따라서 남은 질문은 "필요한가"가 아니라 "지금 열려 있는 것을 좁힐 것인가"다.
  이 결정은 미룰 수 없다. 원격 채널을 실사용으로 열기 전에 정해야 한다.

  예) 배포 결과 파일 조회, 로그 파일 일부 읽기 등이 필요하다면
  platform_toolsets에 file toolset을 추가하되,
  security_guard hook에서 쓰기/삭제는 막고 읽기만 허용하는 방식으로 확장 가능.

결정 필요: 원격 채널에서 파일 접근이 필요한 실제 업무 케이스가 생기면 그때 설계.


### 5-3. 채널별 권한 세분화

현재: Slack/Teams를 동일하게 취급 (REMOTE_PLATFORMS 집합으로 묶음).
추후 고려: 채널 유형(DM vs 채널)이나 사용자별로 권한을 다르게 줄 필요가 생길 수 있음.

예시:
  - 운영 채널(#배포)에서만 특정 명령 허용
  - 특정 Slack 사용자 ID는 CLI에 준하는 권한 부여

현재 구조에서 확장 방법:
  HERMES_SESSION_PLATFORM 외에 session_id에서 chat_id/user_id 파싱 가능.
  security_guard.py에 사용자/채널 기반 예외 로직 추가 가능.


---

## 6. 아키텍처 및 보안 계층

### 6-1. 요청 처리 파이프라인

| 순서 | 단계 | 설명 | 보안 개입 여부 |
|------|------|------|---------------|
| 1 | 메시지 수신 | Slack/Teams/CLI 등 채널에서 사용자 메시지 수신 | - |
| 2 | Gateway (Platform Adapter) | 채널별 어댑터가 메시지를 Hermes 내부 형식으로 변환 | platform_toolsets 화이트리스트 적용 |
| 2.5 | pre_gateway_dispatch hook | 메시지를 모델에 넘기기 직전 hook 체인 실행. 반환값으로 통과·무시·교체 결정 | **[2026-08-09] prompt-gate 플러그인으로 구현. 기본 observe 모드** (LG) |
| 3 | Hermes AI (모델 판단) | LLM이 요청을 해석하고 어떤 도구를 호출할지 결정 | 없음 (날 메시지가 그대로 도달) |
| 4 | model_tools.py | 도구 호출 직전 처리 단계 | approvals.deny 패턴 매칭 (L1) |
| 5 | pre_tool_call hook | 도구 실행 직전 hook 체인 실행 | security_guard.py 차단 로직 실행 (L2) |
| 6 | 도구 실행 | terminal / patch / read_file / write_file 등 실제 도구 실행 | - |
| 7 | transform_tool_result | 도구 결과가 모델에 전달되기 직전 | security-filter 플러그인 민감 정보 마스킹 (L3) |
| 8 | 응답 반환 | 결과를 채널로 전송 | - |

※ 3단계(모델 판단) 이전 개입 지점은 2.5단계(pre_gateway_dispatch)다.
  [2026-08-10 정정] 이 지점은 prompt-gate 플러그인으로 채워졌다. 원래 여기 있던
  "등록된 훅이 없다 / 미구현" 서술은 LG 도입 전 상태였다.
  현재는 mode: observe 라 분류·기록만 하고 통과시키며, 정규식 선판정
  (enforce_hard_block: true)에 걸린 것만 실제로 차단한다.
  설계 배경은 hermes-request-whitelist-plan.md 참조.

  [2026-08-06 3차 정정] 이 표에는 원래 2.5단계가 없었고 "3단계 이전에는
  보안 개입 지점이 없다"고 적혀 있었다. 사실이 아니다 — 지점은 처음부터
  있었고 차단 로직만 비어 있었다.


### 6-2. 보안 계층 상세

| 계층 | 계층명 | 구현 위치 | 적용 플랫폼 | 차단 방식 | yolo/mode=off 우회 | 구현 상태 |
|------|--------|-----------|-------------|-----------|-------------------|-----------||
| LK | 커널 하네스 동결 | systemd drop-in `10-readonly-harness.conf` (`ReadOnlyPaths=`) | 게이트웨이와 그 자식 프로세스 전부 | 하네스 경로가 읽기전용 바인드 마운트로 올라와 쓰기 자체가 EROFS | 우회 불가. 해제에 root 필요하고 `NoNewPrivileges=true` 로 하위에서 sudo 불가 | 완료 |
| L0 | Platform Toolsets 화이트리스트 | config.yaml `platform_toolsets` + `toolsets.py` | 채널별 설정 | 채널에 등록되지 않은 toolset은 도구 자체가 노출되지 않음 | 우회 불가 (설정 레벨) | 설정됨. **원격 채널에는 실효 없음** — hermes-slack이 코어 툴 전체를 포함 |
| LG | 요청 화이트리스트 게이트 | **플러그인** `plugins/prompt-gate/` (config.yaml `hooks:` 절이 아니다 — 아래 정정 참조) | gateway 인바운드 메시지 전용. **CLI·cron·ACP·internal event 는 지나지 않음** | 허용 8종 + 관리자 전용 2종으로 분류되는 요청만 통과. 나머지 11종은 원문을 폐기(`skip`)하고 차단 사유는 게이트웨이가 사용자에게 직접 보낸다 (`on_block: notice`) | 원문이 모델에 도달하지 않음 | **[2026-08-10 확인] `mode: observe`** — 분류·로깅만 하고 통과시키되 `enforce_hard_block: true` 로 정규식 선판정 히트는 실차단. 오차단 0 관측 후 `enforce` 전환 |
| L1 | Approvals Deny | config.yaml `approvals.deny` | 전 플랫폼 | 패턴 매칭된 명령은 모델 판단과 무관하게 실행 거부 | 우회 불가 | 완료 |
| L2 | pre_tool_call Hook | security_guard.py | 설정에 따라 전 플랫폼 또는 원격 한정 | 도구 호출 직전 인자를 검사해 차단 또는 허용 | hook이 deny 반환하면 실행 안 됨 | 완료 |
| L3 | transform_tool_result | security-filter 플러그인 | 전 플랫폼 | 도구 결과에서 민감 정보 정규식 마스킹 | - | 완료 |

L0~L3 은 전부 도구 호출 단위 판정이라, 패턴을 벗어나면 통과한다. LK 는 판정이 아니라
파일시스템 자체를 읽기전용으로 만들므로 수단·경로와 무관하게 막힌다 — `write_file`,
`patch`, `terminal` 의 리다이렉션·`tee`·`sed -i`·인터프리터 경유 쓰기가 한 번에 걸린다.
L2 규칙 4(SOUL.md 자기수정 차단)와 L1 의 `*soul.md*` 패턴은 LK 가 포섭하지만, LK 는
차단 사실을 `security_log` 에 남기지 않으므로 탐지·리포트 목적으로 유지한다.

#### 6-2-1. LK — 동결 대상과 제외 대상

| 구분 | 경로 | 근거 |
|------|------|------|
| 동결 | `config.yaml`, `SOUL.md`, `.env`, `hooks/`, `scripts/`, `plugins/`, `skills/`, `memories/`, `hermes-agent/`, `.git/` | 하네스 정의. 런타임 쓰기 없음 |
| 제외 | `logs/`, `sessions/`, `cache/`, `cron/`, `pastes/` 및 최상위 런타임 파일 | 게이트웨이가 상시 기록 |

`cron/jobs.json` 은 스케줄러가 `next_run_at`·`last_run_at` 을 계속 써야 해서 동결할 수
없다. LK 로 해결되지 않으므로 크론 잡 등록 차단은 L1·L2 가 담당한다 (규칙 5, 6-4 표 R5).

`config.yaml` 동결의 부작용 — `tools/approval.py` 의 영구 allowlist 저장이 EROFS 로
실패한다. 에이전트가 승인 이력을 설정에 굳히지 못하게 하는 것이 목적이므로 의도된 동작이다.

검증: `scripts/verify_harness_readonly.sh` 를 서버에서 실행한다. 실행 중인 게이트웨이의
마운트 네임스페이스에 직접 들어가 확인하므로 설정이 아니라 실제 적용 상태를 본다.

  [2026-08-10 검증] PASS. 드롭인은 `/etc/systemd/system/hermes-gateway.service.d/`
  에 설치돼 있고, 동결 대상 10경로 전부 `Read-only file system`, 런타임 5경로
  (`cron/`·`logs/`·`sessions/`·`cache/`·최상위) 쓰기 정상, 게이트웨이 프로세스
  `NoNewPrivs: 1`. 설정이 아니라 구동 중인 PID 에서 확인한 결과다.

### 6-3. L1 — Approvals Deny 차단 목록

[2026-08-10 확인] 서버 `config.yaml` 의 `approvals.deny` 는 68개다. 그룹별 개수는
프로세스 종료 12, rm 11, SOUL.md 15, 배치(cron·crontab·systemd-run·timer·at) 18,
jobs.json 6, AWS 2(`aws *`·IMDSv2 토큰 헤더), IMDS 주소 1, chmod/chown 3.
아래 표는 그룹 단위 요약이고 개별 패턴은 `config.yaml` 이 정본이다.

| 패턴 | 차단 이유 | 적용 범위 |
|------|-----------|-----------|
| `kill *`, `* kill *` | 프로세스 강제 종료, 복구 불가 | 전 플랫폼 |
| `pkill *`, `* pkill *` | 이름 기반 프로세스 종료 | 전 플랫폼 |
| `killall *`, `* killall *` | 동명 프로세스 전체 종료 | 전 플랫폼 |
| `aws *`, `* aws *` | AWS CLI 전체 차단 | 전 플랫폼 |
| `*169.254.169.254*` | EC2 IMDS 직접 접근 | 전 플랫폼 |
| `*X-aws-ec2-metadata-token*` | IMDSv2 토큰 발급 | 전 플랫폼 |
| `*chmod*777* /*` | 루트 전체 권한 변경 | 전 플랫폼 |
| `*chown*-r* /*` | 루트 소유권 변경 | 전 플랫폼 |
| `rm`, `rm *`, `* rm`, `* rm *`, `*;rm*`, `*\|rm*`, `*&rm*`, `*(rm*`, `` *`rm* ``, `*"rm*`, `*\nrm*` | rm 전체 차단 (경로·옵션 무관). rm 앞에 올 수 있는 구분자를 열거 | 전 플랫폼 |
| `*cron create*` 외 변경 서브커맨드 9개, `*crontab*`, `*systemd-run*`, `*systemctl*timer*`, `at *` 계열 5개 | 배치(크론 잡) 등록·변경. 조회는 미포함 | 전 플랫폼 |
| `*>*jobs.json*`, `*tee*jobs.json*`, `*sed*-i*jobs.json*`, `*cp*jobs.json*`, `*mv*jobs.json*`, `*python*jobs.json*` | 잡 저장소 직접 편집 | 전 플랫폼 |

  [2026-08-06 3차] 경로·옵션별 rm 패턴 17개(`*rm*-rf*/etc*`, `rm -rf ~`,
  `*rm*~/.ssh*`, `rm -rf /home/*` 등)를 위 11개로 대체했다. 옛 패턴은 `-rf` +
  특정 경로 조합만 잡아 `rm /home/hermes/.hermes/.env` 가 통과했다.
  대체 전후 커버리지는 tests/test_rm_block.py 의 포섭 검증으로 확인한다
  (제거한 17개가 잡던 1710조합 중 누락 0건).


### 6-4. L2 — security_guard.py 차단 규칙 상세

| 규칙 ID | 적용 플랫폼 | 대상 도구 | 차단 조건 | 차단 메시지 |
|---------|-------------|-----------|-----------|-------------|
| R1-1 | 전 플랫폼 | terminal | `rm`, `rmdir`, `shred`, `unlink`, `truncate` 포함 | [보안 정책] 파일 삭제 명령은 허용되지 않습니다. |
| R1-2 | 전 플랫폼 | terminal | `find ... -delete` 또는 `find ... -exec rm` 패턴 | 동일 |
| R1-3 | 전 플랫폼 | patch | `*** Delete File:` 지시어 포함 | [보안 정책] 파일 삭제는 허용되지 않습니다. (patch Delete File 지시어 차단) |
| R2-1 | 전 플랫폼 | read_file | path가 `~/private` 하위 | [보안 정책] 접근 금지 디렉토리입니다. ~/private 및 하위 디렉토리는 Hermes 접근이 차단됩니다. |
| R2-2 | 전 플랫폼 | write_file | path가 `~/private` 하위 | 동일 |
| R2-3 | 전 플랫폼 | search_files | path가 `~/private` 하위 | 동일 |
| R2-4 | 전 플랫폼 | patch | Update/Create/Delete File 대상 경로가 `~/private` 하위 | 동일 |
| R2-5 | 전 플랫폼 | terminal | `~/private`, `/home/hermes/private`, `$HOME/private` 패턴 | 동일 |
| R3-1 | 전 플랫폼 | terminal | `169.254.169.254` (EC2 IMDS 직접 접근) | [보안 정책] 인스턴스 메타데이터 접근은 허용되지 않습니다. |
| R3-2 | 전 플랫폼 | terminal | `X-aws-ec2-metadata-token` (IMDSv2 토큰 발급) | 동일 |
| R3-3 | 전 플랫폼 | terminal | `aws ec2 describe-instances` (인스턴스 ID/IP/AMI 조회) | [보안 정책] 인스턴스 정보 조회는 허용되지 않습니다. |
| R3-4 | 전 플랫폼 | terminal | `aws ec2 describe-instance-attribute` | 동일 |
| R3-5 | 전 플랫폼 | terminal | `aws sts get-caller-identity` (IAM role/계정 ID 조회) | [보안 정책] IAM 자격증명 조회는 허용되지 않습니다. |
| R3-6 | 전 플랫폼 | terminal | `aws iam *` (IAM 직접 조회) | 동일 |
| R3-7 | 전 플랫폼 | terminal | `echo $AWS_SECRET_ACCESS_KEY` | [보안 정책] AWS 자격증명 출력은 허용되지 않습니다. |
| R3-8 | 전 플랫폼 | terminal | `echo $AWS_SESSION_TOKEN` | 동일 |
| R3-9 | 전 플랫폼 | terminal | `echo $AWS_ACCESS_KEY_ID` | 동일 |
| R3-10 | 전 플랫폼 | terminal | `printenv AWS_SECRET_ACCESS_KEY` / `printenv AWS_SESSION_TOKEN` | 동일 |
| R3-11 | 전 플랫폼 | terminal | `cat /etc/machine-id` | [보안 정책] 시스템 식별 정보 접근은 허용되지 않습니다. |
| R3-12 | 전 플랫폼 | terminal | `cat /etc/hostname` | 동일 |
| R3-13 | 전 플랫폼 | terminal | `curl` 또는 `wget` + `$AWS_*` 패턴 (자격증명 외부 전송) | [보안 정책] AWS 자격증명 외부 전송은 허용되지 않습니다. |
| R3-14 | 전 플랫폼 | read_file | path가 `.aws/credentials` | [보안 정책] AWS 자격증명 파일 접근은 허용되지 않습니다. |
| R3-15 | 전 플랫폼 | read_file | path가 `.aws/config` | 동일 |
| R3-16 | 전 플랫폼 | read_file | path가 `/etc/hostname` | [보안 정책] 시스템 식별 정보 접근은 허용되지 않습니다. |
| R3-17 | 전 플랫폼 | read_file | path가 `/etc/machine-id` | 동일 |
| R4-1 | 전 플랫폼 | write_file | path가 `~/.hermes/SOUL.md` | [보안 정책] SOUL.md 는 Hermes 가 수정할 수 없습니다. |
| R4-2 | 전 플랫폼 | patch | Update/Create/Delete File 대상이 `~/.hermes/SOUL.md` | 동일 |
| R4-3 | 전 플랫폼 | terminal | `SOUL.md` 언급 + 쓰기 수단(`>`·`tee`·`sed -i`·`cp`·`mv`·`ln`·`truncate`·`dd`·`chmod`·`chown`·`open(`·`git checkout/restore/apply`) | 동일 |

| R5-1 | 전 플랫폼 | terminal | `cron` + 변경 서브커맨드(create·add·new·update·edit·set·enable·disable·delete·remove) | [보안 정책] 배치(크론 잡) 등록·변경은 허용되지 않습니다. |
| R5-2 | 전 플랫폼 | terminal | `crontab` · `systemd-run` · `systemctl *.timer` · `at <시각\|now>` | 동일 |
| R5-3 | 전 플랫폼 | terminal | `jobs.json` 언급 + 쓰기 수단(R4-3 과 동일 목록) | 동일 |
| R5-4 | 전 플랫폼 | write_file | path가 `~/.hermes/cron/jobs.json` | 동일 |
| R5-5 | 전 플랫폼 | patch | Update/Create/Delete File 대상이 `~/.hermes/cron/jobs.json` | 동일 |

R5 의 차단 지점은 스크립트 위치가 아니라 등록 행위다. `~/.hermes/scripts/` 가 LK 로
읽기전용이면 에이전트는 `~/work/` 에 스크립트를 두거나 스크립트 없는 프롬프트 기반
잡으로 우회하는데, 두 경로 모두 스케줄러 등록을 거치므로 그 지점 하나로 포섭된다
(2026-08-07 `daily-farewell` 사례. `cron-jobs.md` 참조). 조회(`cron list`·`show`,
`cat jobs.json`)는 통과하고, 관리자가 ssh 로 실행하는 등록은 훅 밖이라 영향받지 않는다.

R4 는 읽기를 막지 않는다. `cat`·`grep`·`wc` 는 통과하고, `git pull` 은 SOUL.md 를
명시하지 않으므로 걸리지 않는다 — 관리자 로컬 수정 → git push → pull 이 유일한
갱신 경로라는 원칙이 그대로 성립한다.

파일 권한(444·소유자 변경)으로는 막을 수 없다. `~/.hermes` 디렉터리 소유자가
hermes 이므로 파일을 지우고 새로 만드는 경로로 우회된다. 도구 호출을 막는
R4 와 L1 패턴이 실효 있는 수단이다.

잔여 위험 — `git pull` 자체는 허용되므로, 원격 저장소에 쓰기 권한이 있는 주체가
SOUL.md 를 바꾸면 그대로 반영된다. 즉 보호 경계는 "Hermes 의 로컬 수정"까지이고
저장소 접근 통제는 GitHub 권한 문제다.


### 6-5. L3 — security-filter 마스킹 패턴 상세

| 패턴 ID | 대상 | 패턴 특징 | 치환 문자열 |
|---------|------|-----------|-------------|
| M1 | AWS 액세스 키 ID | AKIA/ASIA/AROA/AIDA 접두사 + 대문자+숫자 16자 | `[AWS_KEY_REDACTED]` |
| M2 | AWS 시크릿 키 | 변수명 컨텍스트 + 32자 이상 base64 | `[AWS_SECRET_REDACTED]` |
| M3 | AWS 세션 토큰 | FwoGZX 접두사 또는 변수명 컨텍스트 | `[AWS_SESSION_TOKEN_REDACTED]` |
| M4 | Slack 토큰 | xoxb-/xoxp-/xoxa-/xoxr-/xoxs- | `[SLACK_TOKEN_REDACTED]` |
| M5 | Slack 앱 토큰 | xapp- | `[SLACK_TOKEN_REDACTED]` |
| M6 | Discord 봇 토큰 | 3파트 dot 구분 base64url | `[DISCORD_TOKEN_REDACTED]` |
| M7 | Telegram 봇 토큰 | 숫자:35자 alphanum | `[TELEGRAM_TOKEN_REDACTED]` |
| M8 | GitHub PAT | ghp_/gho_/ghu_/ghs_/ghr_ + 36자 | `[GITHUB_TOKEN_REDACTED]` |
| M9 | GitHub fine-grained PAT | github_pat_ + 82자 | `[GITHUB_TOKEN_REDACTED]` |
| M10 | Stripe 키 | sk_live_/sk_test_/rk_live_/pk_live_/pk_test_ | `[STRIPE_KEY_REDACTED]` |
| M11 | OpenAI API 키 (신형) | sk-proj- + 80자 이상 | `[OPENAI_KEY_REDACTED]` |
| M12 | OpenAI API 키 (구형) | sk- + 48자 이상 | `[OPENAI_KEY_REDACTED]` |
| M13 | Generic 서비스 키 | sk-/pk- + 24자 이상 | `[SERVICE_KEY_REDACTED]` |
| M14 | JWT 토큰 | eyJ 접두사 3파트 base64url | `[JWT_REDACTED]` |
| M15 | Bearer 토큰 | Authorization: Bearer *** | `[BEARER_TOKEN_REDACTED]` |
| M16 | Generic API 키 | api_key/secret_key/access_token = 형태 | `[REDACTED]` |
| M17 | Private Key 블록 | -----BEGIN ... PRIVATE KEY----- | `[PRIVATE_KEY_REDACTED]` |
| M18 | EC2 인스턴스 ID | i-xxxxxxxxxxxxxxxx | `[INSTANCE_ID_REDACTED]` |
| M19 | AMI ID | ami-xxxxxxxxxxxxxxxx | `[AMI_ID_REDACTED]` |
| M20 | EC2 IMDS 주소 | 169.254.169.254 | `[IMDS_REDACTED]` |
| M21 | 사설 IP (10.x) | RFC 1918 | `[PRIVATE_IP_REDACTED]` |
| M22 | 사설 IP (172.16-31.x) | RFC 1918 | `[PRIVATE_IP_REDACTED]` |
| M23 | 사설 IP (192.168.x) | RFC 1918 | `[PRIVATE_IP_REDACTED]` |


### 6-6. hook 적용 범위 외 영역

pre_tool_call hook은 Hermes AI가 도구를 호출하는 순간에만 개입한다.
아래 경우에는 hook이 전혀 영향을 주지 않는다.
(모델 입력 자체에 개입할 수 있는 pre_gateway_dispatch 는 6-1 표 2.5단계 참조)

| 영역 | 설명 |
|------|------|
| 사용자 직접 실행 | 사용자가 서버 터미널에서 직접 입력하는 모든 명령 |
| cron 스크립트 | Hermes cron 또는 시스템 cron이 독립적으로 실행하는 스크립트 |
| 백그라운드 프로세스 | 이미 실행 중인 프로세스가 내부적으로 호출하는 명령 |
| Gateway 내부 동작 | Hermes gateway가 직접 실행하는 subprocess.run 등 내부 로직 |
| 모델 판단 전 입력 | **[2026-08-09 정정] LG(prompt-gate)로 채워졌다.** 단 기본이 observe 모드라 아직 차단하지 않고, gateway 인바운드 경로에만 걸린다. CLI·cron·슬래시 커맨드는 여전히 이 필터 밖이다 |


### 6-7. hook 밖에 있는 방어 장치 [2026-08-06 추가]

아래 두 개는 L0~L3 4계층 모델에 포함되지 않은 별개 경로다.
agent.log 에서 실제 동작이 확인됐다.

| 방어 | 구현 위치 | 동작 |
|------|-----------|------|
| workdir 검증 | tools/terminal_tool.py | 셸 메타문자가 포함된 workdir 를 차단. 로그 예: `Blocked dangerous workdir: /home/hermes"` |
| 내장 security scan | Hermes 코어 | 위험 요소를 등급으로 표시. 로그 예: `Security scan — [MEDIUM] URL uses raw IP address` |

또한 config.yaml 에 `hooks_auto_accept: true` 가 설정돼 있다.
이 문서에 기술되지 않았던 항목이다.
