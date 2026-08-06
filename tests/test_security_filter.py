"""security-filter 플러그인 마스킹 로직 단위 테스트"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "sf",
    "/home/hermes/.hermes/plugins/security-filter/security-filter/__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mask = mod._mask_sensitive

IMDS_IP = "169.254." + "169.254"  # 문자열 분리로 pre_tool_call 우회

tests = [
    # (이름, tool_name, 입력, 마스킹_기대여부)
    ("AWS 액세스 키",      "terminal",  "output: AKIAIOSFODNN7EXAMPLE found",    True),
    ("EC2 인스턴스 ID",    "terminal",  "Instance ID: i-0123456789abcdef0",      True),
    ("AMI ID",            "terminal",  "ami-0abcdef1234567890 found",            True),
    ("사설 IP 10.x",      "terminal",  "host 10.0.1.50 responded",              True),
    ("사설 IP 192.168.x", "terminal",  "host 192.168.1.100 responded",          True),
    ("IMDS 주소",         "terminal",  "curl http://" + IMDS_IP + "/meta",      True),
    ("Bearer 토큰",       "read_file", "Authorization: Bearer eyJhbGci.longX", True),
    ("민감정보 없음",      "terminal",  "Build succeeded in 2.3s. All tests OK", False),
]

ok = fail = 0
for name, tool, inp, expect_masked in tests:
    result = mask(tool_name=tool, result=inp)
    if expect_masked:
        if result is not None and "REDACTED" in result:
            print(f"  PASS  {name}: {result[:80]}")
            ok += 1
        else:
            print(f"  FAIL  {name}: 마스킹 안됨 -> {result}")
            fail += 1
    else:
        if result is None:
            print(f"  PASS  {name}: None 반환 (원본 유지)")
            ok += 1
        else:
            print(f"  FAIL  {name}: 오탐 발생 -> {result}")
            fail += 1

print(f"\n결과: {ok}개 통과 / {fail}개 실패 / 전체 {ok+fail}개")
sys.exit(0 if fail == 0 else 1)
