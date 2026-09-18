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
# When arXiv last refused us. Kept on disk rather than in memory because the
# process that earns the 429 usually exits straight afterwards, and the next
# run a minute later would otherwise walk into the same wall and deepen it.
COOLDOWN_PATH = HOME / "arxiv-cooldown.json"
# Bookkeeping the tool writes about itself, kept out of settings.json. Writing
# it back through save_settings(load_settings()) would persist the merged
# defaults into the user's own file, quietly pinning them to whatever the
# defaults were on the day they first ran fetch, so later upgrades to
# DEFAULT_SETTINGS would never reach them.
STATE_PATH = HOME / "state.json"

# arXiv is polite about this: one request at a time, a few seconds apart.
#
# The interval is 5s rather than arXiv's stated 3s minimum because a profile
# fetch is no longer 5 requests. Three tiers across fourteen categories is
# fourteen requests per run, and at 3s that burst is close enough to arXiv's
# limit that a run which also backfills a date range gets refused partway
# through -- which used to leave the library with a partial day and no record
# of which categories were missing.
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_MIN_INTERVAL = 5.0
ARXIV_COOLDOWN = 300.0

DEFAULT_SETTINGS = {
    "categories": ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "cs.SE"],
    "topics": [
        "agent", "evaluation", "reasoning", "retrieval",
        "multi-agent", "reliability", "interpretability",
    ],
    "encoder": "tfidf-svd",
    "dims": 384,
}
# `max_per_fetch` used to live here. It was a total split across categories, so
# five categories meant twelve papers each, and nothing read it once fetching
# became tiered. Depth is now per tier, per category: see TIER_DEFAULTS. A
# leftover max_per_fetch in a user's settings.json is ignored, not an error.

# How many papers a tier asks arXiv for, per category, per run. These are the
# numbers that decide whether the library actually grows: cs.LG alone posts a
# few hundred papers a day, so a small number here means each run sees the last
# hour of submissions and silently misses the rest.
TIER_DEFAULTS = {
    "core": 60,
    "complementary": 30,
    "stretch": 20,
}

# arXiv's search_query is a URL parameter, so it cannot carry an unbounded
# keyword list. Each run uses a window of this many keywords and advances the
# window next time, so a long topic list is covered across several runs instead
# of being truncated to the same first few forever.
KEYWORDS_PER_QUERY = 6


def _as_list(value) -> list:
    if isinstance(value, str):
        return [value]
    return [v for v in (value or []) if v]


def ranking_topics(settings: dict | None = None) -> list:
    """Every term a paper may be scored against, across all three tiers.

    The scorer used to be handed only the flat `topics` list, which is the core
    tier. That was harmless while core was all there was, and became a silent
    hole the moment fetching grew tiers: `rank_all` drops a paper that matched
    nothing, so a stretch paper fetched for "confounding" scored zero against a
    list that does not contain "confounding" and disappeared from every surface.
    Fetched and invisible is worse than not fetched.

    Measured over a 1,443-paper library: widening from the core list to all
    tiers keeps 19 of 29 stretch papers instead of 12, keeps 96 more papers
    overall, and moves the top ten by one position. The base term divides by the
    topic count, so a longer list does lower scores slightly; the median moved
    0.1704 -> 0.1656, which does not reorder anything that matters.
    """
    return load_profile(settings)["all_topics"]


def load_profile(settings: dict | None = None) -> dict:
    """The interest profile that decides what gets fetched and how it scores.

    Three tiers, following the model the original tool used:

      core           the subject you work in; fetched deepest
      complementary  adjacent lanes that should season the feed, not flood it
      stretch        fields you do not work in, fetched for method transfer:
                     matched on structural keywords (identification, ablation,
                     confounding) rather than on subject matter

    Back-compatible on purpose. A settings.json written by an older version has
    no "profile" key, only flat `categories` and `topics`; that shape is read as
    a core tier so nobody's config breaks on upgrade.
    """
    settings = settings if settings is not None else load_settings()
    raw = settings.get("profile") or {}

    core = dict(raw.get("core") or {})
    # The flat legacy fields are the source of truth when no core tier is set.
    core.setdefault("categories", settings.get("categories", []))
    core.setdefault("topics", settings.get("topics", []))

    tiers = {}
    for name in ("core", "complementary", "stretch"):
        tier = core if name == "core" else dict(raw.get(name) or {})
        keywords = _as_list(tier.get("topics")) + _as_list(tier.get("structural_keywords"))
        tiers[name] = {
            "categories": _as_list(tier.get("categories")),
            "topics": _as_list(tier.get("topics")),
            "structural_keywords": _as_list(tier.get("structural_keywords")),
            "keywords": keywords,
            "per_category": int(tier.get("per_category") or TIER_DEFAULTS[name]),
        }

    return {
        "work_context": raw.get("work_context", ""),
        "tiers": tiers,
        # Everything the scorer should treat as an interest, across all tiers.
        "all_topics": list(dict.fromkeys(
            sum((tiers[t]["topics"] for t in tiers), [])
            + tiers["stretch"]["structural_keywords"]
        )),
        "all_categories": list(dict.fromkeys(
            sum((tiers[t]["categories"] for t in tiers), [])
        )),
    }


def tier_index(profile: dict) -> dict:
    """category -> tier name, for labelling a paper with why it is here.

    Papers only carry a stored `tier` if they arrived through a tiered fetch,
    and `import` drops the field entirely, so on a real library the stored
    value is missing almost everywhere. Deriving it from the category means the
    label works for every paper, including the 1,443 that predate the field.
    Core wins a tie, because a category in two tiers is a category you work in.
    """
    index = {}
    for name in ("stretch", "complementary", "core"):
        for category in profile["tiers"].get(name, {}).get("categories", []):
            index[category] = name
    return index


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


def now_stamp() -> str:
    """Local time with its UTC offset, e.g. 2026-09-17T14:06:31-05:00.

    A date alone cannot answer "did the scheduled fetch run this morning, or is
    this yesterday's library" -- the header said "last fetch today" from one
    minute past midnight until midnight again. The offset is carried so the
    stamp still reads correctly after a timezone change or a laptop that moved.
    """
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state() -> dict:
    """Tool bookkeeping. Never the user's settings, and never fatal."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def record_fetch(added: int, total: int, source: str = "cli",
                 next_offset: int | None = None) -> dict:
    """Write down that a fetch just happened, and when, to the second.

    Called from every path that can fetch -- the CLI, the browser button, the
    MCP tool -- because a timestamp that only one of three entry points writes
    is worse than none: it makes the library look stale precisely when it was
    refreshed by the other two.
    """
    state = load_state()
    state["last_fetch_at"] = now_stamp()
    state["last_fetch"] = now_stamp()[:10]
    state["last_added"] = added
    state["last_total"] = total
    state["last_fetch_source"] = source
    if next_offset is not None:
        state["fetch_offset"] = next_offset
    save_state(state)
    return state


def save_state(state: dict) -> None:
    try:
        ensure_home()
        STATE_PATH.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass          # bookkeeping we cannot persist is not worth failing a fetch over
