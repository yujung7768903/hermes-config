"""마크다운 표 → Block Kit data_table 변환 검증 (slack-table 플러그인)

플러그인은 mrkdwn 으로 이미 변환된 문자열을 받는다(`format_message` 이후).
그래서 입력 예시도 `**bold**` 가 아니라 `*bold*`, `[t](u)` 가 아니라 `<u|t>` 다.

실행: python3 tests/test_slack_table.py
"""
import asyncio
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# SLACK_TABLE_PLUGIN 으로 다른 버전을 지정할 수 있다. 회귀 테스트가 실제로
# 재현하는지 확인할 때 옛 버전을 겨눠 돌려 본다.
PLUGIN = os.environ.get(
    "SLACK_TABLE_PLUGIN", os.path.join(ROOT, "plugins", "slack-table", "__init__.py"))

spec = importlib.util.spec_from_file_location("slack_table", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

build = mod.build_blocks

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def cell_text(cell):
    """rich_text 셀의 텍스트만 이어붙인다."""
    out = []
    for el in cell["elements"][0]["elements"]:
        out.append(el.get("text") or el.get("url") or "")
    return "".join(out)


TABLE = "| 구분 | 스택 |\n| --- | --- |\n| front | Angular |\n| back | Spring |"


print("── 표 탐지 ──")

check("표 없으면 None", build("그냥 문장입니다.") is None)
check("파이프만 있고 구분선 없으면 None",
      build("a | b\nc | d") is None)
check("빈 문자열 None", build("") is None)

blocks = build(TABLE)
check("표만 있으면 data_table 블록 1개",
      blocks is not None and len(blocks) == 1 and blocks[0]["type"] == "data_table",
      repr(blocks))
check("헤더 + 데이터 행 수", blocks and len(blocks[0]["rows"]) == 3)
check("caption 이 붙는다 (data_table 필수 필드)",
      blocks and blocks[0].get("caption"), repr(blocks[0] if blocks else None))
check("헤더 셀은 raw_text (data_table 첫 행 제약)",
      blocks and blocks[0]["rows"][0][0] == {"type": "raw_text", "text": "구분"},
      repr(blocks[0]["rows"][0][0] if blocks else None))
check("데이터 셀은 rich_text",
      blocks and blocks[0]["rows"][1][0]["type"] == "rich_text")
check("데이터 셀은 bold 없음",
      blocks and "style" not in blocks[0]["rows"][1][0]["elements"][0]["elements"][0])
check("셀 내용 유지",
      blocks and cell_text(blocks[0]["rows"][1][1]) == "Angular")

print("── 앞뒤 텍스트 ──")

# 표 바로 앞의 단독 줄은 본문에 남지 않고 그 표의 caption 으로 올라간다.
# caption 은 data_table 필수 필드이고 Slack 이 표 위에 실제로 렌더하므로,
# 고정값 "표" 를 띄우는 대신 사람이 쓴 그 한 줄을 제목으로 쓴다.
mixed = build(f"확인한 내용입니다.\n\n{TABLE}\n\n결론은 위와 같습니다.")
check("앞줄은 caption 으로 올라가고 section 은 남지 않는다",
      mixed is not None and [b["type"] for b in mixed] == ["data_table", "section"],
      repr([b["type"] for b in mixed] if mixed else None))
check("  └ caption 이 그 줄",
      mixed and mixed[0]["caption"] == "확인한 내용입니다.",
      repr(mixed[0].get("caption") if mixed else None))
check("뒤 텍스트 보존",
      mixed and mixed[1]["text"]["text"] == "결론은 위와 같습니다.")
check("section 은 mrkdwn", mixed and mixed[1]["text"]["type"] == "mrkdwn")

# 문단 한가운데 줄은 가져가지 않는다 — 마지막 줄만 사라지면 문단이 깨진다
para = build(f"설명 첫 줄입니다.\n설명 둘째 줄입니다.\n{TABLE}")
check("문단 뒤 표는 caption 을 뺏어가지 않는다",
      para is not None and [b["type"] for b in para] == ["section", "data_table"],
      repr([b["type"] for b in para] if para else None))
check("  └ 문단이 통째로 남는다",
      para and para[0]["text"]["text"] == "설명 첫 줄입니다.\n설명 둘째 줄입니다.")
check("  └ caption 은 고정값으로 폴백", para and para[1]["caption"] == mod.TABLE_CAPTION)

long_lead = "가" * (mod.CAPTION_MAX + 1)
over = build(f"{long_lead}\n\n{TABLE}")
check("긴 줄은 caption 으로 올리지 않는다",
      over is not None and [b["type"] for b in over] == ["section", "data_table"],
      repr([b["type"] for b in over] if over else None))

lead_md = build(f"*가장* 좋아요 많은 글입니다.\n{TABLE}")
check("caption 은 mrkdwn 마커를 뗀 평문",
      lead_md and lead_md[0]["caption"] == "가장 좋아요 많은 글입니다.",
      repr(lead_md[0].get("caption") if lead_md else None))

two = build(f"{TABLE}\n\n그리고\n\n{TABLE}")
check("표 2개 모두 변환",
      two is not None and [b["type"] for b in two] == ["data_table", "data_table"],
      repr([b["type"] for b in two] if two else None))
check("  └ 두 번째 표만 앞줄을 caption 으로 가져간다",
      two and (two[0]["caption"], two[1]["caption"]) == (mod.TABLE_CAPTION, "그리고"),
      repr([b.get("caption") for b in two] if two else None))

print("── 코드펜스 ──")

fenced = "예시입니다:\n\n```\n" + TABLE + "\n```\n"
check("코드펜스 안의 표는 변환하지 않는다", build(fenced) is None)

print("── 셀 인라인 (mrkdwn) ──")

inline = build(
    "| 항목 | 값 |\n| --- | --- |\n"
    "| 코드 | `adapter.py` |\n"
    "| 링크 | <http://a.b/c|주소> |\n"
    "| 굵게 | *중요* |"
)
rows = inline[0]["rows"] if inline else []
check("인라인 표 변환됨", bool(rows) and len(rows) == 4)
check("백틱 → code 스타일",
      rows and rows[1][1]["elements"][0]["elements"][0]["style"]["code"] is True,
      repr(rows[1][1] if rows else None))
check("백틱 마커는 텍스트에서 제거",
      rows and cell_text(rows[1][1]) == "adapter.py")
link_el = rows[2][1]["elements"][0]["elements"][0] if rows else {}
check("<url|label> → link 요소",
      link_el.get("type") == "link"
      and link_el.get("url") == "http://a.b/c"
      and link_el.get("text") == "주소",
      repr(link_el))
check("*bold* → bold 스타일",
      rows and rows[3][1]["elements"][0]["elements"][0]["style"]["bold"] is True)

emo = build("| :x: | 이유 |\n| --- | --- |\n| 수정 :warning: 금지 | 09:30:00 시작 |")
head = emo[0]["rows"][0][0] if emo else {}
check("헤더의 이모지 코드는 raw_text 로 그대로 남는다",
      head == {"type": "raw_text", "text": ":x:"}, repr(head))
mid_els = emo[0]["rows"][1][0]["elements"][0]["elements"] if emo else []
check("문장 중간 이모지도 분리",
      [e["type"] for e in mid_els] == ["text", "emoji", "text"],
      repr(mid_els))
check("시각(09:30:00)은 이모지로 보지 않는다",
      emo and cell_text(emo[0]["rows"][1][1]) == "09:30:00 시작",
      repr(cell_text(emo[0]["rows"][1][1])) if emo else "")

esc = build("| 기호 | 값 |\n| --- | --- |\n| 비교 | a &lt; b &amp; c |")
check("HTML 이스케이프 복원",
      esc and cell_text(esc[0]["rows"][1][1]) == "a < b & c",
      repr(cell_text(esc[0]["rows"][1][1])) if esc else "")

esc_pipe = build("| a | b |\n| --- | --- |\n| x \\| y | z |")
check("이스케이프된 파이프는 셀 구분자가 아니다",
      esc_pipe and len(esc_pipe[0]["rows"][1]) == 2
      and cell_text(esc_pipe[0]["rows"][1][0]) == "x | y",
      repr(esc_pipe[0]["rows"][1] if esc_pipe else None))

print("── 형태가 어긋난 표 ──")

ragged = build("| a | b |\n| --- | --- |\n| 1 |\n| 1 | 2 | 3 |")
check("열 수는 헤더에 맞춰 정규화",
      ragged and all(len(r) == 2 for r in ragged[0]["rows"]),
      repr([len(r) for r in ragged[0]["rows"]] if ragged else None))
check("모자란 셀은 공백으로 채움",
      ragged and cell_text(ragged[0]["rows"][1][1]).strip() == "")

check("헤더만 있고 데이터 없으면 변환하지 않는다",
      build("| a | b |\n| --- | --- |") is None)

big = "| a | b |\n| --- | --- |\n" + "\n".join(
    f"| {i} | x |" for i in range(mod.MAX_TABLE_ROWS + 5))
check("행 상한 초과 → 변환 포기(None)", build(big) is None)

wide_head = "| " + " | ".join(str(i) for i in range(mod.MAX_TABLE_COLS + 2)) + " |"
wide = wide_head + "\n| " + " | ".join(
    "---" for _ in range(mod.MAX_TABLE_COLS + 2)) + " |\n" + wide_head
check("열 상한 초과 → 변환 포기(None)", build(wide) is None)

print("── 긴 텍스트 분할 ──")

long_text = "\n".join(f"{i}번째 줄입니다." * 6 for i in range(60))
split = build(f"{long_text}\n\n{TABLE}")
sections = [b for b in split or [] if b["type"] == "section"]
check("긴 앞 텍스트가 여러 section 으로 나뉜다", len(sections) > 1, f"{len(sections)}개")
check("section 각각이 3000자 미만",
      all(len(b["text"]["text"]) <= mod.MAX_SECTION_CHARS for b in sections))

print("── 인사 Block Kit ──")
# 블록은 플러그인에, 폴백 텍스트는 SOUL.md 에 있다. 두 벌이라 한쪽만 고치면
# 탐지가 조용히 깨진다 — 그래서 SOUL.md 원문을 실제로 읽어 검사한다.
SOUL = os.path.join(ROOT, "SOUL.md")
with open(SOUL, encoding="utf-8") as f:
    soul = f.read()

_after = soul.split("## 인사 응답", 1)
GREETING_TEXT = _after[1].split("```")[1].strip() if len(_after) > 1 else ""

check("SOUL.md 에서 인사 문구를 뽑았다", bool(GREETING_TEXT), repr(GREETING_TEXT[:40]))
check("SOUL.md 인사 문구가 블록으로 바뀐다",
      mod.greeting_blocks(GREETING_TEXT) is not None,
      repr(GREETING_TEXT[:30]) + " … " + repr(GREETING_TEXT[-20:]))

greet = mod.greeting_blocks(GREETING_TEXT) or []
check("header 로 시작한다", greet and greet[0]["type"] == "header")
check("블로그 열기 버튼이 있다",
      any(b.get("accessory", {}).get("action_id") == "open_blog" for b in greet))
check("버튼 url 이 블로그 주소",
      any(b.get("accessory", {}).get("url") == mod.BLOG_URL for b in greet))
check("context 로 끝난다", greet and greet[-1]["type"] == "context")
check("블록 상한 이내", len(greet) <= mod.MAX_BLOCKS)
check("section 은 3000자 미만",
      all(len(b["text"]["text"]) <= mod.MAX_SECTION_CHARS
          for b in greet if b["type"] == "section"))

# format_message 가 마크다운 이탤릭을 mrkdwn 으로 바꿔도 앞뒤 줄은 그대로다
check("이탤릭 변환(_x_) 후에도 탐지된다",
      mod.greeting_blocks(GREETING_TEXT.replace("*모의 블로그*", "_모의 블로그_")) is not None)

check("상수를 되돌려주지 않는다 (호출자가 원본을 못 건드린다)",
      mod.greeting_blocks(GREETING_TEXT)[0] is not mod.GREETING_BLOCKS[0])

check("일반 답변은 인사가 아니다", mod.greeting_blocks("댓글 저장은 CommentController 입니다.") is None)
check("빈 문자열은 인사가 아니다", mod.greeting_blocks("") is None)
check("인사 뒤에 답변이 붙으면 갈아끼우지 않는다 (내용 유실 방지)",
      mod.greeting_blocks(GREETING_TEXT + "\n\n그리고 댓글은 CommentController 입니다.") is None)
check("인사 문구만 인용해도 꼬리가 다르면 그대로",
      mod.greeting_blocks(mod.GREETING_HEAD + " 라고 답하게 되어 있습니다.") is None)

print("── 클라이언트 프록시 ──")


class FakeClient:
    def __init__(self):
        self.calls = []

    async def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ts": "1.0"}

    async def chat_update(self, **kwargs):
        self.calls.append(kwargs)
        return {"ts": "1.0"}

    async def reactions_add(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


fake = FakeClient()
proxy = mod._ClientProxy(fake)

run(proxy.chat_postMessage(channel="C1", text="표 없는 답변", mrkdwn=True))
check("표 없으면 blocks 를 붙이지 않는다", "blocks" not in fake.calls[-1])

run(proxy.chat_postMessage(channel="C1", text=TABLE, mrkdwn=True))
sent = fake.calls[-1]
check("표 있으면 blocks 를 붙인다", "blocks" in sent and sent["blocks"][0]["type"] == "data_table")
check("text 폴백은 그대로 남는다", sent.get("text") == TABLE)
check("channel 등 나머지 인자 보존",
      sent.get("channel") == "C1" and sent.get("mrkdwn") is True)

run(proxy.chat_postMessage(channel="C1", text=TABLE, blocks=[{"type": "divider"}]))
check("이미 blocks 가 있으면 건드리지 않는다",
      fake.calls[-1]["blocks"] == [{"type": "divider"}])

run(proxy.chat_update(channel="C1", ts="1.0", text=TABLE))
check("chat_update 도 blocks 를 붙인다 (스트리밍 편집 경로)",
      "blocks" in fake.calls[-1] and fake.calls[-1]["blocks"][0]["type"] == "data_table")

run(proxy.chat_update(channel="C1", ts="1.0", text="표 없는 편집"))
check("chat_update, 표 없으면 그대로", "blocks" not in fake.calls[-1])

run(proxy.reactions_add(channel="C1", timestamp="1.0", name="x"))
check("가로채지 않는 메서드는 원본으로 넘어간다", fake.calls[-1].get("name") == "x")

run(proxy.chat_postMessage(channel="C1", text=GREETING_TEXT, mrkdwn=True))
sent = fake.calls[-1]
check("인사도 발송 경로에서 blocks 가 실린다",
      sent.get("blocks", [{}])[0].get("type") == "header")
check("인사의 text 폴백도 그대로 남는다", sent.get("text") == GREETING_TEXT)


class RejectClient(FakeClient):
    """blocks 가 실린 첫 호출만 거절한다 (Slack 400 재현)."""

    async def chat_postMessage(self, **kwargs):
        if kwargs.get("blocks"):
            raise RuntimeError("invalid_blocks")
        return await super().chat_postMessage(**kwargs)


reject = mod._ClientProxy(RejectClient())
run(reject.chat_postMessage(channel="C1", text=TABLE, mrkdwn=True))
sent = reject._inner.calls[-1]
check("blocks 가 400 이면 텍스트로 재시도한다 (답변 유실 방지)",
      "blocks" not in sent and sent.get("text") == TABLE, repr(sent))


class BoomClient(FakeClient):
    pass


boom = mod._ClientProxy(BoomClient())
_saved = mod.build_blocks
try:
    def _explode(_t):
        raise RuntimeError("변환기 폭발")
    mod.build_blocks = _explode
    run(boom.chat_postMessage(channel="C1", text=TABLE))
    sent = boom._inner.calls[-1]
    check("변환이 터져도 원문은 발송된다 (fail-open)",
          "blocks" not in sent and sent["text"] == TABLE)
finally:
    mod.build_blocks = _saved

print("── 어댑터 탐색·패치 ──")
# 실제로 헛돌았던 상황: 서버 로더는 이 클래스를
# hermes_plugins.slack_platform.adapter 로 올린다. 예전 코드는
# "platforms.slack.adapter" 로 끝나는 이름만 찾아서 못 잡았고,
# import 폴백이 같은 파일의 별개 모듈을 잡아 헛패치했다.
import types  # noqa: E402


class FakeAdapter:
    def __init__(self):
        self.inner = FakeClient()

    def _get_client(self, chat_id):
        return self.inner


def install_fake_module(name):
    m = types.ModuleType(name)
    cls = type("SlackAdapter", (FakeAdapter,), {})
    m.SlackAdapter = cls
    cls.__module__ = name
    sys.modules[name] = m
    return cls


REAL_NAME = "hermes_plugins.slack_platform.adapter"
ALT_NAME = "plugins.platforms.slack.adapter"

if not hasattr(mod, "_target_classes"):
    check("_target_classes 내부 API 존재", False, "옛 버전 — 모듈명 짐작 방식")
else:
    target = install_fake_module(REAL_NAME)
    alt = install_fake_module(ALT_NAME)
    try:
        found = mod._target_classes()
        check("서버 실제 모듈명(slack_platform)을 찾는다", any(c is target for c in found),
              repr([c.__module__ for c in found]))
        check("같은 파일이 두 이름으로 올라와 있으면 둘 다 찾는다",
              any(c is alt for c in found))

        check("_patch 가 True 를 돌려준다", mod._patch() is True)
        check("대상 클래스가 패치됨", target.__dict__.get("_slack_table_patched") is True)
        check("중복 모듈도 패치됨", alt.__dict__.get("_slack_table_patched") is True)

        inst = target()
        check("패치된 _get_client 가 프록시를 돌려준다",
              isinstance(inst._get_client("C1"), mod._ClientProxy))

        run(inst._get_client("C1").chat_postMessage(channel="C1", text=TABLE))
        check("패치 경로로 실제 blocks 가 실린다",
              "blocks" in inst.inner.calls[-1])

        check("두 번 패치해도 중첩되지 않는다", mod._patch() is True)
        before = target._get_client
        mod._patch()
        check("재실행이 _get_client 를 다시 감싸지 않는다", target._get_client is before)
    finally:
        sys.modules.pop(REAL_NAME, None)
        sys.modules.pop(ALT_NAME, None)

    check("어댑터가 없으면 False (등록 시점 상황)", mod._patch() is False)

print("── 배포 순서 재현 ──")
# 서버에서 실제로 벌어진 순서. 이 순서를 안 지키면 버그가 재현되지 않는다.
#   1) 플러그인 register()  ← 이때 Slack 어댑터 모듈은 아직 없다
#   2) 게이트웨이가 어댑터를 hermes_plugins.slack_platform.adapter 로 로드
#   3) 인바운드 메시지 → pre_gateway_dispatch → 답변 발신
# 옛 코드는 1)에서 모듈명을 짐작해 import 해 버려 별개 클래스를 패치하고,
# 3)에서 재시도하지 않아 실제 어댑터는 끝까지 원본이었다.


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


for name in list(sys.modules):
    if "slack" in name.lower() and name != "slack_table":
        sys.modules.pop(name, None)

ctx = FakeCtx()
mod.register(ctx)                                   # 1) 어댑터 없는 상태로 등록
target = install_fake_module(REAL_NAME)             # 2) 뒤늦게 어댑터 로드
try:
    check("등록 직후에는 아직 패치되지 않는다 (어댑터 미로드)",
          target.__dict__.get("_slack_table_patched") is None)
    check("발신 전 지점에 재시도 훅이 걸려 있다",
          bool(ctx.hooks.get("pre_gateway_dispatch")),
          repr(sorted(ctx.hooks)))

    for cb in ctx.hooks.get("pre_gateway_dispatch", []):   # 3) 인바운드 1건
        check("재시도 훅은 흐름에 끼어들지 않는다 (None 반환)",
              cb(event=object(), gateway=None, session_store=None) is None)

    check("재시도 후 실제 어댑터 클래스가 패치됨",
          target.__dict__.get("_slack_table_patched") is True)

    inst = target()
    run(inst._get_client("C1").chat_postMessage(channel="C1", text=TABLE, mrkdwn=True))
    sent = inst.inner.calls[-1]
    check("이 경로로 나간 표가 blocks 로 실린다",
          "blocks" in sent and sent["blocks"][0]["type"] == "data_table",
          repr(list(sent)))
finally:
    sys.modules.pop(REAL_NAME, None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
