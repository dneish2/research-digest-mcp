"""The browser interface.

Standard library http.server. No Flask, no build step, no node_modules. The
whole point of this surface is that it shows its working: every number a tool
produces comes back with the arithmetic that made it.

It binds to localhost only. This is a personal library, not a service.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import storage
from .config import HOME, load_settings
from .scoring import BOILERPLATE, score_paper, rank_all, explain_sentence
from .trends import compute_trends, cross_pollination

STATIC = Path(__file__).parent / "static"


NEEDS_EXTRA = ("Similarity search is an optional extra. Install it with "
               "'pip install research-digest-mcp[embeddings]', then run "
               "'research-digest embed'.")


def _similar(paper_id: str, limit: int):
    try:
        from .similarity import MixedBasis, SimilaritySearch
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
        return {"status": "no_embeddings",
                "message": "No vectors built yet. Run 'research-digest embed' to enable "
                           "similarity search across your library."}
    by_id = {p["id"]: p for p in storage.load_papers()}
    return {"status": "ok", "results": [
        {"id": pid, "similarity": s,
         "title": by_id.get(pid, {}).get("title", pid),
         "url": by_id.get(pid, {}).get("url", ""),
         "concepts": by_id.get(pid, {}).get("concepts", [])[:5]}
        for pid, s in hits]}


def api(path: str, params: dict) -> dict:
    one = {k: v[0] for k, v in params.items()}

    if path == "/api/status":
        from .mcp import tool_library_status
        return tool_library_status({})

    if path == "/api/search":
        query = one.get("q", "").strip()
        if not query:
            return {"status": "ok", "matched": 0, "results": [], "searched": 0}
        papers = storage.load_papers()
        terms = query.lower().split()
        every = rank_all(papers, terms)
        limit = int(one.get("limit", 25))
        return {
            "status": "ok", "query": query, "terms": terms,
            "searched": len(papers), "matched": len(every),
            "results": [{
                "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                "published": p.get("published", ""), "category": p.get("primary_category", ""),
                "abstract": (p.get("abstract") or "")[:420],
                "concepts": (p.get("concepts") or [])[:6],
                "score": p["score"], "why": p["why"], "why_text": p["why_text"],
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

        topics = load_settings()["topics"]
        for paper in papers:
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
        return {
            "status": "ok", "window": window, "total": len(papers),
            "topics": topics,
            "results": [{
                "id": p["id"], "title": p.get("title", ""), "url": p.get("url", ""),
                "published": p.get("published", ""), "category": p.get("primary_category", ""),
                "abstract": (p.get("abstract") or "")[:600],
                "concepts": (p.get("concepts") or [])[:6],
                "score": p["score"], "why": p["why"], "why_text": p["why_text"],
                "saved": p["saved"], "read": p["read"],
            } for p in papers[:limit]],
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
        from .fetchers import ArxivUnavailable, fetch_settings
        settings = load_settings()
        try:
            result = fetch_settings(settings)
        except ArxivUnavailable as exc:
            return {"status": "error", "message": str(exc)}
        if not result["papers"]:
            return {"status": "error",
                    "message": "arXiv returned nothing. Nothing was written.",
                    "errors": result["errors"]}
        stats = storage.merge_papers(result["papers"], result["run_date"])
        return {
            "status": "ok", "fetched": len(result["papers"]),
            "added": stats["added"], "total": stats["total"],
            "errors": result["errors"],
            "message": (f"Fetched {len(result['papers'])}, {stats['added']} new. "
                        f"Library holds {stats['total']}."
                        + (" Re-run embed to include them in similarity search."
                           if stats["added"] else "")),
        }

    if path == "/api/saved":
        from .mcp import tool_get_saved
        return tool_get_saved({"limit": 200})

    if path == "/api/similar":
        return _similar(one.get("id", ""), int(one.get("limit", 8)))

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
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
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
