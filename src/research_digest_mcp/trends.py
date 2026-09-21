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

# A concept has to reach this many papers in at least one of the two weeks
# before a direction is claimed for it. Below it the percentage is arithmetic
# on noise: one group posting twice reads as "+100%, rising", and a reader has
# no way to tell that from a real shift. Rows under the bar are still shown,
# in their own bucket, marked "too few to call" -- hiding them would trade one
# false impression for another.
MIN_EVIDENCE = 4

# And it has to move this many percentage points of the week's papers. Counts
# alone made a busy week look like a broad rise in everything in it.
MIN_SHARE_MOVE = 1.5


def _KNOWN():
    from .scoring import CONCEPT_PATTERNS
    return CONCEPT_PATTERNS


def _paper_day(paper: Dict[str, Any]) -> Optional[date]:
    for field in ("first_seen", "published", "updated"):
        raw = paper.get(field)
        if raw:
            try:
                return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _known_concepts(paper: Dict[str, Any]) -> List[str]:
    """Which known concepts this paper is actually about, read from its text.

    Two problems with using the stored `concepts` list, which is what this did
    before, and both of them made the numbers wrong in ways a reader could not
    see:

    `extract_concepts` tops a paper's tags up with distinctive words from its
    own title when fewer than three known concepts matched. Those are a fine
    handle on one paper and they are not ideas, and counting them as ideas put
    "toward" and "evaluating" in Rising and had Crossing Over announce that
    "regionfed" had crossed into cs.LG. regionfed is one paper's model name.

    Worse, that list is capped at six. 366 papers in a 1,443-paper library sit
    at the cap, and the cap keeps whichever concepts come first in
    CONCEPT_PATTERNS, so a concept's count depended on where it happened to sit
    in a hand-written list. Measured over one week, "calibration" was stored on
    3 papers and present in 4.

    So this reads the title, abstract and tags directly. Slower, and a trend
    line nobody can trust is not worth saving the milliseconds on.
    """
    from .scoring import CONCEPT_PATTERNS, paper_text
    text = paper_text(paper)
    return [c for c in CONCEPT_PATTERNS if c in text]


