"""Machine-checked regression eval for the research-digest search ranker.

READ-ONLY. Only reads storage.load_papers() and calls the pure scoring
functions in research_digest_mcp.scoring. Never touches ~/.research-digest.

Method
------
1. Mine ~25 candidate phrases (2-4 words) automatically from real paper
   titles/abstracts: any word n-gram that appears verbatim (as a literal
   substring of "title + ' ' + abstract", lowercased) in at least MIN_HITS
   and at most MAX_HITS papers, and is not made entirely of scoring.BOILERPLATE
   words.
2. For each phrase, "papers whose title+abstract contain the exact phrase"
   is the positive set -- a machine-checkable ground truth, no judgment call.
3. Rank the library four ways and score precision@5 / precision@10 against
   that positive set, plus a strict-ordering check (every positive above
   every partial-only match):
     - current   : scoring.rank_all_query(papers, terms)   -- the live ranker
     - old_buggy : scoring.rank_all(papers, terms)          -- BUG-3, the
                    profile scorer fed query terms instead of score_query
     - recency   : papers matching >=1 term, sorted by `published` desc
     - random    : random.Random(42) shuffle of the WHOLE library (the
                    pure-chance floor; NOT restricted to matching papers)
4. Aggregate precision@5/@10 across all mined phrases per method, and pull
   out a few concrete phrase-level before/after examples.

Rerun anytime with:  .venv\\Scripts\\python.exe eval\\eval-regression.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from research_digest_mcp import storage, scoring  # noqa: E402

MIN_HITS = 3
MAX_HITS = 40
NGRAM_LENGTHS = (2, 3, 4)
NUM_PHRASES = 26
SEED = 42
TOP_K = (5, 10)

# Function words that make a poor phrase *boundary* (a phrase starting/ending
# on "a", "the", "to" etc. is usually a meaningless slice across a clause
# boundary, e.g. tokenizing straight through a comma turns "To this end, we
# propose" into the fake two-word "end we"). This is on top of the required
# scoring.BOILERPLATE filter, not a replacement for it: BOILERPLATE catches
# ML-domain filler ("learning", "model"); this catches ordinary English
# function words so the mined set reads as real multi-word terms rather than
# grammatical fragments.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "this", "that", "these", "those", "we", "it",
    "as", "by", "at", "be", "not", "from", "which", "who", "whom", "its",
    "their", "our", "can", "will", "would", "could", "should", "also",
    "such", "end", "have", "has", "had", "i", "you", "they", "than", "then",
}

OUT_JSON = Path(__file__).parent / "eval-regression-results.json"


# --------------------------------------------------------------------------
# Phrase mining
# --------------------------------------------------------------------------

def title_abstract(paper: dict) -> str:
    """title + abstract only, lowered -- deliberately excludes `concepts`
    (which are derived tags, not literal source text) so the positive set
    is defined purely by what a human would see as "the phrase is right
    there in the text"."""
    return f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()


def is_boilerplate_only(phrase: str) -> bool:
    return all(w in scoring.BOILERPLATE for w in phrase.split())


def mine_candidates(papers: list) -> dict:
    """word n-gram (2-4) -> set of paper ids where it appears verbatim.

    Built from adjacent tokens of scoring._WORD.findall(), which is exactly
    the tokenizer scoring.py itself uses. Token adjacency is only a *superset*
    candidate, though: _WORD.findall() ignores punctuation entirely, so two
    words separated by a comma ("...this end, we propose...") look adjacent
    to the tokenizer even though "end we" never appears as a literal
    single-space substring of the real text. Every candidate is therefore
    re-verified with a literal `phrase in text` substring check per paper
    before it counts as a hit -- this is the same check
    scoring.score_query's own phrase-bonus logic uses, so the mined ground
    truth and the ranker's own notion of "matched the phrase" agree.
    """
    raw: dict[str, set] = {}
    texts: dict[str, str] = {}
    for paper in papers:
        text = title_abstract(paper)
        texts[paper["id"]] = text
        tokens = scoring._WORD.findall(text)
        pid = paper["id"]
        for n in NGRAM_LENGTHS:
            for i in range(len(tokens) - n + 1):
                phrase = " ".join(tokens[i:i + n])
                raw.setdefault(phrase, set()).add(pid)

    verified: dict[str, set] = {}
    for phrase, pids in raw.items():
        if len(pids) > MAX_HITS * 4:
            continue  # cheap prefilter: can't possibly survive the true-hit cap
        hits = {pid for pid in pids if phrase in texts[pid]}
        if hits:
            verified[phrase] = hits
    return verified


