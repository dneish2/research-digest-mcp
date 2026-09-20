"""Reading and writing the library files.

One rule here, learned the hard way: every read and write names its encoding.
A bare Path.read_text() uses the platform's locale encoding, which on Windows is
cp1252, and a single accented author name in a 3 MB archive raises
UnicodeDecodeError. Wrapped in a broad except, that surfaces as "no results"
rather than as an error, and the library silently looks empty.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .config import ARCHIVE_PATH, READ_PATH, SAVED_PATH, ensure_home


def read_json(path: Path, default: Any) -> Any:
    """Load a JSON file as UTF-8. A missing file gives the default; a corrupt one raises."""
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{path} is not valid UTF-8 ({exc}). It may have been written by a tool "
            f"that used the platform encoding."
        ) from exc
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    """Write UTF-8 JSON atomically, so an interrupted write cannot truncate the library."""
    ensure_home()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- archive ---------------------------------------------------------------

_VERSION_SUFFIX = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")


def base_id(paper_id: str) -> str:
    """The arXiv id without its version suffix: 2605.30169v2 -> 2605.30169.

    arXiv hands out a new version suffix every time authors revise a paper, so
    the same paper arrives as a different id on a later fetch. Keying the
    archive on the raw id stored it twice, and both copies competed for slots
    in the same result list. Ids with no version suffix are returned unchanged.
    """
    match = _VERSION_SUFFIX.match(paper_id or "")
    return match.group("base") if match else (paper_id or "")


def _version_of(paper_id: str) -> int:
    """The version number in an id, or 0 when it carries none."""
    match = _VERSION_SUFFIX.match(paper_id or "")
    return int(match.group("version")) if match else 0


def _collapse_versions(papers: Dict[str, Any]) -> Dict[str, Any]:
    """Fold v1/v2/... of one paper into a single entry keyed by its base id.

    The newest version wins the record, because that is the revision the author
    intends people to read, but the entry keeps the *earliest* first_seen of the
    group -- the library first saw this paper when v1 arrived, not when the
    revision did, and trends read first_seen.
    """
    groups: Dict[str, list] = {}
    for pid, paper in papers.items():
        if isinstance(paper, dict):
            groups.setdefault(base_id(pid), []).append((pid, paper))

    collapsed: Dict[str, Any] = {}
    for base, members in groups.items():
        members.sort(key=lambda item: _version_of(item[0]))
        newest = dict(members[-1][1])
        seen = [m[1].get("first_seen") for m in members if m[1].get("first_seen")]
        if seen:
            newest["first_seen"] = min(seen)
        newest.setdefault("id", members[-1][0])
        collapsed[base] = newest
    return collapsed


def load_archive() -> Dict[str, Any]:
    archive = read_json(ARCHIVE_PATH, {"papers": {}, "runs": []})
    archive.setdefault("papers", {})
    archive.setdefault("runs", [])
    # Archives written before ids were normalised still hold v1 and v2 of the
    # same paper under two keys. Collapsing on load makes every read correct
    # immediately; the next write persists the collapsed form.
    archive["papers"] = _collapse_versions(archive["papers"])
    return archive


def load_papers() -> List[Dict[str, Any]]:
    """Every paper as a list. The id is the dict key and is copied onto the record."""
    papers = load_archive()["papers"]
    out = []
    for pid, paper in papers.items():
        if isinstance(paper, dict):
            record = dict(paper)
            record.setdefault("id", pid)
            out.append(record)
    return out


def merge_papers(new_papers: List[Dict[str, Any]], run_date: str) -> Dict[str, int]:
    """Add papers we have not seen. Existing entries keep their original first_seen."""
    archive = load_archive()
    papers = archive["papers"]
    added = 0
    for paper in new_papers:
        pid = paper.get("id")
        if not pid:
            continue
        key = base_id(pid)
        if key in papers:
            # An update must not overwrite where the paper originally came from
            # or when it arrived. Once the library is assembled from several
            # services with different coverage and different freshness, "which
            # one told me this" stops being trivia and starts being the only
            # way to defend a number on screen.
            papers[key].update({k: v for k, v in paper.items()
                                if k not in ("first_seen", "source")})
        else:
            paper = dict(paper)
            paper.setdefault("first_seen", run_date)
            paper.setdefault("source", "search")
            papers[key] = paper
            added += 1
    runs = [r for r in archive["runs"] if r != run_date]
    runs.append(run_date)
    archive["runs"] = sorted(runs)
    write_json(ARCHIVE_PATH, archive)
    return {"added": added, "total": len(papers)}


def import_papers(new_papers: List[Dict[str, Any]], run_dates=None) -> Dict[str, int]:
    """Merge papers from an external export — a full archive from another install,
    or an older version of this tool.

    Unlike merge_papers, which stamps one run date onto everything it adds, an
    import brings its own history: each new paper keeps whatever first_seen date
    it already carries (or falls back to its published date), and every date the
    export was built across is folded into `runs`, not just today.
    """
    archive = load_archive()
    papers = archive["papers"]
    added = updated = 0
    for paper in new_papers:
        pid = paper.get("id")
        if not pid:
            continue
        key = base_id(pid)
        if key in papers:
            papers[key].update({k: v for k, v in paper.items() if k != "first_seen"})
            updated += 1
        else:
            paper = dict(paper)
            paper.setdefault("first_seen", str(paper.get("published") or "")[:10] or None)
            papers[key] = paper
            added += 1
    runs = set(archive["runs"]) | {str(d)[:10] for d in (run_dates or []) if d}
    archive["runs"] = sorted(runs)
    write_json(ARCHIVE_PATH, archive)
    return {"added": added, "updated": updated, "total": len(papers)}


SOURCE_LABELS = {
    "oai": "harvested in bulk",
    "search": "matched a keyword fetch",
    "arxiv_search": "found by searching arXiv",
    "saved": "added by you, by id",
    "import": "imported from a file",
    "": "arrived before this was recorded",
}


def source_breakdown(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """How the library was assembled, by count.

    Every score in this tool explains itself. This is the same idea one level
    up: a paper is going to become an assembly of several services with
    different coverage, and "which one told me this" is how you defend what is
    on screen.
    """
    counts: Dict[str, int] = {}
    for paper in papers:
        key = str(paper.get("source") or "")
        counts[key] = counts.get(key, 0) + 1
    total = max(1, len(papers))
    return [
        {"source": key or "unrecorded",
         "label": SOURCE_LABELS.get(key, key),
         "papers": n,
         "share": round(n / total * 100, 1)}
        for key, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def month_coverage(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which months the library actually holds papers from, and which it skipped.

    A gap is invisible from every other surface. Search says "9 matched" over a
    library missing an entire month, and nothing anywhere says the month is
    missing -- so a paper that exists and was simply never fetched is
    indistinguishable from a paper that does not exist. That is the worst answer
    a research tool can give, and it is the one it gave: a July paper could not
    be found, in a library holding 402 papers from May, 369 from June and none
    at all from July.

    Counts by publication date, not by when it was fetched, because the question
    is "what part of the literature am I missing", not "when did I run this".
    """
    from datetime import date

    months: Dict[str, int] = {}
    for paper in papers:
        stamp = str(paper.get("published") or paper.get("first_seen") or "")[:7]
        if len(stamp) == 7:
            months[stamp] = months.get(stamp, 0) + 1
    if not months:
        return {"months": [], "gaps": [], "thin": [], "earliest": "", "latest": ""}

    # Only the window you actually fetch in. A library accumulates a long tail
    # of older papers -- one saved by id, one cross-listed, one pulled in by a
    # date-ranged backfill -- and measured from its true earliest month this
    # reported 215 missing months going back to 1995, which is not a gap, it is
    # a library that was not running in 1995. The window opens at the first
    # month holding a tenth of the busiest one: that is where deliberate
    # fetching started, and a hole after that point is a hole worth filling.
    busiest = max(months.values())
    floor = max(5, busiest // 10)
    active = sorted(m for m, n in months.items() if n >= floor)
    if not active:
        active = sorted(months)
    earliest, latest = active[0], max(months)

    span = []
    year, month = int(earliest[:4]), int(earliest[5:7])
    today = date.today().strftime("%Y-%m")
    while True:
        key = f"{year:04d}-{month:02d}"
        span.append(key)
        if key in (latest, today):
            break
        month += 1
        if month > 12:
            year, month = year + 1, 1
        if len(span) > 600:                      # a corrupt date must not loop forever
            break

    held = [{"month": m, "papers": months.get(m, 0)} for m in span]
    counts = sorted(m["papers"] for m in held if m["papers"])
    typical = counts[len(counts) // 2] if counts else 0
    # The current month is always incomplete, so it is never a gap.
    current = date.today().strftime("%Y-%m")
    return {
        "months": held,
        "gaps": [m["month"] for m in held
                 if m["papers"] == 0 and m["month"] != current],
        # A month holding a fraction of a normal one is a partial fetch, which
        # reads as a quiet month and is not one.
        "thin": [m["month"] for m in held
                 if m["month"] != current and 0 < m["papers"] < max(3, typical // 4)],
        "typical_month": typical,
        "window_start": earliest,
        "earliest": min(months),
        "latest": latest,
        "outside_window": sum(n for m, n in months.items() if m < earliest),
    }


# --- saved / read ----------------------------------------------------------

def load_saved() -> Dict[str, Any]:
    return read_json(SAVED_PATH, {})


def save_paper(paper_id: str, title: str, note: str = "", concepts=None) -> Dict[str, Any]:
    from datetime import datetime, timezone
    saved = load_saved()
    saved[paper_id] = {
        "title": title,
        "note": note,
        "concepts": list(concepts or []),
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json(SAVED_PATH, saved)
    return saved[paper_id]


def unsave_paper(paper_id: str) -> bool:
    saved = load_saved()
    if paper_id in saved:
        del saved[paper_id]
        write_json(SAVED_PATH, saved)
        return True
    return False


def load_read() -> Dict[str, Any]:
    return read_json(READ_PATH, {})


def mark_read(paper_id: str) -> None:
    from datetime import datetime, timezone
    entries = load_read()
    entries[paper_id] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(READ_PATH, entries)
