# Evaluating the ranker

`research-digest` ranks papers with about two hundred lines of keyword scoring — no
model, no embeddings on the search path, no learned weights. Every result can state its
own derivation in a sentence. This document is the argument that the approach is good
enough to ship, the measurements behind it, and an honest account of what those
measurements do not establish.

Everything here is reproducible from this repository. The harness is in `eval/`, the
judgments it scores against are committed alongside it, and every number below names the
command that produces it.

---

## 1. The thesis

> For a personal library of this size — order 10³ papers, one person's standing
> interests — explainable keyword scoring is competitive with learned ranking, and the
> explanation is worth more than the marginal relevance a learned ranker would buy.

Two halves, and they fail differently. The first half is an empirical claim about
relevance and is measured below. The second is a product claim: that a reader who can
see *why* a paper surfaced can correct a bad ranking themselves, which a reader staring
at an opaque score cannot.

### What would falsify it

Stated up front, because a thesis with no failure condition is a slogan:

1. **A semantic gap that keyword scoring cannot cross.** A query whose relevant papers
   share no vocabulary with it. If those queries are common in real use, the approach is
   wrong regardless of how well it does elsewhere. **Section 5 documents one, and it is
   the most interesting result here.**
2. **A learned reranker beating it by a margin worth the cost** on the same queries and
   the same library — not on a public benchmark.
3. **The explanations turning out not to be load-bearing.** If nobody ever opens "why?",
   the second half of the thesis is decoration.

Only (1) has been tested. (2) and (3) are in section 7.

---

## 2. Two eval designs, and why both exist

Neither track alone is worth much. They fail in opposite directions, which is the reason
to run both.

| | **Regression track** | **Judge track** |
|---|---|---|
| Queries | 26, mined automatically | 8, hand-written |
| Ground truth | Exact phrase occurrence | A model's 0–3 relevance judgment |
| Judgment needed | None | 115 blind judgments |
| Reproducible | Yes — `seed 42`, no model | No — re-running means re-judging |
| Measures | Ordering | Relevance |
| Biased toward | Lexical matchers, by construction | Whatever the judge finds plausible |

**The regression track** mines its own queries: any 2–4 word n-gram appearing verbatim in
3–40 papers, excluding boilerplate and phrases that start or end on a function word.
"Papers containing the exact phrase" is then the positive set. No judgment is involved
anywhere, so it is fully machine-checkable and deterministic.

Its weakness is fundamental and worth stating before the numbers rather than after:
**exact-phrase positives reward a lexical matcher by construction.** A ranker that
matches strings is being graded on a task defined in terms of string matching. This track
cannot tell you the ranker is good. It can only tell you whether a change made it worse —
which is exactly what it caught (section 4).

**The judge track** asks eight real questions, retrieves the top 5 under three conditions,
pools and shuffles the results, strips every trace of which condition produced what, and
judges each paper 0–3 (0 irrelevant, 1 tangential, 2 relevant, 3 directly on topic) against
its **full** abstract. Precision@5 counts judgments ≥ 2.

Its weakness is that it is one model's opinion, rendered once.

```bash
.venv\Scripts\python.exe eval\eval-regression.py    # deterministic, ~30s
.venv\Scripts\python.exe eval\compute_metrics.py    # scores the committed judgments
```

---

## 3. Results

### Regression track — 26 mined phrases, deterministic

| Ranker | precision@5 | precision@10 | strict ordering |
|---|---|---|---|
| **current** | **0.708** | **0.404** | **84.6%** (22/26) |
| profile scorer fed query terms | 0.331 | 0.258 | — |
| recency, restricted to matching papers | 0.008 | 0.015 | — |
| random over the whole library | 0.000 | 0.000 | — |

"Strict ordering" is the harder question: were **all** exact-phrase papers placed above
every paper that matched only some of the words. Precision@5 tolerates one bad result in
five; this does not.

One run is the result here, not a sample of one. There is no model and no randomness
outside a fixed `seed 42` shuffle, so the number is a property of the code and the
library, and re-running reproduces it exactly. That is not true of the judge track
below, and the difference between the two is the point of running both.

