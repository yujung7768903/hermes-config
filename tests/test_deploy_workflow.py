"""deploy.yml 검증 — 워크플로우 설정과, SSM 에 보낼 명령이 실제로 조립되는지

YAML 블록 스칼라 안의 히어독은 들여쓰기·인용이 어긋나면 조용히 깨진다. 깨진 채로
배포되면 원격에서 빈 명령이나 반쯤 확장된 명령이 root 로 돈다. 그래서 파싱만 보지
않고 조립 단계를 실제로 실행해 params.json 을 확인한다.

실행: python3 tests/test_deploy_workflow.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "deploy.yml")

spec = yaml.safe_load(open(WORKFLOW))
# PyYAML 은 `on:` 을 불리언 True 로 읽는다 (GitHub 파서는 안 그렇다)
triggers = spec.get("on", spec.get(True))
steps = spec["jobs"]["deploy"]["steps"]

ok = fail = 0


def check(desc, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  PASS  {desc}")
    else:
        fail += 1
        print(f"  FAIL  {desc}\n        기대: {want!r}\n        실제: {got!r}")


print("── 워크플로우 설정 ──")
check("main push 에서만 발동", triggers["push"]["branches"], ["main"])
check("OIDC 토큰 권한", spec["permissions"]["id-token"], "write")
check("동시 실행을 취소하지 않고 줄 세움",
      spec["concurrency"]["cancel-in-progress"], False)
check("전체 히스토리 체크아웃 (before..after 범위용)",
      steps[0]["with"]["fetch-depth"], 0)
check("게이트를 워크플로우 안에 복제하지 않고 스크립트 호출",
      "scripts/deploy_gate.sh" in steps[1]["run"], True)

# 인스턴스 정보·역할 ARN 이 본문에 노출되면 안 된다 — 이 저장소는 서버로 pull 되고
# 에이전트가 읽을 수 있다.
body = open(WORKFLOW).read()
import re
check("인스턴스 ID 리터럴 없음", bool(re.search(r"\bi-[0-9a-f]{8,}", body)), False)
check("계정 ID 로 보이는 12자리 숫자 없음", bool(re.search(r"\b\d{12}\b", body)), False)
for name in ("AWS_ROLE_ARN", "AWS_REGION", "HERMES_INSTANCE_ID"):
    check(f"{name} 은 시크릿 참조", f"secrets.{name}" in body, True)

# ── SSM 명령 조립을 실제로 실행 ────────────────────────────────────────────
run = steps[3]["run"]
head = run.split("CMD=$(aws ssm send-command")[0]
check("aws 호출 앞부분을 분리함", head != run, True)


def build(restart):
    tmp = tempfile.mkdtemp(prefix="deploy-wf-test-")
    env = dict(os.environ, RESTART=restart, INSTANCE_ID="i-test", SHA="0" * 40)
    r = subprocess.run(["bash", "-c", head], cwd=tmp, env=env,
                       capture_output=True, text=True)
    path = os.path.join(tmp, "params.json")
    data = json.load(open(path)) if os.path.exists(path) else None
    shutil.rmtree(tmp, ignore_errors=True)
    return r, data


print("\n── SSM 명령 조립 (restart=1) ──")
r, data = build("1")
check("조립 성공", r.returncode, 0)
check("commands 배열", isinstance(data and data.get("commands"), list), True)
cmds = "\n".join(data["commands"]) if data else ""
check("push 봉인 포함",
      "config remote.origin.pushurl DISABLED-BY-POLICY" in cmds, True)
check("ff-only 머지 포함", "merge --ff-only origin/main" in cmds, True)
check('$R 이 러너에서 확장되지 않음', 'git -C "$R"' in cmds, True)
check("as_hermes 함수의 $@ 가 남아 있음", 'sudo -u hermes -H git -C "$R" "$@"' in cmds, True)
check("__RESTART__ 치환됨", "__RESTART__" in cmds, False)
check("재시작 분기가 참", '"1" = "1"' in cmds, True)
check("systemctl restart 포함", "systemctl restart hermes-gateway" in cmds, True)

print("\n── SSM 명령 조립 (restart=0) ──")
r, data = build("0")
cmds = "\n".join(data["commands"]) if data else ""
check("조립 성공", r.returncode, 0)
check("재시작 분기가 거짓", '"0" = "1"' in cmds, True)
check("머지는 그대로 수행", "merge --ff-only origin/main" in cmds, True)

print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(0 if fail == 0 else 1)
