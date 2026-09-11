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

# Plain English function words. BOILERPLATE is deliberately about this field's
# jargon, not general English, so on its own "of" scores as a *distinctive*
# word (0.6 credit) — it is not in BOILERPLATE and it is not common ML
# vocabulary, it is just common. A query like "chain of thought faithfulness"
# then matches almost every paper on "of" alone, which a preliminary eval
# caught outright: that exact query scored zero precision because papers with
# nothing to do with chain-of-thought outranked the ones that were. These are
# dropped from matching entirely (not merely discounted), the same way a
# search engine ignores "of" rather than scoring it cheaply.
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but",
    "is", "are", "was", "were", "be", "been", "being", "with", "from", "by",
    "as", "it", "its", "this", "that", "these", "those", "into", "over",
    "about", "than", "then", "so", "not", "no", "do", "does", "did", "can",
    "if", "we", "you", "your", "our",
}


def _significant(terms: List[str]) -> List[str]:
    """Query/topic words worth matching against — lowercased, deduped in
    order, with plain English function words dropped."""
    seen, out = set(), []
    for term in terms:
        t = term.lower().strip()
        if not t or t in STOPWORDS or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out

PHRASE_HIT = 1.0
DISTINCT_HIT = 0.6
COMMON_HIT = 0.2
BREADTH_3 = 0.30
BREADTH_2 = 0.15
RECENCY_WEIGHT = 0.20
RECENCY_DAYS = 30
BASE_WEIGHT = 0.8

_WORD = re.compile(r"[a-z0-9][a-z0-9\-]*")

# Query-search tuning (see score_query). Deliberately smaller than the profile
# scorer's breadth bonus: an ad-hoc query should not get the same weight as a
# standing interest, and these are nudges on top of coverage, not the main signal.
QUERY_PHRASE_BONUS = 0.35
QUERY_TF_STEP = 0.02
QUERY_TF_CAP = 0.15

