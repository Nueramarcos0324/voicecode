"""Small helpers for talking to OpenCode and for speech text."""

from __future__ import annotations

import re
import unicodedata

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
_HTML = re.compile(r"<[^>\n]{1,80}>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]{4,}\|?\s*$", re.MULTILINE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SNAKE = re.compile(r"(?<=[A-Za-z])_(?=[A-Za-z])")
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def for_speech(text: str, max_chars: int = 600) -> str:
    """Turn a markdown/code answer into something worth hearing.

    Code is dropped, not read aloud. Bare paths and long identifiers are kept
    because they are often the point ("run pytest in src/"), but lists and
    tables become sentences.
    """
    text = strip_ansi(text or "")
    text = _FENCE.sub("\n", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML.sub("", text)
    text = _HEADING.sub("", text)
    text = _TABLE_RULE.sub("", text)
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("|"))
    text = _BULLET.sub("", text)
    text = text.replace("**", "").replace("__", "").replace("*", "")
    text = _SNAKE.sub(" ", text)
    text = _CAMEL.sub(" ", text)
    text = text.replace("→", " to ").replace("->", " to ")
    text = unicodedata.normalize("NFKC", text)
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANKS.sub("\n\n", text).strip()

    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    # Prefer a sentence boundary in the last 40% of the budget.
    floor = max_chars * 6 // 10
    best = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    if best >= floor:
        cut = cut[: best + 1]
    else:
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip(" ,;:") + ". The rest is in the terminal."
