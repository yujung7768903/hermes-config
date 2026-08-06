# Hermes Teams 연동 설정 가이드 (시행착오 정리)

## 최종 구성 요약

- Hermes Gateway + Microsoft Teams Bot Framework
- devtunnel 로 로컬 서버를 외부에 노출
- systemd 로 devtunnel, hermes-gateway 자동 실행

---

## 1. 사전 준비

### 1-1. Teams 플랫폼 플러그인 활성화

`~/.hermes/config.yaml` 에서 plugins.enabled 에 추가:

```yaml
plugins:
  enabled:
    - deploy-log
    - teams-platform
```

### 1-2. microsoft-teams-apps 패키지 설치

기본 설치에는 Teams SDK 가 포함되어 있지 않아서 수동 설치 필요:

```bash
~/.hermes/hermes-agent/venv/bin/pip install microsoft-teams-apps aiohttp
```

설치 후 gateway 재시작하면 로그에 아래가 뜨면 정상:

```
Teams app initialized successfully
Webhook server listening on 0.0.0.0:3978/api/messages
✓ teams connected
```

### 1-3. .env 설정

`~/.hermes/.env` 에 아래 항목 추가:

```env
TEAMS_CLIENT_ID=<Azure AD 앱 클라이언트 ID>
TEAMS_CLIENT_SECRET=<Azure AD 클라이언트 시크릿>
TEAMS_TENANT_ID=<Azure AD 테넌트 ID>
TEAMS_ALLOWED_USERS=<본인 Azure AD Object ID (UUID 형식)>
```

#### TEAMS_ALLOWED_USERS 확인 방법

Graph Explorer 에서 확인 (관리자 권한 불필요):

1. https://developer.microsoft.com/en-us/graph/graph-explorer 접속
2. Teams 계정으로 로그인
3. `GET https://graph.microsoft.com/v1.0/me` 실행
4. 응답의 `id` 필드 값 사용

#### TEAMS_TENANT_ID 주의사항

Azure AD 앱 등록의 지원 계정 유형에 따라 다르게 설정:

- 단일 테넌트 앱: 실제 tenant ID 사용
- 여러 조직 (Multi-tenant) 앱: `common` 사용 → **단, 송신 인증 실패 발생 가능**

→ **결론: 앱 등록을 단일 테넌트로 맞추고 실제 tenant ID 사용 권장**

---

## 2. Azure AD 앱 등록 및 Bot Framework 설정

### 2-1. Azure AD 앱 확인

Azure Portal (portal.azure.com) > 앱 등록:

- 지원되는 계정 유형: **이 조직 디렉터리의 계정만** (단일 테넌트 권장)
- 클라이언트 시크릿이 만료되지 않았는지 확인

### 2-2. Bot Framework 등록

https://dev.botframework.com/bots/new

| 항목 | 값 |
|------|-----|
| Display name | 원하는 봇 이름 (예: bori) |
| Messaging endpoint | `https://<devtunnel-url>/api/messages` |
| App type | Single Tenant |
| App ID | Azure AD 앱의 클라이언트 ID |
| App Tenant ID | Azure AD 테넌트 ID |

> Teams 앱에서 보이는 봇 이름은 Bot Framework 의 Display name 이 아닌
> Azure AD 앱 등록의 표시 이름을 따릅니다.

### 2-3. Teams 채널 연결

Bot Framework > 해당 봇 > Channels > Microsoft Teams 추가 (Messaging 탭만 선택)

---

## 3. devtunnel 설정

Teams Bot Framework 는 웹훅 방식이라 외부에서 접근 가능한 공개 URL 이 필요합니다.

### 3-1. devtunnel 설치 및 로그인

```bash
# 로그인
devtunnel user login
```

### 3-2. 터널 생성 및 포트 설정

```bash
# 터널 생성 (최초 1회)
devtunnel create hankook-bot

# 포트 설정 - 반드시 http 프로토콜로 설정 (https 로 하면 502 발생)
devtunnel port create hankook-bot -p 3978 --protocol http

# 익명 접근 허용 (Bot Framework 가 인증 없이 접근해야 하므로 필수)
devtunnel access create hankook-bot --anonymous
```

> ⚠️ 포트를 `https` 로 설정하면 502 Bad Gateway 발생
> 반드시 `--protocol http` 로 설정해야 함

### 3-3. devtunnel URL 확인

```bash
devtunnel show hankook-bot
```

출력된 URL (예: `https://jlk9mcwr-3978.jpe1.devtunnels.ms`) 을
Bot Framework Messaging endpoint 에 `/api/messages` 붙여서 등록:

```
https://jlk9mcwr-3978.jpe1.devtunnels.ms/api/messages
```

### 3-4. systemd 서비스 등록 (재부팅 후 자동 시작)

```bash
cat > ~/.config/systemd/user/devtunnel.service << 'EOF'
[Unit]
Description=DevTunnel hankook-bot
After=network.target

[Service]
ExecStart=/usr/local/bin/devtunnel host hankook-bot
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now devtunnel.service
```

로그 확인:

```bash
journalctl --user -u devtunnel.service -f
```

---

## 4. 동작 확인

```bash
# devtunnel URL 응답 확인 (405 가 정상 - POST 만 허용)
curl -s -o /dev/null -w "%{http_code}" https://<devtunnel-url>/api/messages
# → 405 이면 정상

# gateway 상태 확인
hermes gateway status

# gateway 로그 확인
hermes logs
journalctl --user -u hermes-gateway.service -f
```

---

## 5. 시행착오 목록

| 증상 | 원인 | 해결 |
|------|------|------|
| Teams 플랫폼이 gateway 에 로드 안 됨 | `microsoft-teams-apps` 패키지 미설치 | venv 에 수동 pip install |
| config.yaml 에 teams 없음 | plugins.enabled 에 teams-platform 누락 | config.yaml 에 teams-platform 추가 |
| devtunnel URL 502 Bad Gateway | 포트 프로토콜을 https 로 설정 | `--protocol http` 로 재생성 |
| Bot Framework 에서 연결 안 됨 | devtunnel anonymous 접근 미허용 | `devtunnel access create --anonymous` |
| Teams 메시지 수신은 되나 응답 전송 401 | TEAMS_TENANT_ID=common + 단일테넌트 앱 불일치 | tenant ID 를 실제 값으로 변경 |
| TEAMS_ALLOWED_USERS 에 Slack ID 입력 | 잘못된 ID 형식 | Azure AD Object ID (UUID) 로 변경 |
| gateway 에 Teams 있지만 Client connections 0 | devtunnel 이 http 가 아닌 https 프로토콜 | 포트 삭제 후 http 로 재생성 |

---

## 6. 유용한 명령어 모음

```bash
# gateway 재시작
hermes gateway restart

# gateway 상태
hermes gateway status

# gateway 로그 실시간
journalctl --user -u hermes-gateway.service -f

# devtunnel 상태
devtunnel show hankook-bot

# devtunnel 로그 실시간
journalctl --user -u devtunnel.service -f

# devtunnel 재시작
systemctl --user restart devtunnel.service
```
