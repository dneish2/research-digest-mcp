# eval/

The harness behind [`docs/EVAL.md`](../docs/EVAL.md). Read that document first, it is the
argument; this directory is the evidence.

## What `fixtures/` is, and what it is not

`fixtures/corpus.json.gz` is a frozen snapshot of 1,373 arXiv metadata records,
committed so the ranker's score is reproducible on a machine with no library at
all. CI runs against it, which is the only reason the number in `docs/EVAL.md`
means anything: a baseline that reads whatever happens to be on disk is not a
baseline, and mining the phrase set from a live library once moved the published
headline from 0.708 to 0.769 with no change to the ranker. The clock was the
last thing left unfrozen and did the same on its own, 0.7692 to 0.7615 over two
calendar days, because a recency bonus decays against the real date; the harness
now scores as of the day after the newest paper in the corpus. The stable figure
is 0.761.

It is a test fixture and nothing else. Every script here is read-only, none of
them writes to your library, and the fixture is never merged into it. Cloning
this repository gives you an **empty** library: what you hold is whatever you
fetch, and `~/.research-digest` is created on your first fetch and ignored by
git.

```bash
.venv\Scripts\python.exe eval\eval-regression.py   # deterministic, no model, ~30s
.venv\Scripts\python.exe eval\compute_metrics.py   # scores the committed judgments
.venv\Scripts\python.exe eval\gen_tables.py        # regenerates the markdown tables
```

Every script here forces UTF-8 on its own output, so a Windows console in cp1252
no longer has to be configured by hand — printing a title with an accent in it
used to crash them, or mangle it silently.

`eval-regression.py` reads the **frozen corpus and pinned phrase set** in
`fixtures/` by default, so it needs no library and reproduces anywhere. Pass
`--live` to measure the real library instead, `--mine` to regenerate the pinned
phrase set, and `--assert-min P` to exit non-zero below a precision@5 floor
(this is what CI runs).

| file | what it is |
|---|---|
| `eval-regression.py` | Scores four rankers on 26 exact-phrase queries. Read-only; `seed 42`. |
| `fixtures/corpus.json.gz` | The 1,373-paper corpus every regression number is measured on, frozen. |
| `fixtures/phrases.json` | The 26 pinned queries and their verified positive sets. |
| `build_pools.py` | Builds the blind judging pool and the condition key for the judge track. |
| `compute_metrics.py` | Turns judgments + answer key into precision@5, mean relevance@5 and NDCG@5. |
| `gen_tables.py` | Renders `metrics_table.md` and `paper_table.md`. |
| `blind_pool.json` | The 115 papers as they were judged — full abstracts, shuffled, condition stripped. |
| `answer_key.json` | Which condition retrieved what, at which rank. Never shown to the judge. |
| `judgments.json` | The 115 relevance scores (0–3) with a one-line reason each. |
| `metrics.json`, `*_table.md` | Generated. Safe to delete and regenerate. |

`blind_pool.json` is committed rather than regenerated on demand, for the same reason the
corpus is: the judge track was drawn from one person's archive, and without the pool none
of its figures would be checkable by anyone else.

The regression track used to mine its phrases from the live library, so its query set moved
as the library grew — collapsing three duplicate papers changed the headline number with no
ranker change at all. Both the corpus and the phrase set are now pinned here; `docs/EVAL.md`
§3 shows what that drift cost and how the old and new numbers line up.