def select_phrases(candidates: dict, rng: random.Random) -> list:
    def boundary_ok(phrase: str) -> bool:
        words = phrase.split()
        return words[0] not in _STOPWORDS and words[-1] not in _STOPWORDS

    filtered = [
        (phrase, ids) for phrase, ids in candidates.items()
        if MIN_HITS <= len(ids) <= MAX_HITS
        and not is_boilerplate_only(phrase)
        and boundary_ok(phrase)
    ]
    by_len = {2: [], 3: [], 4: []}
    for phrase, ids in filtered:
        by_len[len(phrase.split())].append((phrase, ids))
    for bucket in by_len.values():
        bucket.sort(key=lambda t: t[0])  # deterministic order before sampling

    # Roughly even split across n-gram lengths, capped by what's available.
    per_bucket = NUM_PHRASES // 3
    chosen = []
    for n in (2, 3, 4):
        bucket = by_len[n]
        k = min(per_bucket, len(bucket))
        chosen.extend(rng.sample(bucket, k))
    # top up to NUM_PHRASES from whatever's left, if any bucket came up short
    remaining = [t for n in (2, 3, 4) for t in by_len[n] if t not in chosen]
    rng.shuffle(remaining)
    while len(chosen) < NUM_PHRASES and remaining:
        chosen.append(remaining.pop())
    chosen.sort(key=lambda t: t[0])
    return chosen


# --------------------------------------------------------------------------
# Rankers / baselines
# --------------------------------------------------------------------------

def matches_any_term(paper: dict, terms: list) -> bool:
    words = set(scoring._WORD.findall(scoring.paper_text(paper)))
    return any(t in words for t in terms)


def recency_baseline(papers: list, terms: list) -> list:
    candidates = [p for p in papers if matches_any_term(p, terms)]
    candidates.sort(key=lambda p: str(p.get("published") or ""), reverse=True)
    return [p["id"] for p in candidates]


def random_baseline(papers: list, seed: int) -> list:
    """Pure chance floor: the WHOLE library, not restricted to matching
    papers. This is deliberately the harder bar -- it answers "does the
    ranker beat doing nothing at all", not "does it beat a random reshuffle
    of an already-relevant shortlist"."""
    ids = [p["id"] for p in papers]
    random.Random(seed).shuffle(ids)
    return ids


def current_ranker(papers: list, terms: list) -> list:
    return [p["id"] for p in scoring.rank_all_query(papers, terms)]


def old_buggy_ranker(papers: list, terms: list) -> list:
    return [p["id"] for p in scoring.rank_all(papers, terms)]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def precision_at_k(ranked_ids: list, positive_ids: set, k: int) -> float:
    top = ranked_ids[:k]
    hits = sum(1 for pid in top if pid in positive_ids)
    return hits / k


