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

from .config import (
    ARXIV_API, ARXIV_COOLDOWN, ARXIV_MAX_COOLDOWN, ARXIV_MIN_INTERVAL,
    COOLDOWN_PATH, KEYWORDS_PER_QUERY)

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "research-digest-mcp (+https://github.com/dneish2/research-digest-mcp)"

_last_request = 0.0


class ArxivUnavailable(RuntimeError):
    """arXiv refused or could not be reached. The caller decides what to tell the user."""


class ArxivCoolingDown(ArxivUnavailable):
    """We were refused recently and the cooldown has not expired."""


def _throttle() -> None:
    global _last_request
    wait = ARXIV_MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _cooldown_state() -> dict:
    try:
        import json
        state = json.loads(COOLDOWN_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def cooldown_remaining() -> float:
    """Seconds left before arXiv should be asked again. 0 when clear."""
    try:
        return max(0.0, float(_cooldown_state().get("until", 0)) - time.time())
    except (TypeError, ValueError):
        return 0.0


def cooldown_detail() -> dict:
    """The full backoff picture, for a surface that has to explain a refusal."""
    state = _cooldown_state()
    return {
        "remaining": cooldown_remaining(),
        "strikes": int(state.get("strikes", 0) or 0),
        "last_code": state.get("code"),
        "since": state.get("since", ""),
    }


def _begin_cooldown(code: int = 429) -> None:
    """Record a refusal so the next run waits instead of repeating it.

    A 429 or a 406 is not a transient error to retry past; it is arXiv asking
    for a pause. Retrying through it is what turns a short throttle into a long
    block, so this is written to disk and checked before every request.

    The wait escalates. A flat five minutes was wrong in exactly the way that
    matters: five minutes after a block we would ask again, get refused again,
    and reset the same five minutes, so a client that had annoyed arXiv stayed
    in a loop of politely-spaced refusals indefinitely and every surface read
    "arXiv returned nothing". Each consecutive strike roughly quadruples the
    wait, up to an hour, and a success clears the count.
    """
    import json
    state = _cooldown_state()
    strikes = int(state.get("strikes", 0) or 0) + 1
    wait = min(ARXIV_COOLDOWN * (4 ** (strikes - 1)), ARXIV_MAX_COOLDOWN)
    try:
        COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOLDOWN_PATH.write_text(json.dumps({
            "until": time.time() + wait,
            "strikes": strikes,
            "code": code,
            "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "waited": wait,
        }), encoding="utf-8")
    except OSError:
        pass          # a cooldown we cannot persist is not worth failing over


def note_success() -> None:
    """A request got through, so the escalation resets.

    Only called from a real parsed response. The block is per-client and
    expires on arXiv's schedule, not ours, so the only evidence that we are
    forgiven is an answer.
    """
    if COOLDOWN_PATH.exists():
        clear_cooldown()


def clear_cooldown() -> None:
    COOLDOWN_PATH.unlink(missing_ok=True)


def _get(params: Dict[str, Any], timeout: int = 40) -> bytes:
    """One arXiv request, retried on a timeout.

    A timeout is not the same failure as a refusal. arXiv is frequently slow
    rather than down, and the previous behaviour -- give up, report the category
    as failed, move on -- turned an ordinary slow response into a permanent hole
    in the library, because the next run starts from the newest paper and never
    goes back. Timeouts escalate and retry; a 429 or an HTTP error does not,
    since retrying those is what earned the 429 in the first place.
    """
    left = cooldown_remaining()
    if left > 0:
        detail = cooldown_detail()
        raise ArxivCoolingDown(
            f"arXiv is refusing this client (HTTP {detail.get('last_code', 429)} at "
            f"{detail.get('since', 'recently')}). Waiting {left / 60:.0f} more minutes "
            f"before asking again; this is refusal number {detail.get('strikes', 1)} in "
            f"a row, so the wait has been lengthened. Nothing is wrong with your "
            f"library or your query.")

    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    timeouts = (timeout, timeout * 2)

    for attempt, limit in enumerate(timeouts):
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=limit) as response:
                # Deliberately not clearing the cooldown here. arXiv sits behind
                # a CDN, so a 200 can be an edge cache hit served while the
                # origin is still refusing this client -- and a popular category
                # feed is exactly the kind of URL that stays warm. Treating that
                # as "we are forgiven" would lift the backoff on the strength of
                # a response the origin never saw. The cooldown expires on time
                # instead.
                return response.read()
        except urllib.error.HTTPError as exc:
            # 429 is the documented refusal. 406 is what arXiv returns in
            # practice when it has decided a client is asking too often, so it
            # is treated the same way rather than as a malformed request.
            if exc.code in (429, 406):
                _begin_cooldown(exc.code)
                left = cooldown_remaining()
                raise ArxivUnavailable(
                    f"arXiv refused this client (HTTP {exc.code}). This is a rate "
                    f"limit on us, not a problem with the search: a request for one "
                    f"paper gets the same answer. Backing off {left / 60:.0f} minutes. "
                    f"Nothing was written, and your library is unchanged."
                ) from exc
            raise ArxivUnavailable(f"arXiv returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == len(timeouts) - 1:
                raise ArxivUnavailable(f"Could not reach arXiv: {exc}") from exc
            time.sleep(2)
    raise ArxivUnavailable("Could not reach arXiv.")


def _text(node, path: str, default: str = "") -> str:
    found = node.find(path, NS)
    return (found.text or default).strip() if found is not None else default


def parse_atom(payload: bytes) -> List[Dict[str, Any]]:
    """Turn an arXiv Atom feed into plain paper dicts."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ArxivUnavailable(f"arXiv returned a response that is not valid Atom: {exc}") from exc

    # A parsed Atom feed is the only proof that arXiv is serving us again, so
    # the escalating backoff resets here and nowhere else. Not on a 200: arXiv
    # sits behind a CDN and a cached edge response can arrive while the origin
    # is still refusing this client.
    note_success()

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


def _quote(keyword: str) -> str:
    """One keyword as an arXiv title-or-abstract clause."""
    safe = keyword.replace('"', "").strip()
    return f'ti:"{safe}" OR abs:"{safe}"'


def build_query(category: str, keywords: List[str] | None = None,
                since: str | None = None, until: str | None = None) -> str:
    """The arXiv search_query for one category, narrowed by interest and date.

    Without keywords this asks for everything in the category and takes whatever
    the newest N happen to be, which is how the library used to miss most of its
    own subject: 12 papers out of a category that posts a few hundred a day is
    the last hour of submissions, not the day's. Keywords push the filtering to
    arXiv's side, so the N that come back are N papers about what you care
    about rather than N papers that happened to be posted most recently.
    """
    query = f"cat:{category}"
    if keywords:
        clause = " OR ".join(_quote(k) for k in keywords if k.strip())
        if clause:
            query = f"{query} AND ({clause})"
    if since or until:
        lo = (since or "1991-01-01").replace("-", "") + "0000"
        hi = (until or date.today().isoformat()).replace("-", "") + "2359"
        query = f"{query} AND submittedDate:[{lo} TO {hi}]"
    return query


def fetch_category(category: str, max_results: int = 60,
                   keywords: List[str] | None = None,
                   since: str | None = None, until: str | None = None,
                   start: int = 0) -> List[Dict[str, Any]]:
    """Most recently submitted papers in one arXiv category.

    `keywords` narrows the request to papers whose title or abstract contains at
    least one of them. `since`/`until` bound it by submission date, which is what
    makes a missed day recoverable: without a date range every request starts at
    the newest paper and the gap can never be reached.
    """
    payload = _get({
        "search_query": build_query(category, keywords, since, until),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": max(0, start),
        "max_results": max(1, min(max_results, 200)),
    })
    return parse_atom(payload)


def fetch_by_ids(ids: List[str]) -> List[Dict[str, Any]]:
    """One or more specific papers by arXiv id (e.g. "2609.05339" or
    "2609.05339v1"). Used to add a paper your agent surfaced mid-session,
    rather than waiting for it to show up in a category fetch."""
    ids = [i for i in ids if i]
    if not ids:
        return []
    payload = _get({"id_list": ",".join(ids), "max_results": len(ids)})
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


def _keyword_window(keywords: List[str], offset: int) -> List[str]:
    """The slice of a topic list this run should ask arXiv about.

    A search_query is a URL parameter, so the keyword list has to be bounded.
    Truncating it would mean the same first few topics are queried forever and
    the rest of the profile is decorative. The window advances every run, so a
    forty-topic profile is covered over several days instead of never.
    """
    if not keywords:
        return []
    size = min(KEYWORDS_PER_QUERY, len(keywords))
    start = offset % len(keywords)
    window = keywords[start:start + size]
    if len(window) < size:                     # wrap around the end of the list
        window += keywords[:size - len(window)]
    return window


def fetch_profile(profile: Dict[str, Any], since: str | None = None,
                  until: str | None = None, offset: int = 0) -> Dict[str, Any]:
    """Fetch every tier of an interest profile. Partial failure is reported.

    Returns the papers plus a per-tier account of what was actually asked for,
    so `fetch` can tell the user what it searched rather than only how many rows
    came back. "Fetched 50, 0 new" is not a useful thing to read when what you
    need to know is which topics and which categories produced the zero.
    """
    from .scoring import extract_concepts

    seen: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    plan: List[Dict[str, Any]] = []

    stopped = False
    for tier_name, tier in profile["tiers"].items():
        if stopped:
            break
        keywords = _keyword_window(tier["keywords"], offset)
        for category in tier["categories"]:
            before = len(seen)
            try:
                papers = fetch_category(
                    category, tier["per_category"], keywords, since, until)
                for paper in papers:
                    paper.setdefault("tier", tier_name)
                    seen.setdefault(paper["id"], paper)
                returned = len(papers)
            except ArxivUnavailable as exc:
                errors.append(f"{category}: {exc}")
                returned = 0
                # Once arXiv has refused us, the remaining categories will be
                # refused too. Carrying on produced a wall of identical warnings
                # and dug the block deeper; stop and report what we did get.
                if isinstance(exc, ArxivCoolingDown) or "refused this client" in str(exc):
                    stopped = True
                    break
            plan.append({
                "tier": tier_name,
                "category": category,
                "keywords": keywords,
                "asked_for": tier["per_category"],
                "returned": returned,
                "new_to_this_run": len(seen) - before,
            })

    papers = list(seen.values())
    for paper in papers:
        paper["concepts"] = extract_concepts(paper)

    return {
        "papers": papers,
        "errors": errors,
        "plan": plan,
        "run_date": date.today().isoformat(),
        "since": since,
        "until": until,
        "categories": profile["all_categories"],
        "next_offset": offset + KEYWORDS_PER_QUERY,
        "stopped_early": stopped,
        "cooldown_remaining": cooldown_remaining(),
    }


def fetch_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Back-compatible entry point: fetch the profile implied by `settings`."""
    from .config import load_profile
    return fetch_profile(load_profile(settings))