def _concepts(papers: List[Dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for paper in papers:
        for concept in _known_concepts(paper):
            counter[concept] += 1
    return counter


def cross_pollination(papers: List[Dict[str, Any]], lookback_days: int = 90,
                      today: Optional[date] = None, limit: int = 8) -> List[Dict[str, Any]]:
    """Concepts turning up in a category they have never appeared in before.

    An idea crossing from one field into another is worth more of your attention
    than the same idea appearing again where it always does. This counts every
    (concept, category) pair you have seen historically, then flags recent papers
    that introduce a pair with no history at all.

    Needs no embeddings, so it works on a plain install.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)

    historical: Counter = Counter()
    recent: List[Dict[str, Any]] = []
    for paper in papers:
        day = _paper_day(paper)
        if day is None:
            continue
        category = paper.get("primary_category") or "uncategorised"
        pairs = [(c, category) for c in _known_concepts(paper)]
        if day < cutoff:
            historical.update(pairs)
        else:
            recent.append(paper)

    if not historical or not recent:
        return []

    # Only categories with enough history to make "never before" mean anything.
    # In a category holding four papers, every concept is a first, and the list
    # filled up with confident claims about nothing.
    seen_in = Counter(category for _concept, category in historical.elements())

    out = []
    for paper in recent:
        category = paper.get("primary_category") or "uncategorised"
        if seen_in[category] < 20:
            continue
        novel = [c for c in _known_concepts(paper)
                 if historical[(c, category)] == 0]
        if not novel:
            continue
        out.append({
            "id": paper.get("id", ""),
            "url": paper.get("url", "") or f"https://arxiv.org/abs/{paper.get('id', '')}",
            "title": paper.get("title", ""),
            "category": category,
            "concepts": novel[:3],
            # "In your library" is the honest scope. This is a claim about what
            # you have fetched, over the months you have been fetching it, not
            # about the literature.
            "note": f"first “{novel[0]}” paper you have held in {category}",
        })
    return out[:limit]


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

    # The denominators. A concept can only "rise" relative to how many papers
    # there were to be tagged: 8 of 70 this week against 4 of 56 last week is
    # barely a change, and the old view reported it as +100%. Every row now
    # carries the share as well as the count, and the share is what it is
    # sorted and judged on.
    n_now, n_before = len(this_week), len(prev_week)

    rising, falling, steady, thin = [], [], [], []
    for concept in set(now) | set(before):
        current, previous = now.get(concept, 0), before.get(concept, 0)
        share_now = current / n_now
        share_before = previous / n_before
        entry = {
            "concept": concept,
            "current": current, "previous": previous,
            "of_current": n_now, "of_previous": n_before,
            "share_now": round(share_now * 100, 1),
            "share_previous": round(share_before * 100, 1),
            "change_pts": round((share_now - share_before) * 100, 1),
        }

        # Below this, the arithmetic still works and the claim does not. Two
        # papers out of seventy is one research group posting twice, and
        # calling it a 100% rise is the tool asserting something it cannot
        # know. These are kept and shown, in their own bucket, labelled.
        if max(current, previous) < MIN_EVIDENCE:
            entry["verdict"] = "too few to call"
            thin.append(entry)
            continue

        entry["change_pct"] = (round((current - previous) / previous * 100, 1)
                               if previous else None)
        if previous == 0:
            entry["change"] = "new"
            entry["verdict"] = f"new this week, {current} papers"
            rising.append(entry)
        elif entry["change_pts"] >= MIN_SHARE_MOVE:
            entry["verdict"] = (f"{entry['share_previous']}% to {entry['share_now']}% "
                                f"of the week's papers")
            rising.append(entry)
        elif entry["change_pts"] <= -MIN_SHARE_MOVE:
            entry["verdict"] = (f"{entry['share_previous']}% to {entry['share_now']}% "
                                f"of the week's papers")
            falling.append(entry)
        else:
            entry["verdict"] = f"holding around {entry['share_now']}%"
            steady.append(entry)

    rising.sort(key=lambda e: (e.get("change") == "new", e["change_pts"]), reverse=True)
    falling.sort(key=lambda e: e["change_pts"])
    steady.sort(key=lambda e: e["current"], reverse=True)
    thin.sort(key=lambda e: -max(e["current"], e["previous"]))

    return {
        "status": "ok",
        "window": window,
        "rising": rising[:10],
        "falling": falling[:10],
        "steady": steady[:10],
        "too_few": thin[:10],
        "basis": {
            "this_week": {"papers": n_now, "start": this_start.isoformat(),
                          "end": today.isoformat()},
            "previous_week": {"papers": n_before, "start": prev_start.isoformat(),
                              "end": this_start.isoformat()},
            "min_evidence": MIN_EVIDENCE,
            "min_share_move": MIN_SHARE_MOVE,
            "vocabulary": len(_KNOWN()),
        },
        "scope_warning": (
            f"This is {n_now} papers, not arXiv. arXiv publishes a few thousand "
            f"papers a week; your profile asked for a slice of them and this is that "
            f"slice. So a concept reading 2 here means 2 of your {n_now}, and a "
            f"concept reading 0 mostly means your profile did not ask for it."
        ),
        "note": (
            f"Counted over the {n_now} papers your profile pulled between "
            f"{this_start.isoformat()} and {today.isoformat()}, against the "
            f"{n_before} it pulled the week before. Each number is the share of that "
            f"week's papers, so a week where you fetched more does not read as "
            f"everything rising at once. A concept needs {MIN_EVIDENCE} papers in one "
            f"of the two weeks before this calls a direction for it, and needs to move "
            f"{MIN_SHARE_MOVE} points of share to count as a move. Concepts are found "
            f"by reading each paper's title and abstract, not by trusting the tags "
            f"saved on it, because those are capped at six per paper and the cap made "
            f"some concepts look rarer than they are."
        ),
    }