def strict_ordering_ok(ranked_ids: list, positive_ids: set) -> tuple:
    """True iff every positive that appears in the ranked list outranks
    every non-positive (partial-match-only) paper in that list. Returns
    (ok, num_positives_in_ranked, num_positives_missing_entirely)."""
    positive_ranks = [i for i, pid in enumerate(ranked_ids) if pid in positive_ids]
    partial_ranks = [i for i, pid in enumerate(ranked_ids) if pid not in positive_ids]
    missing = len(positive_ids) - len(positive_ranks)
    if not positive_ranks or not partial_ranks:
        ok = True
    else:
        ok = max(positive_ranks) < min(partial_ranks)
    return ok, len(positive_ranks), missing


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    papers = storage.load_papers()
    print(f"Loaded {len(papers)} papers from the live library (read-only).")

    rng = random.Random(SEED)
    candidates = mine_candidates(papers)
    print(f"Mined {len(candidates)} raw n-gram candidates "
          f"(before the {MIN_HITS}-{MAX_HITS} hit-count filter).")

    phrases = select_phrases(candidates, rng)
    print(f"Selected {len(phrases)} eval phrases.\n")

    by_id = {p["id"]: p for p in papers}
    methods = ["current", "old_buggy", "recency", "random"]
    agg = {m: {k: [] for k in TOP_K} for m in methods}
    strict_results = []
    per_phrase = []

    # Random baseline's ranking of the full library doesn't depend on the
    # phrase (same shuffle every time) -- compute once.
    random_ranked_full = random_baseline(papers, SEED)

    for phrase, positive_ids in phrases:
        terms = phrase.split()
        positive_ids = set(positive_ids)

        ranked = {
            "current": current_ranker(papers, terms),
            "old_buggy": old_buggy_ranker(papers, terms),
            "recency": recency_baseline(papers, terms),
            "random": random_ranked_full,
        }

        row = {"phrase": phrase, "n_positives": len(positive_ids)}
        for m in methods:
            for k in TOP_K:
                p_at_k = precision_at_k(ranked[m], positive_ids, k)
                agg[m][k].append(p_at_k)
                row[f"{m}_p@{k}"] = round(p_at_k, 3)

        ok, in_ranked, missing = strict_ordering_ok(ranked["current"], positive_ids)
        row["strict_ordering_ok"] = ok
        row["positives_in_ranked"] = in_ranked
        row["positives_missing_from_ranked"] = missing
        strict_results.append(ok)

        row["current_top5_titles"] = [
            by_id[pid]["title"][:90] for pid in ranked["current"][:5]
        ]
        row["old_buggy_top5_titles"] = [
            by_id[pid]["title"][:90] for pid in ranked["old_buggy"][:5]
        ]
        row["current_top5_is_positive"] = [pid in positive_ids for pid in ranked["current"][:5]]
        row["old_buggy_top5_is_positive"] = [pid in positive_ids for pid in ranked["old_buggy"][:5]]
        per_phrase.append(row)

    print(f"{'phrase':45s} {'n+':>3s}  cur@5 old@5 rec@5 rnd@5   strict")
    for row in per_phrase:
        print(f"{row['phrase']:45s} {row['n_positives']:3d}  "
              f"{row['current_p@5']:.2f}  {row['old_buggy_p@5']:.2f}  "
              f"{row['recency_p@5']:.2f}  {row['random_p@5']:.2f}   "
              f"{row['strict_ordering_ok']}")

    print("\n=== Aggregate (mean across all phrases) ===")
    summary = {}
    for m in methods:
        summary[m] = {}
        for k in TOP_K:
            vals = agg[m][k]
            mean = sum(vals) / len(vals)
            summary[m][f"p@{k}"] = round(mean, 4)
        print(f"{m:10s}  " + "  ".join(f"p@{k}={summary[m][f'p@{k}']:.3f}" for k in TOP_K))

    strict_rate = sum(strict_results) / len(strict_results)
    print(f"\nstrict ordering (current ranker, positives strictly above "
          f"partial matches): {sum(strict_results)}/{len(strict_results)} "
          f"phrases = {strict_rate:.1%}")

    # A couple of concrete before/after examples: biggest current-vs-old gap.
    per_phrase.sort(key=lambda r: r["current_p@5"] - r["old_buggy_p@5"], reverse=True)
    print("\n=== Biggest current-vs-old-buggy gaps (top 4) ===")
    for row in per_phrase[:4]:
        print(f"\nphrase: {row['phrase']!r}  (n_positives={row['n_positives']})")
        print(f"  current p@5={row['current_p@5']:.2f}   old_buggy p@5={row['old_buggy_p@5']:.2f}")
        print(f"  current top5 (is_positive): {list(zip(row['current_top5_titles'], row['current_top5_is_positive']))}")
        print(f"  old_buggy top5 (is_positive): {list(zip(row['old_buggy_top5_titles'], row['old_buggy_top5_is_positive']))}")

    OUT_JSON.write_text(
        json.dumps({"summary": summary, "strict_ordering_rate": strict_rate,
                     "phrases": per_phrase}, indent=2),
        encoding="utf-8",
    )
    print(f"\nFull per-phrase results written to {OUT_JSON}")


if __name__ == "__main__":
    main()
