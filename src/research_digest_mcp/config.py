"""Paths and settings.

Everything lives in one directory. Point RESEARCH_DIGEST_HOME wherever you like;
the default is ~/.research-digest. Nothing here reaches the network or the
registry, and no file outside this directory is ever written.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def force_utf8_streams(*streams) -> None:
    """Make the given text streams write UTF-8 whatever the console codepage is.

    arXiv titles and author names are full of accents, dashes and the occasional
    CJK or Greek character. On Windows, stdout/stderr default to the locale
    codepage (cp1252), which cannot represent most of that: writing it either
    raises UnicodeEncodeError and kills the process, or -- for characters cp1252
    *can* encode -- writes bytes that are not valid UTF-8, which silently
    corrupts a JSON-RPC stream an MCP client is reading.

    Every entry point that prints a paper has to call this. It lives here, in
    one place, so a new entry point cannot quietly reintroduce the bug.
    """
    for stream in (streams or (sys.stdout, sys.stderr)):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")


HOME = Path(os.environ.get("RESEARCH_DIGEST_HOME", Path.home() / ".research-digest"))

ARCHIVE_PATH = HOME / "archive.json"
SAVED_PATH = HOME / "saved.json"
READ_PATH = HOME / "read.json"
SETTINGS_PATH = HOME / "settings.json"
EMBEDDINGS_DB = HOME / "embeddings.db"

# arXiv is polite about this: one request at a time, a few seconds apart.
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_MIN_INTERVAL = 3.0
ARXIV_COOLDOWN = 90.0

DEFAULT_SETTINGS = {
    "categories": ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "cs.SE"],
    "topics": [
        "agent", "evaluation", "reasoning", "retrieval",
        "multi-agent", "reliability", "interpretability",
    ],
    "max_per_fetch": 60,
    "encoder": "tfidf-svd",
    "dims": 384,
}


def ensure_home() -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    return HOME


def load_settings() -> dict:
    """Settings merged over the defaults. Missing or unreadable file is not an error."""
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                settings.update(user)
        except (OSError, ValueError) as exc:
            # Loud, not silent: a typo in settings.json should not look like a default.
            raise ValueError(f"{SETTINGS_PATH} is not valid JSON: {exc}") from exc
    return settings


def save_settings(settings: dict) -> None:
    ensure_home()
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
