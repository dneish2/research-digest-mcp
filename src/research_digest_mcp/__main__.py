"""Command line entry point.

    research-digest doctor         check the install, print the config to paste
    research-digest fetch          pull papers from arXiv against your profile
    research-digest ask "..."      ask your library a question in plain English
    research-digest search Q       keyword search of your library
    research-digest arxiv Q        search arXiv itself and ADD what comes back
    research-digest digest         today's top papers, written to a dated file
    research-digest workspace      what your own projects say you care about
    research-digest profile        what gets fetched for you, and why
    research-digest export         write your papers out (json/markdown/bibtex)
    research-digest import FILE    load papers from another archive.json
    research-digest embed          build vectors for similarity search
    research-digest status         what is in the library
    research-digest web            open the browser interface
    research-digest mcp            run as an MCP server (what your AI client calls)

The difference worth knowing: `search` reads the shelf, `arxiv` puts something
on it. A library tool whose search cannot reach past what it already holds will
answer "nothing found" for a paper that exists.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import storage
from .config import (
    HOME, force_utf8_streams, load_settings, load_state, ranking_topics,
    record_fetch, save_settings, save_state)


def cmd_fetch(args) -> int:
    from .config import load_profile
    from .fetchers import ArxivUnavailable, fetch_profile
    settings = load_settings()
    profile = load_profile(settings)

    offset = int(load_state().get("fetch_offset", 0))
    window = ""
    if args.since or args.until:
        window = f" submitted {args.since or 'any'} to {args.until or 'today'}"
    print(f"Fetching {len(profile['all_categories'])} categories"
          f" against {len(profile['all_topics'])} topics{window}...", flush=True)

    try:
        result = fetch_profile(profile, since=args.since, until=args.until, offset=offset)
    except ArxivUnavailable as exc:
        print(f"Could not fetch: {exc}", file=sys.stderr)
        return 1

    # Say what was actually searched. "Fetched 50, 0 new" hides which tier or
    # which category produced the zero, which is the only thing worth knowing
    # when the number is disappointing.
    for tier in ("core", "complementary", "stretch"):
        rows = [r for r in result["plan"] if r["tier"] == tier]
        if not rows:
            continue
        got = sum(r["returned"] for r in rows)
        print(f"  {tier:<14} {got:>4} papers from {len(rows)} categories"
              f"  [{', '.join(rows[0]['keywords'][:3])}...]")

    for error in result["errors"]:
        print(f"  warning: {error}", file=sys.stderr)
    if not result["papers"]:
        print("No papers returned. Nothing was written.", file=sys.stderr)
        return 1

    stats = storage.merge_papers(result["papers"], result["run_date"])
    print(f"\nFetched {len(result['papers'])}, {stats['added']} new. "
          f"Library now holds {stats['total']} papers.")

    # Advance the keyword window so the next run asks about the rest of the
    # profile rather than repeating these topics forever, and record the time
    # to the second so the header can say more than "today".
    record_fetch(stats["added"], stats["total"], source="cli",
                 next_offset=result["next_offset"])

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


_IMPORT_CORE_FIELDS = (
    "id", "title", "abstract", "published", "primary_category",
    "categories", "url", "concepts", "authors",
    # Which tier fetched it. Dropping this on import is why no paper in a
    # restored library carried one, and the tier badge would have rendered
    # blank on every card in a library that had been through an import.
    "tier", "updated",
)


def cmd_import(args) -> int:
    """Load papers from another archive.json — this tool's own export format,
    from an older version, or from another machine's library. Safe to run more
    than once: papers already in your library are updated, not duplicated, and
    stale scorer output (score/why/bucket/novelty from whatever version wrote
    the file) is dropped rather than carried in, since the running code
    recomputes all of that on every read anyway.
    """
    from pathlib import Path
    path = Path(args.file)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 1

    source = raw.get("papers") if isinstance(raw, dict) else None
    if not isinstance(source, dict):
        print(f"{path} does not look like a research-digest archive "
              f"(expected a top-level 'papers' object).", file=sys.stderr)
        return 1

    cleaned = []
    for pid, paper in source.items():
        if not isinstance(paper, dict):
            continue
        record = {k: paper.get(k) for k in _IMPORT_CORE_FIELDS if paper.get(k) is not None}
        record.setdefault("id", pid)
        first_seen = paper.get("digest_date") or paper.get("first_seen")
        if first_seen:
            record["first_seen"] = str(first_seen)[:10]
        cleaned.append(record)

    run_dates = raw.get("dates") or raw.get("runs") or []
    stats = storage.import_papers(cleaned, run_dates)
    print(f"Imported {stats['added']} new, updated {stats['updated']} existing "
          f"(from {len(cleaned)} papers in {path.name}). "
          f"Library now holds {stats['total']} papers.")
    if stats["added"]:
        print("Run 'research-digest embed' to rebuild similarity search over the full library.")
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


def cmd_ask(args) -> int:
    """Ask in English from the shell. Same code path as the browser box."""
    from .ask import answer
    question = " ".join(args.question)
    result = answer(question, load_settings(), limit=args.limit,
                    use_llm=not args.no_llm)

    if result["status"] in ("error", "no_subject", "needs_workspace", "empty_library"):
        print(result.get("message", result["status"]), file=sys.stderr)
        return 1

    # The reading line first, always. When the answer is wrong you need to know
    # whether the question was misread or the library is thin, and those two
    # need opposite fixes.
    print(f"read as: {result['reading']}\n")
    if result["status"] == "no_match":
        print(f"Nothing in {result['searched']} papers matched.")
        print("Try: research-digest arxiv "
              f"{' '.join(result['terms'])!r} to go and get some.")
        return 1

    print(f"{result['matched']} of {result['searched']} papers matched.\n")
    for i, paper in enumerate(result["results"], 1):
        print(f"{i}. [{paper['score']:.3f}] {paper['title']}")
        print(f"   {paper['url']}  {paper['published']}  {paper['category']}")
        if paper.get("about"):
            print(f"   {paper['about']}")
        print()
    if result.get("related"):
        print(f"try next: {', '.join(result['related'])}")
    return 0


def cmd_arxiv(args) -> int:
    """Search arXiv itself and add what comes back. The only command that grows
    the library outside of a profile fetch."""
    from .mcp import tool_fetch_papers
    result = tool_fetch_papers({"query": " ".join(args.query),
                                "limit": args.limit, "save": args.save})
    if result["status"] != "ok":
        print(result.get("message", result["status"]), file=sys.stderr)
        return 1
    print(result["message"] + "\n")
    for i, paper in enumerate(result["results"], 1):
        print(f"{i}. {paper['title']}")
        print(f"   {paper['url']}  {paper['published']}")
    return 0


def cmd_workspace(args) -> int:
    """What this tool would read off your workspace, without searching anything."""
    from .workspace import WorkspaceUnavailable, configured_root, scan, search_terms
    settings = load_settings()
    root = args.root or configured_root(settings)
    if not root:
        print("No workspace folder set. Pass a path, or set one in the Profile tab.\n"
              "Nothing on disk is read until you name a folder.", file=sys.stderr)
        return 1
    try:
        result = scan(root, limit=args.limit)
    except WorkspaceUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result["how"] + "\n")
    print(f"Projects: {', '.join(result['projects'][:10])}\n")
    for term in result["terms"]:
        print(f"  {term['term']:<26} {term['files']:>4} files  "
              f"{term['spread']:>3} projects")
    print(f"\nA question about your workspace would search for: "
          f"{', '.join(search_terms(result))}")
    return 0


def cmd_export(args) -> int:
    from .web import _export
    result = _export(args.what, args.format)
    if args.output:
        from pathlib import Path
        target = Path(args.output)
        target.write_text(result["body"], encoding="utf-8")
        print(f"Wrote {result['count']} papers to {target}.")
    else:
        print(result["body"])
    return 0


def cmd_digest(args) -> int:
    from .digest import build_digest, write_digest
    papers = storage.load_papers()
    if not papers:
        print(f"No papers in {HOME}. Run 'research-digest fetch' first.", file=sys.stderr)
        return 1
    settings = load_settings()
    result = build_digest(papers, ranking_topics(settings),
                          for_date=args.date, size=args.size)
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


def cmd_doctor(args) -> int:
    from .doctor import run
    return run(check_network=args.network)


def cmd_profile(args) -> int:
    """Show what the tool thinks you read, and what that causes it to request.

    The gap between those two is the thing worth being able to see: a profile
    that is never turned into a query is indistinguishable from no profile, and
    that is precisely how the library drifted away from its owner's interests
    without anything appearing to be broken.
    """
    from .config import load_profile
    from .fetchers import _keyword_window, build_query

    settings = load_settings()
    profile = load_profile(settings)
    offset = int(settings.get("fetch_offset", 0))
    papers = storage.load_papers()

    held = {}
    for paper in papers:
        key = paper.get("primary_category", "")
        held[key] = held.get(key, 0) + 1

    if profile["work_context"]:
        print(profile["work_context"])
        print()
    print(f"{len(profile['all_categories'])} categories, "
          f"{len(profile['all_topics'])} topics, {len(papers)} papers held.\n")

    for name, tier in profile["tiers"].items():
        if not tier["categories"]:
            continue
        window = _keyword_window(tier["keywords"], offset)
        print(f"{name}  ({tier['per_category']} per category per run)")
        for category in tier["categories"]:
            count = held.get(category, 0)
            flag = "" if count else "   <- configured, holding nothing"
            print(f"    {category:<16} {count:>5} held{flag}")
        print(f"    next run asks about: {', '.join(window)}")
        if args.show_query and tier["categories"]:
            print(f"    query: {build_query(tier['categories'][0], window)}")
        print()
    return 0


def main(argv=None) -> int:
    # Without this, `research-digest search` or `fetch` crashes the moment a
    # result has an accented name or a Greek letter in it. See config.
    force_utf8_streams()

    parser = argparse.ArgumentParser(
        prog="research-digest",
        description="A personal research library your AI assistant can read.")
    sub = parser.add_subparsers(dest="command")

    fetch = sub.add_parser("fetch", help="pull papers from arXiv against your profile")
    fetch.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                       help="only papers submitted on or after this date. Use it to "
                            "backfill a stretch the daily run missed.")
    fetch.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                       help="only papers submitted on or before this date")
    fetch.set_defaults(func=cmd_fetch)

    doc = sub.add_parser("doctor", help="check the install and print the config to paste")
    doc.add_argument("--network", action="store_true",
                     help="also make one arXiv request to confirm it answers")
    doc.set_defaults(func=cmd_doctor)

    prof = sub.add_parser("profile", help="what gets fetched for you, and why")
    prof.add_argument("--show-query", action="store_true",
                      help="print the exact arXiv query each tier sends")
    prof.set_defaults(func=cmd_profile)

    imp = sub.add_parser("import", help="load papers from another archive.json")
    imp.add_argument("file", help="path to an archive.json (this tool's own export format)")
    imp.set_defaults(func=cmd_import)

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

    search = sub.add_parser("search", help="keyword search of your library")
    search.add_argument("query", nargs="+")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    ask = sub.add_parser("ask", help="ask your library a question in plain English")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--limit", type=int, default=8)
    ask.add_argument("--no-llm", action="store_true",
                     help="skip the local model even if one is configured, and read "
                          "the question by rule only")
    ask.set_defaults(func=cmd_ask)

    arxiv = sub.add_parser("arxiv", help="search arXiv itself and add what comes back")
    arxiv.add_argument("query", nargs="+")
    arxiv.add_argument("--limit", type=int, default=20)
    arxiv.add_argument("--save", action="store_true", help="bookmark everything fetched")
    arxiv.set_defaults(func=cmd_arxiv)

    ws = sub.add_parser("workspace", help="what your own projects say you are interested in")
    ws.add_argument("root", nargs="?", default=None,
                    help="folder to read, default the one set in settings")
    ws.add_argument("--limit", type=int, default=20)
    ws.set_defaults(func=cmd_workspace)

    exp = sub.add_parser("export", help="write your papers out as json, markdown or bibtex")
    exp.add_argument("--what", choices=["saved", "queue", "all"], default="saved")
    exp.add_argument("--format", choices=["json", "markdown", "bibtex"], default="json")
    exp.add_argument("-o", "--output", default=None, help="file to write, default stdout")
    exp.set_defaults(func=cmd_export)

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
