"""Key resolution — env, --key flag, stored key file. Never prints the key."""

from __future__ import annotations

import os
import stat
from pathlib import Path

DEFAULT_BASE_URL = "https://api.rivenai.io/v1"
KEY_DIR = Path.home() / ".rvn"
KEY_FILE = KEY_DIR / "key"
CONSOLE_URL = "https://platform.rivenai.io/console/keys"
TOPUP_URL = "https://chat.rivenai.io/settings/billing?tab=quota"
PAYG_URL = "https://platform.rivenai.io/pricing"

VALID_PREFIXES = ("rvn_",)


class MissingKeyError(RuntimeError):
    """No API key could be resolved."""


def _check_prefix(key: str) -> str:
    key = key.strip()
    if not key.startswith(VALID_PREFIXES):
        raise ValueError(
            "Riven API keys start with 'rvn_'. Paste the full key from "
            f"{CONSOLE_URL}"
        )
    return key


def resolve_key(flag_key: str | None = None, key_file: str | None = None) -> str:
    """Resolution order: --key flag > RIVEN_API_KEY env > stored key file.

    Raises MissingKeyError with a human-friendly remedy message.
    """
    if flag_key:
        return _check_prefix(flag_key)
    env = os.environ.get("RIVEN_API_KEY", "").strip()
    if env:
        return _check_prefix(env)
    path = Path(key_file) if key_file else KEY_FILE
    if path.is_file():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return _check_prefix(stored)
    raise MissingKeyError(
        "No API key found. Either:\n"
        "  1. run  rvn login  (paste a key from the console), or\n"
        f"  2. set the RIVEN_API_KEY environment variable, or\n"
        "  3. pass it once with  --key rvn_...\n"
        f"Keys are minted at the console: {CONSOLE_URL}"
    )


def store_key(key: str) -> Path:
    """Write the key to ~/.rvn/key with 0600 perms. Returns the path."""
    key = _check_prefix(key)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key + "\n", encoding="utf-8")
    KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return KEY_FILE


def stored_key_location() -> Path:
    return KEY_FILE
