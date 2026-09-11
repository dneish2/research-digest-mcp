"""Combine blind judgments with the answer key (which condition ranked what)
to compute precision@5, mean relevance@5, and NDCG@5 for current / recency /
random, per query and aggregate. Read-only w.r.t. the library; only reads/writes
files under eval/.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

OUT_DIR = Path(__file__).parent

answer_key = json.loads((OUT_DIR / "answer_key.json").read_text(encoding="utf-8"))
judgments = json.loads((OUT_DIR / "judgments.json").read_text(encoding="utf-8"))
blind_pool = json.loads((OUT_DIR / "blind_pool.json").read_text(encoding="utf-8"))

CONDITIONS = ["current", "recency", "random"]


def dcg(rels):
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(rels))


def ndcg_at_5(rels, pool_rels):
    """NDCG@5 of a retrieved list, normalised against the best five papers in
    the *judged pool* for this query — not against a re-sort of the retrieved
    list itself.

    The bug this replaces: `ideal = sorted(rels, reverse=True)` normalises each
    condition against its own results, so the question it answers is "did you
    put your five in the right order", not "did you find the right five". A
    condition that retrieves five irrelevant papers all judged 1 scores a
    perfect 1.0, because [1,1,1,1,1] is already its own ideal ordering. That is
    how the random baseline scored NDCG@5 = 0.97 on "agentic evaluation" while
    its precision@5 was 0.00 — a result that should have been read as a broken
    metric on sight, since a random shuffle of 1,376 papers cannot rank well.

    Caveat that survives the fix: the pool is the union of the three
    conditions' top-5, so the ideal is the best five papers *some condition
    surfaced*, not the best five in the library. A relevant paper none of them
    retrieved is invisible to this metric, which makes every NDCG here an
    optimistic bound on NDCG over the full library. Pooled evaluation has this
    property by construction; it is worth stating, not hiding.
    """
    idcg = dcg(sorted(pool_rels, reverse=True)[:5])
    if idcg == 0:
        return 0.0
    return dcg(rels) / idcg


per_query_metrics = {}
agg = {c: {"precision_sum": 0.0, "relevance_sum": 0.0, "ndcg_sum": 0.0, "n": 0} for c in CONDITIONS}

titles_by_id = {}
for q, papers in blind_pool.items():
    for p in papers:
        titles_by_id[p["id"]] = p["title"]

for query, key in answer_key.items():
    papers = key["papers"]
    qj = judgments[query]
    # every judged paper for this query, the pool NDCG normalises against
    pool_rels = [v["score"] for v in qj.values()]

    metrics = {}
    for cond in CONDITIONS:
        # build ranked list of (pid, rank) for this condition, sorted by rank
        ranked = sorted(
            [(pid, entry[cond]) for pid, entry in papers.items() if entry[cond] is not None],
            key=lambda x: x[1],
        )
        rels = [qj[pid]["score"] for pid, _rank in ranked]
        n = len(rels)
        precision = sum(1 for r in rels if r >= 2) / n if n else 0.0
        mean_rel = sum(rels) / n if n else 0.0
        ndcg = ndcg_at_5(rels, pool_rels)
        metrics[cond] = {
            "precision_at_5": round(precision, 3),
            "mean_relevance_at_5": round(mean_rel, 3),
            "ndcg_at_5": round(ndcg, 3),
            "n": n,
            "relevances": rels,
            "papers": [{"id": pid, "title": titles_by_id[pid], "score": qj[pid]["score"]} for pid, _ in ranked],
        }
        agg[cond]["precision_sum"] += precision
        agg[cond]["relevance_sum"] += mean_rel
        agg[cond]["ndcg_sum"] += ndcg
        agg[cond]["n"] += 1

    per_query_metrics[query] = metrics

aggregate = {}
for cond in CONDITIONS:
    n = agg[cond]["n"]
    aggregate[cond] = {
        "mean_precision_at_5": round(agg[cond]["precision_sum"] / n, 3),
        "mean_relevance_at_5": round(agg[cond]["relevance_sum"] / n, 3),
        "mean_ndcg_at_5": round(agg[cond]["ndcg_sum"] / n, 3),
    }

result = {"per_query": per_query_metrics, "aggregate": aggregate}
(OUT_DIR / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

print("=== AGGREGATE (mean over 8 queries) ===")
for cond in CONDITIONS:
    a = aggregate[cond]
    print(f"{cond:10} precision@5={a['mean_precision_at_5']:.3f}  mean_rel@5={a['mean_relevance_at_5']:.3f}  ndcg@5={a['mean_ndcg_at_5']:.3f}")

print()
print("=== PER QUERY ===")
for query, metrics in per_query_metrics.items():
    print(f"\n{query!r}")
    for cond in CONDITIONS:
        m = metrics[cond]
        print(f"  {cond:10} precision@5={m['precision_at_5']:.3f}  mean_rel@5={m['mean_relevance_at_5']:.3f}  ndcg@5={m['ndcg_at_5']:.3f}  rels={m['relevances']}")
