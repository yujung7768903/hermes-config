"""발신 직전에 Slack Block Kit 으로 다시 쓴다 — 마크다운 표, 그리고 인사 응답.

문제
  코어는 에이전트 답변을 항상 `chat_postMessage(text=..., mrkdwn=True)` 로만
  보낸다(plugins/platforms/slack/adapter.py `SlackAdapter.send`). Slack mrkdwn 에는
  표 문법이 없어서 `| a | b |` 가 파이프째 그대로 찍힌다. 인사 응답도 같은 이유로
  불릿과 줄바꿈만 남아 첫인상이 심심하다.

가로채는 지점
  `SlackAdapter._get_client(chat_id)` 가 돌려주는 Slack 클라이언트를 프록시로 감싸고,
  `chat_postMessage` 페이로드에 표가 있으면 `blocks` 를 얹는다. `send` 자체를
  가로채지 않는 이유는, 거기에 붙어 있는 뒷정리를 복제하지 않기 위해서다 —
  슬래시 커맨드 ephemeral 분기, thread_ts 해석, reply_broadcast, 자동응답용
  `_bot_message_ts` 등록, 길이 분할. 이걸 베끼면 코어가 바뀔 때마다 어긋난다.
  클라이언트 경계에서는 페이로드 한 겹만 다시 쓰면 된다.

  대신 여기 도달하는 text 는 이미 `format_message` 를 지난 **mrkdwn** 이다.
  `**bold**` 는 `*bold*` 로, `[t](u)` 는 `<u|t>` 로 바뀌어 있고 평문의 `<`·`>`·`&` 는
  HTML 이스케이프돼 있다. 셀 파서가 마크다운이 아니라 mrkdwn 을 읽는 이유다.
  표 구분 문자(`|`, `---`)는 format_message 가 건드리지 않으므로 탐지는 그대로 된다.

fail-open
  변환 중 어떤 예외가 나도 원래 text-only 페이로드로 보낸다. 발송이 blocks 때문에
  거절돼도(400) 텍스트만으로 한 번 더 보낸다. 표가 못 나오는 것과 답변이 통째로
  안 가는 것은 무게가 다르다.

적용 범위
  Slack 만. 표도 인사도 아닌 메시지는 페이로드를 건드리지 않는다. 이미 `blocks` 를
  실어 보내는 호출(deploy-log 공지 등)도 건드리지 않는다.
"""

from __future__ import annotations

import copy
import html
import logging
import re
import sys

logger = logging.getLogger(__name__)


# ── 한계값 ────────────────────────────────────────────────────────────────
# 넘으면 변환을 포기하고 기존 text 그대로 보낸다. 깨진 표를 보여주는 쪽이
# API 400 으로 답변 전체를 날리는 것보다 낫다.
MAX_BLOCKS = 50          # Slack: 메시지당 블록 수 상한
MAX_SECTION_CHARS = 2900 # Slack: section.text 3000자 상한 (여유분 제외)
# data_table 실제 상한은 행 201·열 20 이지만 보수적으로 잡아 둔다.
# 실제로 더 큰 표가 필요해지면 여기만 올리고 실물로 확인한다.
MAX_TABLE_ROWS = 50
MAX_TABLE_COLS = 10
# caption 은 data_table 필수 필드이고 Slack 이 표 위에 **실제로 렌더한다.**
# 그래서 표 바로 앞 한 줄을 제목으로 끌어올리고, 없을 때만 이 고정값을 쓴다.
TABLE_CAPTION = "표"
CAPTION_MAX = 80


# ── 표 탐지 ───────────────────────────────────────────────────────────────
_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{1,}:?\s*\|)+\s*$")
_FENCE_RE = re.compile(r"^\s*```")


_ENTITY_RE = re.compile(r"<[^<>\n]*>")
_PIPE = "\x00PIPE\x00"


def _split_cells(line: str) -> list[str]:
    """`| a | b |` → ['a', 'b'].

    셀 구분자가 아닌 파이프가 둘 있다. `\\|` 리터럴, 그리고 mrkdwn 링크
    `<url|label>` 의 구분자다. 후자는 format_message 가 만들어 넣은 것이라
    표 안에 링크만 들어가면 행이 통째로 어긋난다.
    """
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    body = body.replace("\\|", _PIPE)
    body = _ENTITY_RE.sub(lambda m: m.group(0).replace("|", _PIPE), body)
    return [p.replace(_PIPE, "|").strip() for p in body.split("|")]


