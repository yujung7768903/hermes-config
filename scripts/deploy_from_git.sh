#!/usr/bin/env bash
# main 에 올라온 관리자 커밋만 서버에 반영한다.
#
#   설치:  sudo /home/hermes/.hermes/scripts/deploy_from_git.sh --install
#   해제:  sudo rm /etc/cron.d/hermes-deploy
#   로그:  /var/log/hermes-deploy.log
#
# 왜 폴링인가 — hermes 서버는 private 서브넷이고 bastion 인바운드 SSH 는 사내 IP 로
# 제한돼 있다. GitHub Actions 러너와 GitHub 웹훅은 둘 다 랜덤 IP 에서 들어오므로
# 보안그룹을 열지 않고는 서버에 도달할 수 없다. 그래서 서버가 1분마다 fetch 한다.
#
# 왜 /opt 로 복사해 쓰는가 — 이 크론은 root 로 돌면서 systemctl restart 를 한다.
# 스크립트를 hermes 소유 트리(~/.hermes/scripts)에서 직접 실행하면, 그 파일을 쓸 수
# 있는 주체가 root 실행을 얻는다. 그래서 root 소유 사본을 실행하고, 사본 갱신은
# 관리자가 --install 을 다시 돌리는 명시적 행위로만 일어난다.

set -euo pipefail

REPO=${HERMES_REPO:-/home/hermes/.hermes}
LOG=${HERMES_DEPLOY_LOG:-/var/log/hermes-deploy.log}
LOCK=${HERMES_DEPLOY_LOCK:-/var/lock/hermes-deploy.lock}
# 빈 문자열도 존중해야 한다 (테스트가 현재 유저로 git 을 돌린다) — :- 가 아니라 -
GIT_AS=${HERMES_GIT_AS-sudo -u hermes -H}
RESTART_CMD=${HERMES_RESTART_CMD-systemctl restart hermes-gateway}

INSTALL_DIR=/opt/hermes-deploy
CRON_FILE=/etc/cron.d/hermes-deploy

# 이 이메일이 author 인 커밋만 반영한다.
# author 는 위조 가능하므로 이건 보안 경계가 아니라 오배포 방지다 — 실제 경계는
# GitHub 저장소의 push 권한이다. 에이전트가 커밋을 올려도, 팀원이 실수로 main 에
# 올려도, 여기서 걸러서 서버에는 안 닿게 한다.
ALLOWED_AUTHORS=(
  yu-jung0422@hankookilbo.com
  yu-jung31476@naver.com
  68562176+yujung7768903@users.noreply.github.com
)

# 이 경로가 바뀌었을 때만 게이트웨이를 재시작한다.
# hooks/ 는 도구 호출마다 새 subprocess 로 실행되므로 재시작이 필요 없고,
# docs/·tests/ 는 런타임이 읽지 않는다. 불필요한 재시작은 진행 중인 세션을 끊는다.
RESTART_PATHS='^(config\.yaml|SOUL\.md|plugins/|skills/|memories/)'

log() { printf '%s  %s\n' "$(date -Is)" "$*" >>"$LOG"; }

# shellcheck disable=SC2086  # GIT_AS 는 의도적으로 단어분리한다
g() { $GIT_AS git -C "$REPO" "$@"; }

install_self() {
  [[ $EUID -eq 0 ]] || { echo "sudo 로 실행하세요" >&2; exit 2; }
  local src
  src=$(realpath "$0")

  install -d -m 755 "$INSTALL_DIR"
  if [[ $src != "$INSTALL_DIR/deploy.sh" ]]; then
    install -m 755 -o root -g root "$src" "$INSTALL_DIR/deploy.sh"
  fi

  cat >"$CRON_FILE" <<'CRON'
# hermes-config main 자동 반영 (deploy_from_git.sh --install 이 생성)
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
* * * * * root /opt/hermes-deploy/deploy.sh >/dev/null 2>&1
CRON
  chmod 644 "$CRON_FILE"

  # 서버 저장소에서 push 를 봉인한다. pushurl 은 fetch/pull 에 영향이 없고,
  # ~/.hermes/.git 은 systemd ReadOnlyPaths 로 동결돼 있어 에이전트가 되돌릴 수 없다.
  g config remote.origin.pushurl DISABLED-BY-POLICY

  touch "$LOG" && chmod 644 "$LOG"
  echo "설치 완료"
  echo "  실행본:  $INSTALL_DIR/deploy.sh"
  echo "  크론:    $CRON_FILE (1분 주기)"
  echo "  로그:    $LOG"
  echo "  push:    $(g config --get remote.origin.pushurl)"
}

[[ ${1:-} == --install ]] && { install_self; exit 0; }

# 겹쳐 실행 방지. fetch 가 느린 날 크론이 쌓이면 같은 머지를 두 번 시도한다.
exec 9>"$LOCK"
flock -n 9 || exit 0

# refspec 을 명시한다 — `fetch origin main` 은 FETCH_HEAD 만 확실히 갱신하고
# refs/remotes/origin/main 갱신은 git 설정에 따라 달라진다.
g fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main' \
  || { log "FETCH-FAIL"; exit 1; }

local_head=$(g rev-parse HEAD)
remote_head=$(g rev-parse origin/main)
[[ $local_head == "$remote_head" ]] && exit 0

# 들어올 커밋 전부를 검사한다. 하나라도 허용 목록 밖이면 통째로 보류한다 —
# 내 커밋이 위에 얹혀 있다고 아래 커밋을 같이 반영하면 필터가 무의미해진다.
foreign=""
while read -r sha email; do
  allowed=0
  for a in "${ALLOWED_AUTHORS[@]}"; do
    [[ $email == "$a" ]] && { allowed=1; break; }
  done
  (( allowed )) || foreign+="${sha:0:8}<$email> "
done < <(g log --format='%H %ae' "$local_head..$remote_head")

if [[ -n $foreign ]]; then
  log "HOLD  허용 밖 author: ${foreign}(HEAD 유지 ${local_head:0:8})"
  exit 0
fi

changed=$(g diff --name-only "$local_head" "$remote_head")
count=$(g rev-list --count "$local_head..$remote_head")

if ! g merge --ff-only --quiet origin/main; then
  log "MERGE-FAIL  ff-only 불가 — 로컬 변경 또는 히스토리 재작성 (HEAD ${local_head:0:8})"
  exit 1
fi
log "DEPLOYED  ${local_head:0:8}..${remote_head:0:8} ($count commits)"

if grep -qE "$RESTART_PATHS" <<<"$changed"; then
  # shellcheck disable=SC2086
  if $RESTART_CMD; then log "RESTART  hermes-gateway"; else log "RESTART-FAIL"; fi
else
  log "NO-RESTART  재시작 불필요 경로만 변경"
fi
