"""Fetching papers from arXiv.

Standard library only: urllib for the request, ElementTree for the Atom feed.
arXiv asks for one request at a time with a few seconds between them, and
answers a burst with 429. Both are respected here rather than worked around.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Dict, List

from .config import ARXIV_API, ARXIV_COOLDOWN, ARXIV_MIN_INTERVAL

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "research-digest-mcp (+https://github.com/dneish2/research-digest-mcp)"

_last_request = 0.0


class ArxivUnavailable(RuntimeError):
    """arXiv refused or could not be reached. The caller decides what to tell the user."""


def _throttle() -> None:
    global _last_request
    wait = ARXIV_MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _get(params: Dict[str, Any], timeout: int = 40) -> bytes:
    _throttle()
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ArxivUnavailable(
                f"arXiv rate-limited this client. Wait {ARXIV_COOLDOWN:.0f}s and try again."
            ) from exc
        raise ArxivUnavailable(f"arXiv returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ArxivUnavailable(f"Could not reach arXiv: {exc}") from exc


def _text(node, path: str, default: str = "") -> str:
    found = node.find(path, NS)
    return (found.text or default).strip() if found is not None else default


def parse_atom(payload: bytes) -> List[Dict[str, Any]]:
    """Turn an arXiv Atom feed into plain paper dicts."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ArxivUnavailable(f"arXiv returned a response that is not valid Atom: {exc}") from exc

    papers = []
    for entry in root.findall("atom:entry", NS):
        raw_id = _text(entry, "atom:id")
        if not raw_id:
            continue
        paper_id = raw_id.rsplit("/abs/", 1)[-1]
        primary = entry.find("arxiv:primary_category", NS)
        papers.append({
            "id": paper_id,
            "title": " ".join(_text(entry, "atom:title").split()),
            "abstract": " ".join(_text(entry, "atom:summary").split()),
            "published": _text(entry, "atom:published")[:10],
            "updated": _text(entry, "atom:updated")[:10],
            "url": f"https://arxiv.org/abs/{paper_id}",
            "authors": [
                _text(a, "atom:name")
                for a in entry.findall("atom:author", NS)
            ][:8],
            "primary_category": primary.get("term") if primary is not None else "",
            "categories": [
                c.get("term") for c in entry.findall("atom:category", NS) if c.get("term")
            ],
        })
    return papers


def fetch_category(category: str, max_results: int = 60) -> List[Dict[str, Any]]:
    """Most recently submitted papers in one arXiv category."""
    payload = _get({
        "search_query": f"cat:{category}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max(1, min(max_results, 200)),
    })
    return parse_atom(payload)


def search(query: str, max_results: int = 40) -> List[Dict[str, Any]]:
    """Free-text search across title and abstract."""
    payload = _get({
        "search_query": f"all:{query}",
        "sortBy": "relevance",
        "start": 0,
        "max_results": max(1, min(max_results, 100)),
    })
    return parse_atom(payload)


def fetch_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch every configured category. Partial failure is reported, not hidden."""
    from .scoring import extract_concepts

    seen: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    per_category = max(5, settings["max_per_fetch"] // max(len(settings["categories"]), 1))

    for category in settings["categories"]:
        try:
            for paper in fetch_category(category, per_category):
                seen.setdefault(paper["id"], paper)
        except ArxivUnavailable as exc:
            errors.append(f"{category}: {exc}")

    papers = list(seen.values())
    for paper in papers:
        paper["concepts"] = extract_concepts(paper)

    return {
        "papers": papers,
        "errors": errors,
        "run_date": date.today().isoformat(),
        "categories": settings["categories"],
    }
