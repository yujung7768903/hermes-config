"""security_log 유틸 + security_guard / security-filter 연동 통합 테스트"""
import importlib.util, sys, os
from pathlib import Path
from datetime import datetime, timezone

HOOKS_DIR   = Path.home() / ".hermes" / "hooks"
PLUGIN_FILE = Path.home() / ".hermes/plugins/security-filter/security-filter/__init__.py"
LOG_DIR     = Path.home() / ".hermes/logs/security"

# security_log 임포트
sys.path.insert(0, str(HOOKS_DIR))
from security_log import write as log_write, _today_file, _rotate, _LOG_DIR, _KEEP_DAYS

# security-filter 임포트
spec = importlib.util.spec_from_file_location("sf", str(PLUGIN_FILE))
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mask = mod._mask_sensitive

print("=== 1. 로그 디렉토리 생성 확인 ===")
log_write("TEST", tool="test", platform="cli", session="test-session",
          rule="테스트 규칙", detail="통합 테스트 실행")
today_file = _today_file()
assert today_file.exists(), f"로그 파일 생성 실패: {today_file}"
print(f"  OK  {today_file}")

print("\n=== 2. 로그 포맷 확인 ===")
lines = today_file.read_text(encoding="utf-8").splitlines()
last = lines[-1]
print(f"  마지막 라인: {last}")
assert "TEST" in last and "테스트 규칙" in last, "포맷 오류"
print("  OK  포맷 정상")

print("\n=== 3. MASKED 이벤트 기록 (security-filter 경유) ===")
result = mask(tool_name="read_file", result="DISCORD_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4.GxxXxX.abcdefghijklmnopqrstuvwxyz1234")
assert result is not None and "REDACTED" in result, "마스킹 실패"
lines_after = today_file.read_text(encoding="utf-8").splitlines()
masked_lines = [l for l in lines_after if "MASKED" in l]
assert masked_lines, "MASKED 로그 미기록"
print(f"  OK  {masked_lines[-1]}")

print("\n=== 4. rotation — 14일 초과 파일 삭제 확인 ===")
from datetime import timedelta
# 15일 전 파일 생성
old_file = LOG_DIR / "2000-01-01.log"
old_file.write_text("old log\n", encoding="utf-8")
_rotate()
assert not old_file.exists(), "오래된 파일이 삭제되지 않음"
print(f"  OK  2000-01-01.log 삭제됨")

# 오늘 파일은 남아있어야 함
assert today_file.exists(), "오늘 파일이 삭제됨"
print(f"  OK  {today_file.name} 유지됨")

# 13일 전 파일은 남아야 함
recent_date = (datetime.now(timezone.utc).date() - timedelta(days=13)).strftime("%Y-%m-%d")
recent_file = LOG_DIR / f"{recent_date}.log"
recent_file.write_text("recent log\n", encoding="utf-8")
_rotate()
assert recent_file.exists(), f"13일 전 파일({recent_date})이 삭제됨"
recent_file.unlink()  # 정리
print(f"  OK  {recent_date}.log (13일 전) 유지됨")

print(f"\n총 보관 기간: {_KEEP_DAYS}일")
print(f"로그 위치: {LOG_DIR}")
print("\n모든 테스트 통과!")
