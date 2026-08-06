"""현재 security-filter 패턴이 각 플랫폼 토큰을 잡는지 검증"""
import importlib.util, re

spec = importlib.util.spec_from_file_location(
    "sf", "/home/hermes/.hermes/plugins/security-filter/security-filter/__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mask = mod._mask_sensitive

# 실제 형태에 가까운 샘플 토큰들
samples = [
    # (이름, 샘플)
    # Slack: xoxb-{숫자}-{숫자}-{24자 alphanumeric}
    ("Slack bot token",       "token=xoxb-17653355560-17650555000-abc123def456ghi789jkl0"),
    ("Slack user token",      "token=xoxp-17653355560-17650555000-abcdefghijklmnopqrstuvwx"),
    ("Slack app-level token", "SLACK_TOKEN=xapp-1-A0123456789-1234567890-abcdef1234567890abcdef1234567890abcdef12345678"),
    # Discord: base64(user_id).timestamp_base64.hmac_base64
    ("Discord bot token",     "DISCORD_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4.GxxXxX.abcdefghijklmnopqrstuvwxyz1234"),
    # Telegram: {bot_id}:{35자 alphanumeric}
    ("Telegram bot token",    "BOT_TOKEN=1234567890:ABCDefghIJKLmnopQRSTuvwxYZ12345678901"),
    # Stripe
    ("Stripe secret key",     "STRIPE_KEY=sk_live_abcdefghijklmnopqrstuvwxyz1234567890"),
    # JWT
    ("JWT token",             "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
]

for name, inp in samples:
    result = mask(tool_name="terminal", result=inp)
    caught = result is not None and "REDACTED" in result
    status = "CAUGHT" if caught else "MISSED"
    print(f"  {status}  {name}")
    if caught:
        print(f"         -> {result[:90]}")
