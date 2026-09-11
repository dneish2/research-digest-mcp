"""Command line entry point.

    research-digest fetch      pull today's papers from arXiv
    research-digest embed      build vectors for similarity search
    research-digest status     what is in the library
    research-digest search Q   search from the shell
    research-digest digest     today's top papers, written to a dated file
    research-digest web        open the browser interface
    research-digest mcp        run as an MCP server (what your AI client calls)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import storage
from .config import HOME, load_settings, save_settings


def cmd_fetch(args) -> int:
    from .fetchers import ArxivUnavailable, fetch_settings
    settings = load_settings()
    print(f"Fetching {', '.join(settings['categories'])} from arXiv...", flush=True)
    try:
        result = fetch_settings(settings)
    except ArxivUnavailable as exc:
        print(f"Could not fetch: {exc}", file=sys.stderr)
        return 1

    for error in result["errors"]:
        print(f"  warning: {error}", file=sys.stderr)
    if not result["papers"]:
        print("No papers returned. Nothing was written.", file=sys.stderr)
        return 1

    stats = storage.merge_papers(result["papers"], result["run_date"])
    print(f"Fetched {len(result['papers'])}, {stats['added']} new. "
          f"Library now holds {stats['total']} papers.")
    if stats["added"]:
        print("Run 'research-digest embed' to include them in similarity search.")
    return 0


def cmd_embed(args) -> int:
    from .embeddings import EncoderUnavailable, embed_all
    from .similarity import EmbeddingStore

    papers = storage.load_papers()
    if not papers:
        print(f"No papers in {HOME}. Run 'research-digest fetch' first.", file=sys.stderr)
        return 1

    engine = args.engine or load_settings()["encoder"]
    # flush before the work: otherwise a message on stderr overtakes this line
    # and the error appears above the step it belongs to.
    print(f"Embedding {len(papers)} papers with {engine} (one fit over the whole library)...",
          flush=True)
    try:
        result = embed_all(papers, engine)
    except EncoderUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    written = EmbeddingStore().replace_all(result["vectors"], result["engine"], result["basis"])
    print(f"Wrote {written} vectors, {result['dims']} columns, basis {result['basis']}.")
    print("The previous fit was replaced. Vectors from different fits are never mixed.")
    return 0


def cmd_status(args) -> int:
    from .mcp import tool_library_status
    status = tool_library_status({})
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    print(f"Library      {status['home']}")
    print(f"Papers       {status['papers']}")
    if status.get("earliest"):
        print(f"Range        {status['earliest']} to {status['latest']}")
    print(f"Saved        {status['saved']}")
    print(f"Categories   {', '.join(status['categories'])}")
    emb = status.get("embeddings", {})
    if emb.get("available") is False:
        print(f"Embeddings   not installed. {emb.get('hint', '')}")
    elif emb.get("vectors"):
        fit = (emb.get("fits") or [{}])[0]
        state = "usable" if emb.get("usable") else "NEEDS REBUILD"
        print(f"Embeddings   {emb['vectors']} vectors, {fit.get('dims')} columns, {state}")
        if emb.get("warning"):
            print(f"             {emb['warning']}")
    else:
        print("Embeddings   none yet. Run 'research-digest embed'.")
    return 0


def cmd_search(args) -> int:
    from .mcp import tool_search_papers
    result = tool_search_papers({"query": " ".join(args.query), "limit": args.limit})
    if result.get("status") != "ok":
        print(result.get("message", result.get("status")), file=sys.stderr)
        return 1
    print(f"{result['matched']} of {result['searched']} papers matched.\n")
    for i, paper in enumerate(result["results"], 1):
        print(f"{i}. [{paper['score']:.3f}] {paper['title']}")
        print(f"   {paper['url']}  {paper['published']}")
        print(f"   why: {paper['why']}\n")
    return 0


def cmd_digest(args) -> int:
    from .digest import build_digest, write_digest
    papers = storage.load_papers()
    if not papers:
        print(f"No papers in {HOME}. Run 'research-digest fetch' first.", file=sys.stderr)
        return 1
    settings = load_settings()
    result = build_digest(papers, settings["topics"], for_date=args.date, size=args.size)
    if not result["picks"]:
        print(f"Nothing in {len(papers)} papers matched your topics today.", file=sys.stderr)
        return 1
    path = write_digest(result)
    print(f"{result['date']}: {len(result['picks'])} picks from "
          f"{result['considered']} matches. Wrote {path}\n")
    for i, paper in enumerate(result["picks"], 1):
        print(f"{i}. {paper['title']}")
        print(f"   {paper.get('about', '')}\n")
    return 0


def cmd_web(args) -> int:
    from .web import run
    run(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_mcp(args) -> int:
    from .mcp import serve
    serve()
    return 0


def cmd_config(args) -> int:
    settings = load_settings()
    if args.add_topic:
        for topic in args.add_topic:
            if topic not in settings["topics"]:
                settings["topics"].append(topic)
    if args.add_category:
        for category in args.add_category:
            if category not in settings["categories"]:
                settings["categories"].append(category)
    if args.add_topic or args.add_category:
        save_settings(settings)
        print("Saved.")
    print(json.dumps(settings, indent=2))
    return 0


def main(argv=None) -> int:
    # Same fix as mcp.serve(): on Windows, stdout/stderr default to the locale
    # codepage, which cannot carry most arXiv titles and author names. Without
    # this, `research-digest search` or `fetch` crashes the moment a result has
    # an accented name or a Greek letter in it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")

    parser = argparse.ArgumentParser(
        prog="research-digest",
        description="A personal research library your AI assistant can read.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("fetch", help="pull today's papers from arXiv").set_defaults(func=cmd_fetch)

    embed = sub.add_parser("embed", help="build vectors for similarity search")
    embed.add_argument("--engine", choices=["tfidf-svd", "minilm"], default=None)
    embed.set_defaults(func=cmd_embed)

    digest = sub.add_parser("digest", help="today's top papers, written to a dated file")
    digest.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    digest.add_argument("--size", type=int, default=5)
    digest.set_defaults(func=cmd_digest)

    status = sub.add_parser("status", help="what is in the library")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    search = sub.add_parser("search", help="search from the shell")
    search.add_argument("query", nargs="+")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    web = sub.add_parser("web", help="open the browser interface")
    web.add_argument("--port", type=int, default=8756)
    web.add_argument("--no-browser", action="store_true")
    web.set_defaults(func=cmd_web)

    sub.add_parser("mcp", help="run as an MCP server over stdio").set_defaults(func=cmd_mcp)

    config = sub.add_parser("config", help="show or change settings")
    config.add_argument("--add-topic", action="append")
    config.add_argument("--add-category", action="append")
    config.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