def _segments(text: str) -> list[tuple[str, object]]:
    """본문을 [('text', str) | ('table', list[list[str]])] 로 쪼갠다.

    코드펜스 안쪽은 표로 보지 않는다 — 예시로 붙여 넣은 표까지 블록으로 바꾸면
    "이렇게 쓰면 된다"는 설명이 설명이 아니게 된다.
    """
    lines = text.split("\n")
    out: list[tuple[str, object]] = []
    buf: list[str] = []
    in_fence = False
    i = 0

    def flush():
        if buf:
            out.append(("text", "\n".join(buf)))
            buf.clear()

    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            i += 1
            continue
        if (
            not in_fence
            and _ROW_RE.match(line)
            and i + 1 < len(lines)
            and _SEP_RE.match(lines[i + 1])
        ):
            header = _split_cells(line)
            rows = [header]
            i += 2
            while i < len(lines) and _ROW_RE.match(lines[i]) and not _FENCE_RE.match(lines[i]):
                rows.append(_split_cells(lines[i]))
                i += 1
            flush()
            out.append(("table", rows))
            continue
        buf.append(line)
        i += 1

    flush()
    return out


# ── mrkdwn 인라인 → rich_text elements ────────────────────────────────────
# 셀은 section 이 아니라 rich_text 라서 mrkdwn 이 렌더되지 않는다.
# 이미 mrkdwn 으로 변환된 문자열을 다시 풀어 style 로 옮긴다.
_INLINE_RE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|<(?P<lurl>[^>|\n]+)\|(?P<ltext>[^>\n]*)>"
    r"|<(?P<url>(?:https?|mailto|tel):[^>\n]+)>"
    # 이모지 코드는 section(mrkdwn)에서는 알아서 렌더되지만 rich_text 에서는
    # 글자 그대로 나온다. 시각(12:30:45)을 잘못 잡지 않게 양옆 영숫자를 배제한다.
    r"|(?<![A-Za-z0-9]):(?P<emoji>[a-z0-9_+'-]+):(?![A-Za-z0-9])"
    r"|\*(?P<bold>[^*\n]+)\*"
    r"|_(?P<italic>[^_\n]+)_"
    r"|~(?P<strike>[^~\n]+)~"
)


def _text_element(value: str, style: dict) -> dict | None:
    if not value:
        return None
    el: dict = {"type": "text", "text": value}
    if style:
        el["style"] = dict(style)
    return el


def _inline_elements(cell: str) -> list[dict]:
    """mrkdwn 셀 문자열 → rich_text_section 의 elements.

    중첩 스타일(`*_x_*`)은 바깥 하나만 살린다. 표 셀에서 그 이상은 필요 없다.
    """
    elements: list[dict] = []
    pos = 0

    def add_plain(raw: str):
        el = _text_element(html.unescape(raw), {})
        if el:
            elements.append(el)

    for m in _INLINE_RE.finditer(cell):
        add_plain(cell[pos:m.start()])
        pos = m.end()
        if m.group("code") is not None:
            el = _text_element(html.unescape(m.group("code")), {"code": True})
        elif m.group("lurl") is not None:
            el = {"type": "link", "url": html.unescape(m.group("lurl"))}
            label = html.unescape(m.group("ltext") or "").strip()
            if label:
                el["text"] = label
        elif m.group("url") is not None:
            el = {"type": "link", "url": html.unescape(m.group("url"))}
        elif m.group("emoji") is not None:
            el = {"type": "emoji", "name": m.group("emoji")}
        elif m.group("bold") is not None:
            el = _text_element(html.unescape(m.group("bold")), {"bold": True})
        elif m.group("italic") is not None:
            el = _text_element(html.unescape(m.group("italic")), {"italic": True})
        else:
            el = _text_element(html.unescape(m.group("strike")), {"strike": True})
        if el:
            elements.append(el)

    add_plain(cell[pos:])
    # 빈 셀도 rich_text 는 elements 를 요구한다
    return elements or [{"type": "text", "text": " "}]


def _cell(value: str) -> dict:
    return {
        "type": "rich_text",
        "elements": [
            {"type": "rich_text_section", "elements": _inline_elements(value)}
        ],
    }


def _plain(value: str) -> str:
    """mrkdwn 한 줄 → 마커를 뗀 평문. raw_text 자리(헤더·caption)에 쓴다."""
    return "".join(
        el.get("text")
        or el.get("url")
        or (f":{el['name']}:" if el.get("type") == "emoji" else "")
        for el in _inline_elements(value)
    )


