#!/usr/bin/env python3
"""
pre_gateway_dispatch 훅 — 사용자 메시지 트리거 처리

트리거 1: "반가워. 헤르메스야!"
  → /home/hermes/.hermes/skills/delete_test 가 있으면 삭제, 없으면 생성

트리거 2: "아오 [이름]"
  → /home/hermes/.hermes/profiles/<이름>/ 생성 (한글이면 영문 변환)

반환값:
  None          → 정상 dispatch 계속
  {"action": "skip"} → 메시지 무시 (사용 안 함)
  {"action": "rewrite", "text": "..."} → 메시지 교체 (사용 안 함)
"""

import json
import os
import re
import shutil
import sys

# ── stdin 파싱 ────────────────────────────────────────────────────────────────
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# pre_gateway_dispatch payload: event 객체의 text 추출
event = payload.get("extra", {}).get("event") or {}
if isinstance(event, dict):
    text = str(event.get("text", ""))
else:
    text = ""

# text가 없으면 extra 직접 탐색
if not text:
    text = str(payload.get("extra", {}).get("text", ""))

if not text:
    sys.exit(0)

BASE_SKILLS = "/home/hermes/.hermes/skills"
BASE_PROFILES = "/home/hermes/.hermes/profiles"

# ── 트리거 1: "반가워. 헤르메스야!" ────────────────────────────────────────────
if "반가워. 헤르메스야!" in text:
    target = os.path.join(BASE_SKILLS, "delete_test")
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
        # 결과를 로그에 남김
        with open("/tmp/trigger_log.txt", "a") as f:
            f.write(f"[TRIGGER1] delete_test 삭제됨\n")
    else:
        os.makedirs(os.path.join(target), exist_ok=True)
        skill_md = os.path.join(target, "SKILL.md")
        with open(skill_md, "w") as f:
            f.write("---\nname: delete_test\ndescription: \"delete_test 스킬\"\n---\n")
        with open("/tmp/trigger_log.txt", "a") as f:
            f.write(f"[TRIGGER1] delete_test 생성됨\n")
    sys.exit(0)

# ── 한글 → 영문 변환 테이블 ────────────────────────────────────────────────────
HANGUL_MAP = {
    "가": "ga", "나": "na", "다": "da", "라": "ra", "마": "ma",
    "바": "ba", "사": "sa", "아": "a", "자": "ja", "차": "cha",
    "카": "ka", "타": "ta", "파": "pa", "하": "ha",
    "각": "gak", "낙": "nak", "닥": "dak", "락": "rak", "막": "mak",
    "각": "gak", "간": "gan", "강": "gang", "갈": "gal", "감": "gam", "갑": "gap",
    "나": "na", "남": "nam", "낭": "nang", "납": "nap",
    "태": "tae", "영": "yeong", "지": "ji", "수": "su", "민": "min",
    "준": "jun", "현": "hyeon", "우": "u", "서": "seo", "진": "jin",
    "혁": "hyeok", "호": "ho", "성": "seong", "연": "yeon", "재": "jae",
    "기": "gi", "동": "dong", "원": "won", "석": "seok", "정": "jeong",
    "훈": "hun", "철": "cheol", "수": "su", "경": "gyeong", "환": "hwan",
    "헤": "he", "르": "reu", "메": "me", "스": "seu",
    "테": "te", "트": "teu",
    "오": "o", "이": "i", "유": "yu", "으": "eu",
    "시": "si", "미": "mi", "리": "ri", "니": "ni", "비": "bi",
    "키": "ki", "티": "ti", "피": "pi", "히": "hi",
}

def hangul_to_roman(text: str) -> str:
    """간단한 한글 → 영문 변환 (음절 단위)"""
    result = []
    for ch in text:
        if ch in HANGUL_MAP:
            result.append(HANGUL_MAP[ch])
        elif "\uAC00" <= ch <= "\uD7A3":
            # 유니코드 한글 분해
            code = ord(ch) - 0xAC00
            cho = code // (21 * 28)
            jung = (code % (21 * 28)) // 28
            jong = code % 28
            CHO = ["g","kk","n","d","tt","r","m","b","pp","s","ss","","j","jj","ch","k","t","p","h"]
            JUNG = ["a","ae","ya","yae","eo","e","yeo","ye","o","wa","wae","oe","yo","u","wo","we","wi","yu","eu","ui","i"]
            JONG = ["","k","kk","ks","n","nj","nh","t","ll","lm","lp","ls","lt","lp","lh","m","p","ps","s","ss","ng","j","ch","k","t","p","h"]
            result.append(CHO[cho] + JUNG[jung] + JONG[jong])
        else:
            result.append(ch)
    return "".join(result)

def normalize_profile_name(name: str) -> str:
    """프로필 이름 정규화: 한글→영문, 소문자, 공백→하이픈"""
    name = name.strip()
    # 한글 포함 여부 확인
    if re.search(r"[\uAC00-\uD7A3]", name):
        name = hangul_to_roman(name)
    name = name.lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    return name

# ── 트리거 2: "아오 [이름]" ────────────────────────────────────────────────────
ao_match = re.search(r"아오\s+(.+?)(?:\s*$|[.!?])", text.strip())
if ao_match:
    raw_name = ao_match.group(1).strip()
    profile_name = normalize_profile_name(raw_name)

    if profile_name:
        profile_path = os.path.join(BASE_PROFILES, profile_name)
        if os.path.isdir(profile_path):
            with open("/tmp/trigger_log.txt", "a") as f:
                f.write(f"[TRIGGER2] 프로필 이미 존재: {profile_name}\n")
        else:
            for subdir in ["skills", "plugins", "cron", "memories"]:
                os.makedirs(os.path.join(profile_path, subdir), exist_ok=True)
            with open("/tmp/trigger_log.txt", "a") as f:
                f.write(f"[TRIGGER2] 프로필 생성됨: {profile_name}\n")

sys.exit(0)
