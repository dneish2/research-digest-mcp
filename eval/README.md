# eval/

The harness behind [`docs/EVAL.md`](../docs/EVAL.md). Read that document first — it is the
argument; this directory is the evidence.

```bash
# Windows: set PYTHONIOENCODING=utf-8 first, or printing paper titles will crash.
.venv\Scripts\python.exe eval\eval-regression.py   # deterministic, no model, ~30s
.venv\Scripts\python.exe eval\compute_metrics.py   # scores the committed judgments
.venv\Scripts\python.exe eval\gen_tables.py        # regenerates the markdown tables
```

| file | what it is |
|---|---|
| `eval-regression.py` | Mines 26 exact-phrase queries from the library and scores four rankers against them. Read-only; `seed 42`. |
| `build_pools.py` | Builds the blind judging pool and the condition key for the judge track. |
| `compute_metrics.py` | Turns judgments + answer key into precision@5, mean relevance@5 and NDCG@5. |
| `gen_tables.py` | Renders `metrics_table.md` and `paper_table.md`. |
| `blind_pool.json` | The 115 papers as they were judged — full abstracts, shuffled, condition stripped. |
| `answer_key.json` | Which condition retrieved what, at which rank. Never shown to the judge. |
| `judgments.json` | The 115 relevance scores (0–3) with a one-line reason each. |
| `metrics.json`, `*_table.md` | Generated. Safe to delete and regenerate. |

`blind_pool.json` is committed rather than regenerated on demand. The library it was drawn
from is one person's 1,376-paper archive and is not in this repository, so without the pool
none of the judge-track figures would be checkable by anyone else.

`eval-regression.py` mines its phrases from the live library, which means its query set
moves as the library grows. That is a known weakness, not a feature — pinning the phrase
set is the first item in the CI proposal in `docs/EVAL.md` §7.2.