def _header_cell(value: str) -> dict:
    """헤더 행 전용. data_table 의 첫 행은 raw_text 만 받는다.

    rich_text 를 넣으면 400 이라 mrkdwn 마커를 살릴 수 없다. `*구분*` 이 별표째
    찍히지 않도록 인라인 파서를 한 번 태워 평문만 뽑는다.
    """
    return {"type": "raw_text", "text": _plain(value) or " "}


def _take_caption(text: str) -> tuple[str, str | None]:
    """표 앞 텍스트에서 제목 한 줄을 떼어낸다 → (남은 텍스트, caption).

    떼는 조건은 그 줄이 **단독 줄**일 때뿐이다 — 구간의 첫 줄이거나 앞이 빈 줄.
    문단 한가운데 줄을 가져가면 본문에서 마지막 줄만 사라져 문단이 깨진다.
    """
    lines = text.split("\n")
    idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if idx is None:
        return text, None
    if idx > 0 and lines[idx - 1].strip():
        return text, None
    caption = _plain(lines[idx]).strip()
    if not caption or len(caption) > CAPTION_MAX:
        return text, None
    return "\n".join(lines[:idx]), caption


def _table_block(rows: list[list[str]], caption: str | None = None) -> dict | None:
    if len(rows) < 2:
        return None  # 헤더만 있고 데이터가 없으면 표로 만들 이유가 없다
    width = len(rows[0])
    if not 1 <= width <= MAX_TABLE_COLS or len(rows) > MAX_TABLE_ROWS:
        return None
    built = []
    for idx, row in enumerate(rows):
        cells = (row + [""] * width)[:width]  # 열 수는 헤더에 맞춘다
        make = _header_cell if idx == 0 else _cell
        built.append([make(c) for c in cells])
    return {"type": "data_table", "caption": caption or TABLE_CAPTION, "rows": built}


def _section_blocks(text: str) -> list[dict]:
    """텍스트 구간 → section 블록. 3000자 상한에 맞춰 줄 경계로 나눈다."""
    body = text.strip("\n")
    if not body.strip():
        return []
    chunks: list[str] = []
    current = ""
    for line in body.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_SECTION_CHARS and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": c[:MAX_SECTION_CHARS]}}
        for c in chunks
        if c.strip()
    ]


def build_blocks(text: str) -> list[dict] | None:
    """mrkdwn 본문 → blocks. 표가 없거나 변환이 안 되면 None(= 기존 경로)."""
    if not text or "|" not in text:
        return None
    segments = _segments(text)
    if not any(kind == "table" for kind, _ in segments):
        return None

    # 표 바로 앞 단독 줄을 그 표의 caption 으로 옮긴다. 본문에서는 빼서
    # 같은 문장이 제목과 section 에 두 번 나오지 않게 한다.
    captions: dict[int, str] = {}
    for i, (kind, _payload) in enumerate(segments):
        if kind != "table" or i == 0 or segments[i - 1][0] != "text":
            continue
        rest, caption = _take_caption(segments[i - 1][1])  # type: ignore[arg-type]
        if caption:
            segments[i - 1] = ("text", rest)
            captions[i] = caption

    blocks: list[dict] = []
    for i, (kind, payload) in enumerate(segments):
        if kind == "text":
            blocks.extend(_section_blocks(payload))  # type: ignore[arg-type]
            continue
        block = _table_block(payload, captions.get(i))  # type: ignore[arg-type]
        if block is None:
            return None  # 상한을 넘은 표 하나 때문에 나머지를 쪼개진 않는다
        blocks.append(block)

    if not blocks or len(blocks) > MAX_BLOCKS:
        return None
    return blocks


# ── 인사 응답 ─────────────────────────────────────────────────────────────
# 인사는 SOUL.md "## 인사 응답" 에 박아 둔 고정 답변이다. 고정이니 블록도 고정해
# 두고 통째로 갈아끼운다. 모델이 Block Kit JSON 을 직접 짓게 하지 않는 이유는,
# 인젝션으로 임의 링크·버튼을 심을 여지를 만들지 않기 위해서다.
#
# 아래 블록과 SOUL.md 의 텍스트는 같은 내용을 두 벌 적은 것이다. 한쪽만 고치면
# 탐지가 조용히 깨진다 — tests/test_slack_table.py 가 SOUL.md 원문으로 탐지를
# 검사하므로 문구를 바꿀 때는 그 테스트를 돌려 확인한다.
GREETING_HEAD = "안녕하세요. Hermes입니다"
GREETING_TAIL = "읽고 답하는 것만 합니다"
BLOG_URL = "http://16.184.55.44:4200/"

