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

def load_archive() -> Dict[str, Any]:
    archive = read_json(ARCHIVE_PATH, {"papers": {}, "runs": []})
    archive.setdefault("papers", {})
    archive.setdefault("runs", [])
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
        if pid in papers:
            papers[pid].update({k: v for k, v in paper.items() if k != "first_seen"})
        else:
            paper = dict(paper)
            paper.setdefault("first_seen", run_date)
            papers[pid] = paper
            added += 1
    runs = [r for r in archive["runs"] if r != run_date]
    runs.append(run_date)
    archive["runs"] = sorted(runs)
    write_json(ARCHIVE_PATH, archive)
    return {"added": added, "total": len(papers)}


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
