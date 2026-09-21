"""How much the field is publishing on a subject, as opposed to how much you fetched.

The Trends tab counted concepts across your own library, which answers a
question nobody asked. Reported as: "I do find it interesting that it's based on
my local library or download history, but frankly I'm more interested in
industry trends. For example from 1 week or 1 month ago, how many papers are
being reported on rag or evaluation or memory."

That is a different measurement and it needs a different source. The constraint
is that the whole field will not fit on a laptop and should not have to:

  > I don't want to download the entire arXiv library on my local machine. I
  > just want to be able to access and query that library appropriately.

So this streams and does not keep. A harvest request returns about 1,300
records; each one is counted against the tracked terms, bucketed by the day it
was published, and thrown away. What lands on disk is a few numbers per day.
Measured: one day of cs is about 1,140 records and arrives in well under a
second, and 90 days of counts is a file of a few kilobytes.

Two things this gets right that the library-based version could not:

  It counts every cs paper, not the fourteen categories your profile fetches.
  A share of the field has to be measured against the field.

  It records the denominator per day. "17 papers on rag" is meaningless without
  "of 1,140 that day", and a week where arXiv published more is otherwise
  indistinguishable from a week where a subject rose.

And one thing it has to get right to be worth reading at all: word boundaries.
Matching terms as substrings, which is what the concept vocabulary has always
done, counted `rag` in 262 of one day's 1,140 papers. The real number is 17.
The other 245 are words like leve*rag*es and sto*rag*e. A trend line drawn
through 94% noise is worse than no trend line, because it is confident.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import HOME

CENSUS_PATH = HOME / "census.json"

# How many of the newest days a trend line leaves out.
#
# Records are dated by when arXiv last touched them and a paper is announced a
# day or three after it was created, so counting publication day D properly
# means harvesting datestamps D through about D+3. Those datestamps are in the
# future, and arXiv refuses a future `until` outright ("until date too late"),
# so the newest days are always short until a later run fills them in. Reading
# them as real counts makes every subject look like it is collapsing this week.
REPORTING_LAG = 3

# The subjects tracked by default. Deliberately a small, hand-written list of
# things that are actually discussed, rather than every concept in the scorer:
# a trend line is only worth drawing for a subject with enough papers a week to
# have a shape. `aliases` exist because the field does not agree on one spelling
# and counting only one of them undercounts the subject, which looks exactly
# like the subject being smaller than it is.
DEFAULT_TERMS: Dict[str, List[str]] = {
    "rag": ["rag", "retrieval-augmented", "retrieval augmented"],
    "agents": ["agent", "agents", "agentic"],
    "multi-agent": ["multi-agent", "multi agent", "multiagent"],
    "evaluation": ["evaluation", "benchmark", "benchmarking"],
    "memory": ["memory", "memories"],
    "reasoning": ["reasoning", "chain-of-thought", "chain of thought"],
    "reinforcement learning": ["reinforcement learning", "rlhf", "rlvr"],
    "alignment": ["alignment", "aligned"],
    "interpretability": ["interpretability", "mechanistic interpretability"],
    "hallucination": ["hallucination", "hallucinations", "hallucinate"],
    "fine-tuning": ["fine-tuning", "fine tuning", "finetuning", "lora"],
    "distillation": ["distillation", "distill"],
    "quantization": ["quantization", "quantisation", "quantized"],
    "diffusion": ["diffusion"],
    "world model": ["world model", "world models"],
    "tool use": ["tool use", "tool-use", "function calling"],
    "long context": ["long context", "long-context", "context window"],
    "safety": ["safety", "jailbreak", "red-teaming", "red teaming"],
    "robotics": ["robot", "robotic", "manipulation", "embodied"],
    "code generation": ["code generation", "code-generation", "program synthesis"],
    "uncertainty": ["uncertainty", "calibration", "calibrated"],
    "efficiency": ["efficient inference", "kv cache", "kv-cache", "sparse attention"],
    "multimodal": ["multimodal", "multi-modal", "vision-language"],
    "privacy": ["differential privacy", "federated learning", "privacy-preserving"],
    "graph": ["graph neural", "knowledge graph", "graph-based"],
}


def _pattern(phrase: str) -> str:
    """One alias as a word-bounded regex, tolerant about how it is joined.

    A hyphenated phrase is written three ways in practice (multi-agent, multi
    agent, multiagent) and they are one subject. Everything is bounded, which is
    the whole point: `\\brag\\b` finds rag and not storage.
    """
    parts = re.split(r"[\s\-]+", phrase.strip().lower())
    joined = r"[\s\-]*".join(re.escape(part) for part in parts)
    return rf"\b{joined}\b"


def compile_terms(terms: Optional[Dict[str, List[str]]] = None) -> List[Tuple[str, Any]]:
    terms = terms or DEFAULT_TERMS
    return [(name, re.compile("|".join(_pattern(a) for a in aliases)))
            for name, aliases in terms.items()]


class Tally:
    """Per publication day: how many papers, and how many mentioned each term.

    Counts records rather than keeping them, which is what makes a field-wide
    measurement possible on a laptop. The object is fed one paper at a time and
    never holds more than the running totals.
    """

    def __init__(self, terms: Optional[Dict[str, List[str]]] = None):
        self.matchers = compile_terms(terms)
        self.days: Dict[str, Dict[str, Any]] = {}
        self.seen = 0
        self.dated = 0

    def observe(self, paper: Dict[str, Any]) -> None:
        self.seen += 1
        day = str(paper.get("published") or "")[:10]
        if len(day) != 10:
            return
        self.dated += 1
        bucket = self.days.setdefault(day, {"papers": 0, "terms": {}})
        bucket["papers"] += 1
        text = f"{paper.get('title') or ''} {paper.get('abstract') or ''}".lower()
        for name, pattern in self.matchers:
            if pattern.search(text):
                bucket["terms"][name] = bucket["terms"].get(name, 0) + 1


def load() -> Dict[str, Any]:
    try:
        data = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"days": {}, "terms": sorted(DEFAULT_TERMS), "sets": [], "updated": ""}


def save(data: Dict[str, Any]) -> None:
    try:
        CENSUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CENSUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except OSError:
        pass


def merge(tally: Tally, sets: List[str], covered: Tuple[str, str]) -> Dict[str, Any]:
    """Fold a walk's counts into the stored census, keeping only complete days.

    A day is replaced rather than added to. A second pass over the same day is a
    recount of the same papers, not more of them, and adding would double every
    number without any sign on screen that it had happened.

    And a day outside the walked window is thrown away, which matters more than
    it sounds. Harvest records are dated by last change, so a 90 day walk also
    returns every old paper revised in those 90 days: the first real run came
    back with 1,123 days of counts reaching back to 2002-02-22. Those old days
    are not measurements. They hold only the handful of 2002 papers that
    happened to be revised this summer, and a trend window landing on one would
    compare a full week against a rounding error while looking exactly like a
    real comparison.
    """
    since, until = covered
    data = load()
    for day, bucket in tally.days.items():
        if since and (day < since or (until and day > until)):
            continue
        data["days"][day] = bucket
    data["terms"] = sorted(DEFAULT_TERMS)
    data["sets"] = sorted(set(data.get("sets") or []) | set(sets))
    data["updated"] = date.today().isoformat()
    spans = data.setdefault("spans", [])
    spans.append({"since": covered[0], "until": covered[1],
                  "at": date.today().isoformat(), "days": len(tally.days),
                  "records": tally.seen})
    del spans[:-30]
    save(data)
    return data


def walk(since: str, until: str, sets: Optional[List[str]] = None,
         on_page: Optional[Callable[..., None]] = None,
         interval: float = 3.0) -> Tally:
    """Stream a datestamp range and count it. Nothing is stored per paper.

    `keep` returns False for every record, so the generator counts and yields
    nothing: the point is the tally, and accumulating 140,000 papers to throw
    them away would defeat the exercise.
    """
    from .harvest import harvest
    tally = Tally()
    for oai_set in (sets or ["cs"]):
        def page(pages, seen, _kept, _set=oai_set):
            if on_page:
                on_page(_set, pages, seen, tally.dated)

        def keep(paper, _t=tally):
            _t.observe(paper)
            return False

        for _ in harvest(oai_set, since, until, keep=keep, interval=interval,
                         on_page=page):
            pass                                  # keep() never lets one through
    return tally


def backfill(days: int = 90, sets: Optional[List[str]] = None,
             today: Optional[date] = None,
             on_page: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    """Count the last `days` days of publication, and store the counts."""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    # Today, and no later. Reaching forward would be the right thing to ask for,
    # because a paper published today carries a datestamp a day or three from
    # now, and arXiv refuses it outright: "until date too late". So the newest
    # publication days always come out short and are only completed by a later
    # run, which is what `lag` in compare() exists to hide from a trend line.
    until = today.isoformat()
    tally = walk(since, until, sets=sets, on_page=on_page)
    return merge(tally, sets or ["cs"], (since, today.isoformat()))


# ------------------------------------------------------------------- reading it

def coverage(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data or load()
    days = data.get("days") or {}
    if not days:
        return {"days": 0, "first": "", "last": "", "papers": 0}
    keys = sorted(days)
    return {"days": len(keys), "first": keys[0], "last": keys[-1],
            "papers": sum(b.get("papers", 0) for b in days.values())}


def _sum_window(days: Dict[str, Any], start: date, end: date) -> Dict[str, Any]:
    """Totals across [start, end). Missing days are reported, not assumed zero.

    A day the census never walked and a day arXiv published nothing look
    identical in the totals and mean opposite things, so the count of days
    actually present travels with the window.
    """
    papers = 0
    terms: Dict[str, int] = {}
    present = 0
    cursor = start
    while cursor < end:
        bucket = days.get(cursor.isoformat())
        if bucket:
            present += 1
            papers += bucket.get("papers", 0)
            for name, n in (bucket.get("terms") or {}).items():
                terms[name] = terms.get(name, 0) + n
        cursor += timedelta(days=1)
    return {"papers": papers, "terms": terms, "present": present,
            "asked": (end - start).days,
            "start": start.isoformat(), "end": end.isoformat()}


def compare(window: int = 7, offset: int = 0, against: int = 7,
            against_offset: int = 7, today: Optional[date] = None,
            data: Optional[Dict[str, Any]] = None,
            lag: int = REPORTING_LAG) -> Dict[str, Any]:
    """Two windows of the field, side by side.

    `window`/`offset` is the recent side and `against`/`against_offset` the
    comparison, both counted backwards from today in days. So the default is the
    last week against the week before it, and a month ago is offset 30.

    `lag` drops the newest days entirely. They are not missing, they are not
    announced yet, and including them makes every subject look like it is
    falling off a cliff this week.
    """
    data = data or load()
    days = data.get("days") or {}
    today = today or date.today()
    edge = today - timedelta(days=lag)

    recent_end = edge - timedelta(days=offset)
    recent = _sum_window(days, recent_end - timedelta(days=window), recent_end)
    prior_end = edge - timedelta(days=against_offset)
    prior = _sum_window(days, prior_end - timedelta(days=against), prior_end)

    cov = coverage(data)
    if not recent["papers"] or not prior["papers"]:
        thin = "the recent window" if not recent["papers"] else "the comparison window"
        return {
            "status": "no_data",
            "reason": (
                f"No counted papers in {thin}. The census holds {cov['days']} days "
                f"({cov['first']} to {cov['last']})"
                if cov["days"] else
                "The field census has not been built yet. It streams arXiv's "
                "harvest feed, counts what it sees and keeps only the counts, so "
                "it adds nothing to your library."),
            "coverage": cov, "recent": recent, "prior": prior, "rows": [],
        }

    rows = []
    for name in sorted(set(recent["terms"]) | set(prior["terms"])):
        now = recent["terms"].get(name, 0)
        before = prior["terms"].get(name, 0)
        share_now = now / recent["papers"] * 100
        share_before = before / prior["papers"] * 100
        rows.append({
            "term": name,
            "now": now, "before": before,
            "of_now": recent["papers"], "of_before": prior["papers"],
            "share_now": round(share_now, 2),
            "share_before": round(share_before, 2),
            "change_pts": round(share_now - share_before, 2),
            "change_pct": (round((now - before) / before * 100, 1)
                           if before else None),
            # Per-day rates, because the two windows need not be the same length:
            # comparing a week against a month on raw counts would report every
            # subject as having collapsed.
            "per_day_now": round(now / max(1, recent["present"]), 1),
            "per_day_before": round(before / max(1, prior["present"]), 1),
        })
    rows.sort(key=lambda r: -r["change_pts"])

    return {
        "status": "ok",
        "recent": recent,
        "prior": prior,
        "rows": rows,
        "coverage": cov,
        "lag": lag,
        "scope": (
            f"Counted over every cs paper arXiv published in each window, "
            f"{recent['papers']:,} in the recent one and {prior['papers']:,} in the "
            f"comparison, not over your library. Each figure is the share of that "
            f"window's papers, so a busier week does not read as everything rising."
        ),
        "method": (
            f"Terms are matched on whole words, with the spellings the field "
            f"actually uses grouped together. Substring matching, which the "
            f"library-based view used, found rag in 262 of one day's 1,140 papers; "
            f"the word-bounded count is 17, and the rest are words like leverages "
            f"and storage. The newest {lag} days are left out of both windows "
            f"because arXiv has not announced them yet."
        ),
    }
