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
from .trends import compute_trends

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

    if path == "/api/trends":
        return compute_trends(storage.load_papers())

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
