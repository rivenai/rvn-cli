"""Citation parsing — the chat-surface UX in the terminal.

The gateway's grounded models (riven-research = sonar-reasoning-pro) return
citations as an inline trailing block:

    Sources:
    [1] Title - https://example.com/a
    [2] ...

We parse that block into structured citations, strip it from the prose, and
render `[n] source` lines under the answer — mirroring the web chat surface.
Some models also emit inline markers like [2][4]; those stay in the prose and
resolve against the numbered source list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCES_HEADER_RE = re.compile(r"^\s*Sources:\s*$", re.MULTILINE)
SOURCE_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s*(.*?)\s*-\s*(https?://\S+?)\s*$", re.MULTILINE)


@dataclass
class Citation:
    number: int
    title: str
    url: str

    def to_dict(self) -> dict:
        return {"number": self.number, "title": self.title, "url": self.url}


def parse_answer(content: str) -> tuple[str, list[Citation]]:
    """Split a model answer into (prose, citations).

    Returns prose with the trailing Sources: block removed, plus structured
    citations. If no block is present, prose is unchanged and citations empty.
    """
    citations: list[Citation] = []
    for m in SOURCE_LINE_RE.finditer(content):
        citations.append(Citation(int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    prose = content
    header = SOURCES_HEADER_RE.search(content)
    if header and citations:
        prose = content[: header.start()].rstrip()
    return prose, citations


def render_markdown(prose: str, citations: list[Citation]) -> str:
    """Plain-text render: answer, blank line, then [n] source lines."""
    out = prose.rstrip()
    if citations:
        out += "\n\nSources:"
        for c in citations:
            out += f"\n[{c.number}] {c.title} — {c.url}"
    return out


def citations_to_json(citations: list[Citation]) -> list[dict]:
    return [c.to_dict() for c in citations]