# Sentence-initial phrases that, in an arXiv abstract, almost always introduce
# the paper's own contribution rather than background or motivation. Checked in
# order against each sentence; used by about_sentence().
_ABOUT_CUES = (
    "we propose", "we present", "we introduce", "we show", "we study",
    "we demonstrate", "we develop", "we describe", "we design", "we build",
    "this paper", "this work", "in this paper", "in this work",
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_ABOUT_MAX_CHARS = 220


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
        if not topic_l or topic_l in STOPWORDS:
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


def _match_set(text: str) -> set:
    """Every form a query term can legitimately match in `text`.

    _WORD keeps hyphenated compounds whole, so "chain-of-thought" is a single
    token and a search for "chain of thought" matched no part of it — while
    physics papers about an oscillator chain matched on "chain" and ranked
    above the papers actually about chain-of-thought reasoning. Compounds now
    also contribute their parts, so a query reaches inside a hyphenated term
    without the searcher having to guess the author's hyphenation.
    """
    words = set(_WORD.findall(text))
    for word in [w for w in words if "-" in w]:
        words.update(part for part in word.split("-") if part)
    return words


def score_query(paper: Dict[str, Any], terms: List[str],
                 today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Score a paper against free-text search terms.

    This is score_paper's sibling for ad-hoc queries rather than a standing
    interest profile, and it ranks differently on purpose. score_paper's breadth
    bonus assumes matching 2-3 of a dozen-odd standing topics is a meaningful
    signal about the paper; fed a 2-3 word search query instead, every result
    would get the same full bonus regardless of relevance — a two-word query for
    "agentic evaluation" could be topped by a paper that only mentions
    "evaluation" in passing. So here: papers rank by what fraction of the query
    they cover, weighted by how distinctive each matched term is, and there is a
    small bonus for the exact phrase and for the terms appearing more than once
    (a paper centrally about the topic, not a glancing mention). A paper needs
    at least one term to appear at all; matching more of them ranks it higher.

    Returns None (not a zero score) when nothing matched, so callers can tell
    "did not match this query" apart from "matched, but weakly".
    """
    raw = [t.lower().strip() for t in terms if t.strip()]
    terms = _significant(terms)
    if not terms:
        return None
    text = paper_text(paper)
    words = _match_set(text)

    matched: List[Dict[str, Any]] = []
    weighted = 0.0
    occurrences = 0
    for term in terms:
        if term not in words:
            continue
        common = term in BOILERPLATE
        credit = COMMON_HIT if common else DISTINCT_HIT
        weighted += credit
        matched.append({
            "topic": term,
            "kind": "common word" if common else "distinctive word",
            "credit": credit,
        })
        occurrences += text.count(term)

    if not matched:
        return None

    # Credit-weighted, like score_paper — not a flat "matched N of M" fraction.
    # Two terms both present is not automatically full marks: a paper matching
    # two common words in passing should not tie a paper where the terms are
    # distinctive and central. This is what keeps a glancing off-topic mention
    # of your search terms from tying the paper actually about them.
    coverage = len(matched) / len(terms)
    base = min(weighted / len(terms), 1.0) * BASE_WEIGHT

    # Built from the raw query, not the stopword-filtered terms. Filtering first
    # was a quiet bug: "chain of thought" became the phrase "chain thought",
    # which appears in no paper, so every query with a function word inside it
    # silently lost its exact-phrase bonus. On the 26-phrase regression set that
    # cost precision@5 on 9 of the 11 phrases containing one, and on none of the
    # 15 without — 0.708 to 0.531 overall. Stopwords still do not earn match
    # credit of their own; they just no longer break the phrase they sit in.
    phrase_bonus = 0.0
    if len(raw) > 1:
        for phrase in (" ".join(raw), "-".join(raw)):
            if phrase in text:
                phrase_bonus = QUERY_PHRASE_BONUS
                matched.append({"topic": phrase, "kind": "phrase", "credit": phrase_bonus})
                break

    extra_mentions = max(occurrences - len(matched), 0)
    tf_bonus = min(extra_mentions * QUERY_TF_STEP, QUERY_TF_CAP)

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

    total = min(base + phrase_bonus + tf_bonus + recency, 1.0)
    return {
        "score": round(total, 4),
        "why": {
            "matched": matched,
            "components": {
                "base": round(base, 4),
                "phrase_bonus": round(phrase_bonus, 4),
                "tf_bonus": round(tf_bonus, 4),
                "recency": round(recency, 4),
            },
            "terms_matched": len([m for m in matched if m["kind"] != "phrase"]),
            "terms_considered": len(terms),
            "coverage": round(coverage, 2),
            "age_days": age_days,
            "capped": base + phrase_bonus + tf_bonus + recency > 1.0,
        },
    }


def explain_sentence(why: Dict[str, Any]) -> str:
    """The derivation as one plain-English line, for the UI and the MCP tools.

    Reads either score_paper's component shape (base/breadth_bonus/recency) or
    score_query's (base/phrase_bonus/tf_bonus/recency) — whichever bonuses are
    present get a clause, in a fixed order, so the sentence reads the same way
    regardless of which scorer produced it.
    """
    matched = why.get("matched", [])
    if not matched:
        return "No topic matched. It ranks on recency alone."
    names = ", ".join(f"{m['topic']} ({m['kind']})" for m in matched[:4])
    comp = why.get("components", {})
    bits = [f"matched {names}"]
    if comp.get("breadth_bonus"):
        bits.append(f"+{comp['breadth_bonus']:.2f} for spanning {why.get('topics_matched')} topics")
    if comp.get("phrase_bonus"):
        bits.append(f"+{comp['phrase_bonus']:.2f} for matching the exact phrase")
    if comp.get("tf_bonus"):
        bits.append(f"+{comp['tf_bonus']:.2f} for how often the terms appear")
    if comp.get("recency"):
        bits.append(f"+{comp['recency']:.2f} for being {why.get('age_days')} days old")
    return "; ".join(bits) + "."


def _clip(sentence: str, max_chars: int) -> str:
    sentence = " ".join(sentence.split())
    if len(sentence) <= max_chars:
        return sentence
    cut = sentence[:max_chars].rsplit(" ", 1)[0].rstrip(",;: ")
    return cut + "…"


def about_sentence(paper: Dict[str, Any], max_chars: int = _ABOUT_MAX_CHARS) -> str:
    """One plain sentence describing what the paper actually did — pure string
    work, no model. Most arXiv abstracts contain a sentence that announces the
    contribution ("We propose...", "This paper presents..."); that sentence is a
    better lead than the first sentence of the abstract, which is usually
    background or motivation. Falls back to the first sentence, then to a clip
    of the raw abstract, so this never returns empty for a paper that has one.
    """
    abstract = (paper.get("abstract") or "").strip()
    if not abstract:
        return ""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(abstract) if s.strip()]
    if not sentences:
        return _clip(abstract, max_chars)
    for sentence in sentences:
        low = sentence.lower()
        if any(low.startswith(cue) for cue in _ABOUT_CUES):
            return _clip(sentence, max_chars)
    return _clip(sentences[0], max_chars)


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


def rank_all_query(papers: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
    """Free-text search over the library. See score_query for how this differs
    from rank_all, which is calibrated for the standing topic profile."""
    scored = []
    for paper in papers:
        result = score_query(paper, terms)
        if result is None:
            continue
        record = dict(paper)
        record["score"] = result["score"]
        record["why"] = result["why"]
        record["why_text"] = explain_sentence(result["why"])
        scored.append(record)
    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored


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
