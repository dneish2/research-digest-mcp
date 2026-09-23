"""Which arXiv endpoint a fetch uses, what it is doing, and what happened.

arXiv runs two services and this tool has always had both. They are not
alternatives in the "either would do" sense; they fail independently:

  export.arxiv.org/api/query   search. Ranks results, takes a keyword query,
                               and rate-limits on client reputation. Once it
                               decides to refuse you it answers 406 to
                               everything, including a request for one paper.

  oaipmh.arxiv.org/oai         harvest. No ranking and no keyword query, takes
                               a date range, returns about a thousand records
                               per request, and answers load with a 503 that
                               says exactly how long to wait.

The old routing sent a fetch to search unless the caller passed a date window.
Measured on this machine while writing this: search was refusing with 406 and
59 minutes left on its backoff, while harvest served 1,140 cs records in 0.2s.
So the button was unusable for an hour for want of choosing the other endpoint,
and every surface reported it as arXiv being unavailable. It was not. One of
arXiv's two services was.

Hence this module. It decides the route, records what it did, and exposes both
to the interface, because a fetch that cannot say which service it used and
what window it asked for cannot be debugged from the outside.

Two dating facts are encoded here, because both of them look like bugs:

  A harvest date range filters on DATESTAMP, when the record last changed, not
  when the paper came out. Harvesting one day returns papers created over the
  preceding several days plus every old paper revised that day.

  arXiv announces on a delay. Measured over datestamp day 2026-09-16: 769 of
  the papers were created on the 15th, 225 on the 14th, 27 on the 13th, and
  none at all on the 16th itself. So the newest day or two of the literature
  cannot be fetched today by anyone, and a library whose newest paper is three
  days old is usually correct rather than broken.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import HOME, load_profile, now_stamp, record_fetch

FETCH_LOG = HOME / "fetch-log.jsonl"

# A catch-up harvests one request per day of its window, so an unbounded window
# is an unbounded wait. Past this the honest move is to say how far behind the
# library is and let the reader choose, rather than starting a twenty minute
# job from a button that looks instant.
CATCH_UP_MAX_DAYS = 31

# How far back a catch-up looks for days it holds nothing from.
#
# Starting at the newest paper in the library is the obvious choice and it is
# wrong, because it assumes everything behind the newest paper is complete.
# Measured on this library the day this was written: it held 672 papers from
# Sep 16 and 531 from Sep 17, and nothing at all from Sep 11 through Sep 15.
# A catch-up starting at the newest paper would have skipped straight over
# those five days, every day, forever, and a search across them would have
# returned a confident nothing. So a catch-up starts at the oldest day inside
# this window that the library holds no papers from.
CATCH_UP_LOOKBACK = 21

# Roughly what one harvest request returns, measured rather than documented:
# arXiv served 1,140 cs records for a single datestamp day in one page, and
# 1,300 per page when walking a longer range with a resumption token. Used only
# to describe the shape of the walk on screen, never to predict a total.
HARVEST_PAGE_SIZE = 1300

# How far behind the present arXiv's announcements run. Used only to explain a
# gap, never to hide one.
ANNOUNCE_LAG_DAYS = 2

# Keep the tail of the log bounded when reading. The file is append-only and
# small (one line per run), but a year of hourly runs is still 8,000 lines and
# no surface needs more than the last few dozen.
LOG_READ_LIMIT = 400


# ---------------------------------------------------------------- live state

# One fetch at a time, so this is a single slot rather than a table. It lives
# in memory on purpose: a job that did not survive the process did not finish,
# and resuming half a harvest from a file would be inventing state. What
# survives a restart is the log, which only records completed runs.
_progress_lock = threading.Lock()
_progress: Dict[str, Any] = {"state": "idle"}


def progress() -> Dict[str, Any]:
    """What the running fetch is doing right now, as the browser polls it."""
    with _progress_lock:
        snapshot = dict(_progress)
    if snapshot.get("started_at"):
        snapshot["elapsed"] = round(time.time() - snapshot["started_at"], 1)
    return snapshot


def _set_progress(**fields) -> None:
    with _progress_lock:
        _progress.update(fields)


def _reset_progress(**fields) -> None:
    with _progress_lock:
        _progress.clear()
        _progress.update(fields)


def is_running() -> bool:
    return progress().get("state") == "running"


# ------------------------------------------------------------------ the plan

def _newest_published(papers: List[Dict[str, Any]]) -> str:
    newest = ""
    for paper in papers:
        published = str(paper.get("published") or "")[:10]
        if published > newest:
            newest = published
    return newest


def _covered_days(papers: List[Dict[str, Any]]) -> set:
    return {str(p.get("published") or "")[:10] for p in papers}


def recent_gaps(papers: List[Dict[str, Any]], today: Optional[date] = None,
                lookback: int = CATCH_UP_LOOKBACK) -> Dict[str, List[str]]:
    """Empty recent days, split into the two kinds, which are not alike.

    A `hole` is a day we hold nothing from that is OLDER than the newest paper
    we hold. That one is a real gap: we demonstrably received papers published
    after it, so the day was reachable and we do not have it.

    A `pending` day is newer than our newest paper, which means arXiv has not
    announced it to anybody yet. Nothing can be done about those and they are
    not a fault in the library.

    The split is evidence rather than a constant, and it has to be, because a
    constant is wrong twice: the first version of this counted back a fixed two
    days and then reported Sep 18 as missing on Sep 20, which was simply a
    Friday that had not been announced over a weekend. A tool that reports an
    unavoidable delay as a hole teaches you to ignore its gap warnings, and
    then the real hole goes unread too.
    """
    today = today or date.today()
    covered = _covered_days(papers)
    newest = _newest_published(papers)
    # An empty library has neither kind. Without this guard every recent day
    # fell into `pending`, so someone who had just installed the tool was told
    # that 22 days were "empty because arXiv has not announced them yet". That
    # is false, and it reads as the tool being broken before they have fetched
    # anything at all.
    if not newest:
        return {"holes": [], "pending": []}
    holes, pending = [], []
    for offset in range(lookback, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        if day in covered:
            continue
        (holes if newest and day < newest else pending).append(day)
    return {"holes": holes, "pending": pending}


def _endpoint_health() -> Dict[str, Any]:
    """What each service is doing, separately.

    Separately is the whole point. A single "arXiv is down" flag was what let
    one service's backoff disable the other, and clearing search's cooldown
    because a harvest succeeded would be a lie about a different server.
    """
    from .fetchers import cooldown_detail
    cool = cooldown_detail()
    remaining = int(cool.get("remaining") or 0)
    strikes = int(cool.get("strikes") or 0)
    return {
        "harvest": {
            "label": "harvest feed",
            "host": "oaipmh.arxiv.org",
            "purpose": "bulk records over a date range",
            "state": "ready",
            "note": ("Answers load with a 503 that names its own wait, so it "
                     "does not carry a block between runs."),
        },
        "search": {
            "label": "search API",
            "host": "export.arxiv.org",
            "purpose": "ranked results for a keyword query",
            "state": "cooling" if remaining > 0 else "ready",
            "remaining": remaining,
            "strikes": strikes,
            "last_code": cool.get("last_code"),
            "since": cool.get("since", ""),
            "note": (f"Refusing this client since {cool.get('since', '')}, "
                     f"refusal {strikes} in a row. This is a block on this "
                     f"machine and not about your query."
                     if remaining > 0 else "No refusal on record."),
        },
    }


def plan(settings: Optional[Dict[str, Any]] = None, since: str = "",
         until: str = "", today: Optional[date] = None) -> Dict[str, Any]:
    """What a fetch would do right now, without doing it.

    The interface calls this on every page load, so a reader can see which
    service is about to be used and how far behind the library is before
    pressing anything.
    """
    from . import storage
    from .config import load_settings

    settings = settings if settings is not None else load_settings()
    today = today or date.today()
    papers = storage.load_papers()
    newest = _newest_published(papers)

    if since:
        window_since = since
        window_until = until or today.isoformat()
        kind = "backfill"
    else:
        kind = "catch up"
        # From the oldest recent day we hold nothing from, not from today and
        # not from the newest paper. Starting at today leaves a hole
        # permanently; starting at the newest paper steps over any hole behind
        # it, which is the same bug one day later.
        holes = recent_gaps(papers, today)["holes"]
        if holes:
            start = datetime.strptime(holes[0], "%Y-%m-%d").date()
        elif newest:
            start = datetime.strptime(newest, "%Y-%m-%d").date()
        else:
            start = today - timedelta(days=7)
        window_since = start.isoformat()
        window_until = today.isoformat()

    span = (datetime.strptime(window_until, "%Y-%m-%d").date()
            - datetime.strptime(window_since, "%Y-%m-%d").date()).days + 1
    capped = span > CATCH_UP_MAX_DAYS
    if capped and kind == "catch up":
        window_since = (today - timedelta(days=CATCH_UP_MAX_DAYS - 1)).isoformat()
        span = CATCH_UP_MAX_DAYS

    behind = ((today - datetime.strptime(newest, "%Y-%m-%d").date()).days
              if newest else None)

    return {
        "status": "ok",
        "route": "harvest",
        "kind": kind,
        "reason": ("The harvest feed takes a date range directly and is "
                   "provisioned separately from search, so it stays available "
                   "when search is refusing."),
        # No request count is promised here. The first version of this screen
        # said "one request per day, 22 in total" and it was not true: the
        # harvest walks the whole range in pages of about 1,300 records, so the
        # count follows how much arXiv published, not how many days you asked
        # for. A number on screen should be one you can get back to.
        "window": {"since": window_since, "until": window_until, "days": span,
                   "page_size": HARVEST_PAGE_SIZE},
        "capped": capped,
        "cap_note": (f"Your library is further behind than one run should "
                     f"cover, so this asks for the most recent "
                     f"{CATCH_UP_MAX_DAYS} days. Run it again, or use a month "
                     f"on the Profile page, to reach further back."
                     if capped else ""),
        "library": {
            "papers": len(papers),
            "newest_published": newest,
            "behind_days": behind,
            "lookback": CATCH_UP_LOOKBACK,
            **recent_gaps(papers, today),
        },
        # A fresh install is its own state and deserves its own sentence. The
        # gap and staleness language below is all relative to papers you already
        # hold, and none of it means anything when you hold none.
        "first_run": not papers,
        "first_run_note": (
            f"Your library is empty, which is where everyone starts. This first "
            f"fetch asks arXiv for the last {span} days in the categories your "
            f"profile lists, and nothing else on this page will have much to say "
            f"until it finishes."
            if not papers else ""),
        "lag_note": (
            "arXiv announces on a delay, so the newest two or three days are "
            "not available to anyone yet. Measured on one real day of records: "
            "769 of them had been published the day before, 225 two days "
            "before, 27 three days before, and none that same day. Weekends "
            "stretch it further. So a library whose newest paper is a few days "
            "old is up to date, not broken."
        ),
        "endpoints": _endpoint_health(),
        "running": is_running(),
        "categories": load_profile(settings)["all_categories"],
    }


# ------------------------------------------------------------------- the run

def _append_log(entry: Dict[str, Any]) -> None:
    try:
        FETCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FETCH_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass          # a run we cannot log is not worth failing the fetch over


def log_tail(limit: int = 20) -> List[Dict[str, Any]]:
    """The last `limit` runs, newest first. A missing log is not an error."""
    try:
        with FETCH_LOG.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-LOG_READ_LIMIT:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def log_days(days: int = 14, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """One cell per day: did a fetch run, did it work, what did it add.

    A daily job is only trustworthy if you can see the days it did not run.
    So every day in the window gets a row, including the empty ones, and an
    empty day reads as "no run recorded" rather than as nothing at all.
    """
    today = today or date.today()
    runs = log_tail(LOG_READ_LIMIT)
    by_day: Dict[str, Dict[str, Any]] = {}
    for entry in runs:
        day = str(entry.get("at", ""))[:10]
        if not day:
            continue
        cell = by_day.setdefault(day, {"day": day, "runs": 0, "added": 0,
                                       "ok": 0, "failed": 0, "sources": []})
        cell["runs"] += 1
        cell["added"] += int(entry.get("added") or 0)
        cell["ok" if entry.get("ok") else "failed"] += 1
        source = entry.get("source") or "unknown"
        if source not in cell["sources"]:
            cell["sources"].append(source)

    out = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        out.append(by_day.get(day, {"day": day, "runs": 0, "added": 0,
                                    "ok": 0, "failed": 0, "sources": []}))
    return out


def run_fetch(settings: Optional[Dict[str, Any]] = None, since: str = "",
              until: str = "", source: str = "web",
              today: Optional[date] = None) -> Dict[str, Any]:
    """Fetch through the harvest feed, falling back to search if it refuses.

    Returns the same shape the old /api/refresh returned, plus `route`, so a
    caller can say which service answered. Every outcome is logged, successes
    and refusals alike, because "did the daily job run" is a question about
    attempts and not only about papers.
    """
    from . import storage
    from .config import load_settings
    from .fetchers import ArxivUnavailable, cooldown_detail, fetch_settings
    from .harvest import HarvestUnavailable, harvest_profile

    settings = settings if settings is not None else load_settings()
    decided = plan(settings, since, until, today=today)
    window = decided["window"]
    started = time.time()

    _reset_progress(
        state="running", job=uuid.uuid4().hex[:8], route="harvest",
        started_at=started, window=window, kind=decided["kind"],
        lines=[], seen=0, kept=0,
        message=(f"Asking the harvest feed for {window['since']} to "
                 f"{window['until']}."))

    def on_page(oai_set: str, pages: int, seen: int, kept: int) -> None:
        _set_progress(
            seen=seen, kept=kept,
            message=(f"{oai_set}: request {pages}, {seen:,} records read, "
                     f"{kept:,} kept"))
        with _progress_lock:
            lines = _progress.setdefault("lines", [])
            lines.append({"at": time.strftime("%H:%M:%S"), "set": oai_set,
                          "page": pages, "seen": seen, "kept": kept})
            del lines[:-40]

    result: Dict[str, Any] = {}
    route = "harvest"
    errors: List[str] = []
    try:
        result = harvest_profile(
            load_profile(settings), since=window["since"], until=window["until"],
            on_page=on_page, published_only=True)
        errors = list(result.get("errors") or [])
    except HarvestUnavailable as exc:
        # Only now is search worth trying, and only because the reader asked
        # for papers rather than for a particular server.
        errors = [f"harvest feed: {exc}"]
        _set_progress(route="search", message=(
            "The harvest feed refused, so this is falling back to the search "
            "API."))
        route = "search"
        try:
            result = fetch_settings(settings)
            errors += list(result.get("errors") or [])
        except ArxivUnavailable as search_exc:
            errors.append(f"search API: {search_exc}")
            return _finish(source, route, window, started, decided,
                           added=0, total=len(storage.load_papers()),
                           fetched=0, errors=errors, ok=False, blocked=True,
                           message=(
                               "Both of arXiv's services refused this machine. "
                               f"Harvest: {exc}. Search: {search_exc}."),
                           cooldown=cooldown_detail())

    papers = result.get("papers") or []
    if not papers:
        blocked = any("refus" in str(e).lower() or "406" in str(e)
                      or "429" in str(e) for e in errors)
        return _finish(
            source, route, window, started, decided, added=0,
            total=len(storage.load_papers()), fetched=0, errors=errors,
            ok=not blocked, blocked=blocked,
            message=(errors[0] if blocked else _empty_message(decided, window)),
            cooldown=cooldown_detail())

    stats = storage.merge_papers(papers, result.get("run_date")
                                 or date.today().isoformat())
    record_fetch(stats["added"], stats["total"], source=source,
                 next_offset=result.get("next_offset"))
    return _finish(
        source, route, window, started, decided, added=stats["added"],
        total=stats["total"], fetched=len(papers), errors=errors, ok=True,
        blocked=False, seen=result.get("seen", 0),
        message=(
            f"Read {result.get('seen', len(papers)):,} records from arXiv's "
            f"{window['since']} to {window['until']} window, kept "
            f"{len(papers):,} in your categories, {stats['added']:,} of them new. "
            f"Library holds {stats['total']:,}."
            + (" Re-run embed to include the new ones in similarity search."
               if stats["added"] else "")),
        cooldown=cooldown_detail())


def _empty_message(decided: Dict[str, Any], window: Dict[str, Any]) -> str:
    """Why an answered request can still hold nothing.

    Worth spelling out, because this is the case that reads as a broken tool
    and is usually the announcement delay doing exactly what it does.
    """
    return (
        f"arXiv answered and had no papers published between {window['since']} "
        f"and {window['until']} in your categories. "
        + decided["lag_note"])


def _finish(source: str, route: str, window: Dict[str, Any], started: float,
            decided: Dict[str, Any], *, added: int, total: int, fetched: int,
            errors: List[str], ok: bool, blocked: bool, message: str,
            cooldown: Dict[str, Any], seen: int = 0) -> Dict[str, Any]:
    entry = {
        "at": now_stamp(),
        "source": source,
        "route": route,
        "kind": decided["kind"],
        "since": window["since"],
        "until": window["until"],
        "days": window["days"],
        "seen": seen,
        "fetched": fetched,
        "added": added,
        "total": total,
        "ok": ok,
        "blocked": blocked,
        "errors": errors[:5],
        "seconds": round(time.time() - started, 1),
        "message": message,
    }
    _append_log(entry)
    # The cooldown goes on the progress result but not into the log. The log is
    # a record of what happened; a countdown is only true for the minute you
    # read it, and a stale one on screen is what makes people retry into a block.
    _reset_progress(state="done", route=route, window=window,
                    result=dict(entry, cooldown=cooldown))
    return {
        "status": "ok" if ok else "error",
        "route": route,
        "window": window,
        "fetched": fetched,
        "seen": seen,
        "added": added,
        "total": total,
        "errors": errors,
        "blocked": blocked,
        "cooldown": cooldown,
        "seconds": entry["seconds"],
        "message": message,
    }


def start_census(days: int = 90, sets: Optional[List[str]] = None,
                 source: str = "web") -> Dict[str, Any]:
    """Build the field census on the same single job slot as a fetch.

    One slot rather than two, and not for want of threads: two harvest walks at
    once is two clients hammering one endpoint, which is how a polite tool earns
    the 503 it then reports as arXiv being unavailable.
    """
    from . import census
    if is_running():
        return {"status": "error", "message": "A fetch is already running.",
                "progress": progress()}
    job = uuid.uuid4().hex[:8]
    _reset_progress(state="running", job=job, kind="census", route="harvest",
                    started_at=time.time(), lines=[], seen=0, kept=0,
                    message=f"Counting the last {days} days of arXiv.")

    def on_page(oai_set: str, pages: int, seen: int, dated: int) -> None:
        _set_progress(seen=seen, kept=dated, message=(
            f"{oai_set}: request {pages}, {seen:,} papers counted, nothing stored"))
        with _progress_lock:
            lines = _progress.setdefault("lines", [])
            lines.append({"at": time.strftime("%H:%M:%S"), "set": oai_set,
                          "page": pages, "seen": seen, "kept": dated})
            del lines[:-40]

    def work():
        started = time.time()
        try:
            data = census.backfill(days=days, sets=sets, on_page=on_page)
            cov = census.coverage(data)
            entry = {
                "at": now_stamp(), "source": source, "route": "harvest",
                "kind": "census", "since": cov["first"], "until": cov["last"],
                "days": cov["days"], "seen": cov["papers"], "fetched": 0,
                "added": 0, "total": cov["papers"], "ok": True, "blocked": False,
                "errors": [], "seconds": round(time.time() - started, 1),
                "message": (
                    f"Counted {cov['papers']:,} arXiv papers across {cov['days']} "
                    f"days, {cov['first']} to {cov['last']}. Nothing was added to "
                    f"your library: only the daily counts are kept."),
            }
            _append_log(entry)
            _reset_progress(state="done", kind="census", result=entry)
        except Exception as exc:
            _reset_progress(state="done", kind="census", result={
                "ok": False, "blocked": False, "added": 0,
                "message": f"{type(exc).__name__}: {exc}"})

    threading.Thread(target=work, daemon=True, name=f"census-{job}").start()
    return {"status": "ok", "job": job, "progress": progress()}


def start_background(settings: Optional[Dict[str, Any]] = None, since: str = "",
                     until: str = "", source: str = "web") -> Dict[str, Any]:
    """Run a fetch on a thread so the page can watch it happen.

    A catch-up is one request per day of its window and a month backfill is
    thirty, which is far longer than a click should block for. Holding the
    request open also meant the interface could not show progress it already
    had: the harvest reports every page it reads and nothing was listening.
    """
    if is_running():
        return {"status": "error", "message": "A fetch is already running.",
                "progress": progress()}
    job = uuid.uuid4().hex[:8]
    _reset_progress(state="running", job=job, started_at=time.time(),
                    message="Starting.", lines=[], seen=0, kept=0)

    def work():
        try:
            run_fetch(settings, since=since, until=until, source=source)
        except Exception as exc:                      # never leave it "running"
            _reset_progress(state="done", result={
                "ok": False, "blocked": False, "added": 0,
                "message": f"{type(exc).__name__}: {exc}"})

    threading.Thread(target=work, daemon=True, name=f"fetch-{job}").start()
    return {"status": "ok", "job": job, "progress": progress()}
