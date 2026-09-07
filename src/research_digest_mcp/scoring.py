"""Ranking papers, and showing your work.

No model is involved in deciding what is worth reading. The score is arithmetic
over words you chose, and every score returns the arithmetic alongside it, so a
result can always answer "why is this here and not something else".

The parts:

  phrase match      a multi-word topic appearing verbatim         1.00
  distinctive word  a single topic word that is not boilerplate   0.60
  common word       a topic word that appears in most CS papers   0.20
  breadth bonus     3+ of your topics matched                     +0.30
                    2 of your topics matched                      +0.15
  recency           linear decay across 30 days                   up to +0.20

The 0.20 for common words is a hand-built IDF. Without it, "learning" matches
almost every paper in cs.LG and the ranking collapses to noise.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Words so common in this literature that matching one tells you almost nothing.
BOILERPLATE = {
    "learning", "model", "models", "network", "networks", "neural", "deep",
    "data", "training", "train", "method", "methods", "approach", "framework",
    "system", "systems", "task", "tasks", "performance", "results", "novel",
    "language", "large", "based", "using", "study", "analysis", "problem",
}

PHRASE_HIT = 1.0
DISTINCT_HIT = 0.6
COMMON_HIT = 0.2
BREADTH_3 = 0.30
BREADTH_2 = 0.15
RECENCY_WEIGHT = 0.20
RECENCY_DAYS = 30
BASE_WEIGHT = 0.8

_WORD = re.compile(r"[a-z0-9][a-z0-9\-]*")


def paper_text(paper: Dict[str, Any]) -> str:
    parts = [paper.get("title", ""), paper.get("abstract", "")]
    parts.extend(paper.get("concepts", []) or [])
    return " ".join(str(p) for p in parts).lower()


def score_paper(paper: Dict[str, Any], topics: List[str],
                today: Optional[date] = None) -> Dict[str, Any]:
    """Score one paper and return the score together with its full derivation."""
    text = paper_text(paper)
    words = set(_WORD.findall(text))

    matched: List[Dict[str, Any]] = []
    weighted = 0.0
    for topic in topics:
        topic_l = topic.lower().strip()
        if not topic_l:
            continue
        topic_words = topic_l.split()
        if len(topic_words) > 1:
            if topic_l in text:
                weighted += PHRASE_HIT
                matched.append({"topic": topic, "kind": "phrase", "credit": PHRASE_HIT})
            continue
        if topic_l in words:
            common = topic_l in BOILERPLATE
            credit = COMMON_HIT if common else DISTINCT_HIT
            weighted += credit
            matched.append({
                "topic": topic,
                "kind": "common word" if common else "distinctive word",
                "credit": credit,
            })

    base = min(weighted / max(len(topics), 1), 1.0) * BASE_WEIGHT

    count = len(matched)
    if count >= 3:
        breadth = BREADTH_3
    elif count == 2:
        breadth = BREADTH_2
    else:
        breadth = 0.0

    recency, age_days = 0.0, None
    published = paper.get("published")
    if published:
        try:
            pub = datetime.strptime(str(published)[:10], "%Y-%m-%d").date()
            age_days = ((today or date.today()) - pub).days
            if age_days >= 0:
                recency = max(0.0, 1.0 - age_days / RECENCY_DAYS) * RECENCY_WEIGHT
        except ValueError:
            pass

    total = min(base + breadth + recency, 1.0)
    return {
        "score": round(total, 4),
        "why": {
            "matched": matched,
            "components": {
                "base": round(base, 4),
                "breadth_bonus": round(breadth, 4),
                "recency": round(recency, 4),
            },
            "topics_matched": count,
            "topics_considered": len(topics),
            "age_days": age_days,
            "capped": base + breadth + recency > 1.0,
        },
    }


def explain_sentence(why: Dict[str, Any]) -> str:
    """The derivation as one plain-English line, for the UI and the MCP tools."""
    matched = why.get("matched", [])
    if not matched:
        return "No topic matched. It ranks on recency alone."
    names = ", ".join(f"{m['topic']} ({m['kind']})" for m in matched[:4])
    comp = why.get("components", {})
    bits = [f"matched {names}"]
    if comp.get("breadth_bonus"):
        bits.append(f"+{comp['breadth_bonus']:.2f} for spanning {why['topics_matched']} topics")
    if comp.get("recency"):
        bits.append(f"+{comp['recency']:.2f} for being {why.get('age_days')} days old")
    return "; ".join(bits) + "."


def rank_all(papers: List[Dict[str, Any]], topics: List[str]) -> List[Dict[str, Any]]:
    """Score every paper, keep the ones that matched at all, best first.

    Returns the complete match set. Callers slice it for display, but report
    counts from this list, so "matched" means matched and not "shown".
    """
    scored = []
    for paper in papers:
        result = score_paper(paper, topics)
        if not result["why"]["matched"]:
            continue
        record = dict(paper)
        record["score"] = result["score"]
        record["why"] = result["why"]
        record["why_text"] = explain_sentence(result["why"])
        scored.append(record)
    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored


def rank(papers: List[Dict[str, Any]], topics: List[str],
         limit: int = 20) -> List[Dict[str, Any]]:
    """The top `limit` matches. Use rank_all when you need the true match count."""
    return rank_all(papers, topics)[:limit]


CONCEPT_PATTERNS = [
    "multi-agent", "agentic", "reinforcement learning", "chain-of-thought",
    "retrieval-augmented", "fine-tuning", "distillation", "quantization",
    "interpretability", "alignment", "hallucination", "benchmark",
    "evaluation", "long-horizon", "tool use", "planning", "reasoning",
    "diffusion", "transformer", "attention", "embedding", "world model",
    "robustness", "uncertainty", "calibration", "reward model",
]


def extract_concepts(paper: Dict[str, Any], limit: int = 6) -> List[str]:
    """Concept tags, from a known vocabulary first, then distinctive title words."""
    text = paper_text(paper)
    found = [p for p in CONCEPT_PATTERNS if p in text][:limit]
    if len(found) < 3:
        for word in _WORD.findall(str(paper.get("title", "")).lower()):
            if len(word) > 4 and word not in BOILERPLATE and word not in found:
                found.append(word)
            if len(found) >= limit:
                break
    return found[:limit]
