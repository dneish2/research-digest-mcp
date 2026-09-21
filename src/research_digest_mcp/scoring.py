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
    # Field labels, not subjects. A search for "finance ai" was led by a paper
    # on healthcare workforce readiness, because "ai" carried the same weight
    # as "finance" and appears in roughly half this corpus. Matching one of
    # these says only that the paper is in computer science.
    "ai", "llm", "llms", "ml", "nlp", "artificial", "intelligence", "machine",
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


def term_groups(terms: List[str]) -> List[Dict[str, Any]]:
    """Each query word as a unit that has to be satisfied as a whole.

    A hyphenated word is one idea, not several. Opening "nvidia-labs" into two
    independent terms meant a paper containing only "labs" counted as a third
    of the query and came back as a result: a search for NVIDIA-labs returned a
    paper on animal welfare in travel agents, because it says "labs" somewhere.

    So a compound matches if the paper has it written that way, OR has all of
    its parts. Either is the idea; one part is not. The searcher still does not
    have to guess the author's hyphenation, which is what the splitting was for
    in the first place.
    """
    groups: List[Dict[str, Any]] = []
    seen = set()
    for term in terms:
        text = term.lower().strip().strip(".,;:!?")
        if not text or text in STOPWORDS or text in seen:
            continue
        seen.add(text)
        parts = [p for p in text.split("-") if p and p not in STOPWORDS] \
            if "-" in text else []
        groups.append({"term": text, "parts": parts if len(parts) > 1 else []})
    return groups


def group_matches(group: Dict[str, Any], words: set) -> bool:
    """Is this query word satisfied by the paper's vocabulary?"""
    if group["term"] in words:
        return True
    return bool(group["parts"]) and all(p in words for p in group["parts"])


def _significant(terms: List[str], split_compounds: bool = False) -> List[str]:
    """Query/topic words worth matching against — lowercased, deduped in
    order, with plain English function words dropped.

    A hyphenated term is split into its parts as well as kept whole, because
    the searcher should not have to guess the author's hyphenation. _match_set
    already opens compounds up on the *text* side, so "chain of thought"
    reached a paper writing "chain-of-thought"; the reverse did not hold, and
    a search for "LLM-as-judge" matched only the papers that spell it with
    both hyphens — 8 of a library that holds well over a hundred on the
    subject. Both sides are now normalised the same way.
    """
    seen, out = set(), []
    for term in terms:
        t = term.lower().strip().strip(".,;:!?")
        if not t or t in STOPWORDS:
            continue
        # Splitting is for matching, not for display: a suggestion chip reading
        # "multi-agent, multi, agent" is three chips for one idea.
        pieces = [t] + t.split("-") if (split_compounds and "-" in t) else [t]
        for piece in pieces:
            if not piece or piece in STOPWORDS or piece in seen:
                continue
            seen.add(piece)
            out.append(piece)
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
    """Everything a search is allowed to look at.

    Authors were not in here, which meant author search did not exist: an
    author with five papers in the library returned one result, and that one
    was a coincidental word match. The names were stored on 1,182 of 1,443
    papers the whole time and simply never read.

    Affiliation, comment and journal_ref are here for the same reason and were
    not even being parsed out of the feed. Affiliation is the only field that
    can answer "papers out of NVIDIA"; comment is where "Accepted at NeurIPS
    2026" lives, which is a thing people genuinely want to filter on.
    """
    parts = [paper.get("title", ""), paper.get("abstract", "")]
    parts.extend(paper.get("concepts", []) or [])
    parts.extend(paper.get("authors", []) or [])
    parts.extend(paper.get("affiliations", []) or [])
    parts.append(paper.get("comment", "") or "")
    parts.append(paper.get("journal_ref", "") or "")
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
    groups = term_groups(terms)
    if not groups:
        return None
    text = paper_text(paper)
    words = _match_set(text)

    matched: List[Dict[str, Any]] = []
    weighted = 0.0
    occurrences = 0
    for group in groups:
        if not group_matches(group, words):
            continue
        term = group["term"]
        common = term in BOILERPLATE
        credit = COMMON_HIT if common else DISTINCT_HIT
        weighted += credit
        matched.append({
            "topic": term,
            "kind": "common word" if common else "distinctive word",
            "credit": credit,
        })
        occurrences += text.count(term) or min(
            (text.count(p) for p in group["parts"]), default=0)

    if not matched:
        return None
    terms = [g["term"] for g in groups]

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
    # cost precision@5 on all 11 phrases containing one, and on none of the 15
    # without — 0.708 to 0.531 overall. Stopwords still do not earn match
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
        return "Matches none of your topics. It is here on how recent it is."

    # Plain words, in the order a person would say them. The old sentence read
    # "matched agent (distinctive word), evaluation (distinctive word),
    # learning (common word); +0.30 for spanning 4 topics", which is the
    # scorer's vocabulary, not anybody's.
    # The phrase match is reported in its own clause below, so listing it here
    # too produced "Mentions agent, memory and agent memory".
    strong = [m["topic"] for m in matched
              if m["kind"] not in ("common word", "phrase")][:4]
    weak = [m["topic"] for m in matched if m["kind"] == "common word"][:2]

    def listed(words):
        if len(words) == 1:
            return words[0]
        return ", ".join(words[:-1]) + " and " + words[-1]

    if strong:
        opening = f"mentions {listed(strong)}"
        if weak:
            opening += f", plus {listed(weak)}, which most papers here use"
    elif weak:
        opening = (f"only matches {listed(weak)}, which most papers here use, "
                   f"so this is a weak match")
    else:
        opening = "matches your search as a whole phrase"

    bits = [opening]
    comp = why.get("components", {})
    if comp.get("phrase_bonus"):
        bits.append("has your exact phrase in it")
    if comp.get("breadth_bonus"):
        bits.append(f"covers {why.get('topics_matched')} of your topics at once")
    if comp.get("tf_bonus"):
        bits.append("comes back to those words repeatedly rather than in passing")
    if comp.get("recency"):
        age = why.get("age_days")
        bits.append("posted today" if age == 0
                    else "posted yesterday" if age == 1
                    else f"only {age} days old")
    return ". ".join(b[0].upper() + b[1:] if b else b for b in bits) + "."


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