GREETING_BLOCKS = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": "안녕하세요. Hermes입니다:wave:"},
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": '저는 *"Hermes 를 사내 팀 에이전트로 써도 되는가"* 를 검증하기 위해 '
                    "수행되고 있는 테스트 에이전트입니다.",
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "악의적인 데이터를 주입할 수 있는지, \n민감정보가 새어나갈 수 있는지\n "
                    "실제로 부딪혀 보고 *보안 정책을 만드는 것이 목적* 입니다.",
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "실환경을 붙이는 것 자체가 위험해서, 사내 코드 대신 *모의 블로그* 를 "
                    "담당합니다.",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "블로그 열기"},
            "url": BLOG_URL,
            "action_id": "open_blog",
        },
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*이런 것을 물어보세요*\n\n"
                    '• *구조·흐름* — "댓글 저장은 어느 API 를 타?", "마크다운은 어디서 렌더돼?"\n'
                    "• *에러 원인* — 로그·스택트레이스를 붙여주시면 코드·설정과 대조해 원인을 지목합니다\n"
                    '• *코드 위치·영향 범위* — "이 값 바꾸면 어디까지 영향 가?"\n'
                    '• *설계 의사결정* — "왜 Hermes 였어?", "다른 방법은 없었어?", "이 방식의 문제점은?"\n'
                    '• *보안 정책* — "지금 무엇을 막고 있고 무엇이 열려 있어?"',
        },
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*이런 건 안돼요*\n\n"
                    "• *코드 변경* \n"
                    "• *배포* \n"
                    "• *자가발전* — 다른 사용자의 답변에 영향을 줄 수 있으므로 금지 \n"
                    "(개선 요청은 대기 상태로 전환하여 관리자 승인 하에 발전 가능)\n"
                    "• *데이터 변경 및 데이터 구조 확인* — 데이터베이스 구조는 관리자만 확인 가능 \n"
                    "• *스킬 or 배치 추가* — 악의적으로 스크립트를 추가하거나 관리자의 의도에 "
                    "벗어난 로직을 추가할 수 있으므로 금지  ",
        },
    },
    {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "읽고 답하는 것만 합니다"}],
    },
]


def greeting_blocks(text: str) -> list[dict] | None:
    """인사 고정 답변이면 고정 블록. 아니면 None(= 기존 경로).

    앞뒤 양쪽을 본다. "안녕, 그리고 댓글 저장은 어디서 해?" 처럼 인사 뒤에 답변이
    이어 붙은 메시지까지 갈아끼우면 뒤에 붙은 답변이 통째로 사라진다.
    """
    body = (text or "").strip()
    if not body.startswith(GREETING_HEAD) or not body.endswith(GREETING_TAIL):
        return None
    return copy.deepcopy(GREETING_BLOCKS)


# ── 클라이언트 프록시 ──────────────────────────────────────────────────────
class _ClientProxy:
    """발신 메서드만 가로채고 나머지는 원본 클라이언트로 넘긴다.

    `chat_update` 도 덮는다 — 스트리밍이 켜져 있으면 최종 답변이 새 메시지가
    아니라 자리표시 메시지 편집(`edit_message` → `chat_update`)으로 전달된다.
    """

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_inner"), name, value)

    def _with_blocks(self, args, kwargs):
        try:
            if not args and not kwargs.get("blocks"):
                text = kwargs.get("text") or ""
                blocks = greeting_blocks(text) or build_blocks(text)
                if blocks:
                    # text 는 남겨 둔다 — 알림 미리보기·접근성 폴백으로 쓰인다
                    kwargs["blocks"] = blocks
        except Exception as exc:  # pragma: no cover - 변환 실패가 발신을 막으면 안 된다
            logger.warning("[slack-table] 블록 변환 실패 → 기존 텍스트로 보낸다: %s", exc)
        return kwargs

    async def _send(self, method, args, kwargs):
        """blocks 를 얹어 보내고, 그 때문에 거절되면 텍스트만으로 한 번 더 보낸다.

        블록 스펙 위반은 발송 시점의 400 으로 나온다. 변환기 안에서는 잡을 수 없고,
        여기서 재시도하지 않으면 답변이 통째로 유실된다.
        """
        payload = self._with_blocks(args, dict(kwargs))
        if "blocks" not in payload or "blocks" in kwargs:
            return await method(*args, **payload)
        try:
            return await method(*args, **payload)
        except Exception as exc:
            logger.warning("[slack-table] blocks 발송 거절 → 텍스트로 재시도: %s", exc)
            return await method(*args, **kwargs)

    async def chat_postMessage(self, *args, **kwargs):
        inner = object.__getattribute__(self, "_inner")
        return await self._send(inner.chat_postMessage, args, kwargs)

    async def chat_update(self, *args, **kwargs):
        inner = object.__getattribute__(self, "_inner")
        return await self._send(inner.chat_update, args, kwargs)


