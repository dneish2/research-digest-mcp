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
from .config import HOME, force_utf8_streams, load_settings
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
    bare = arxiv_id.split("v")[0]

    by_bare = {p["id"].split("v")[0]: p for p in storage.load_papers()}
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
    result = build_digest(papers, settings["topics"],
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
    papers = storage.load_papers()
    settings = load_settings()
    status: Dict[str, Any] = {
        "status": "ok",
        "home": str(HOME),
        "papers": len(papers),
        "saved": len(storage.load_saved()),
        "read": len(storage.load_read()),
        "categories": settings["categories"],
        "topics": settings["topics"],
    }
    days = sorted(str(p.get("first_seen") or p.get("published") or "")[:10]
                  for p in papers if p.get("first_seen") or p.get("published"))
    if days:
        status["earliest"] = days[0]
        status["latest"] = days[-1]
    status["runs"] = storage.load_archive().get("runs", [])[-10:]

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


HANDLERS = {
    "search_papers": tool_search_papers,
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
