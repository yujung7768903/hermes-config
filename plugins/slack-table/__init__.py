"""마크다운 표를 Slack Block Kit `table` 블록으로 내보낸다.

문제
  코어는 에이전트 답변을 항상 `chat_postMessage(text=..., mrkdwn=True)` 로만
  보낸다(plugins/platforms/slack/adapter.py `SlackAdapter.send`). Slack mrkdwn 에는
  표 문법이 없어서 `| a | b |` 가 파이프째 그대로 찍힌다.

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
  변환 중 어떤 예외가 나도 원래 text-only 페이로드로 보낸다. 표가 못 나오는 것과
  답변이 통째로 안 가는 것은 무게가 다르다.

적용 범위
  Slack 만. 표가 없는 메시지는 페이로드를 건드리지 않는다. 이미 `blocks` 를 실어
  보내는 호출(deploy-log 공지 등)도 건드리지 않는다.
"""

from __future__ import annotations

import html
import importlib
import logging
import re
import sys

logger = logging.getLogger(__name__)


# ── 한계값 ────────────────────────────────────────────────────────────────
# 넘으면 변환을 포기하고 기존 text 그대로 보낸다. 깨진 표를 보여주는 쪽이
# API 400 으로 답변 전체를 날리는 것보다 낫다.
MAX_BLOCKS = 50          # Slack: 메시지당 블록 수 상한
MAX_SECTION_CHARS = 2900 # Slack: section.text 3000자 상한 (여유분 제외)
# ponytail: 표 크기 상한은 Slack 문서값이 아니라 보수적으로 잡은 값이다.
# 실제로 더 큰 표가 필요해지면 여기만 올리고 실물로 확인한다.
MAX_TABLE_ROWS = 50
MAX_TABLE_COLS = 10


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


def _inline_elements(cell: str, *, bold: bool = False) -> list[dict]:
    """mrkdwn 셀 문자열 → rich_text_section 의 elements.

    중첩 스타일(`*_x_*`)은 바깥 하나만 살린다. 표 셀에서 그 이상은 필요 없다.
    """
    elements: list[dict] = []
    base = {"bold": True} if bold else {}
    pos = 0

    def add_plain(raw: str):
        el = _text_element(html.unescape(raw), base)
        if el:
            elements.append(el)

    for m in _INLINE_RE.finditer(cell):
        add_plain(cell[pos:m.start()])
        pos = m.end()
        if m.group("code") is not None:
            el = _text_element(html.unescape(m.group("code")), {**base, "code": True})
        elif m.group("lurl") is not None:
            el = {"type": "link", "url": html.unescape(m.group("lurl"))}
            label = html.unescape(m.group("ltext") or "").strip()
            if label:
                el["text"] = label
            if base:
                el["style"] = dict(base)
        elif m.group("url") is not None:
            el = {"type": "link", "url": html.unescape(m.group("url"))}
            if base:
                el["style"] = dict(base)
        elif m.group("emoji") is not None:
            el = {"type": "emoji", "name": m.group("emoji")}
        elif m.group("bold") is not None:
            el = _text_element(html.unescape(m.group("bold")), {**base, "bold": True})
        elif m.group("italic") is not None:
            el = _text_element(html.unescape(m.group("italic")), {**base, "italic": True})
        else:
            el = _text_element(html.unescape(m.group("strike")), {**base, "strike": True})
        if el:
            elements.append(el)

    add_plain(cell[pos:])
    # 빈 셀도 rich_text 는 elements 를 요구한다
    return elements or [{"type": "text", "text": " "}]


def _cell(value: str, *, header: bool) -> dict:
    return {
        "type": "rich_text",
        "elements": [
            {"type": "rich_text_section", "elements": _inline_elements(value, bold=header)}
        ],
    }