# ── 등록 ──────────────────────────────────────────────────────────────────
# 모듈명을 짐작해서 import 하면 안 된다. 코어 로더는 이 파일을
# `hermes_plugins.slack_platform.adapter` 로 올리는데, 같은 파일을
# `plugins.platforms.slack.adapter` 로 다시 import 하면 **별개 클래스 객체**가
# 생기고 그걸 패치해도 게이트웨이는 원본을 쓴다. 로그에는 "적용됨" 이 찍히지만
# 아무 효과가 없다 — 실제로 그렇게 한 번 헛돌았다.
# 그래서 이미 sys.modules 에 올라온 것만 본다. import 는 하지 않는다.
def _target_classes() -> list[type]:
    found: list[type] = []
    for name, mod in list(sys.modules.items()):
        if mod is None or "slack" not in name.lower():
            continue
        cls = getattr(mod, "SlackAdapter", None)
        if isinstance(cls, type) and not any(cls is seen for seen in found):
            found.append(cls)
    return found


def _wrap(original):
    def _get_client(self, *args, **kwargs):
        return _ClientProxy(original(self, *args, **kwargs))

    return _get_client


def _patch() -> bool:
    """찾은 SlackAdapter 를 전부 감싼다. 하나라도 감쌌으면 True.

    같은 파일이 두 모듈명으로 올라와 있을 수 있으니 고르지 않고 다 감싼다.
    """
    patched = False
    for cls in _target_classes():
        # 상속으로 물려받은 플래그를 자기 것으로 착각하지 않게 __dict__ 로 본다
        if cls.__dict__.get("_slack_table_patched"):
            patched = True
            continue
        original = getattr(cls, "_get_client", None)
        if original is None:
            logger.error(
                "[slack-table] %s.SlackAdapter 에 _get_client 가 없다 — 코어가 "
                "바뀌었다. 표는 계속 텍스트로 나간다.", cls.__module__)
            continue
        cls._get_client = _wrap(original)
        cls._slack_table_patched = True
        patched = True
        logger.info("[slack-table] 적용됨 — %s.SlackAdapter", cls.__module__)
    return patched


def register(ctx) -> None:
    # 어댑터는 플러그인 등록보다 늦게 로드된다(플러그인 04:26:48 → Slack 연결
    # 04:26:50). 그래서 등록 시점에는 보통 못 찾는다.
    _patch()

    # 발신 전에 반드시 지나는 지점에서 다시 시도한다. pre_gateway_dispatch 는
    # 인바운드 메시지마다 게이트웨이 프로세스에서 돌므로, 그 답변이 나가기 전에
    # 패치가 보장된다. 콜백은 동기 함수여야 하고(async 는 코어가 조용히 버린다)
    # kwargs 를 다 받아야 하며(부족하면 TypeError 로 삼켜진다), 흐름에 끼어들지
    # 않으려면 None 을 돌려줘야 한다.
    def _retry(**_kwargs):
        _patch()
        return None

    hooked = []
    for hook in ("pre_gateway_dispatch", "on_session_start"):
        try:
            ctx.register_hook(hook, _retry)
            hooked.append(hook)
        except Exception as exc:  # pragma: no cover
            logger.warning("[slack-table] %s 훅 등록 실패: %s", hook, exc)

    # 기동 로그를 반드시 한 줄 남긴다. 적용 로그는 어댑터가 뜬 뒤에야 찍히므로,
    # 이 줄이 없으면 "플러그인이 안 실려서 조용한 것" 과 "아직 적용 전이라
    # 조용한 것" 을 로그로 구분할 수 없다.
    logger.info("[slack-table] 등록됨 — 재시도 훅: %s", ", ".join(hooked) or "(없음)")