### Judge track — 8 queries, 115 blind judgments

| Condition | precision@5 | mean relevance@5 | NDCG@5 |
|---|---|---|---|
| **current** | **0.800** | **2.225** | **0.827** |
| recency | 0.225 | 0.875 | 0.378 |
| random | 0.125 | 0.500 | 0.174 |

Excluding the one query the library cannot answer at all (section 5), current reaches
precision@5 **0.914** and NDCG@5 **0.945** over the remaining seven. Both numbers are
reported because they answer different questions: 0.800 is what a reader experiences
across these eight queries, 0.914 is what the ranker does when the library contains an
answer.

**These judge-track figures describe the ranker as it was when the pool was built, not as
it is today.** The eval ran, found a bug, the bug was fixed, and the eval was never
re-run — re-running it honestly requires re-judging, because the fixes change which papers
appear in the top 5 and those papers have no judgments. Re-deriving the stored rankings
with today's code reproduces 5 of the 8 queries' top-5 exactly; the three that diverge are
precisely the ones the fixes touch. Treat 0.800 as a lower bound for the current ranker
and as a measured value for the previous one.

### The NDCG numbers here are not the ones the first pass reported

The original harness computed ideal DCG by sorting **the retrieved list itself**:

```python
ideal = sorted(rels, reverse=True)      # wrong
```

That asks "did you order your five correctly", not "did you find the right five". Five
irrelevant papers all judged 1 score a perfect 1.0, because `[1,1,1,1,1]` is already its
own ideal ordering. It produced a random baseline scoring **NDCG@5 = 0.97 on a query where
its precision@5 was 0.00** — a number that should have been read as a broken metric on
sight, since a random shuffle of 1,376 papers cannot rank well.

Corrected to normalise against the best five in the judged pool, the random baseline falls
from 0.54 to 0.174 and recency from 0.72 to 0.378. Current barely moves (0.85 → 0.827),
which is why the bug survived a read-through: it flattered the baselines, not the system
under test, so the headline gap looked *smaller* than it was.

A caveat survives the fix. The pool is the union of the three conditions' top-5, so the
"ideal" is the best five papers *some condition surfaced*, not the best five in the
library. A relevant paper none of them retrieved is invisible. Every NDCG here is
therefore an optimistic bound on NDCG over the full library. Pooled evaluation has this
property by construction; it is worth stating rather than hiding.

---

## 4. What the eval actually caught

This is the part worth reading. The tracks earned their keep by disagreeing with each
other.

The judge track found that `chain of thought faithfulness` scored **precision@5 = 0.00**.
The cause was that `BOILERPLATE` — the list of words that score less — is this field's
jargon, not general English. "of" was not in it, so it scored as a *distinctive* word
worth 0.6 credit and matched nearly every paper in the library. A stopword list was added.

Nobody measured the fix against the other track. It cost **0.708 → 0.531**, and strict
ordering **88.5% → 46.2%**.

The mechanism: stopword filtering ran before the exact-phrase bonus was assembled, so the
phrase for "chain of thought" became "chain thought" — a string in no paper — and every
query with a function word inside it silently lost the bonus. The per-phrase breakdown is
unambiguous:

| | phrases | mean precision@5 change |
|---|---|---|
| containing a stopword | 11 | **all 11** fell |
| containing none | 15 | 0 of 15 moved |

Building the phrase from the raw query while still excluding stopwords from match credit
restores 0.708 with the stopword fix kept.

| ranker state | precision@5 | strict ordering |
|---|---|---|
| (a) query scorer, before either fix | 0.708 | 88.5% |
| (b) + stopword list — **what was shipped, unmeasured** | 0.531 | 46.2% |
| (c) + phrase built from the raw query | 0.708 | 88.5% |
| (d) + hyphen expansion — **today** | 0.708 | 84.6% |

Reproduce with `eval\eval-regression.py`; row (d) is the committed code.

The general lesson is not about stopwords. It is that a fix motivated by one eval and
never checked against the other is a coin flip, and that this one was *shipped* in state
(b) — a 25% relative precision loss that no test failed on, because the tests encoded the
bug that motivated the fix and nothing encoded the cost.

