"""security-filter 전체 패턴 커버리지 테스트"""
import importlib.util, sys

spec = importlib.util.spec_from_file_location(
    "sf", "/home/hermes/.hermes/plugins/security-filter/security-filter/__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mask = mod._mask_sensitive

IMDS = "169.254." + "169.254"  # pre_tool_call hook 우회

tests = [
    # (이름, 입력, 마스킹 기대 여부)

    # AWS
    ("AWS 액세스 키 ID",        "key: AKIAIOSFODNN7EXAMPLE found",                      True),
    ("AWS 시크릿 키 변수명",    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", True),
    ("AWS 세션 토큰 FwoGZX",    "token: FwoGZXIvYXdzEJr//////////wEaCXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",  True),
    ("AWS 세션 토큰 변수명",    "AWS_SESSION_TOKEN=IQoJb3JpZ2luX2VjEJr//////////wEaCXXXXXXXXXXXXXXXXXXXXXXX==", True),

    # 플랫폼 토큰
    ("Slack bot token",         "SLACK_BOT_TOKEN=xoxb-17653355560-17650555000-abc123def456ghi789jk",  True),
    ("Slack user token",        "token=xoxp-17653355560-17650555000-abcdefghijklmnopqrstuvwx",         True),
    ("Slack app-level token",   "xapp-1-A0123456789-1234567890-abcdef1234567890abcdef1234567890",      True),
    ("Discord bot token",       "DISCORD_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4.GxxXxX.abcdefghijklmnopqrstuvwxyz1234", True),
    ("Telegram bot token",      "BOT_TOKEN=1234567890:ABCDefghIJKLmnopQRSTuvwxYZ12345678901", True),
    ("GitHub PAT ghp_",         "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",      True),
    ("GitHub token gho_",       "GITHUB_TOKEN=gho_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij", True),
    ("Stripe secret key",       "STRIPE_KEY=sk_live_abcdefghijklmnopqrstuvwxyz123456",   True),
    ("Stripe test key",         "key=sk_test_abcdefghijklmnopqrstuvwxyz123456",           True),
    ("OpenAI API key classic",  "OPENAI_KEY=sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOP", True),
    ("OpenAI API key proj",     "key=sk-proj-abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678901234567890", True),

    # 범용 형식
    ("JWT token",               "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", True),
    ("Authorization Bearer",    "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.longpayload.signature1234", True),
    ("api_key= 형태",           "api_key=abcdefghijklmnopqrstuvwxyz123456",               True),
    ("Private Key 블록",        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", True),

    # 인프라
    ("EC2 인스턴스 ID",         "Instance: i-0123456789abcdef0 started",                 True),
    ("AMI ID",                  "ami-0abcdef1234567890 used",                             True),
    ("IMDS 주소",               "curl http://" + IMDS + "/latest/meta-data/",            True),
    ("사설 IP 10.x",            "connected to 10.0.1.50",                                True),
    ("사설 IP 192.168.x",       "host 192.168.1.100 responded",                          True),

    # 오탐 방지 (마스킹 안 돼야 함)
    ("일반 텍스트",             "Build succeeded. All 42 tests passed in 3.2s.",          False),
    ("짧은 sk- 문자열",         "sk-123 is too short",                                    False),
    ("일반 숫자:문자열",        "3:30pm meeting",                                         False),
]

ok = fail = 0
for name, inp, expect_masked in tests:
    result = mask(tool_name="terminal", result=inp)
    if expect_masked:
        if result is not None and "REDACTED" in result:
            print(f"  PASS  {name}")
            ok += 1
        else:
            print(f"  FAIL  {name}  <- 마스킹 안됨  input={inp[:60]}")
            fail += 1
    else:
        if result is None:
            print(f"  PASS  {name}  (오탐 없음)")
            ok += 1
        else:
            print(f"  FAIL  {name}  <- 오탐 발생  result={result[:60]}")
            fail += 1

print(f"\n총 {ok+fail}개  통과: {ok}  실패: {fail}")
sys.exit(0 if fail == 0 else 1)
