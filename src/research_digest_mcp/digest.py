"""The daily digest: a deterministic, dated top-N.

This is the feature the tool is named for, and the rebuild shipped without it —
`rank_all` sorted by score is a live view, not a digest. A digest is something
narrower: a small, dated, reproducible selection you can skim once and be done
with, the way the bridge version's `research-digest.md` worked.

Deterministic on purpose: the same library, the same topics and the same date
always produce the same picks. Nothing here calls a model or a random number
generator, so running it twice does not reshuffle what you already skimmed.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import HOME
from .scoring import about_sentence, explain_sentence, rank_all

DIGESTS_DIR = HOME / "digests"

DEFAULT_SIZE = 5
# At most this many picks may share a primary_category, so one prolific
# category (there is always one) cannot fill every slot. A cheap stand-in for
# the bridge's category-diversity pass, without a second axis to configure.
PER_CATEGORY_CAP = 2


def _pick(ranked: List[Dict[str, Any]], size: int) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    per_category: Dict[str, int] = {}
    for paper in ranked:
        category = paper.get("primary_category", "")
        if per_category.get(category, 0) >= PER_CATEGORY_CAP:
            continue
        picks.append(paper)
        per_category[category] = per_category.get(category, 0) + 1
        if len(picks) >= size:
            break
    return picks


def build_digest(papers: List[Dict[str, Any]], topics: List[str],
                  for_date: Optional[str] = None, size: int = DEFAULT_SIZE) -> Dict[str, Any]:
    """Rank the library against `topics` and pick the day's top `size`, capped
    per category. Every pick carries its `about` line and its `pick_reason` (the
    same explain_sentence used everywhere else), so the digest is self-contained
    — it does not need the live app to make sense of a pick a week later."""
    for_date = for_date or _date.today().isoformat()
    ranked = rank_all(papers, topics)
    picks = _pick(ranked, max(size, 0))
    for paper in picks:
        paper["about"] = about_sentence(paper)
        paper["pick_reason"] = explain_sentence(paper["why"])
    return {
        "date": for_date,
        "considered": len(ranked),
        "topics": list(topics),
        "picks": picks,
    }


def render_markdown(digest: Dict[str, Any]) -> str:
    lines = [f"# Research digest — {digest['date']}", ""]
    lines.append(
        f"{len(digest['picks'])} of {digest['considered']} papers matching "
        f"{', '.join(digest['topics']) or 'your topics'}, best first."
    )
    lines.append("")
    for i, paper in enumerate(digest["picks"], 1):
        lines.append(f"## {i}. {paper.get('title', '')}")
        lines.append(
            f"{paper.get('id', '')} · {paper.get('published', '')} · "
            f"{paper.get('primary_category', '')}"
        )
        lines.append("")
        if paper.get("about"):
            lines.append(paper["about"])
            lines.append("")
        url = paper.get("url", "")
        if url:
            lines.append(f"[{url}]({url})")
            lines.append("")
        if paper.get("pick_reason"):
            lines.append(f"> {paper['pick_reason']}")
            lines.append("")
    return "\n".join(lines)


def write_digest(digest: Dict[str, Any]) -> Path:
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGESTS_DIR / f"{digest['date']}.md"
    path.write_text(render_markdown(digest), encoding="utf-8")
    return path
