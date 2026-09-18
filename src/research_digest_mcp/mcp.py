"""The MCP server: JSON-RPC over stdin and stdout.

No SDK. One JSON object per line in, one per line out, which is all the
protocol requires and makes the server trivial to drive from a shell.

House rule: a tool that cannot answer says why. It never returns an empty list
that looks like "nothing matched" when the real story is "scikit-learn is not
installed" or "you have not fetched any papers yet".
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import date
from typing import Any, Dict, List, Optional

from . import storage
from .config import (
    HOME, force_utf8_streams, load_settings, load_state, ranking_topics,
    record_fetch)
from .scoring import (
    about_sentence, explain_sentence, extract_concepts, rank, rank_all, rank_all_query,
    score_paper,
)
from .trends import compute_trends

_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}(v\d+)?")

SERVER_NAME = "research-digest"
SERVER_VERSION = "1.0.0"
PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "search_papers",
        "description": (
            "Search the user's own research library by keyword. Returns matching papers "
            "with a plain-English explanation of why each one ranked where it did."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words or a phrase to look for"},
                "limit": {"type": "integer", "description": "How many to return", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_similar",
        "description": (
            "Find papers close to a given paper by embedding similarity. Requires "
            "embeddings to have been built."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "arXiv id, for example 2605.15040v1"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "get_trends",
        "description": (
            "Concepts rising or falling in the user's feed week over week. Reports "
            "'no_data' rather than a false decline when a week is empty."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_saved",
        "description": "The papers the user bookmarked, with their notes. Good for understanding what they care about.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 50}},
        },
    },
    {
        "name": "suggest_reading",
        "description": (
            "Suggest unread papers on a topic, ranked by the same explainable keyword "
            "scorer as search_papers. Keyword-based only — it does not use embeddings, "
            "even when they are built."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "save_paper",
        "description": (
            "Add a specific paper to the library by arXiv id or URL and bookmark it — "
            "for the paper your agent found mid-session that a category fetch may never "
            "surface on its own. If the paper is already in the library, just bookmarks it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_or_url": {
                    "type": "string",
                    "description": "e.g. 2609.05339, 2609.05339v1, or an arxiv.org/abs URL",
                },
                "note": {"type": "string", "description": "Why you're keeping it"},
            },
            "required": ["id_or_url"],
        },
    },
    {
        "name": "get_digest",
        "description": (
            "Today's top papers against the user's standing topics — a small, dated, "
            "reproducible pick (default 5, capped per category), not the full ranked "
            "library. Also written to a markdown file the user can read outside the agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
                "size": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "library_status",
        "description": (
            "What is actually in the library right now: paper count, date range, "
            "whether embeddings exist and are usable, and what is missing."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "fetch_papers",
        "description": (
            "Search arXiv itself — not the user's library — and add what comes back. "
            "This is the tool that GROWS the shelf: search_papers can only find what "
            "has already been fetched, so use this when the user asks for something "
            "their library does not hold, or asks for anything newer than its last "
            "fetch. Rate-limited by arXiv; one call, not a loop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Free text, searched across title and abstract"},
                "limit": {"type": "integer", "default": 20,
                          "description": "How many to pull back, max 100"},
                "save": {"type": "boolean", "default": False,
                         "description": "Also bookmark everything fetched"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_library",
        "description": (
            "Ask a question in plain English ('anything on agent evaluation from this "
            "month?') and get back the query plan plus the matching papers. Use this "
            "instead of search_papers when the user's words are a question rather than "
            "keywords — it strips the asking, picks the date window, and reports which "
            "of their words matched nothing so a typo does not silently cost the answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["question"],
        },
    },
    {
        "name": "suggest_profile_terms",
        "description": (
            "Read what the user has actually saved and propose topics their fetch "
            "profile is missing. Answers 'what am I reading that I never told this "
            "tool to look for'. Proposes only; it never edits settings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 12}},
        },
    },
]


def _summary(paper: Dict[str, Any], abstract: bool = False) -> Dict[str, Any]:
    out = {
        "id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "url": paper.get("url", ""),
        "published": paper.get("published", ""),
        "category": paper.get("primary_category", ""),
        "concepts": (paper.get("concepts") or [])[:6],
        "about": about_sentence(paper),
    }
    if "score" in paper:
        out["score"] = paper["score"]
    if "why_text" in paper:
        out["why"] = paper["why_text"]
    if abstract:
        out["abstract"] = paper.get("abstract") or ""
    return out


def _no_library() -> Dict[str, Any]:
    return {
        "status": "empty_library",
        "message": (
            f"No papers in {HOME}. Run 'research-digest fetch' to pull today's "
            f"papers from arXiv, then 'research-digest embed' if you want similarity."
        ),
        "results": [],
    }


# --- tools -----------------------------------------------------------------

def tool_search_papers(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"status": "error", "message": "Give me a query."}
    papers = storage.load_papers()
    if not papers:
        return _no_library()

    terms = [t for t in query.lower().split() if t]
    limit = int(args.get("limit", 10))
    every = rank_all_query(papers, terms)
    ranked = every[:limit]
    return {
        "status": "ok",
        "query": query,
        "searched": len(papers),
        "matched": len(every),
        "showing": len(ranked),
        "how": (
            "Ranked by how much of your query matched (a distinctive word counts more "
            "than a common one, the exact phrase counts most) plus how often the terms "
            "appear and how recent the paper is. Every result must match at least one "
            "term; matching more of them ranks higher."
        ),
        "results": [_summary(p, abstract=True) for p in ranked],
    }


def tool_get_similar(args: Dict[str, Any]) -> Dict[str, Any]:
    paper_id = str(args.get("paper_id", "")).strip()
    if not paper_id:
        return {"status": "error", "message": "Give me a paper_id."}
    try:
        from .similarity import MixedBasis, SimilaritySearch
    except ImportError:
        return {"status": "unavailable",
                "message": "Similarity needs numpy: pip install 'research-digest-mcp[embeddings]'"}
    try:
        search = SimilaritySearch()
        hits = search.find_similar(paper_id, int(args.get("limit", 5)))
    except MixedBasis as exc:
        return {"status": "needs_rebuild", "message": str(exc)}
    except ImportError:
        return {"status": "unavailable",
                "message": "Similarity needs numpy: pip install 'research-digest-mcp[embeddings]'"}

    if not hits:
        store_count = 0
        try:
            from .similarity import EmbeddingStore
            store_count = EmbeddingStore().count()
        except Exception:
            pass
        if store_count == 0:
            return {"status": "no_embeddings",
                    "message": "No embeddings yet. Run 'research-digest embed'."}
        return {"status": "not_found",
                "message": f"{paper_id} is not in the vector store ({store_count} papers embedded).",
                "results": []}

    by_id = {p["id"]: p for p in storage.load_papers()}
    return {
        "status": "ok",
        "paper_id": paper_id,
        "how": "Cosine similarity between TF-IDF+SVD vectors fitted over the whole library.",
        "results": [
            {**_summary(by_id.get(pid, {"id": pid, "title": pid})), "similarity": score}
            for pid, score in hits
        ],
    }


def tool_get_trends(args: Dict[str, Any]) -> Dict[str, Any]:
    papers = storage.load_papers()
    if not papers:
        return _no_library()
    return compute_trends(papers)


def tool_get_saved(args: Dict[str, Any]) -> Dict[str, Any]:
    saved = storage.load_saved()
    if not saved:
        return {"status": "empty", "message": "Nothing saved yet.", "results": []}
    by_id = {p["id"]: p for p in storage.load_papers()}
    items = []
    for pid, entry in saved.items():
        base = by_id.get(pid, {})
        items.append({
            "id": pid,
            "title": entry.get("title") or base.get("title", ""),
            "note": entry.get("note", ""),
            "saved_at": entry.get("saved_at", ""),
            "concepts": entry.get("concepts") or base.get("concepts", []),
            "url": base.get("url", f"https://arxiv.org/abs/{pid}"),
        })
    items.sort(key=lambda e: e.get("saved_at", ""), reverse=True)
    return {"status": "ok", "saved_count": len(items),
            "results": items[: int(args.get("limit", 50))]}


def tool_suggest_reading(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return {"status": "error", "message": "Give me a topic."}
    papers = storage.load_papers()
    if not papers:
        return _no_library()

    limit = int(args.get("limit", 5))
    read = set(storage.load_read())
    candidates = [p for p in papers if p["id"] not in read]
    every = rank_all_query(candidates, topic.lower().split())
    ranked = every[:limit]

    method = "keyword scoring"
    if not ranked:
        return {"status": "no_match",
                "message": f"Nothing in the library matches {topic!r}.",
                "searched": len(candidates), "results": []}

    return {
        "status": "ok",
        "topic": topic,
        "method": method,
        "matched": len(every),
        "showing": len(ranked),
        "excluded_already_read": len(papers) - len(candidates),
        "results": [_summary(p, abstract=True) for p in ranked],
    }


def tool_save_paper(args: Dict[str, Any]) -> Dict[str, Any]:
    raw = str(args.get("id_or_url", "")).strip()
    match = _ARXIV_ID.search(raw)
    if not match:
        return {"status": "error",
                "message": f"{raw!r} does not look like an arXiv id (want something like "
                           f"2609.05339, 2609.05339v1, or an arxiv.org/abs URL)."}
    arxiv_id = match.group(0)
    bare = storage.base_id(arxiv_id)

    by_bare = {storage.base_id(p["id"]): p for p in storage.load_papers()}
    paper = by_bare.get(bare)
    already_had = paper is not None

    if paper is None:
        try:
            from .fetchers import ArxivUnavailable, fetch_by_ids
        except Exception as exc:  # pragma: no cover - stdlib only, should not happen
            return {"status": "error", "message": f"Could not reach the fetcher: {exc}"}
        try:
            fetched = fetch_by_ids([arxiv_id])
        except ArxivUnavailable as exc:
            return {"status": "error", "message": str(exc)}
        if not fetched:
            return {"status": "not_found",
                    "message": f"arXiv has no paper matching {arxiv_id}."}
        paper = fetched[0]
        paper["concepts"] = extract_concepts(paper)
        storage.merge_papers([paper], date.today().isoformat())

    note = str(args.get("note", "") or "")
    storage.save_paper(paper["id"], paper.get("title", ""), note, paper.get("concepts") or [])

    message = f"Saved {paper.get('title', '')!r}."
    if not already_had:
        message += " Added to the library — run 'research-digest embed' to include it in similarity search."
    return {
        "status": "ok",
        "id": paper["id"],
        "title": paper.get("title", ""),
        "url": paper.get("url", f"https://arxiv.org/abs/{paper['id']}"),
        "already_in_library": already_had,
        "message": message,
    }


def tool_get_digest(args: Dict[str, Any]) -> Dict[str, Any]:
    papers = storage.load_papers()
    if not papers:
        return _no_library()
    from .digest import build_digest, write_digest
    settings = load_settings()
    result = build_digest(papers, ranking_topics(settings),
                           for_date=args.get("date"), size=int(args.get("size", 5)))
    if not result["picks"]:
        return {"status": "no_match",
                "message": "Nothing in the library matched your topics today.", "results": []}
    path = write_digest(result)
    return {
        "status": "ok",
        "date": result["date"],
        "considered": result["considered"],
        "path": str(path),
        "results": [{**_summary(p), "pick_reason": p["pick_reason"]} for p in result["picks"]],
    }


def tool_library_status(args: Dict[str, Any]) -> Dict[str, Any]:
    import sys
    from pathlib import Path

    from . import __version__

    papers = storage.load_papers()
    settings = load_settings()
    status: Dict[str, Any] = {
        # An empty library answering "ok" is how an agent concludes that a
        # library with nothing in it is fine. Every other tool here names its
        # own failure; the one used to orient should too.
        "status": "ok" if papers else "empty_library",
        "home": str(HOME),
        "papers": len(papers),
        "saved": len(storage.load_saved()),
        "read": len(storage.load_read()),
        "categories": settings["categories"],
        "topics": settings["topics"],
        # Which copy of the package answered. Two installs at different commits
        # on one machine, with no way to tell them apart from any output, is a
        # whole afternoon of debugging the wrong code.
        "version": __version__,
        "interpreter": sys.executable,
        "package_path": str(Path(__file__).resolve().parent),
    }
    if not papers:
        status["message"] = ("The library is empty. Run 'research-digest fetch' "
                             "to pull papers from arXiv.")
    days = sorted(str(p.get("first_seen") or p.get("published") or "")[:10]
                  for p in papers if p.get("first_seen") or p.get("published"))
    if days:
        status["earliest"] = days[0]
        status["latest"] = days[-1]
    status["runs"] = storage.load_archive().get("runs", [])[-10:]

    # The exact moment of the last fetch, not just its date. Absent for a
    # library last fetched by a version that only wrote the date -- reported as
    # absent rather than back-filled from a file mtime, because a guessed
    # timestamp is indistinguishable from a real one once it is on screen.
    state = load_state()
    status["last_fetch_at"] = state.get("last_fetch_at") or ""
    status["last_fetch_source"] = state.get("last_fetch_source") or ""
    status["last_added"] = state.get("last_added")

    try:
        from .similarity import EmbeddingStore
        store = EmbeddingStore()
        bases = store.bases()
        status["embeddings"] = {
            "vectors": store.count(),
            "fits": [{"encoder": e, "basis": b, "dims": d, "rows": n} for e, b, d, n in bases],
            "usable": len(bases) <= 1,
        }
        if len(bases) > 1:
            status["embeddings"]["warning"] = (
                "More than one fit present. Similarity is disabled until you re-embed.")
        elif not bases:
            status["embeddings"]["hint"] = "No vectors yet. Run 'research-digest embed'."
    except ImportError:
        status["embeddings"] = {"available": False,
                                "hint": "pip install 'research-digest-mcp[embeddings]'"}
    return status


def tool_fetch_papers(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search arXiv live and merge the results into the library.

    The reviews kept calling this "live search", but the useful framing is
    narrower: every other tool here reads a shelf, and this is the only one
    that puts something on it. A library tool whose search cannot reach past
    what it already holds will answer "nothing found" for a paper that exists,
    which is the worst answer a research tool can give.
    """
    query = str(args.get("query", "")).strip()
    if not query:
        return {"status": "error", "message": "Give me something to search arXiv for."}
    limit = max(1, min(int(args.get("limit", 20) or 20), 100))

    from .fetchers import ArxivUnavailable, search as arxiv_search
    try:
        found = arxiv_search(query, limit)
    except ArxivUnavailable as exc:
        return {"status": "unavailable", "message": str(exc), "results": []}
    if not found:
        return {"status": "no_match",
                "message": f"arXiv returned nothing for {query!r}.", "results": []}

    for paper in found:
        paper["concepts"] = extract_concepts(paper)
    stats = storage.merge_papers(found, date.today().isoformat())
    record_fetch(stats["added"], stats["total"], source="arxiv_search")

    if args.get("save"):
        for paper in found:
            storage.save_paper(paper["id"], paper.get("title", ""),
                               f"from arXiv search: {query}", paper.get("concepts") or [])

    return {
        "status": "ok",
        "query": query,
        "fetched": len(found),
        "added": stats["added"],
        "already_held": len(found) - stats["added"],
        "library_total": stats["total"],
        "message": (f"Pulled {len(found)} from arXiv, {stats['added']} new to the "
                    f"library (now {stats['total']})."
                    + (" Re-run 'research-digest embed' to include them in "
                       "similarity search." if stats["added"] else "")),
        "results": [_summary(p, abstract=True) for p in found],
    }