---

## 5. The query that stays at zero, and what it means

`chain of thought faithfulness` still scores precision@5 = 0.00. The previous pass died
mid-investigation. Finishing it changes what the whole eval means, so it gets its own
section.

**It is not a judging failure.** The retrieved papers are about Scrum Master learning
paths and synthesising labelled wireless signals. The judge was right.

**A real ranker bug was hiding underneath it.** The tokenizer keeps hyphenated compounds
whole, so `chain-of-thought` is a single token that the query term "chain" never equals.
A search for "chain of thought" returned **0 of the 10 chain-of-thought papers in the
library** in its top ten; the results were spin-chain and oscillator-chain *physics*
papers, which do contain a standalone "chain". Letting compounds also contribute their
parts takes that query to **10 of 10, first at rank 1**. That fix is real, and it is
shipped.

**It does not rescue this query, and nothing could.** Of 1,376 papers:

| | count |
|---|---|
| mention chain-of-thought | 10 |
| mention faithful* | 18 |
| **mention both** | **0** |

There is nothing correct to retrieve. The single paper the judge scored ≥ 2 across the
entire pool is *"Necessary or Sufficient? Evaluating LLM Explanations With Behavioural
Evidence"* — which contains **none** of the query's words. Not "chain", not "thought",
not "faithful". It is topically adjacent and lexically disjoint.

So this query is a **library-coverage failure first** and a **demonstration of the
method's ceiling second**. A keyword matcher cannot retrieve that paper by construction —
which is falsifier (1) from section 1, found in the wild.

One more measurement, because the obvious next sentence is "so use embeddings": the
similarity store already built over this library (tf-idf + SVD, 384 dimensions, all 1,376
papers) **also fails to bridge it.** That paper is not a near neighbour of any
chain-of-thought paper in the library, and its own nearest neighbours are about black-box
action monitoring and protein structure determination. "Add embeddings" is not supported
by the evidence available here; it is a hypothesis, and section 7 says what it would cost
to test properly.

---

## 6. What this does not prove

The section to read first if you are deciding whether to believe any of the above.

- **Exact-phrase positives reward a lexical matcher by construction.** The regression
  track's ground truth is "contains this string" and the system under test matches
  strings. 0.708 is evidence against regressions, not evidence of quality.
- **Eight queries is not a benchmark.** They were written by the same person who built
  the ranker, after using it. Nothing controls for choosing queries it happens to serve.
- **There is no held-out set.** Every number here comes from the data that motivated the
  fixes. The hyphen fix was found by investigating a query in the eval and is reported
  against a demonstration on that same query. That is a development number. It is quoted
  as one.
- **The judge track is N=1 and model-graded.** 115 judgments, one pass, one model, no
  inter-rater agreement, no human spot-check of the judge. A second judging pass could
  move the aggregate and nobody has run one. Where a number depends on a model, one run
  is not a result — this one is a single run, and is labelled as such rather than
  defended.
- **The judge-track numbers describe a ranker that has since changed** (section 3).
- **Every NDCG is pooled and therefore optimistic** (section 3).
- **Recall is never measured anywhere.** Both tracks score what was retrieved. Neither
  can see a relevant paper that no condition surfaced — which is exactly the failure mode
  section 5 found, and it took a manual count of the library to see it.
- **One library, one person, one topic profile.** 1,376 papers in five arXiv categories
  reflecting one reader's interests. Nothing here transfers to a different corpus without
  re-running it.

The honest summary: this establishes that the ranker is substantially better than
recency and than chance, that a specific regression was caught and fixed, and that one
class of query defeats the approach entirely. It does not establish that keyword scoring
is competitive with learned ranking, because nothing learned was ever run against it.

---

## 7. Eval candidates, and what each would take

Ordered by evidence bought per hour, not by ambition.

### 7.1 Implicit feedback capture — labels from ordinary use

**~2 hours. The only item here that produces labels nobody had to write.**

An append-only `~/.research-digest/feedback.jsonl`, one JSON object per line:

