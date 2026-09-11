"""Build the blind judging pools for the LLM-judge relevance eval (track B).

Read-only: only imports storage.load_papers() and scoring.rank_all_query /
scoring.score_query. Writes only under eval/.

For each query, computes three top-5 rankings (current / recency / random)
over the same candidate pool, takes the union of unique papers across the
three top-5 lists, and writes:

  eval/blind_pool.json   -- id, title, abstract only, order shuffled,
                                NO condition/rank info. This is what gets
                                read during judging.
  eval/answer_key.json   -- which condition(s)/rank each paper id came
                                from, per query. NOT read until after all
                                judgments are recorded.

Run with PYTHONHASHSEED=0 so the random-baseline seed (hash(query) % 10000)
is reproducible across runs.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from research_digest_mcp import storage, scoring

OUT_DIR = Path(__file__).parent

QUERIES = [
    "agentic evaluation",
    "retrieval augmented generation",
    "reward hacking",
    "tool calling benchmark",
    "chain of thought faithfulness",
    "multi-agent coordination",
    "model context protocol",
    "reasoning distillation",
]


def main():
    papers = storage.load_papers()
    by_id = {p["id"]: p for p in papers}

    blind_pool = {}   # query -> list of {id, title, abstract} shuffled
    answer_key = {}    # query -> {id: {"current": rank|None, "recency": rank|None, "random": rank|None}}

    rng_shuffle = random.Random(42)  # for shuffling display order only, not baseline sampling

    for query in QUERIES:
        terms = query.lower().split()
        candidate_pool = [p for p in papers if scoring.score_query(p, terms) is not None]
        if len(candidate_pool) < 5:
            raise RuntimeError(f"Candidate pool too small for {query!r}: {len(candidate_pool)}")

        current_ranked = scoring.rank_all_query(papers, terms)
        current_top5 = current_ranked[:5]

        recency_top5 = sorted(
            candidate_pool, key=lambda p: str(p.get("published", "")), reverse=True
        )[:5]

        sample_n = min(5, len(candidate_pool))
        random_top5 = random.Random(hash(query) % 10000).sample(candidate_pool, sample_n)

        conditions = {
            "current": [p["id"] for p in current_top5],
            "recency": [p["id"] for p in recency_top5],
            "random": [p["id"] for p in random_top5],
        }

        union_ids = []
        seen = set()
        for cond in ("current", "recency", "random"):
            for pid in conditions[cond]:
                if pid not in seen:
                    seen.add(pid)
                    union_ids.append(pid)

        # answer key: id -> {condition: rank (1-indexed) or None}
        key_for_query = {}
        for pid in union_ids:
            entry = {}
            for cond in ("current", "recency", "random"):
                if pid in conditions[cond]:
                    entry[cond] = conditions[cond].index(pid) + 1
                else:
                    entry[cond] = None
            key_for_query[pid] = entry
        answer_key[query] = {
            "candidate_pool_size": len(candidate_pool),
            "papers": key_for_query,
        }

        # blind pool: shuffle order, strip condition/rank/score info
        shuffled = list(union_ids)
        rng_shuffle.shuffle(shuffled)
        blind_pool[query] = [
            {
                "id": pid,
                "title": by_id[pid]["title"],
                "abstract": by_id[pid]["abstract"],
                "published": str(by_id[pid].get("published", ""))[:10],
            }
            for pid in shuffled
        ]

    (OUT_DIR / "blind_pool.json").write_text(
        json.dumps(blind_pool, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / "answer_key.json").write_text(
        json.dumps(answer_key, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for query in QUERIES:
        print(f"{query!r:38} pool={answer_key[query]['candidate_pool_size']:5}  union_unique={len(blind_pool[query])}")


if __name__ == "__main__":
    main()