def _table_block(rows: list[list[str]]) -> dict | None:
    if len(rows) < 2:
        return None  # 헤더만 있고 데이터가 없으면 표로 만들 이유가 없다
    width = len(rows[0])
    if not 1 <= width <= MAX_TABLE_COLS or len(rows) > MAX_TABLE_ROWS:
        return None
    built = []
    for idx, row in enumerate(rows):
        cells = (row + [""] * width)[:width]  # 열 수는 헤더에 맞춘다
        built.append([_cell(c, header=(idx == 0)) for c in cells])
    return {"type": "table", "rows": built}


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

    blocks: list[dict] = []
    for kind, payload in segments:
        if kind == "text":
            blocks.extend(_section_blocks(payload))  # type: ignore[arg-type]
            continue
        block = _table_block(payload)  # type: ignore[arg-type]
        if block is None:
            return None  # 상한을 넘은 표 하나 때문에 나머지를 쪼개진 않는다
        blocks.append(block)

    if not blocks or len(blocks) > MAX_BLOCKS:
        return None
    return blocks


# ── 클라이언트 프록시 ──────────────────────────────────────────────────────
class _ClientProxy:
    """`chat_postMessage` 만 가로채고 나머지는 원본 클라이언트로 넘긴다."""

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_inner"), name, value)

    async def chat_postMessage(self, *args, **kwargs):
        inner = object.__getattribute__(self, "_inner")
        try:
            if not args and not kwargs.get("blocks"):
                blocks = build_blocks(kwargs.get("text") or "")
                if blocks:
                    # text 는 남겨 둔다 — 알림 미리보기·접근성 폴백으로 쓰인다
                    kwargs["blocks"] = blocks
        except Exception as exc:  # pragma: no cover - 변환 실패가 발신을 막으면 안 된다
            logger.warning("[slack-table] 표 변환 실패 → 기존 텍스트로 보낸다: %s", exc)
        return await inner.chat_postMessage(*args, **kwargs)


# ── 등록 ──────────────────────────────────────────────────────────────────
_ADAPTER_PATHS = (
    "hermes_plugins.platforms.slack.adapter",
    "plugins.platforms.slack.adapter",
    "hermes.plugins.platforms.slack.adapter",
)


def _find_adapter_cls():
    """SlackAdapter 클래스를 찾는다. 모듈 경로는 설치 형태마다 다르다."""
    for name, mod in list(sys.modules.items()):
        if name.endswith("platforms.slack.adapter"):
            cls = getattr(mod, "SlackAdapter", None)
            if isinstance(cls, type):
                return cls
    for path in _ADAPTER_PATHS:
        try:
            cls = getattr(importlib.import_module(path), "SlackAdapter", None)
        except Exception:
            continue
        if isinstance(cls, type):
            return cls
    return None


def _patch() -> bool:
    cls = _find_adapter_cls()
    if cls is None:
        return False
    if getattr(cls, "_slack_table_patched", False):
        return True
    original = getattr(cls, "_get_client", None)
    if original is None:
        logger.error(
            "[slack-table] SlackAdapter._get_client 가 없다 — 코어가 바뀌었다. "
            "표는 계속 텍스트로 나간다."
        )
        return True  # 재시도해도 결과가 같다. 매 세션 로그를 반복하지 않는다

    def _get_client(self, *args, **kwargs):
        return _ClientProxy(original(self, *args, **kwargs))

    cls._get_client = _get_client
    cls._slack_table_patched = True
    logger.info("[slack-table] 적용됨 — 마크다운 표를 Block Kit table 로 보낸다")
    return True


def register(ctx) -> None:
    if _patch():
        return
    # 어댑터 모듈이 아직 로드되지 않았다. 첫 세션 시작 시 한 번 더 시도한다.
    # 콜백은 반드시 동기 함수여야 하고 kwargs 를 다 받아야 한다 (async 는 코어가
    # 조용히 버리고, 인자 부족은 TypeError 로 삼켜진다).
    def _retry(**_kwargs):
        _patch()

    ctx.register_hook("on_session_start", _retry)
    logger.info("[slack-table] 어댑터 미로드 — on_session_start 에서 재시도한다")