def tool_ask_library(args: Dict[str, Any]) -> Dict[str, Any]:
    """A question in English, answered from the library.

    Delegates to ask.answer, which is the same code the browser's question box
    runs. The tool's job here is only to say it in an agent's vocabulary.
    """
    from .ask import answer

    question = str(args.get("question", "")).strip()
    if not question:
        return {"status": "error", "message": "Ask me something."}

    result = answer(question, load_settings(), limit=int(args.get("limit", 10)))
    result["how"] = (
        "The question was read into search terms by rule (and by a local model "
        "if one is configured), then ranked by the same arithmetic scorer as "
        "search_papers. No model orders these results."
    )
    if result.get("status") in ("ok", "no_match"):
        result["next_step"] = (
            "Nothing in the library matched. fetch_papers searches arXiv itself "
            "and can add what is missing."
            if not result.get("results") else
            "fetch_papers searches arXiv itself if the user wants more than the "
            "library holds."
        )
    return result


def tool_suggest_profile_terms(args: Dict[str, Any]) -> Dict[str, Any]:
    """Terms common in what the user saved that their fetch profile never asks for.

    The gap between what someone bookmarks and what their profile requests is
    the profile's blind spot, and it is invisible from either side on its own.
    """
    saved = storage.load_saved()
    if not saved:
        return {"status": "empty",
                "message": "Nothing saved yet, so there is nothing to learn from.",
                "results": []}

    by_id = {p["id"]: p for p in storage.load_papers()}
    known = {t.lower() for t in ranking_topics()}
    counts: Dict[str, int] = {}
    examples: Dict[str, List[str]] = {}
    for pid, entry in saved.items():
        paper = by_id.get(pid, {})
        title = entry.get("title") or paper.get("title", "")
        for concept in set((entry.get("concepts") or []) + (paper.get("concepts") or [])):
            term = str(concept).lower().strip()
            if not term or term in known or len(term) < 4:
                continue
            counts[term] = counts.get(term, 0) + 1
            examples.setdefault(term, []).append(title)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    # One occurrence is a coincidence, not a pattern worth changing a profile for.
    ranked = [(t, n) for t, n in ranked if n >= 2] or ranked[:3]
    limit = int(args.get("limit", 12))
    return {
        "status": "ok" if ranked else "no_gap",
        "saved_count": len(saved),
        "profile_topics": len(known),
        "how": ("Concepts tagged on papers you saved, minus the topics your profile "
                "already asks arXiv for. Ranked by how many saved papers carry each."),
        "message": ("Your profile already covers everything your saved papers are about."
                    if not ranked else
                    f"{len(ranked)} concepts show up in what you save but are not in "
                    f"your fetch profile."),
        "results": [{"term": term, "saved_papers": n,
                     "examples": examples[term][:3]} for term, n in ranked[:limit]],
    }


HANDLERS = {
    "search_papers": tool_search_papers,
    "fetch_papers": tool_fetch_papers,
    "ask_library": tool_ask_library,
    "suggest_profile_terms": tool_suggest_profile_terms,
    "get_similar": tool_get_similar,
    "get_trends": tool_get_trends,
    "get_saved": tool_get_saved,
    "suggest_reading": tool_suggest_reading,
    "save_paper": tool_save_paper,
    "get_digest": tool_get_digest,
    "library_status": tool_library_status,
}


# --- protocol --------------------------------------------------------------

def handle(message: Dict[str, Any]) -> Dict[str, Any]:
    method = message.get("method", "")
    msg_id = message.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method in ("notifications/initialized", "initialized"):
        return {}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        handler = HANDLERS.get(name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"}}
        try:
            payload = handler(params.get("arguments") or {})
        except Exception as exc:  # a tool crash must not kill the session
            payload = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
            print(traceback.format_exc(), file=sys.stderr)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(payload, indent=1, ensure_ascii=False)}]}}

    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    # storage.py solved this on the read side; this is the same fix on the write
    # side, and it covers stdin too because the client sends UTF-8 as well.
    force_utf8_streams(stdin, stdout, sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
