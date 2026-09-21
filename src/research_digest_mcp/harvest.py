"""Bulk metadata from arXiv's OAI-PMH feed.

This exists because the search API and the harvest API are two different
services with two different budgets, and the tool was only ever using the
expensive one.

  export.arxiv.org/api/query   built for search. Returns up to 200 records,
                               ranks them, and rate-limits hard. Ask too often
                               and it refuses with 406 for hours.

  oaipmh.arxiv.org/oai         built for bulk harvesting. Returns about a
                               thousand records per request, is designed to be
                               walked with a resumption token, and takes a
                               date range directly.

Measured on a blocked machine: while the query API was returning 406 to a
request for a single paper, the OAI endpoint served 1,058 cs records for one
day in 3.2 MB. They are separately provisioned, so being cut off from search
does not cut you off from filling in your library.

It also carries more. The search API drops DOI, licence, journal reference and
splits author names into one flat string; OAI gives all of them, plus keyname
and forenames separately, plus the created and updated dates that let you tell
a new paper from a revision.

One thing to understand before trusting a harvest:

  `from` and `until` filter on DATESTAMP, which is when the record last
  changed, not when the paper came out.

So harvesting July returns papers created in July *and* older papers revised in
July. That is usually what you want for filling a hole, and it is never what
you want for answering "what came out in July", so `created` is stored as
`published` and the caller can filter on it.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Callable, Dict, Iterator, List, Optional

OAI_BASE = "https://oaipmh.arxiv.org/oai"
OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arx": "http://arxiv.org/OAI/arXiv/",
}
USER_AGENT = "research-digest-mcp (+https://github.com/dneish2/research-digest-mcp)"

# The OAI spec's own answer to load: the server replies 503 with Retry-After
# and expects you to wait exactly that long. Honouring it is why this endpoint
# stays available when the search API does not.
DEFAULT_INTERVAL = 3.0
MAX_RETRY_WAIT = 120.0
MAX_PAGES = 200

# arXiv's OAI sets are archives, not the fine-grained categories the search API
# uses. You harvest `cs` and filter to cs.AI yourself; there is no cs.AI set.
SET_FOR_ARCHIVE = {
    "cs": "cs", "stat": "stat", "math": "math", "econ": "econ",
    "eess": "eess", "q-bio": "q-bio", "q-fin": "q-fin", "physics": "physics",
    "astro-ph": "physics:astro-ph", "cond-mat": "physics:cond-mat",
    "gr-qc": "physics:gr-qc", "hep-ex": "physics:hep-ex",
    "hep-lat": "physics:hep-lat", "hep-ph": "physics:hep-ph",
    "hep-th": "physics:hep-th", "nlin": "physics:nlin",
    "nucl-ex": "physics:nucl-ex", "nucl-th": "physics:nucl-th",
    "quant-ph": "physics:quant-ph",
}


class HarvestUnavailable(RuntimeError):
    """The OAI endpoint refused or could not be reached."""


def sets_for_categories(categories: List[str]) -> List[str]:
    """The OAI sets that cover a list of fine-grained categories.

    ["cs.AI", "cs.LG", "stat.ME"] -> ["cs", "stat"]. Deduped, because
    harvesting `cs` twice would download every cs paper twice.
    """
    out = []
    for category in categories:
        archive = str(category).split(".")[0]
        oai_set = SET_FOR_ARCHIVE.get(archive)
        if oai_set and oai_set not in out:
            out.append(oai_set)
    return out


def _request(params: Dict[str, str], timeout: int = 90) -> bytes:
    url = f"{OAI_BASE}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                # The documented back-pressure signal. It tells you exactly how
                # long to wait, so there is no guessing and no backoff to tune.
                wait = min(float(exc.headers.get("Retry-After") or 20), MAX_RETRY_WAIT)
                time.sleep(wait)
                continue
            raise HarvestUnavailable(
                f"arXiv's harvest endpoint returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise HarvestUnavailable(
                    f"Could not reach arXiv's harvest endpoint: {exc}") from exc
            time.sleep(4)
    raise HarvestUnavailable("arXiv's harvest endpoint kept asking us to wait.")


def _text(node, path: str, default: str = "") -> str:
    found = node.find(path, OAI_NS)
    return " ".join((found.text or default).split()) if found is not None else default


def _record_to_paper(record) -> Optional[Dict[str, Any]]:
    meta = record.find("oai:metadata/arx:arXiv", OAI_NS)
    if meta is None:
        return None                       # a deleted record carries no metadata
    paper_id = _text(meta, "arx:id")
    if not paper_id:
        return None

    authors = []
    for author in meta.findall("arx:authors/arx:author", OAI_NS):
        keyname = _text(author, "arx:keyname")
        forenames = _text(author, "arx:forenames")
        full = " ".join(p for p in (forenames, keyname) if p)
        if full:
            authors.append(full)

    categories = _text(meta, "arx:categories").split()
    return {
        "id": paper_id,
        "title": _text(meta, "arx:title"),
        "abstract": _text(meta, "arx:abstract"),
        # `created` is publication; `updated` is the latest revision. The
        # search API conflates these and this does not.
        "published": _text(meta, "arx:created"),
        "updated": _text(meta, "arx:updated") or _text(meta, "arx:created"),
        "url": f"https://arxiv.org/abs/{paper_id}",
        "authors": authors[:8],
        "affiliations": [],
        "comment": _text(meta, "arx:comments"),
        "journal_ref": _text(meta, "arx:journal-ref"),
        "doi": _text(meta, "arx:doi"),
        "license": _text(meta, "arx:license"),
        "primary_category": categories[0] if categories else "",
        "categories": categories,
        "source": "oai",
    }


def _deleted_count(root) -> int:
    return sum(1 for h in root.findall(".//oai:header", OAI_NS)
               if h.get("status") == "deleted")


def harvest(oai_set: str, since: str, until: Optional[str] = None,
            keep: Optional[Callable[[Dict[str, Any]], bool]] = None,
            interval: float = DEFAULT_INTERVAL,
            on_page: Optional[Callable[[int, int, int], None]] = None,
            max_pages: int = MAX_PAGES) -> Iterator[Dict[str, Any]]:
    """Walk one OAI set over a date range, yielding papers.

    `keep` filters before anything is accumulated, because a cs harvest is
    every cs paper and a caller usually wants five of its forty categories.
    Filtering here rather than after means a month of cs costs memory for the
    papers you asked for, not for all of them.
    """
    params = {"verb": "ListRecords", "metadataPrefix": "arXiv",
              "set": oai_set, "from": since}
    if until:
        params["until"] = until

    pages = 0
    seen = 0
    while pages < max_pages:
        payload = _request(params)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise HarvestUnavailable(
                f"arXiv's harvest endpoint returned unreadable XML: {exc}") from exc

        error = root.find("oai:error", OAI_NS)
        if error is not None:
            code = error.get("code", "")
            # "no records match" is an answer, not a failure. It is what an
            # empty week looks like and it must not read as an outage.
            if code == "noRecordsMatch":
                return
            raise HarvestUnavailable(
                f"arXiv's harvest endpoint said: {code}. {(error.text or '').strip()}")

        records = root.findall("oai:ListRecords/oai:record", OAI_NS)
        kept = 0
        for record in records:
            paper = _record_to_paper(record)
            if paper is None:
                continue
            if keep is not None and not keep(paper):
                continue
            kept += 1
            yield paper
        pages += 1
        seen += len(records)
        if on_page:
            on_page(pages, seen, kept)

        token_node = root.find("oai:ListRecords/oai:resumptionToken", OAI_NS)
        token = (token_node.text or "").strip() if token_node is not None else ""
        if not token:
            return
        # A resumption token replaces every other argument. Sending them
        # alongside it is an error the server reports as badArgument.
        params = {"verb": "ListRecords", "resumptionToken": token}
        time.sleep(interval)


def harvest_profile(profile: Dict[str, Any], since: str, until: Optional[str] = None,
                    on_page: Optional[Callable[[int, int, int], None]] = None,
                    interval: float = DEFAULT_INTERVAL,
                    published_only: bool = False) -> Dict[str, Any]:
    """Harvest every archive a profile touches, keeping only its categories.

    Deliberately not keyword-filtered. A harvest is for making the library
    complete over a stretch of time; the topic list decides what gets ranked
    highly afterwards, not what is allowed to exist. That difference is the
    reason a keyword-driven daily fetch leaves holes and this does not.
    """
    wanted = set(profile.get("all_categories") or [])
    if not wanted:
        return {"papers": [], "sets": [], "errors": ["No categories configured."],
                "kept": 0, "seen": 0}

    def keep(paper):
        if not wanted.intersection(paper.get("categories") or []):
            return False
        # The datestamp gotcha, made optional. Harvesting July returns papers
        # created in July plus every older paper revised in July, so a harvest
        # aimed at "fill the hole where July's papers should be" has to filter
        # on `created`. A harvest aimed at "catch up on everything that moved"
        # should not.
        if published_only:
            published = str(paper.get("published") or "")
            if not (published >= since and (not until or published <= until)):
                return False
        return True
    papers: List[Dict[str, Any]] = []
    errors: List[str] = []
    sets = sets_for_categories(sorted(wanted))
    seen_total = 0

    for oai_set in sets:
        counter = {"seen": 0}

        def page(pages, seen, _kept, _set=oai_set, _c=counter):
            _c["seen"] = seen
            # Report the running total of papers actually collected, not the
            # per-page count plus the total, which was double counting and
            # showed more kept than seen.
            if on_page:
                on_page(_set, pages, seen, len(papers))

        try:
            for paper in harvest(oai_set, since, until, keep=keep,
                                 interval=interval, on_page=page):
                papers.append(paper)
        except HarvestUnavailable as exc:
            errors.append(f"{oai_set}: {exc}")
        seen_total += counter["seen"]

    from .scoring import extract_concepts
    for paper in papers:
        paper["concepts"] = extract_concepts(paper)

    return {
        "papers": papers,
        "sets": sets,
        "errors": errors,
        "kept": len(papers),
        "seen": seen_total,
        "since": since,
        "until": until,
        "run_date": date.today().isoformat(),
    }
