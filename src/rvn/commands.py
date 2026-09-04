"""Command implementations: chat (one-shot + REPL), models, deep, pages, login.

Every network path is the OpenAI-compatible gateway (default
https://api.rivenai.io/v1). Streaming uses SSE; citations render as
[n] source lines. --json emits machine-readable output for scripting.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from typing import Any, Callable, Iterator

from . import USER_AGENT, __version__
from .citations import citations_to_json, parse_answer, render_markdown
from .config import CONSOLE_URL, DEFAULT_BASE_URL
from .transport import RivenAPIError, request, stream_sse
from .transport import _DEFAULT_OPENER, _iter_lines

DEFAULT_MODEL = "riven-research"  # grounded tier — search-by-default with citations
DEEP_POLL_INTERVAL = 3.0
DEEP_TIMEOUT_S = 300


def _emit_json(obj: dict) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


# ── rvn chat ─────────────────────────────────────────────────────────────

def _build_messages(query: str, history: list[dict] | None = None) -> list[dict]:
    msgs = list(history or [])
    msgs.append({"role": "user", "content": query})
    return msgs


def _print_stream_delta(delta: str) -> None:
    sys.stdout.write(delta)
    sys.stdout.flush()


class _SourcesSuppressor:
    """Streams text through while holding back the trailing Sources: block.

    The gateway's grounded models append citations as a trailing block; the
    CLI renders its own formatted [n] lines after the stream ends, so the raw
    block must not be echoed mid-stream. Holds back any partial 'Sources:'
    prefix that could still grow into the header.
    """

    HEADER = "\nSources:"

    def __init__(self):
        self.full = ""
        self.emitted = 0

    def feed(self, delta: str) -> str:
        self.full += delta
        header_at = self.full.find(self.HEADER, self.emitted - 2 if self.emitted else 0)
        if header_at >= 0:
            end = header_at  # everything before the header is prose
        else:
            # hold back a tail that might be a partial header (e.g. "\nSou")
            hold = 0
            for k in range(1, min(len(self.HEADER), len(self.full) - self.emitted) + 1):
                if self.full.endswith(self.HEADER[:k]):
                    hold = k
            end = len(self.full) - hold
        piece = self.full[self.emitted:end]
        self.emitted = max(self.emitted, min(end, len(self.full)))
        if header_at >= 0:
            self.emitted = header_at
        return piece

    def flush(self) -> str:
        # called only when the whole answer had no Sources block
        if self.emitted < len(self.full):
            piece = self.full[self.emitted:]
            self.emitted = len(self.full)
            return piece
        return ""


def chat(
    query: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    as_json: bool = False,
    history: list[dict] | None = None,
    print_fn: Callable[[str], None] = _print_stream_delta,
    stream: bool = True,
    opener: Any = None,
) -> dict:
    """One-shot grounded ask. Returns a result dict (also used by tests).

    Streams SSE content deltas to print_fn; when the stream completes, parses
    the trailing Sources: block and renders [n] source lines (chat-surface UX).
    """
    url = f"{base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": _build_messages(query, history),
        "stream": stream,
    }
    collected: list[str] = []
    meta: dict[str, Any] = {"model": model, "request_id": None}
    suppressor = _SourcesSuppressor()

    def on_chunk(chunk: dict) -> None:
        nonlocal meta
        if chunk.get("model"):
            meta["model"] = chunk["model"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            piece = delta.get("content") or choice.get("text")
            if piece:
                collected.append(piece)
                if not as_json:
                    # stream live, but hold back the trailing Sources: block —
                    # we re-render it ourselves after the stream completes.
                    live = suppressor.feed(piece)
                    if live:
                        print_fn(live)

    if stream:
        for chunk in stream_sse(url, api_key, payload, opener=opener):
            on_chunk(chunk)
    else:
        status, body, hdrs = request(url, api_key, "POST", payload, opener=opener)
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        collected.append(content)
        if not as_json:
            live = suppressor.feed(content)
            if live:
                print_fn(live)
        meta["model"] = body.get("model", model)
        meta["request_id"] = hdrs.get("X-Riven-Request-Id")

    full = "".join(collected)
    prose, citations = parse_answer(full)
    if not as_json:
        if not citations:
            tail = suppressor.flush()
            if tail:
                print_fn(tail)
        rendered = render_markdown("", citations)
        if rendered.strip():
            print_fn("\n\n" + rendered.strip("\n"))
    return {
        "content": prose,
        "citations": citations_to_json(citations),
        "grounded": bool(citations),
        **meta,
    }


def repl(api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL) -> None:
    """Interactive mode: rvn chat -i — grounded multi-turn REPL."""
    print(f"rvn {__version__} — grounded chat on {model}. Ctrl-D to exit.")
    history: list[dict] = []
    try:
        while True:
            try:
                query = input("\r? ").strip()
            except EOFError:
                break
            if not query:
                continue
            if query.lower() in {"/exit", "/quit"}:
                break
            if query.lower() == "/clear":
                history.clear()
                print("(history cleared)")
                continue
            sys.stdout.write("\n")
            try:
                result = chat(query, api_key, base_url, model, history=history)
            except RivenAPIError as e:
                print(f"\n{e}", file=sys.stderr)
                continue
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": result["content"]})
            sys.stdout.write("\n\n")
    except KeyboardInterrupt:
        pass
    print("\nbye")


# ── rvn models ───────────────────────────────────────────────────────────

def models(api_key: str, base_url: str = DEFAULT_BASE_URL, as_json: bool = False, opener: Any = None) -> dict:
    url = f"{base_url}/models"
    status, body, _ = request(url, api_key, "GET", opener=opener)
    data = body.get("data", []) if isinstance(body, dict) else []
    if as_json:
        _emit_json({"object": "list", "data": data})
    else:
        print(f"{len(data)} models available on the Riven gateway:\n")
        live = [m for m in data if m.get("status") == "live"]
        other = [m for m in data if m.get("status") != "live"]
        print(f"  {'MODEL':38} {'STATUS':12} TIER")
        for m in live:
            print(f"  {m.get('id', ''):38} {m.get('status', ''):12} {m.get('tier', '')}")
        if other:
            print(f"\n  ({len(other)} additional non-live entries; pass --json to list all)")
    return {"object": "list", "data": data}


# ── rvn deep ──────────────────────────────────────────────────────────────

GUEST_ASK_URL = "https://rivenai.io/api/guest/ask"
PHASE_LABELS = {
    "planning": "Decomposing the question",
    "searching": "Searching the web",
    "analyzing": "Cross-referencing sources",
    "composing": "Composing the report",
}


def deep(
    query: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    as_json: bool = False,
    poll_interval: float = DEEP_POLL_INTERVAL,
    timeout_s: float = DEEP_TIMEOUT_S,
    progress_fn: Callable[[str], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    opener: Any = None,
    _opener: Any = None,
    endpoint: str = GUEST_ASK_URL,
) -> dict:
    """Deep research — the deep-research endpoint, streamed end to end.

    POSTs {message, mode: "deep_research"} to the deep-research surface
    (same SSE pipeline behind the /research web surface): it emits
    event: status / subquestion / source progress frames while the
    planning → searching → analyzing → composing pipeline runs, then a
    cited report. We render each phase live and the sources as [n] lines.
    """
    if _opener is not None:
        opener = _opener
    if progress_fn is None:
        if as_json:
            progress_fn = lambda s: None  # noqa: E731
        else:
            def progress_fn(s: str) -> None:
                print(f"\r  {s}", end="", flush=True)

    collected: list[str] = []
    sources: list[dict] = []
    phases_seen: set[str] = set()
    subqs: dict[int, dict] = {}
    thread_id = None
    suppressor = _SourcesSuppressor()

    payload = {
        "message": query,
        "model": "riven-research",
        "mode": "deep_research",
    }
    events = _post_sse_events(endpoint, api_key, payload, timeout_s, opener)
    current_phase = None
    for event, data in events:
        if event == "thread":
            thread_id = data.get("threadId")
            progress_fn("research thread opened")
        elif event == "status":
            phase = data.get("phase", "")
            msg = data.get("message", "")
            if phase and phase not in phases_seen:
                phases_seen.add(phase)
                progress_fn(PHASE_LABELS.get(phase, phase))
            elif msg:
                progress_fn(msg[:70])
            current_phase = phase
        elif event == "subquestion":
            n = data.get("n")
            if n is not None:
                subqs[n] = data
                progress_fn(f"sub-question {n}/{max(subqs) if subqs else n}: {str(data.get('question', ''))[:60]}")
        elif event == "source":
            sources.append(data)
        elif event == "delta":
            piece = data.get("content") or ""
            if piece:
                if not collected:
                    if "composing" not in phases_seen:
                        phases_seen.add("composing")
                        progress_fn("composing — streaming the report")
                collected.append(piece)
                if not as_json:
                    live = suppressor.feed(piece)
                    if live:
                        sys.stdout.write("\r" + " " * 74 + "\r")
                        sys.stdout.write(live)
                        sys.stdout.flush()
        elif event == "done":
            for s in data.get("sources") or []:
                if s not in sources:
                    sources.append(s)
        elif event == "related":
            continue

    full = "".join(collected)
    if not full:
        raise RivenAPIError(502, {"message": "deep research returned no content"})
    # The research model may inline its own Sources: block — strip it and
    # prefer its parsed citations; merge with any event/done sources by URL.
    prose, inline_cits = parse_answer(full)
    if inline_cits:
        for c in inline_cits:
            if not any(s.get("url") == c.url for s in sources):
                sources.append({"title": c.title, "url": c.url})
    else:
        prose = prose.strip()
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for s in sources:
        u = s.get("url", "")
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(s)
    sources = deduped
    if not as_json:
        if sources:
            sys.stdout.write("\n\nSources:")
            for i, s in enumerate(sources, 1):
                sys.stdout.write(f"\n[{i}] {s.get('title', s.get('domain', ''))} — {s.get('url', '')}")
        sys.stdout.write("\n\n(" + str(len(sources)) + " sources · thread " + str(thread_id) + ")\n")
    return {
        "content": prose,
        "citations": [
            {"number": i, "title": s.get("title", ""), "url": s.get("url", "")}
            for i, s in enumerate(sources, 1)
        ],
        "grounded": bool(sources),
        "thread_id": thread_id,
        "phases": sorted(phases_seen),
        "model": "riven-research",
    }


def _post_sse_events(
    url: str,
    api_key: str,
    payload: dict,
    timeout_s: float,
    opener: Any = None,
) -> Iterator[tuple[str, dict]]:
    """POST to the deep-research surface and yield (event, data) SSE pairs.

    The surface emits named events (`event: status` + `data: {...}`), unlike
    the OpenAI-style unnamed SSE the inference gateway uses.
    """
    data = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "text/event-stream",
    }
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    resp = (opener or _DEFAULT_OPENER).open(req, timeout=timeout_s)
    try:
        event_name = "message"
        data_lines: list[str] = []

        def flush() -> Iterator[tuple[str, dict]]:
            nonlocal data_lines
            if data_lines:
                raw = "\n".join(data_lines).strip()
                data_lines = []
                if raw and raw != "[DONE]":
                    try:
                        yield event_name, json.loads(raw)
                    except json.JSONDecodeError:
                        pass

        for line in _iter_lines(resp):
            text = line.decode("utf-8", "replace")
            if not text:
                for pair in flush():
                    yield pair
                event_name = "message"
                continue
            if text.startswith("event:"):
                event_name = text[6:].strip()
            elif text.startswith("data:"):
                data_lines.append(text[5:].strip())
            elif text.startswith(":"):
                continue  # keepalive comment
        for pair in flush():
            yield pair
    finally:
        try:
            resp.close()
        except Exception:
            pass


# ── rvn pages ─────────────────────────────────────────────────────────────

def pages(
    topic: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    as_json: bool = False,
    tier: str = "research",
    web_base: str = "https://rivenai.io",
) -> dict:
    """Generate a Riven Page (Pages v0.1 API).

    Mirrors the documented v0.1 contract: POST {title, prompt, tier} to the
    Pages generation route, which returns {slug, url, citation_count, ...}.
    Note (verified 2026-09-04): the public Pages surface is session-gated —
    API-key callers receive 401 NO_SESSION / 404; run  rvn login  and use the
    console to publish pages until the key-auth route ships.
    """
    title = topic.strip()[:120] or "Riven Page"
    web_url = web_base.rstrip("/") + "/api/pages/generate"
    # The Pages API lives on the web surface, not the inference gateway.
    try:
        status, body, _ = request(web_url, api_key, "POST", {"title": title, "prompt": topic, "tier": tier}, opener=opener)
        page = {
            "slug": body.get("slug"),
            "url": body.get("url"),
            "title": body.get("title", title),
            "tier": body.get("tier", tier),
            "citation_count": body.get("citation_count"),
            "grounding_real": body.get("grounding_real"),
            "generated_at": body.get("generated_at"),
        }
    except RivenAPIError as e:
        if e.status in (401, 404):
            page = {
                "error": "pages_key_auth_not_yet_shipped",
                "message": (
                    "The Pages v0.1 API currently authenticates console sessions, "
                    "not API keys. Generate pages from the console ("
                    + web_base + "/pages) until key-auth ships."
                ),
                "hint": "Run rvn pages --help for details; this command will go live with the Pages key-auth route.",
            }
        else:
            raise
    if as_json:
        _emit_json(page)
    else:
        if "error" in page:
            print(f"rvn pages: {page['message']}")
        else:
            print(f"Published: {page['url']}")
            print(f"  title:    {page['title']}")
            print(f"  tier:     {page['tier']}")
            print(f"  citations: {page['citation_count']} (grounding_real={page['grounding_real']})")
    return page


# ── rvn login ─────────────────────────────────────────────────────────────

def login(flag_key: str | None = None) -> dict:
    """Store an API key in ~/.rvn/key (minted via the console flow, or pasted)."""
    from .config import resolve_key, store_key, stored_key_location

    if flag_key:
        path = store_key(flag_key)
        return {"stored": True, "path": str(path)}
    print("Riven CLI login")
    print(f"1. Open the console key manager: {CONSOLE_URL}")
    print("2. Mint a key (they start with rvn_).")
    print("3. Paste it below. (Input is hidden; the key is never displayed.)\n")
    try:
        import getpass

        key = getpass.getpass("API key: ").strip()
    except Exception:
        key = input("API key: ").strip()
    if not key:
        print("No key entered — nothing stored.", file=sys.stderr)
        raise SystemExit(1)
    path = store_key(key)
    print(f"Key stored at {path} (chmod 600). Try:  rvn chat \"who is the CEO of NVIDIA\"")
    return {"stored": True, "path": str(path)}


def whoami(api_key: str, base_url: str = DEFAULT_BASE_URL, as_json: bool = False) -> dict:
    """Sanity check: list models with the stored key (auth validation)."""
    result = models(api_key, base_url, as_json=as_json)
    if not as_json:
        loc = stored_key_location()
        print(f"\nAuth OK. Key file: {loc}")
    return result
