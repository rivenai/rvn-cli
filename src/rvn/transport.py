"""Transport layer — stdlib-only HTTP client with SSE streaming.

Every request goes to the Riven gateway's OpenAI-compatible API. The API key
is only ever placed in the Authorization header; it is never logged, printed,
or echoed in error messages.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from . import USER_AGENT
from .config import PAYG_URL, TOPUP_URL

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 3


class RivenAPIError(RuntimeError):
    """Typed error raised for any non-2xx gateway response."""

    def __init__(self, status: int, body: dict | str, request_id: str | None = None):
        self.status = status
        self.body = body
        self.request_id = request_id
        super().__init__(self.friendly_message())

    # -- friendly, human-facing message ------------------------------------
    def friendly_message(self) -> str:
        b = self.body if isinstance(self.body, dict) else {}
        code = b.get("code") or (b.get("error") if isinstance(b.get("error"), dict) else {}).get("code")
        message = b.get("message") or (
            b.get("error").get("message") if isinstance(b.get("error"), dict) else None
        ) or (b if isinstance(self.body, str) else "request failed")

        if self.status == 401:
            return (
                "Invalid or missing API key (401).\n"
                "  Run  rvn login  to store a key, or set RIVEN_API_KEY.\n"
                "  Keys are minted at the console: https://platform.rivenai.io/console/keys"
            )
        if self.status == 402:
            if code == "quota_exceeded":
                used = b.get("used_actions")
                limit = b.get("monthly_action_limit")
                detail = f" ({used}/{limit} used this cycle)" if used is not None and limit is not None else ""
                return (
                    "Your monthly quota has been used up (402)." + detail + "\n"
                    f"  Top up or upgrade: {TOPUP_URL}\n"
                    f"  Or switch to pay-as-you-go: {PAYG_URL}"
                )
            if code == "ALLOTMENT_EXCEEDED":
                return (
                    "Monthly Computer Mode action allotment exceeded (402).\n"
                    "  Upgrade your plan: https://platform.rivenai.io/console/computer/plan\n"
                    f"  Or switch to pay-as-you-go: {PAYG_URL}"
                )
            return (
                "Payment required (402).\n"
                f"  Top up: {TOPUP_URL}  —  PAYG: {PAYG_URL}"
            )
        if self.status == 429:
            retry = b.get("retry_after") or b.get("retry_after_ms")
            hint = f" Retry in ~{retry}s." if retry else ""
            return f"Rate limit reached (429).{hint} Wait a moment and try again."
        if self.status == 404:
            return f"Not found (404): {message}"
        if self.status in (400, 422):
            return f"Invalid request ({self.status}): {message}"
        return f"Gateway error ({self.status}): {message}"

    def to_json_dict(self) -> dict:
        return {
            "error": {
                "status": self.status,
                "code": (self.body or {}).get("code") if isinstance(self.body, dict) else None,
                "message": str(self),
                "request_id": self.request_id,
            }
        }


def _headers(api_key: str, extra: dict | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _read_error(e: urllib.error.HTTPError) -> RivenAPIError:
    raw = b""
    try:
        raw = e.read()
    except Exception:
        pass
    try:
        body: Any = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        body = raw.decode("utf-8", "replace")[:400]
    return RivenAPIError(e.code, body, e.headers.get("X-Riven-Request-Id"))


def request(
    url: str,
    api_key: str,
    method: str = "GET",
    payload: dict | None = None,
    extra_headers: dict | None = None,
    timeout: float = 120.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, Any, dict]:
    """Non-streaming JSON request with retry on transient failures.

    Returns (status, parsed_json_or_text, response_headers).
    Raises RivenAPIError on non-2xx after retries.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    last_error: RivenAPIError | None = None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=_headers(api_key, extra_headers))
        try:
            resp = (opener or _DEFAULT_OPENER).open(req, timeout=timeout)
            raw = resp.read()
            hdrs = dict(resp.headers)
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                parsed = raw.decode("utf-8", "replace")[:400]
            return resp.status, parsed, hdrs
        except urllib.error.HTTPError as e:
            err = _read_error(e)
            if e.code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                last_error = err
                time.sleep(0.5 * (2**attempt))
                continue
            raise err
        except urllib.error.URLError as e:
            # network-level failure — retry, then raise a friendly error
            if attempt < MAX_RETRIES:
                time.sleep(0.5 * (2**attempt))
                continue
            raise RivenAPIError(0, {"message": f"Could not reach the Riven gateway: {e.reason}"})
    raise last_error or RivenAPIError(0, {"message": "request failed"})


def stream_sse(
    url: str,
    api_key: str,
    payload: dict | None = None,
    method: str = "POST",
    extra_headers: dict | None = None,
    timeout: float = 300.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> Iterator[dict]:
    """POST and yield parsed SSE event payloads (`data: {...}` lines).

    Retries connection errors before the first byte arrives; once streaming
    has started, failures surface as RivenAPIError / RuntimeError.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=_headers(api_key, extra_headers))
        try:
            resp = (opener or _DEFAULT_OPENER).open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            err = _read_error(e)
            if e.code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                last_error = err
                time.sleep(0.5 * (2**attempt))
                continue
            raise err
        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                last_error = e
                time.sleep(0.5 * (2**attempt))
                continue
            raise RivenAPIError(0, {"message": f"Could not reach the Riven gateway: {e.reason}"})
        # stream and parse SSE
        try:
            for line in _iter_lines(resp):
                if not line or not line.startswith(b"data:"):
                    continue
                chunk_raw = line[5:].strip()
                if not chunk_raw or chunk_raw == b"[DONE]":
                    if chunk_raw == b"[DONE]":
                        return
                    continue
                try:
                    yield json.loads(chunk_raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return
    raise last_error or RivenAPIError(0, {"message": "stream failed"})


def _iter_lines(resp: Any) -> Iterator[bytes]:
    """Yield lines from an HTTP response, tolerating partial SSE frames."""
    buf = b""
    while True:
        chunk = resp.read(1024)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.rstrip(b"\r")
    if buf:
        yield buf.rstrip(b"\r")


_DEFAULT_OPENER = urllib.request.build_opener()


def build_opener_custom() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener()
