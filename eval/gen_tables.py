"""Generate markdown tables (per-paper judgment table, per-query/aggregate metrics
table) from judgments.json / answer_key.json / metrics.json, for eval-judge.md."""
import json
from pathlib import Path

D = Path(__file__).parent
ak = json.loads((D / "answer_key.json").read_text(encoding="utf-8"))
jg = json.loads((D / "judgments.json").read_text(encoding="utf-8"))
bp = json.loads((D / "blind_pool.json").read_text(encoding="utf-8"))
metrics = json.loads((D / "metrics.json").read_text(encoding="utf-8"))

titles = {p["id"]: p["title"] for q in bp for p in bp[q]}

out = []

for query in ak:
    out.append(f"\n#### `{query}`\n")
    out.append("| # | Paper | current | recency | random | score | reasoning |")
    out.append("|---|---|---|---|---|---|---|")
    papers = ak[query]["papers"]
    # sort by id for stable, blind-looking order (not by rank/condition)
    for i, (pid, entry) in enumerate(sorted(papers.items()), 1):
        c = entry["current"] or "-"
        r = entry["recency"] or "-"
        rnd = entry["random"] or "-"
        title = titles[pid].replace("|", "/")
        score = jg[query][pid]["score"]
        reason = jg[query][pid]["reason"].replace("|", "/")
        out.append(f"| {i} | [{pid}] {title} | {c} | {r} | {rnd} | **{score}** | {reason} |")

(D / "paper_table.md").write_text("\n".join(out), encoding="utf-8")

# metrics table
m = ["| Query | current P@5 | current NDCG@5 | recency P@5 | recency NDCG@5 | random P@5 | random NDCG@5 |",
     "|---|---|---|---|---|---|---|"]
for query, pq in metrics["per_query"].items():
    c, r, rnd = pq["current"], pq["recency"], pq["random"]
    m.append(f"| {query} | {c['precision_at_5']:.2f} | {c['ndcg_at_5']:.2f} | {r['precision_at_5']:.2f} | {r['ndcg_at_5']:.2f} | {rnd['precision_at_5']:.2f} | {rnd['ndcg_at_5']:.2f} |")
a = metrics["aggregate"]
m.append(f"| **Aggregate (mean of 8)** | **{a['current']['mean_precision_at_5']:.2f}** | **{a['current']['mean_ndcg_at_5']:.2f}** | **{a['recency']['mean_precision_at_5']:.2f}** | **{a['recency']['mean_ndcg_at_5']:.2f}** | **{a['random']['mean_precision_at_5']:.2f}** | **{a['random']['mean_ndcg_at_5']:.2f}** |")
(D / "metrics_table.md").write_text("\n".join(m), encoding="utf-8")

print("wrote paper_table.md and metrics_table.md")
print()
print("\n".join(m))