```json
{"event":"search","ts":"...","surface":"web","query_id":"q_7f3a1c","query":"agentic evaluation",
 "terms":["agentic","evaluation"],"searched":1376,"matched":37,
 "results":[{"id":"2609.01234v1","rank":1,"score":0.81}]}
{"event":"save","ts":"...","paper_id":"2609.01234v1","surface":"web"}
```

One function in `storage.py`, `log_feedback()`, best-effort so a logging failure can never
break a search. Called from **both `web.py` and `mcp.py`** — that is the load-bearing
detail. Two of the three personas for this tool never open the web UI, so logging only the
web surface would build an eval set describing a minority of real use. That is the
two-universes bug class, already on file in this workspace from another repo; there is no
excuse for repeating it here.

Join a save back to the query that surfaced it **at analysis time** ("which search in the
preceding 30 minutes had this paper in its results"), not in the write path. Zero frontend
change, and the ambiguity is rare in a single-user tool. Thread a `query_id` through the
client only if real data proves the join noisy.

What it cannot give you: **recall, ever.** Every label is conditioned on the ranker having
already surfaced the paper. This is the same blind spot clickthrough logs have at any
search engine, and it is the exact failure mode section 5 found — so this track can never
replace a judged eval, only supplement it. And absence of a click is not a negative label;
it means "not got to".

Cold start is total. Day one the file has zero lines and no backfill is possible.

### 7.2 Put the regression track in CI

**~1 hour.** It is deterministic and takes about thirty seconds. Pin the phrase set rather
than re-mining it, so the fixtures stop moving under the metric, and fail the build on a
precision@5 drop beyond a set margin. Had this existed, state (b) in section 4 would never
have been committed.

The fixtures must be pinned to a file, not mined from the live library at run time. A
baseline computed from live data is not a baseline — it moves when the thing it is meant
to hold still for moves.

### 7.3 A held-out query set, judged once and then left alone

**~3 hours to build, ~30 minutes per re-run.** Sixteen queries: eight written before
looking at any results, eight mined from `feedback.jsonl` once 7.1 has data. Judge once,
commit the judgments, and never tune against them. Everything in this document is a
development number until this exists.

### 7.4 A second judging pass, by a different model

**~1 hour.** Re-judge the existing 115-paper pool with a different judge and report
agreement. If two judges disagree materially, the judge-track numbers carry an error bar
nobody has drawn yet — and right now they are quoted as point estimates.

### 7.5 The learned-reranker comparison the thesis actually needs

**~4 hours.** The thesis claims competitiveness with learned ranking, and nothing learned
has been run. Needs query→vector retrieval, which does not exist today: the similarity
store is paper→paper only, so there is no way to encode a query and search. Build that,
then run a cross-encoder rerank over the keyword top-50 and judge the difference blind.

Two results are worth having in advance. One: when this was tried in a neighbouring
project, reranking improved recall but *amplified* misleading evidence, so measure
precision and faithfulness, not recall alone. Two: the tf-idf+SVD store already in place
cannot bridge the section 5 gap either, so a genuine dense encoder is the minimum bar for
this experiment to mean anything.

### 7.6 Ask whether the explanations are load-bearing

**~30 minutes, once 7.1 ships.** Log expansion of the "why?" control. If nobody opens it,
half the thesis is decoration and the UI should spend that space differently. This is the
cheapest test of a claim the whole project rests on, and it currently has no evidence
either way.

---

## 8. Reproducing everything here

```bash
.venv\Scripts\python.exe eval\eval-regression.py   # section 3, table 1; deterministic
.venv\Scripts\python.exe eval\compute_metrics.py   # section 3, table 2, from committed judgments
.venv\Scripts\python.exe eval\gen_tables.py        # regenerates the markdown tables
```

`eval/` holds the harness, the 115 committed judgments with their one-line reasoning
(`judgments.json`, rendered in `paper_table.md`), the condition key (`answer_key.json`),
and the blind pool as judged (`blind_pool.json`). The pool is committed rather than
regenerated because the library it was drawn from is one person's and is not in this
repository — without it, no figure above is checkable by anyone else.

On Windows, set `PYTHONIOENCODING=utf-8` first. Printing paper titles in the console
codepage is the exact bug class this repository has already fixed twice.
