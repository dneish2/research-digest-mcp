"""The shape of the library, as groups rather than as a list.

A ranked list answers "what is the best match for this". It cannot answer
"what is in here", "what sits next to what", or "what am I accumulating without
noticing", and those are the questions you have when you are trying to get your
bearings rather than trying to find one paper.

So: group the library by which concepts appear together, and lay the groups out
by how much they overlap. Two rules keep this honest.

Nothing here invents a relationship. A link between two concepts means a
specific number of papers contain both, that number is shown, and clicking it
gives you those papers. There is no embedding, no projection, no model, and
therefore nothing that can put two things near each other for a reason nobody
can reconstruct. A map you cannot audit is a map you have to believe.

And it costs nothing. Pure counting over text already in memory, no
dependencies, no build step, no API call. It runs on the same 1,400-paper
library in well under a second, which is what lets it be a tab rather than a
feature you schedule.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# Below this a "cluster" is a coincidence. Two papers sharing a word is not a
# theme in your reading, and a map made of those is a map of noise.
MIN_CLUSTER = 4
MIN_LINK = 3


def _concepts_of(paper: Dict[str, Any]) -> List[str]:
    from .scoring import CONCEPT_PATTERNS, paper_text
    text = paper_text(paper)
    return [c for c in CONCEPT_PATTERNS if c in text]


def build(papers: List[Dict[str, Any]], saved: Optional[Dict[str, Any]] = None,
          limit: int = 22) -> Dict[str, Any]:
    """Concepts as nodes, co-occurrence as links, laid out on a circle.

    The layout is deterministic and explainable: strongest concepts first,
    placed around a ring, then nudged toward whatever they overlap most. A
    force simulation would look better and would move every time you opened it,
    which is the opposite of what a map is for.
    """
    saved = saved or {}
    counts: Dict[str, int] = {}
    pairs: Dict[Tuple[str, str], int] = {}
    by_concept: Dict[str, List[str]] = {}
    saved_hits: Dict[str, int] = {}
    unread_hits: Dict[str, int] = {}
    read = set()

    for paper in papers:
        found = _concepts_of(paper)
        pid = paper.get("id", "")
        for concept in found:
            counts[concept] = counts.get(concept, 0) + 1
            by_concept.setdefault(concept, []).append(pid)
            if pid in saved:
                saved_hits[concept] = saved_hits.get(concept, 0) + 1
        for i, left in enumerate(found):
            for right in found[i + 1:]:
                key = (left, right) if left < right else (right, left)
                pairs[key] = pairs.get(key, 0) + 1

    top = [c for c, n in sorted(counts.items(), key=lambda kv: -kv[1])
           if n >= MIN_CLUSTER][:limit]
    if not top:
        return {"status": "empty",
                "message": ("Not enough papers yet to see a shape. Fetch a few "
                            "hundred and this fills in."),
                "nodes": [], "links": []}

    rank = {c: i for i, c in enumerate(top)}
    links = [
        {"source": a, "target": b, "papers": n,
         # Overlap as a share of the smaller of the two, so a link between a
         # huge concept and a small one is measured against the small one. Raw
         # counts made everything look like it connects to "evaluation".
         "strength": round(n / max(1, min(counts[a], counts[b])), 3)}
        for (a, b), n in pairs.items()
        if a in rank and b in rank and n >= MIN_LINK
    ]
    links.sort(key=lambda link: -link["strength"])
    links = links[:70]

    # Ring layout. Position is by overall size so the biggest ideas sit apart
    # from each other, then each node slides toward its strongest partner. It
    # is the same every time you open it, which is what makes it a place.
    positions: Dict[str, Tuple[float, float]] = {}
    n_top = len(top)
    for i, concept in enumerate(top):
        angle = (2 * math.pi * i) / n_top
        # Bigger concepts sit further in, so the middle of the map reads as the
        # middle of your reading.
        radius = 1.0 - 0.45 * (counts[concept] / counts[top[0]])
        positions[concept] = (math.cos(angle) * radius, math.sin(angle) * radius)

    for _ in range(3):
        moved: Dict[str, Tuple[float, float]] = {}
        for concept in top:
            x, y = positions[concept]
            partners = [(link["target"] if link["source"] == concept else link["source"],
                         link["strength"])
                        for link in links
                        if concept in (link["source"], link["target"])]
            if partners:
                wx = sum(positions[p][0] * s for p, s in partners)
                wy = sum(positions[p][1] * s for p, s in partners)
                total = sum(s for _p, s in partners) or 1
                x = x * 0.75 + (wx / total) * 0.25
                y = y * 0.75 + (wy / total) * 0.25
            moved[concept] = (x, y)
        positions = moved

    nodes = []
    for concept in top:
        x, y = positions[concept]
        partners = sorted(
            ((link["target"] if link["source"] == concept else link["source"],
              link["papers"])
             for link in links if concept in (link["source"], link["target"])),
            key=lambda pair: -pair[1])[:4]
        nodes.append({
            "concept": concept,
            "papers": counts[concept],
            "saved": saved_hits.get(concept, 0),
            "share": round(counts[concept] / len(papers) * 100, 1),
            "x": round(x, 4), "y": round(y, 4),
            "with": [{"concept": c, "papers": n} for c, n in partners],
        })

    return {
        "status": "ok",
        "total": len(papers),
        "nodes": nodes,
        "links": links,
        "how": (f"Each circle is a concept and its size is how many of your "
                f"{len(papers)} papers mention it. A line means papers mention both, "
                f"and it is drawn only when at least {MIN_LINK} papers do. Nothing is "
                f"estimated: every number here is a count you can get back to by "
                f"clicking it."),
    }


def detail(papers: List[Dict[str, Any]], concept: str, other: str = "",
           saved: Optional[Dict[str, Any]] = None,
           read: Optional[Dict[str, Any]] = None,
           limit: int = 12) -> Dict[str, Any]:
    """Everything behind one circle, or behind one line between two.

    The map could be clicked before this existed and the click ran a search,
    which threw the reader onto another screen with no statement of what had been
    clicked or why those results. Reported as: "when I click it, it does show me
    some arxiv or maybe the section of what I clicked, but that interaction is
    not intuitive or transparent enough, so it's hard to know what's going on."

    A click now has something to land on: the count, the share, what it sits
    beside and how often, and the papers themselves. Every number here is a count
    over the library, so the panel can say where each one came from.
    """
    from .scoring import about_sentence, paper_text
    saved = saved or {}
    read = read or {}
    target = concept.lower()
    partner = (other or "").lower()

    matching: List[Dict[str, Any]] = []
    with_counts: Dict[str, int] = {}
    total = 0
    for paper in papers:
        found = _concepts_of(paper)
        if target not in found:
            continue
        total += 1
        for neighbour in found:
            if neighbour != target:
                with_counts[neighbour] = with_counts.get(neighbour, 0) + 1
        if partner and partner not in found:
            continue
        matching.append(paper)

    # Newest first. A concept's papers are a reading list, and for a reading list
    # recency beats a relevance score against a term every one of them contains.
    matching.sort(key=lambda p: str(p.get("published") or ""), reverse=True)

    rows = []
    for paper in matching[:limit]:
        pid = paper.get("id", "")
        rows.append({
            "id": pid,
            "title": paper.get("title", ""),
            "url": paper.get("url", "") or f"https://arxiv.org/abs/{pid}",
            "published": paper.get("published", ""),
            "category": paper.get("primary_category", ""),
            "about": about_sentence(paper),
            "concepts": [c for c in _concepts_of(paper)][:6],
            "saved": pid in saved,
            "read": pid in read,
        })

    kept = sum(1 for p in matching if p.get("id") in saved)
    partners = sorted(with_counts.items(), key=lambda kv: -kv[1])[:8]
    return {
        "status": "ok",
        "concept": target,
        "pair": partner,
        "papers": len(matching),
        "concept_papers": total,
        "library": len(papers),
        "share": round(len(matching) / max(1, len(papers)) * 100, 2),
        "saved": kept,
        "with": [{"concept": c, "papers": n} for c, n in partners],
        "results": rows,
        "more": max(0, len(matching) - len(rows)),
        "how": (
            f"{len(matching)} of your {len(papers)} papers mention both "
            f"“{target}” and “{partner}”. Counted by reading each paper's title, "
            f"abstract and tags, which is the same count the line's thickness is "
            f"drawn from."
            if partner else
            f"{len(matching)} of your {len(papers)} papers mention “{target}” "
            f"somewhere in the title, abstract or tags. That is the count the "
            f"circle's size is drawn from."
        ),
    }


def bridges(papers: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """Papers sitting between two parts of your library that rarely meet.

    This is the part worth having. A search finds papers about what you already
    know to ask for, and the ranked list puts the most typical ones on top,
    which means the tool is at its worst exactly where reading is most useful:
    a paper joining two things you care about separately and had not connected.

    Scored by how rare the pairing is, not by how good the paper is. A paper
    covering two concepts that 200 other papers also cover together is a normal
    paper. One covering two concepts that only two other papers pair is worth a
    look even if it is otherwise unremarkable.
    """
    counts: Dict[str, int] = {}
    pairs: Dict[Tuple[str, str], int] = {}
    per_paper: List[Tuple[Dict[str, Any], List[str]]] = []

    for paper in papers:
        found = _concepts_of(paper)
        per_paper.append((paper, found))
        for concept in found:
            counts[concept] = counts.get(concept, 0) + 1
        for i, left in enumerate(found):
            for right in found[i + 1:]:
                key = (left, right) if left < right else (right, left)
                pairs[key] = pairs.get(key, 0) + 1

    if not pairs:
        return []

    out = []
    for paper, found in per_paper:
        best = None
        for i, left in enumerate(found):
            for right in found[i + 1:]:
                key = (left, right) if left < right else (right, left)
                seen = pairs[key]
                # Both sides have to be substantial in your library, and the
                # pairing has to be rare. A concept you hold three papers on is
                # not one of "two things you care about".
                if counts[left] < 15 or counts[right] < 15 or seen > 4:
                    continue
                rarity = (counts[left] * counts[right]) / max(1, seen)
                if best is None or rarity > best[0]:
                    best = (rarity, left, right, seen)
        if best:
            _rarity, left, right, seen = best
            out.append({
                "id": paper.get("id", ""),
                "title": paper.get("title", ""),
                "url": paper.get("url", "") or f"https://arxiv.org/abs/{paper.get('id','')}",
                "published": paper.get("published", ""),
                "pair": [left, right],
                "note": (f"You hold {counts[left]} papers on {left} and "
                         f"{counts[right]} on {right}, and only {seen} that do both."),
                "rarity": round(_rarity, 1),
            })

    out.sort(key=lambda row: -row["rarity"])
    return out[:limit]
