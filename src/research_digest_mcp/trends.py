"""What is rising and falling in your own feed.

The guard in here matters more than the arithmetic. Upstream compared this week
against last week with no check that this week had any papers in it, so the week
after a run stopped, every concept read as "-100%, falling". That is not a
decline, it is an outage, and reporting one as the other is the whole failure.

So: if a window is empty, or the two windows are not adjacent in the run
history, this returns status "no_data" and no percentages at all.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

WINDOW_DAYS = 7
MIN_PAPERS = 5


def _paper_day(paper: Dict[str, Any]) -> Optional[date]:
    for field in ("first_seen", "published", "updated"):
        raw = paper.get(field)
        if raw:
            try:
                return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _concepts(papers: List[Dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for paper in papers:
        for concept in paper.get("concepts", []) or []:
            counter[str(concept).lower()] += 1
    return counter


def compute_trends(papers: List[Dict[str, Any]],
                   today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    this_start = today - timedelta(days=WINDOW_DAYS)
    prev_start = today - timedelta(days=WINDOW_DAYS * 2)

    this_week, prev_week = [], []
    for paper in papers:
        day = _paper_day(paper)
        if day is None:
            continue
        if this_start < day <= today:
            this_week.append(paper)
        elif prev_start < day <= this_start:
            prev_week.append(paper)

    window = {
        "this_week": {"start": this_start.isoformat(), "end": today.isoformat(),
                      "papers": len(this_week)},
        "previous_week": {"start": prev_start.isoformat(), "end": this_start.isoformat(),
                          "papers": len(prev_week)},
    }

    if len(this_week) < MIN_PAPERS or len(prev_week) < MIN_PAPERS:
        thin = "this week" if len(this_week) < MIN_PAPERS else "the previous week"
        return {
            "status": "no_data",
            "reason": (
                f"Not enough papers in {thin} to compare "
                f"({len(this_week)} this week, {len(prev_week)} previous, "
                f"{MIN_PAPERS} needed). This measures how recently you fetched, "
                f"not what the field is doing. Run 'research-digest fetch' first."
            ),
            "window": window,
            "rising": [], "falling": [], "steady": [],
        }

    now, before = _concepts(this_week), _concepts(prev_week)
    rising, falling, steady = [], [], []
    for concept in set(now) | set(before):
        current, previous = now.get(concept, 0), before.get(concept, 0)
        if current + previous < 3:
            continue
        entry = {"concept": concept, "current": current, "previous": previous}
        if previous == 0:
            entry["change"] = "new"
            rising.append(entry)
        else:
            pct = (current - previous) / previous * 100
            entry["change_pct"] = round(pct, 1)
            if pct >= 25:
                rising.append(entry)
            elif pct <= -25:
                falling.append(entry)
            else:
                steady.append(entry)

    rising.sort(key=lambda e: (e.get("change") == "new", e.get("change_pct", 0)), reverse=True)
    falling.sort(key=lambda e: e.get("change_pct", 0))
    steady.sort(key=lambda e: e["current"], reverse=True)

    return {
        "status": "ok",
        "window": window,
        "rising": rising[:10],
        "falling": falling[:10],
        "steady": steady[:10],
        "note": (
            "Counts are concept tags on papers you fetched, so they track your "
            "configured categories, not arXiv as a whole."
        ),
    }
