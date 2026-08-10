#!/usr/bin/env bash
# 이 push 를 서버에 반영해도 되는가를 판정한다. GitHub Actions 러너에서 실행된다.
#
#   사용:  bash scripts/deploy_gate.sh <before-sha> <after-sha>
#   출력:  restart=0|1  (stdout, $GITHUB_OUTPUT 에 그대로 붙일 형식)
#   종료:  0 = 반영해도 됨, 1 = 허용 밖 author 가 섞여 있어 반영 불가
#
# 판정을 워크플로우 YAML 안에 인라인으로 쓰지 않고 이 파일로 뺀 이유는 테스트다
# (tests/test_deploy_gate.py). 무엇이 프로덕션에 닿을지를 정하는 게이트라서
# 눈으로 읽고 넘기는 대신 실행되는 검증을 붙였다.

set -euo pipefail

BEFORE=${1:-}
AFTER=${2:-HEAD}

# 이 이메일이 author 인 커밋만 반영한다.
# author 는 위조 가능하므로 이건 보안 경계가 아니라 오배포 방지다 — 실제 경계는
# GitHub 저장소의 push 권한이다.
ALLOWED_AUTHORS=(
  yu-jung0422@hankookilbo.com
  yu-jung31476@naver.com
  68562176+yujung7768903@users.noreply.github.com
)

# 이 경로가 바뀌었을 때만 게이트웨이를 재시작한다.
# hooks/ 는 도구 호출마다 새 subprocess 로 실행되므로 재시작이 필요 없고,
# docs/·tests/·.github/ 는 런타임이 읽지 않는다.
# 불필요한 재시작은 진행 중인 세션을 끊는다.
RESTART_PATHS='^(config\.yaml|SOUL\.md|plugins/|skills/|memories/)'

# 브랜치 생성 push 는 before 가 40개의 0 이고, force push 나 얕은 클론에서는
# before 가 이 저장소에 없는 sha 일 수 있다. 둘 다 검사 범위를 HEAD 커밋 하나로
# 좁힌다 — 범위를 못 구했다고 통째로 통과시키면 게이트가 무의미해진다.
base=""
if [[ -n $BEFORE && ! $BEFORE =~ ^0+$ ]] && git cat-file -e "$BEFORE^{commit}" 2>/dev/null; then
  base=$BEFORE
elif git cat-file -e "$AFTER~1^{commit}" 2>/dev/null; then
  base="$AFTER~1"
  echo "before($BEFORE) 를 쓸 수 없어 $AFTER~1 로 대체했다" >&2
else
  echo "before($BEFORE) 도 $AFTER~1 도 없어 $AFTER 커밋 하나만 검사한다" >&2
fi

if [[ -z $base ]]; then
  authors=$(git log -1 --format='%H %ae' "$AFTER")
  changed=$(git show --name-only --format= "$AFTER")
else
  authors=$(git log --format='%H %ae' "$base..$AFTER")
  changed=$(git diff --name-only "$base" "$AFTER")
fi

foreign=""
count=0
while read -r sha email; do
  [[ -z $sha ]] && continue
  count=$((count + 1))
  allowed=0
  for a in "${ALLOWED_AUTHORS[@]}"; do
    [[ $email == "$a" ]] && { allowed=1; break; }
  done
  (( allowed )) || foreign+="${sha:0:8} <$email>"$'\n'
done <<<"$authors"

if [[ -n $foreign ]]; then
  {
    echo "허용 밖 author 가 섞여 있어 반영하지 않는다:"
    echo "$foreign"
    echo "허용 목록: ${ALLOWED_AUTHORS[*]}"
    echo
    echo "내 커밋이 위에 얹혀 있다고 아래 커밋을 같이 반영하면 이 필터가 무의미해지므로,"
    echo "이 push 전체를 보류한다. 서버 HEAD 는 그대로다."
  } >&2
  exit 1
fi

echo "검사한 커밋 $count 개 — author 전부 허용 목록" >&2
echo "변경 파일:" >&2
echo "$changed" | sed 's/^/  /' >&2

if grep -qE "$RESTART_PATHS" <<<"$changed"; then
  echo "restart=1"
else
  echo "restart=0"
fi
