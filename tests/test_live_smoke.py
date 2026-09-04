"""Integration smoke against the LIVE gateway — gated by RIVEN_API_KEY.

Run:  RIVEN_API_KEY=rvn_... python -m pytest tests/test_live_smoke.py -s

Uses a low-cost key. The key is never printed.
"""

import os

import pytest

from rvn.commands import chat, deep, models
from rvn.config import resolve_key
from rvn.transport import RivenAPIError

pytestmark = pytest.mark.skipif(
    not os.environ.get("RIVEN_API_KEY"), reason="set RIVEN_API_KEY to run the live smoke"
)


@pytest.fixture(scope="module")
def key() -> str:
    return resolve_key()


def test_live_models(key):
    result = models(key, as_json=True)
    ids = [m["id"] for m in result["data"]]
    assert len(ids) > 50
    assert "riven-research" in ids


def test_live_grounded_chat_with_citations(key):
    printed: list[str] = []
    result = chat("who is the CEO of NVIDIA", key, print_fn=printed.append)
    assert "Jensen Huang" in result["content"]
    assert result["grounded"] is True
    assert len(result["citations"]) >= 3
    assert all(c["url"].startswith("http") for c in result["citations"])
    rendered = "".join(printed)
    assert "Sources:" in rendered
    assert "[1]" in rendered


def test_live_401_is_friendly():
    with pytest.raises(RivenAPIError) as e:
        models("rvn_invalid000000000000000000000000")
    msg = str(e.value)
    assert "rvn login" in msg


def test_live_deep_research(key):
    result = deep(
        "What changed in the most recent Python release? Keep it to one paragraph.",
        key,
        progress_fn=None,
    )
    assert result["content"] and len(result["content"]) > 80
    assert result["grounded"] is True
    assert result["thread_id"]
    assert result["phases"]  # pipeline stages were streamed
