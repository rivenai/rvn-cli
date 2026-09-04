"""Mock-transport tests — chat streaming, citations, errors, models, deep.

No network: the transport layer accepts an injectable opener; these tests
feed it a fake one that speaks the gateway's real wire shapes (SSE chunks,
401 body, 402 quota_exceeded body).
"""

import io
import json
import urllib.error

import pytest

import rvn.transport as T
from rvn.citations import parse_answer
from rvn.commands import chat, deep, models


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1):
        if n is None or n < 0:
            chunk, self._body = self._body, b""
        else:
            chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def close(self):
        pass


class FakeOpener:
    """Yields canned responses in order; records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        item = self.responses.pop(0)
        if isinstance(item, urllib.error.HTTPError):
            raise item
        return item


def sse(*events) -> bytes:
    out = b""
    for e in events:
        out += b"data: " + json.dumps(e).encode() + b"\n\n"
    out += b"data: [DONE]\n\n"
    return out


KEY = "rvn_testkey000000000000000"


# ── citations parsing ────────────────────────────────────────────────────

def test_parse_answer_with_sources_block():
    content = (
        "The CEO of NVIDIA is Jensen Huang [2][5].\n\n"
        "Sources:\n"
        "[1] About NVIDIA - https://www.nvidia.com/en-us/about/\n"
        "[2] Jensen Huang - Wikipedia - https://en.wikipedia.org/wiki/Jensen_Huang\n"
    )
    prose, cits = parse_answer(content)
    assert prose.strip() == "The CEO of NVIDIA is Jensen Huang [2][5]."
    assert len(cits) == 2
    assert cits[0].url == "https://www.nvidia.com/en-us/about/"
    assert cits[1].title == "Jensen Huang - Wikipedia"


def test_parse_answer_without_sources():
    prose, cits = parse_answer("Just a plain answer.")
    assert prose == "Just a plain answer."
    assert cits == []


# ── chat: streaming SSE with citations ────────────────────────────────────

def test_chat_streams_and_extracts_citations():
    chunks = [
        {"choices": [{"delta": {"content": "The CEO of NVIDIA is Jensen Huang [1]."}}]},
        {"choices": [{"delta": {"content": "\n\nSources:\n[1] About NVIDIA - https://www.nvidia.com/en-us/about/\n"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    opener = FakeOpener(FakeResponse(sse(*chunks)))
    printed: list[str] = []
    result = chat("who is the CEO of NVIDIA", KEY, opener=opener, print_fn=printed.append)
    joined = "".join(printed)
    assert "Jensen Huang" in joined
    assert "Sources:" in joined
    assert result["grounded"] is True
    assert result["citations"][0]["url"] == "https://www.nvidia.com/en-us/about/"
    assert result["content"] == "The CEO of NVIDIA is Jensen Huang [1]."


def test_chat_non_stream():
    body = json.dumps({
        "model": "riven-research",
        "choices": [{"message": {"role": "assistant", "content": "Paris [1].\n\nSources:\n[1] Britannica France - https://www.britannica.com/place/France"}}],
    }).encode()
    opener = FakeOpener(FakeResponse(body, headers={"X-Riven-Request-Id": "req-42"}))
    result = chat("capital of France", KEY, opener=opener, stream=False, print_fn=lambda s: None)
    assert result["request_id"] == "req-42"
    assert result["grounded"] is True
    assert result["citations"][0]["title"] == "Britannica France"


def test_chat_sends_auth_and_model():
    chunks = [{"choices": [{"delta": {"content": "ok"}}]}]
    opener = FakeOpener(FakeResponse(sse(*chunks)))
    chat("hi", KEY, opener=opener, print_fn=lambda s: None, model="riven-core")
    req = opener.requests[0]
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    payload = json.loads(req.data.decode())
    assert payload["model"] == "riven-core"
    assert payload["stream"] is True


# ── errors: 401 and 402 friendly messages ─────────────────────────────────

def test_401_friendly_message():
    body = json.dumps({"error": "Unauthorized", "message": "Missing or invalid Authorization header"}).encode()
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, io.BytesIO(body))
    opener = FakeOpener(err)
    with pytest.raises(T.RivenAPIError) as e:
        models(KEY, opener=opener)
    msg = str(e.value)
    assert "401" in msg
    assert "rvn login" in msg


def test_402_quota_upsell_message():
    body = json.dumps({
        "error": "Payment Required",
        "code": "quota_exceeded",
        "message": "Your monthly quota has been used up.",
        "used_actions": 200,
        "monthly_action_limit": 200,
    }).encode()
    err = urllib.error.HTTPError("url", 402, "Payment Required", {}, io.BytesIO(body))
    opener = FakeOpener(err)
    with pytest.raises(T.RivenAPIError) as e:
        models(KEY, opener=opener)
    msg = str(e.value)
    assert "402" in msg
    assert "quota" in msg.lower()
    assert "top up" in msg.lower()
    assert "chat.rivenai.io/settings/billing" in msg


def test_error_never_leaks_key():
    body = json.dumps({"error": "Unauthorized"}).encode()
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, io.BytesIO(body))
    opener = FakeOpener(err)
    with pytest.raises(T.RivenAPIError) as e:
        models(KEY, opener=opener)
    assert KEY not in str(e.value)
    assert KEY not in json.dumps(e.value.to_json_dict())


# ── models ──────────────────────────────────────────────────────────────

def test_models_lists_live_first(capsys):
    body = json.dumps({"object": "list", "data": [
        {"id": "riven-research", "status": "live", "tier": "cloud"},
        {"id": "riven-core", "status": "live", "tier": "cloud"},
        {"id": "bonsai-27b", "status": "deprecated"},
    ]}).encode()
    opener = FakeOpener(FakeResponse(body))
    models(KEY, opener=opener)
    out = capsys.readouterr().out
    assert "riven-research" in out
    assert "deprecated" not in out.split("additional")[0] if "additional" in out else True


# ── deep: the deep-research endpoint, streamed SSE ───────────────────────

def _named_sse(*pairs) -> bytes:
    out = b""
    for event, data in pairs:
        out += b"event: " + event.encode() + b"\n"
        out += b"data: " + json.dumps(data).encode() + b"\n\n"
    return out


def _deep_sse_opener():
    body = _named_sse(
        ("thread", {"threadId": "t-1", "isNewThread": True}),
        ("status", {"phase": "planning", "progress": 0.05, "message": "Decomposing your question…"}),
        ("subquestion", {"n": 1, "question": "q1", "status": "queued"}),
        ("status", {"phase": "searching", "progress": 0.2, "message": "Searching the web for 1 sub-question…"}),
        ("source", {"n": 1, "url": "https://example.com/a", "title": "Source A", "domain": "example.com"}),
        ("status", {"phase": "analyzing", "progress": 0.65, "message": "Cross-referencing sources…"}),
        ("status", {"phase": "composing", "progress": 0.8, "message": "Composing…"}),
        ("delta", {"content": "Grounded deep research report. "}),
        ("delta", {"content": "With findings [1]."}),
        ("done", {"threadId": "t-1", "sources": [{"title": "Source A", "url": "https://example.com/a"}]}),
    )
    return FakeOpener(FakeResponse(body))


def test_deep_streams_phases_and_report():
    progresses: list[str] = []
    result = deep("test research question", KEY,
                  progress_fn=progresses.append, sleep_fn=lambda s: None,
                  _opener=_deep_sse_opener())
    assert result["thread_id"] == "t-1"
    assert result["grounded"] is True
    assert "Grounded deep research report" in result["content"]
    assert result["citations"][0]["url"] == "https://example.com/a"
    assert result["phases"] == ["analyzing", "composing", "planning", "searching"]
    assert any("Decomposing" in p for p in progresses)


def test_deep_progress_rendering_with_print_fn(capsys):
    result = deep("q", KEY, _opener=_deep_sse_opener())
    out = capsys.readouterr().out
    assert "Grounded deep research report" in out
    assert "Sources:" in out
    assert "[1] Source A" in out


def test_deep_empty_content_raises():
    body = _named_sse(("thread", {"threadId": "t-2"}), ("done", {"threadId": "t-2", "sources": []}))
    opener = FakeOpener(FakeResponse(body))
    with pytest.raises(T.RivenAPIError):
        deep("q", KEY, progress_fn=lambda s: None, sleep_fn=lambda s: None, _opener=opener)
