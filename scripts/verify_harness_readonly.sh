#!/usr/bin/env bash
# 하네스 동결이 실제로 걸려 있는지 검사한다. 서버에서 ec2-user 로 실행한다.
#
#   ssh ai-agent 'bash -s' < scripts/verify_harness_readonly.sh
#
# 실행 중인 게이트웨이의 마운트 네임스페이스 안으로 들어가 확인하므로,
# 유닛 설정이 아니라 지금 돌고 있는 프로세스에 실제로 적용된 상태를 본다.
# pipefail 은 쓰지 않는다 — 차단 판정은 touch 의 종료코드가 아니라 출력 문자열로
# 하는데, 파이프라인에 pipefail 을 걸면 grep 이 매칭해도 touch 의 실패코드가 남아
# 정반대로 판정된다.
set -u

H=/home/hermes/.hermes
PID=$(pgrep -f "hermes gateway run" | head -1)
[ -n "$PID" ] || { echo "FAIL: 게이트웨이 프로세스 없음"; exit 1; }

# 네임스페이스 안에서 hermes 로 실행
inns() { sudo nsenter -t "$PID" -m runuser -u hermes -- "$@" 2>&1; }

fail=0
check_ro() {  # 쓰기가 막혀야 하는 경로
  local out; out=$(inns touch "$1")
  case "$out" in
    *"Read-only file system"*) echo "  OK   차단됨: $1" ;;
    *) echo "  FAIL 쓰기 가능: $1 ($out)"; fail=1; inns rm -f "$1" >/dev/null ;;
  esac
}
check_rw() {  # 런타임상 쓰기가 돼야 하는 경로
  local out; out=$(inns touch "$1")
  if [ -z "$out" ]; then
    echo "  OK   쓰기가능: $1"; inns rm -f "$1" >/dev/null
  else
    echo "  FAIL 차단됨: $1 ($out)"; fail=1
  fi
}

echo "동결 대상 (쓰기 차단돼야 함)"
for p in config.yaml SOUL.md .env hooks/.probe scripts/.probe plugins/.probe \
         skills/.probe memories/.probe hermes-agent/.probe .git/.probe; do
  check_ro "$H/$p"
done

echo "런타임 경로 (쓰기 가능해야 함)"
for p in cron/.probe logs/.probe sessions/.probe cache/.probe .probe; do
  check_rw "$H/$p"
done

echo "읽기 (가능해야 함)"
if inns head -1 "$H/config.yaml" >/dev/null; then echo "  OK   config.yaml 읽기"; else echo "  FAIL config.yaml 읽기"; fail=1; fi
if inns head -1 "$H/SOUL.md"    >/dev/null; then echo "  OK   SOUL.md 읽기";    else echo "  FAIL SOUL.md 읽기";    fail=1; fi

echo "권한 상승 (차단돼야 함)"
# nsenter 로 들어간 프로세스는 no_new_privs 를 물려받지 않으므로 명령을 실행해
# 보는 것으로는 판정할 수 없다. 게이트웨이 프로세스의 플래그를 직접 읽는다.
if sudo grep -q "^NoNewPrivs:[[:space:]]*1" "/proc/$PID/status"; then
  echo "  OK   NoNewPrivs=1 (게이트웨이 하위에서 sudo 불가)"
else
  echo "  FAIL NoNewPrivs 미설정"; fail=1
fi

[ "$fail" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$fail"
