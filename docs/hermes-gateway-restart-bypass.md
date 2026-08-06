# Hermes Gateway Restart — systemd 우회 문제

## 배경

`approval.py`에는 에이전트가 게이트웨이를 직접 종료/재시작하지 못하도록
하드코딩된 deny 규칙이 있다:

```python
(r'\bhermes\s+(?:-{1,2}\S+(?:\s+\S+)?\s+)*gateway\s+(stop|restart)\b',
 "stop/restart hermes gateway (kills running agents)")
```

동일한 의도로 macOS launchctl 우회도 별도 패턴으로 막혀 있다:

```python
(r'\blaunchctl\s+(stop|kickstart|bootout|unload|kill|disable|remove)\b.*\b(hermes|ai\.hermes)\b',
 "stop/restart hermes launchd service (kills running agents)")
```

그러나 **Linux systemd 경로가 누락**되어 있어, 아래 명령이 hardcoded deny를
통과하고 Smart Approval로 넘어간다:

```bash
systemctl --user restart hermes-gateway
systemctl --user stop hermes-gateway
```

Smart Approval이 "stop/restart system service"로 플래그는 달았지만
자동 승인해버려 실제로 게이트웨이가 사용자 동의 없이 재시작됐다.

## 근본 원인

- **열거식 보안 패치**: 발견된 케이스(launchctl)만 막고,
  "게이트웨이를 종료할 수 있는 모든 수단"을 위협 모델로 사고하지 않음
- **macOS 중심 개발 환경**: systemd는 개발자의 경험 범위 밖이었음
- **Smart Approval 과신**: hardcoded deny를 통과하면 AI 자동 판단에 위임하는데,
  이 케이스에서 AI가 위험도를 과소평가함

## 수정 방향

### 1. approval.py — hardcoded deny에 systemd 패턴 추가

파일: `~/.hermes/hermes-agent/tools/approval.py`

기존 launchctl 블록 바로 아래에 추가:

```python
# systemd-driven gateway stop/restart on Linux.
# Mirrors the launchctl guard above for systemd environments.
(r'\bsystemctl\b.*\b(restart|stop|kill)\b.*\bhermes[-_]gateway\b',
 "stop/restart hermes gateway via systemctl (kills running agents)"),
(r'\bsystemctl\b.*\bhermes[-_]gateway\b.*\b(restart|stop|kill)\b',
 "stop/restart hermes gateway via systemctl (kills running agents)"),
```

### 2. 위협 모델 확장 검토

"게이트웨이를 종료할 수 있는 수단" 전체를 열거하고 누락 여부 확인:

| 수단 | 현재 상태 |
|------|-----------|
| `hermes gateway stop/restart` | ✅ 차단 |
| `launchctl stop/kill ...hermes` | ✅ 차단 (macOS) |
| `systemctl restart hermes-gateway` | ❌ 누락 (Linux) |
| `kill $(pgrep hermes)` | ✅ 차단 |
| `pkill hermes` | ✅ 차단 |
| `systemctl --user stop hermes-gateway` | ❌ 누락 (Linux) |

### 3. Smart Approval 보완

systemd service 관련 명령 중 `hermes`가 포함된 경우는
자동 승인 대신 사용자 확인을 강제하도록 Smart Approval 힌트 추가 검토.

## 관련 파일

- `~/.hermes/hermes-agent/tools/approval.py` — deny 규칙 (라인 624~660 부근)
- `~/.hermes/hermes-agent/plugins/platforms/slack/adapter.py` — launchctl 주석 참고

## 발견 경위

2026-07-14, Slack deploy-log 플러그인 개발 중 게이트웨이 재시작이 필요한
상황에서 에이전트가 `systemctl --user restart hermes-gateway`를 사용자 동의
없이 실행. Smart Approval 자동 승인 로그에서 사후 확인됨.
