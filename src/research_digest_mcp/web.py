"""The browser interface.

Standard library http.server. No Flask, no build step, no node_modules. The
whole point of this surface is that it shows its working: every number a tool
produces comes back with the arithmetic that made it.

It binds to localhost only. This is a personal library, not a service.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import storage
from .config import HOME, load_settings, load_state, ranking_topics, save_settings
from .scoring import (BOILERPLATE, about_sentence, explain_sentence, rank_all_query,
                      score_paper, score_query)
from .trends import compute_trends, cross_pollination

STATIC = Path(__file__).parent / "static"


NEEDS_EXTRA = ("Similarity search is an optional extra. Install it with "
               "'pip install research-digest-mcp[embeddings]', then run "
               "'research-digest embed'.")


def _similar(paper_id: str, limit: int):
    try:
        from .similarity import EmbeddingStore, MixedBasis, SimilaritySearch
    except ImportError:
        return {"status": "unavailable", "message": NEEDS_EXTRA}
    try:
        hits = SimilaritySearch().find_similar(paper_id, limit)
    except MixedBasis as exc:
        return {"status": "needs_rebuild", "message": str(exc)}
    except ImportError:
        # numpy is imported lazily inside the search, so the failure can land here too.
        return {"status": "unavailable", "message": NEEDS_EXTRA}
    if not hits:
        # An empty result can mean two different things and they need different
        # messages: either nothing has ever been embedded, or this particular
        # paper just hasn't (it was fetched after the last 'embed' run). Telling
        # someone to rebuild a store that is actually fine is its own bug.
        try:
            store_count = EmbeddingStore().count()
        except Exception:
            store_count = 0
        if store_count == 0:
            return {"status": "no_embeddings",
                    "message": "No vectors built yet. Run 'research-digest embed' to enable "
                               "similarity search across your library."}
        return {"status": "not_found",
                "message": (f"This paper was added since the last 'research-digest embed' "
                             f"({store_count} others are in the store). Run it again to "
                             f"include this one."),
                "results": []}
    by_id = {p["id"]: p for p in storage.load_papers()}
    return {"status": "ok", "results": [
        {"id": pid, "similarity": s,
         "title": by_id.get(pid, {}).get("title", pid),
         "url": by_id.get(pid, {}).get("url", ""),
         "concepts": by_id.get(pid, {}).get("concepts", [])[:5]}
        for pid, s in hits]}


def _semantic_expand(seed_ids: list, exclude: set, limit: int = 12) -> dict:
    """Papers near the ones a keyword search already found, by vector.

    This is the one thing the embedding store could always have done for
    search and never did. `find_similar` answers "papers like this paper", and
    because the fitted vectoriser is thrown away after embedding there is no
    way to turn a typed query into a vector at all. But the keyword hits are
    papers, so they can seed the vector side: search for "nvidia", get the 7
    papers that say it, then ask the vectors what sits near those 7.

    On a real library that turns 7 literal matches into a shelf of GPU
    scheduling, kernel and inference-serving papers that never use the word.
    Presented separately and labelled, because it answers a different question:
    the keyword list is what you asked for, this is what it is next to.
    """
    if not seed_ids:
        return {"status": "no_seeds", "results": []}
    try:
        from .similarity import MixedBasis, SimilaritySearch
    except ImportError:
        return {"status": "unavailable", "message": NEEDS_EXTRA, "results": []}
    try:
        hits = SimilaritySearch().find_similar_to_group(seed_ids, limit + len(exclude))
    except MixedBasis as exc:
        return {"status": "needs_rebuild", "message": str(exc), "results": []}
    except Exception:
        return {"status": "unavailable", "message": NEEDS_EXTRA, "results": []}

    by_id = {p["id"]: p for p in storage.load_papers()}
    saved, read = storage.load_saved(), storage.load_read()
    rows = []
    for pid, score in hits:
        if pid in exclude or pid not in by_id:
            continue
        paper = by_id[pid]
        rows.append({
            "id": pid, "title": paper.get("title", ""),
            "url": paper.get("url", "") or f"https://arxiv.org/abs/{pid}",
            "published": paper.get("published", ""),
            "category": paper.get("primary_category", ""),
            "about": about_sentence(paper),
            "concepts": (paper.get("concepts") or [])[:6],
            "similarity": score,
            "saved": pid in saved, "read": pid in read,
            "why_text": f"Does not contain your words. Sits close to the papers that "
                        f"do, at {score:.2f} similarity.",
        })
        if len(rows) >= limit:
            break
    if not rows:
        return {"status": "no_match", "results": []}
    return {
        "status": "ok",
        "results": rows,
        "how": ("Found by comparing vectors, not words. Each of these sits near the "
                "papers your search matched, so they are about the same thing "
                "without necessarily saying it the same way."),
    }


def _paper_detail(pid: str, query: str = "") -> dict:
    """Everything the detail panel needs, in one call: the full record, the
    lead sentence, and the precomputed similar list. UI-4: before this, opening
    a paper meant a fetch for the row data (already in hand from the grid) plus
    a second fetch for 'similar' once the panel was already open. One endpoint
    means the client can prefetch on hover and render the panel from cache."""
    papers = {p["id"]: p for p in storage.load_papers()}
    paper = papers.get(pid)
    if not paper:
        return {"status": "error", "message": f"No paper {pid}"}
    # Score the paper the same way the list the reader came from scored it. Search
    # ranks by query relevance; the panel used to answer with the standing-profile
    # score regardless, so opening the top hit for "chain of thought" showed
    # "match 0.42" under a list that had ranked it at 0.83. Two different numbers
    # under one label is worse than either number alone.
    if query.strip():
        result = score_query(paper, query.lower().split()) or {
            "score": 0.0,
            "why": {"matched": [], "components": {}},
        }
        basis = "query"
    else:
        result = score_paper(paper, ranking_topics())
        basis = "topics"
    saved = storage.load_saved()
    read = storage.load_read()
    return {
        "status": "ok",
        "id": paper["id"],
        "title": paper.get("title", ""),
        "url": paper.get("url", "") or f"https://arxiv.org/abs/{pid}",
        "published": paper.get("published", ""),
        "category": paper.get("primary_category", ""),
        "authors": paper.get("authors") or [],
        "concepts": paper.get("concepts") or [],
        "about": about_sentence(paper),
        "abstract": paper.get("abstract") or "",
        "score": result["score"],
        "why": result["why"],
        "why_text": explain_sentence(result["why"]),
        "score_basis": basis,
        "saved": pid in saved,
        "read": pid in read,
        "note": (saved.get(pid) or {}).get("note", ""),
        "similar": _similar(pid, 6),
    }


def _bibtex(paper: dict) -> str:
    """A BibTeX entry for an arXiv preprint.

    eprint/archivePrefix rather than a fabricated journal: these are preprints,
    and inventing a venue for one is the kind of small lie that survives into a
    submitted bibliography.
    """
    pid = paper.get("id", "")
    first = (paper.get("authors") or ["unknown"])[0].split()[-1].lower()
    year = str(paper.get("published") or "")[:4] or "n.d."
    key = re.sub(r"[^a-z0-9]", "", f"{first}{year}{pid.split('.')[0]}") or pid
    authors = " and ".join(paper.get("authors") or []) or "Unknown"
    title = (paper.get("title") or "").replace("{", "").replace("}", "")
    return (f"@misc{{{key},\n"
            f"  title = {{{title}}},\n"
            f"  author = {{{authors}}},\n"
            f"  year = {{{year}}},\n"
            f"  eprint = {{{pid}}},\n"
            f"  archivePrefix = {{arXiv}},\n"
            f"  primaryClass = {{{paper.get('primary_category', '')}}},\n"
            f"  url = {{{paper.get('url') or 'https://arxiv.org/abs/' + pid}}}\n"
            f"}}")


def _export(what: str, fmt: str) -> dict:
    """Your papers, in a form something else can read.

    A personal library you cannot get back out of is a personal library you are
    renting. Three formats because three things happen to these: a citation
    manager wants BibTeX, a note app wants markdown, another copy of this tool
    wants the archive JSON that `research-digest import` reads.
    """
    papers = {p["id"]: p for p in storage.load_papers()}
    saved = storage.load_saved()
    if what == "all":
        rows = list(papers.values())
        label = "library"
    elif what == "queue":
        read = storage.load_read()
        rows = [papers[i] for i in saved if i in papers and i not in read]
        label = "reading queue"
    else:
        rows = [papers[i] for i in saved if i in papers]
        label = "saved papers"

    rows.sort(key=lambda p: str(p.get("published") or ""), reverse=True)
    stamp = date.today().isoformat()

    if fmt == "bibtex":
        body = "\n\n".join(_bibtex(p) for p in rows)
        return {"status": "ok", "count": len(rows), "format": "bibtex",
                "filename": f"research-digest-{what}-{stamp}.bib", "body": body}

    if fmt == "markdown":
        lines = [f"# {label}, {stamp}", "", f"{len(rows)} papers.", ""]
        for paper in rows:
            note = (saved.get(paper["id"]) or {}).get("note", "")
            lines.append(f"## {paper.get('title', '')}")
            lines.append(f"{paper.get('url', '')} · {paper.get('published', '')} · "
                         f"{paper.get('primary_category', '')}")
            about = about_sentence(paper)
            if about:
                lines.append("")
                lines.append(about)
            if note:
                lines.append("")
                lines.append(f"> {note}")
            lines.append("")
        return {"status": "ok", "count": len(rows), "format": "markdown",
                "filename": f"research-digest-{what}-{stamp}.md",
                "body": "\n".join(lines)}

    # The default: the same shape `research-digest import` reads back in.
    payload = {
        "exported": stamp,
        "runs": storage.load_archive().get("runs", []),
        "papers": {p["id"]: {**p, "note": (saved.get(p["id"]) or {}).get("note", "")}
                   for p in rows},
    }
    return {"status": "ok", "count": len(rows), "format": "json",
            "filename": f"research-digest-{what}-{stamp}.json",
            "body": json.dumps(payload, indent=1, ensure_ascii=False)}


def api(path: str, params: dict) -> dict:
    one = {k: v[0] for k, v in params.items()}

    if path == "/api/status":
        from .mcp import tool_library_status
        return tool_library_status({})

    if path == "/api/search":
        query = one.get("q", "").strip()
        if not query:
            return {"status": "ok", "matched": 0, "results": [], "searched": 0}
        from .config import load_profile, tier_index
        from .scoring import _significant
        papers = storage.load_papers()
        saved = storage.load_saved()
        read = storage.load_read()
        tiers = tier_index(load_profile())
        # Normalised the same way the scorer will normalise them, so the terms
        # the response reports are the terms it actually searched for -- the
        # highlighting on the cards reads this list.
        terms = _significant(query.lower().split())
        every = rank_all_query(papers, query.lower().split())
        limit = int(one.get("limit", 25))

        # Say when the answer is thin. Nine papers each matching a third of the
        # query were presented exactly like nine good hits, and a search for
        # "NVIDIA-labs" came back led by a paper about animal welfare. A result
        # list that cannot distinguish "here it is" from "here is the closest
        # thing I have" is a list that quietly answers the wrong question.
        best = every[0]["score"] if every else 0.0
        best_cover = every[0]["why"].get("coverage", 0) if every else 0
        weak = bool(every) and (best < 0.40 or best_cover < 0.6)
        return {
            "status": "ok", "query": query, "terms": terms,
            "searched": len(papers), "matched": len(every),
            "best_score": round(best, 3),
            "weak": weak,
            "weak_note": (
                f"Nothing here is a strong match for {query!r}. The best of "
                f"{len(every)} covers {int(best_cover * 100)}% of what you asked for. "
                f"Your library may simply not have this yet."
            ) if weak else "",
            "coverage": storage.month_coverage(papers) if (weak or not every) else None,
            # Offered when the literal answer is thin, which is exactly when
            # "what is this next to" is more use than "what says this word".
            "related_papers": (
                _semantic_expand([p["id"] for p in every[:8]],
                                 {p["id"] for p in every})
                if every and (weak or len(every) < 25) else None),
            "results": [{
                "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                "published": p.get("published", ""), "category": p.get("primary_category", ""),
                "about": about_sentence(p),
                "concepts": (p.get("concepts") or [])[:6],
                "score": p["score"], "why": p["why"], "why_text": p["why_text"],
                "saved": p["id"] in saved, "read": p["id"] in read,
                "tier": p.get("tier") or tiers.get(p.get("primary_category"), ""),
            } for p in every[:limit]],
        }

    if path == "/api/papers":
        # Browse, as opposed to search. The grid opens on this.
        from datetime import date, datetime, timedelta
        papers = storage.load_papers()
        saved = storage.load_saved()
        read = storage.load_read()
        window = one.get("when", "all").lower()

        def day_of(paper):
            for field in ("first_seen", "published", "updated"):
                raw = paper.get(field)
                if raw:
                    try:
                        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
                    except ValueError:
                        continue
            return None

        today = date.today()
        if window == "today":
            papers = [p for p in papers if day_of(p) == today]
        elif window == "week":
            cutoff = today - timedelta(days=7)
            papers = [p for p in papers if (day_of(p) or date.min) > cutoff]
        elif window == "saved":
            papers = [p for p in papers if p["id"] in saved]
        elif window == "queue":
            papers = [p for p in papers if p["id"] in saved and p["id"] not in read]

        from .config import load_profile, tier_index
        tiers = tier_index(load_profile())
        topics = ranking_topics()
        for paper in papers:
            paper["tier"] = paper.get("tier") or tiers.get(paper.get("primary_category"), "")
            result = score_paper(paper, topics)
            paper["score"] = result["score"]
            paper["why"] = result["why"]
            paper["why_text"] = explain_sentence(result["why"])
            paper["saved"] = paper["id"] in saved
            paper["read"] = paper["id"] in read

        if one.get("sort", "score") == "date":
            papers.sort(key=lambda p: str(day_of(p) or ""), reverse=True)
        else:
            papers.sort(key=lambda p: p["score"], reverse=True)

        limit = int(one.get("limit", 120))
        # The grid used to render papers[:120] and caption it with the full
        # total, so 1,443 papers were claimed and 120 were reachable. Paging is
        # the honest fix: the caller says where it is, and the response says
        # whether there is more.
        offset = max(0, int(one.get("offset", 0)))
        page = papers[offset:offset + limit]
        return {
            "status": "ok", "window": window, "total": len(papers),
            "topics": topics,
            "offset": offset, "showing": len(page),
            "has_more": offset + len(page) < len(papers),
            "results": [{
                "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                "published": p.get("published", ""), "category": p.get("primary_category", ""),
                "about": about_sentence(p),
                "concepts": (p.get("concepts") or [])[:6],
                "score": p["score"], "why": p["why"], "why_text": p["why_text"],
                "saved": p["saved"], "read": p["read"], "tier": p.get("tier", ""),
            } for p in page],
        }

    if path == "/api/save":
        pid = one.get("id", "")
        papers = {p["id"]: p for p in storage.load_papers()}
        if pid not in papers:
            return {"status": "error", "message": f"No paper {pid}"}
        if pid in storage.load_saved():
            storage.unsave_paper(pid)
            return {"status": "ok", "saved": False}
        paper = papers[pid]
        storage.save_paper(pid, paper.get("title", ""), "", paper.get("concepts") or [])
        return {"status": "ok", "saved": True}

    if path == "/api/read":
        pid = one.get("id", "")
        storage.mark_read(pid)
        return {"status": "ok", "read": True}

    if path == "/api/digest":
        from .digest import build_digest, write_digest
        papers = storage.load_papers()
        if not papers:
            return {"status": "error", "message": "No papers yet. Fetch first."}
        settings = load_settings()
        result = build_digest(papers, ranking_topics(settings), for_date=one.get("date"))
        digest_path = write_digest(result)
        return {
            "status": "ok", "date": result["date"], "considered": result["considered"],
            "picks": [{
                "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                "published": p.get("published", ""), "category": p.get("primary_category", ""),
                "about": p.get("about", ""), "pick_reason": p.get("pick_reason", ""),
            } for p in result["picks"]],
            "path": str(digest_path),
        }

    if path == "/api/trends":
        papers = storage.load_papers()
        result = compute_trends(papers)
        result["crossing"] = cross_pollination(papers)
        return result

    if path == "/api/note":
        pid, note = one.get("id", ""), one.get("note", "")
        saved = storage.load_saved()
        if pid not in saved:
            return {"status": "error", "message": "Save the paper first."}
        entry = saved[pid]
        storage.save_paper(pid, entry.get("title", ""), note, entry.get("concepts") or [])
        return {"status": "ok", "note": note}

    if path == "/api/refresh":
        # Fetch from the browser, so a daily pull does not need the terminal.
        from .config import record_fetch
        from .fetchers import ArxivUnavailable, cooldown_detail, fetch_settings
        settings = load_settings()
        # A date window makes a missed month recoverable. Without one, every
        # fetch starts from the newest paper, so a gap stays a gap forever and
        # the only symptom is a search that confidently finds nothing.
        since, until = one.get("since"), one.get("until")
        try:
            if since or until:
                # Backfill goes through the harvest feed, not search. They are
                # different services with different budgets: measured on a
                # machine the search API was refusing outright, the harvest
                # endpoint served a thousand records a request. Filling a hole
                # is also exactly the job it is built for, since it takes a
                # date range directly instead of always starting from the
                # newest paper.
                from .config import load_profile
                from .harvest import HarvestUnavailable, harvest_profile
                try:
                    result = harvest_profile(
                        load_profile(settings), since=since, until=until,
                        published_only=True)
                    result["run_date"] = result.get("run_date") or date.today().isoformat()
                except HarvestUnavailable as exc:
                    return {"status": "error", "message": str(exc),
                            "blocked": True, "cooldown": cooldown_detail()}
            else:
                result = fetch_settings(settings)
        except ArxivUnavailable as exc:
            return {"status": "error", "message": str(exc),
                    "blocked": True, "cooldown": cooldown_detail()}
        if not result["papers"]:
            # "arXiv returned nothing" was a lie whenever arXiv had refused us,
            # and refusal is by far the likeliest reason for an empty fetch.
            # The real cause was in `errors` and no surface ever showed it, so
            # a rate limit looked like an empty month. Report the cause.
            reasons = result["errors"] or []
            blocked = any("refus" in str(r).lower() or "429" in str(r)
                          or "406" in str(r) for r in reasons)
            if blocked:
                message = reasons[0]
            elif reasons:
                message = (f"Nothing came back, and {len(reasons)} categories "
                           f"reported a problem. First: {reasons[0]}")
            else:
                message = ("arXiv answered, and had no papers matching your topics "
                           "in that window. Nothing was written. Widening the date "
                           "range or the topic list is what changes this.")
            return {"status": "error", "message": message,
                    "blocked": blocked, "errors": reasons,
                    "cooldown": cooldown_detail()}
        stats = storage.merge_papers(result["papers"], result["run_date"])
        # The browser button used to skip this, so fetching from the UI left the
        # header still reporting the last *terminal* fetch -- the surface you
        # just used was the one that did not update.
        record_fetch(stats["added"], stats["total"], source="web",
                     next_offset=result.get("next_offset"))
        return {
            "status": "ok", "fetched": len(result["papers"]),
            "added": stats["added"], "total": stats["total"],
            "errors": result["errors"],
            "message": (f"Fetched {len(result['papers'])}, {stats['added']} new. "
                        f"Library holds {stats['total']}."
                        + (" Re-run embed to include them in similarity search."
                           if stats["added"] else "")),
        }

    if path == "/api/ask":
        # The question box. Same function the ask_library MCP tool runs.
        from .ask import answer
        return answer(one.get("q", ""), load_settings(),
                      limit=int(one.get("limit", 40)),
                      use_llm=one.get("llm", "1") != "0")

    if path == "/api/arxiv":
        # Live arXiv search: the only endpoint that can GROW the library.
        from .mcp import tool_fetch_papers
        return tool_fetch_papers({"query": one.get("q", ""),
                                  "limit": int(one.get("limit", 25))})

    if path == "/api/llm":
        from . import llm
        # Nested, not spread: probe() has its own status vocabulary (absent,
        # no_models, ready) and spreading it over the envelope made "no local
        # model installed" arrive at the client as a failed request.
        return {"status": "ok", "llm": llm.probe(load_settings()),
                "providers": [{"key": k, **v} for k, v in llm.PROVIDERS.items()]}

    if path == "/api/workspace":
        from .workspace import WorkspaceUnavailable, configured_root, scan
        settings = load_settings()
        root = one.get("root") or ""
        target = Path(root).expanduser() if root.strip() else configured_root(settings)
        if target is None:
            return {"status": "not_configured",
                    "message": ("No workspace folder set. Name one and this reads your "
                                "own projects for the terms to search by. It reads "
                                "README and manifest files only, and nothing leaves "
                                "this machine.")}
        try:
            return scan(target, limit=int(one.get("limit", 25)))
        except WorkspaceUnavailable as exc:
            return {"status": "error", "message": str(exc)}

    if path == "/api/map":
        from .clusters import bridges, build
        papers = storage.load_papers()
        result = build(papers, storage.load_saved(), limit=int(one.get("limit", 22)))
        result["bridges"] = bridges(papers)
        return result

    if path == "/api/categories":
        # The picker. Without this the profile screen is a list of codes you
        # either happen to know or quietly stop reading.
        from .categories import STARTER_PROFILES, STRUCTURAL_SUGGESTIONS, catalogue
        papers = storage.load_papers()
        counts = {}
        for paper in papers:
            code = paper.get("primary_category", "")
            counts[code] = counts.get(code, 0) + 1
        groups = catalogue()
        for group in groups:
            for row in group["categories"]:
                row["held"] = counts.get(row["code"], 0)
        return {
            "status": "ok",
            "groups": groups,
            "starters": [{"key": k, **{kk: vv for kk, vv in v.items()}}
                         for k, v in STARTER_PROFILES.items()],
            "structural_suggestions": STRUCTURAL_SUGGESTIONS,
        }

    if path == "/api/suggest-terms":
        from .mcp import tool_suggest_profile_terms
        return tool_suggest_profile_terms({"limit": int(one.get("limit", 15))})

    if path == "/api/export":
        return _export(one.get("what", "saved"), one.get("format", "json"))

    if path == "/api/saved":
        from .mcp import tool_get_saved
        return tool_get_saved({"limit": 200})

    if path == "/api/similar":
        return _similar(one.get("id", ""), int(one.get("limit", 8)))

    if path == "/api/paper":
        return _paper_detail(one.get("id", ""), one.get("q", ""))

    if path == "/api/explain":
        # The scoring playground: score arbitrary text against arbitrary topics.
        topics = [t.strip() for t in one.get("topics", "").split(",") if t.strip()]
        paper = {
            "title": one.get("title", ""),
            "abstract": one.get("abstract", ""),
            "published": one.get("published", ""),
            "concepts": [],
        }
        result = score_paper(paper, topics)
        return {
            "status": "ok", "score": result["score"], "why": result["why"],
            "why_text": explain_sentence(result["why"]),
            "boilerplate": sorted(BOILERPLATE),
        }

    if path == "/api/settings":
        return {"status": "ok", "home": str(HOME), **load_settings()}

    if path == "/api/profile":
        # What the tool believes you are interested in, and what that belief
        # actually causes it to request. The second half is the point: a profile
        # you cannot see is indistinguishable from one that is not being used,
        # which is exactly how the fetch quietly stopped matching the library.
        from .config import KEYWORDS_PER_QUERY, load_profile
        from .fetchers import _keyword_window, build_query, cooldown_remaining

        settings = load_settings()
        profile = load_profile(settings)
        offset = int(load_state().get("fetch_offset", 0))
        papers = storage.load_papers()
        counts = {}
        for paper in papers:
            counts[paper.get("primary_category", "")] = \
                counts.get(paper.get("primary_category", ""), 0) + 1

        from .categories import describe
        from .config import ARXIV_MIN_INTERVAL

        tiers = []
        requests_total = 0
        for name, tier in profile["tiers"].items():
            window = _keyword_window(tier["keywords"], offset)
            requests_total += len(tier["categories"])
            tiers.append({
                "name": name,
                "categories": [
                    {"name": c, "held": counts.get(c, 0), **describe(c)}
                    for c in tier["categories"]
                ],
                "topics": tier["topics"],
                "structural_keywords": tier["structural_keywords"],
                "per_category": tier["per_category"],
                "keywords_total": len(tier["keywords"]),
                "keywords_this_run": window,
                "example_query": (
                    build_query(tier["categories"][0], window)
                    if tier["categories"] else ""
                ),
                # One request per category. Spelling out the arithmetic is the
                # only way "papers per category per run" means anything: the
                # cost of this tier is requests, and requests are rate-limited.
                "requests": len(tier["categories"]),
                "max_papers": len(tier["categories"]) * tier["per_category"],
            })

        return {
            "status": "ok",
            "work_context": profile["work_context"],
            "tiers": tiers,
            "keywords_per_query": KEYWORDS_PER_QUERY,
            "total_categories": len(profile["all_categories"]),
            "total_topics": len(profile["all_topics"]),
            "library_total": len(papers),
            "workspace_root": str(settings.get("workspace_root") or ""),
            "home": str(HOME),
            "sources": storage.source_breakdown(papers),
            # What a fetch actually costs, in the only currency arXiv charges
            # in. arXiv asks for one request at a time a few seconds apart, and
            # this tool waits 5s between them on purpose, so the run time is
            # decided by how many categories you configured -- not by how many
            # papers you asked for.
            "fetch_cost": {
                "requests": requests_total,
                "seconds_between": ARXIV_MIN_INTERVAL,
                "estimated_seconds": int(requests_total * ARXIV_MIN_INTERVAL),
                "max_papers": sum(t["max_papers"] for t in tiers),
                "note": (
                    f"A fetch makes {requests_total} requests, one per category, "
                    f"{ARXIV_MIN_INTERVAL:.0f} seconds apart, so it takes about "
                    f"{int(requests_total * ARXIV_MIN_INTERVAL / 60)} min "
                    f"{int(requests_total * ARXIV_MIN_INTERVAL) % 60}s. arXiv answers a "
                    f"burst with HTTP 429; if that happens this backs off for 5 minutes "
                    f"and says so rather than retrying into a longer block. Raising "
                    f"papers-per-category costs no extra requests. Adding a category does."
                ),
            },
            "cooldown_remaining": cooldown_remaining(),
            # Every category the library holds papers in, including ones the
            # profile does not ask for. That difference is the useful one: a
            # category you are accumulating without asking is a category you
            # should probably be asking for.
            "held_categories": sorted(
                ({"name": c, "held": n, "configured": c in profile["all_categories"]}
                 for c, n in counts.items() if c),
                key=lambda row: -row["held"]),
        }

    return {"status": "error", "message": f"No such endpoint: {path}"}


# --- writes ----------------------------------------------------------------

_CATEGORY = re.compile(r"^[a-z]+(?:-[a-z]+)?\.[A-Za-z-]{2,}$")
TIER_NAMES = ("core", "complementary", "stretch")


def _clean_list(value, limit: int = 300) -> list:
    """A list of non-empty strings from whatever the browser sent, deduped in order."""
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    seen, out = set(), []
    for item in (value or []):
        text = " ".join(str(item).split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _write_profile(payload: dict) -> dict:
    """Save an edited interest profile back to settings.json.

    Validated rather than trusted, even though the only client is a page served
    from this same process: an arXiv category that is not shaped like one turns
    into a query that quietly returns nothing forever, and "the fetch stopped
    matching the library and nothing looked broken" is the exact failure this
    whole screen exists to prevent.

    The legacy flat `categories`/`topics` keys are kept in step with the core
    tier, because `load_profile` falls back to them and a settings file whose
    two halves disagree is a bug waiting for an upgrade to trigger it.
    """
    settings = load_settings()
    profile = dict(settings.get("profile") or {})
    rejected = []

    if "work_context" in payload:
        profile["work_context"] = " ".join(str(payload["work_context"]).split())[:2000]

    for name in TIER_NAMES:
        if name not in (payload.get("tiers") or {}):
            continue
        incoming = payload["tiers"][name] or {}
        tier = dict(profile.get(name) or {})

        if "categories" in incoming:
            good, bad = [], []
            for category in _clean_list(incoming["categories"], 60):
                (good if _CATEGORY.match(category) else bad).append(category)
            tier["categories"] = good
            rejected.extend(f"{name}: {c} is not an arXiv category (want cs.AI, stat.ME…)"
                            for c in bad)
        if "topics" in incoming:
            tier["topics"] = _clean_list(incoming["topics"])
        if "structural_keywords" in incoming:
            tier["structural_keywords"] = _clean_list(incoming["structural_keywords"])
        if "per_category" in incoming:
            try:
                # Capped at arXiv's own page size. A larger number is not a
                # bigger fetch, it is a request arXiv silently truncates.
                tier["per_category"] = max(1, min(int(incoming["per_category"]), 200))
            except (TypeError, ValueError):
                rejected.append(f"{name}: per_category must be a whole number")
        profile[name] = tier

    settings["profile"] = profile
    core = profile.get("core") or {}
    if core.get("categories"):
        settings["categories"] = core["categories"]
    if core.get("topics"):
        settings["topics"] = core["topics"]

    if "workspace_root" in payload:
        root = str(payload["workspace_root"] or "").strip()
        if root:
            candidate = Path(root).expanduser()
            if not candidate.is_dir():
                rejected.append(f"{root} is not a folder on this machine")
                root = str(settings.get("workspace_root") or "")
            else:
                root = str(candidate)
        settings["workspace_root"] = root

    save_settings(settings)
    from .config import load_profile
    saved = load_profile(settings)
    return {
        "status": "ok",
        "rejected": rejected,
        "message": (f"Saved. {len(saved['all_categories'])} categories, "
                    f"{len(saved['all_topics'])} topics. The next fetch uses this."
                    + (f" {len(rejected)} entries were not saved." if rejected else "")),
        "total_categories": len(saved["all_categories"]),
        "total_topics": len(saved["all_topics"]),
    }


def _write_llm(payload: dict) -> dict:
    """Save the model settings. Every field is optional.

    A remote endpoint is allowed. An earlier version refused anything but
    localhost, which was the wrong shape of protection: it stopped someone
    using LM Studio on another box on their own LAN, while the actual
    requirement is only that the page never claims privacy it is not
    delivering. So the URL is accepted and `probe` reports, from the URL
    itself, exactly where questions go. The promise follows the setting rather
    than the setting being bent to fit the promise.

    What never leaves is unchanged either way: your papers and your workspace
    files. Only the question text is ever sent.
    """
    from . import llm
    settings = load_settings()
    block = dict(settings.get("llm") or {})
    for flag in ("enabled", "dismissed"):
        if flag in payload:
            block[flag] = bool(payload[flag])
    if "provider" in payload:
        provider = str(payload["provider"] or "").strip()
        if provider not in llm.PROVIDERS:
            return {"status": "error",
                    "message": f"Unknown provider {provider!r}. "
                               f"Pick one of: {', '.join(llm.PROVIDERS)}."}
        block["provider"] = provider
    if "base_url" in payload:
        url = str(payload["base_url"] or "").strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", url):
            return {"status": "error",
                    "message": f"{url!r} is not a URL. It should look like "
                               f"http://127.0.0.1:11434 or https://api.example.com/v1."}
        block["base_url"] = url
    if "model" in payload:
        block["model"] = str(payload["model"] or "").strip()
    if "api_key" in payload:
        block["api_key"] = str(payload["api_key"] or "").strip()
    settings["llm"] = block
    save_settings(settings)
    probed = llm.probe(settings)
    return {"status": "ok", "llm": probed,
            "message": probed.get("message", "") + " " + probed.get("privacy", "")}


def api_write(path: str, payload: dict) -> dict:
    if path == "/api/profile":
        return _write_profile(payload)
    if path == "/api/llm":
        return _write_llm(payload)
    if path == "/api/settings":
        return _write_profile(payload)
    return {"status": "error", "message": f"No such endpoint: {path}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "research-digest"

    def log_message(self, fmt, *args):  # quiet by default
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_POST(self):
        """Writes. Settings are edited here, so the profile stops being read-only.

        Two guards, both against the same thing -- a page in another tab quietly
        rewriting this library's config. The server binds to localhost, but a
        localhost bind does not stop a website you are visiting from POSTing to
        it, so: no CORS headers are ever sent (so no cross-origin page can read
        a reply), and a request carrying an Origin from anywhere but this server
        is refused outright.
        """
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{self.headers.get('Host', '')}",
                                     f"http://127.0.0.1:{self.server.server_address[1]}",
                                     f"http://localhost:{self.server.server_address[1]}"):
            self._send_json({"status": "error",
                             "message": "Cross-origin writes are refused."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 2_000_000:
            self._send_json({"status": "error", "message": "Payload too large."}, 413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json({"status": "error", "message": f"Bad JSON: {exc}"}, 400)
            return
        try:
            self._send_json(api_write(parsed.path, payload))
        except Exception as exc:
            self._send_json({"status": "error",
                             "message": f"{type(exc).__name__}: {exc}"}, 500)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            try:
                payload = api(path, parse_qs(parsed.query))
                code = 200
            except Exception as exc:
                payload = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                code = 500
            self._send_json(payload, code)
            return

        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def run(port: int = 8756, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Research digest running at {url}")
    print(f"Library: {HOME}")
    print("Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()