_DIST_CACHE: Dict[Any, Any] = {}


def score_distribution(papers: List[Dict[str, Any]],
                       topics: List[str]) -> Dict[str, Any]:
    """Where every real paper in the library falls, so one score can be placed.

    The scoring page shows a number between 0 and 1 and let a reader work out
    for themselves what a good one is. They cannot, and neither could I until I
    measured it: the median paper that matches anything at all scores 0.162, and
    a score of 0.50 is already above 99.7% of the library. The page's own worked
    example scores 0.70, which is off the top of the real distribution entirely.

    Without this, "0.1 to 0.9" reads like a percentage. It is not one. It is a
    position in a distribution that is squashed against the bottom, and the only
    honest way to say so is to show the distribution.

    Cached on the topics and the library size, because it costs 1.7 seconds over
    24,000 papers and the page recomputes on every keystroke.
    """
    key = (tuple(topics), len(papers))
    hit = _DIST_CACHE.get(key)
    if hit is not None:
        return hit

    scores = sorted(score_paper(paper, topics)["score"] for paper in papers)
    total = len(scores) or 1
    nonzero = [s for s in scores if s > 0]

    def above(value: float) -> float:
        low, high = 0, len(scores)
        while low < high:                        # bisect_left, without the import
            mid = (low + high) // 2
            if scores[mid] < value:
                low = mid + 1
            else:
                high = mid
        return round(low / total * 100, 1)

    result = {
        "library": len(papers),
        "matched": len(nonzero),
        "median": round(nonzero[len(nonzero) // 2], 3) if nonzero else 0.0,
        "top_score": round(scores[-1], 3) if scores else 0.0,
        # A handful of landmarks, so the scale reads as what it is rather than
        # as a percentage.
        "landmarks": [{"score": v, "above": above(v)}
                      for v in (0.1, 0.2, 0.3, 0.5, 0.7, 0.9)],
        "_scores": scores,
    }
    _DIST_CACHE.clear()               # one topic list at a time is all this needs
    _DIST_CACHE[key] = result
    return result


def percentile_of(score: float, distribution: Dict[str, Any]) -> float:
    """What share of the library this score beats."""
    scores = distribution.get("_scores") or []
    if not scores:
        return 0.0
    low, high = 0, len(scores)
    while low < high:
        mid = (low + high) // 2
        if scores[mid] < score:
            low = mid + 1
        else:
            high = mid
    return round(low / len(scores) * 100, 1)


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


def term_coverage(papers: List[Dict[str, Any]], terms: List[str]) -> Dict[str, int]:
    """How many papers each term appears in at all.

    A term that appears in zero papers is not a narrow filter, it is noise: it
    cannot promote anything, and because ranking is by what fraction of the
    query a paper covers, it drags every real result down by the same amount.
    That is how a typo silently costs you the answer -- "fidn" matches nothing,
    so every paper scores 2/3 instead of 2/2 and the ordering flattens.

    Rather than guess at spelling, the caller drops the dead terms and says
    which ones it dropped. Wrong guesses are visible; silent dilution is not.
    """
    groups = term_groups(terms)
    counts = {g["term"]: 0 for g in groups}
    if not groups:
        return counts
    for paper in papers:
        words = _match_set(paper_text(paper))
        for group in groups:
            if group_matches(group, words):
                counts[group["term"]] += 1
    return counts


def live_terms(papers: List[Dict[str, Any]], terms: List[str]):
    """(terms that appear somewhere in the library, terms that appear nowhere)."""
    counts = term_coverage(papers, terms)
    live = [t for t, n in counts.items() if n > 0]
    dead = [t for t, n in counts.items() if n == 0]
    return live, dead


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
