# How search works, and what it cannot do

Written because a search for `nvidia` returned 7 papers out of 1,443 and there
was no way to tell whether that was the truth or a broken tool. It was the
truth, and the tool being unable to say so was the real defect.

---

## The short version

There are three different machines in here and they answer three different
questions. Most confusion about this tool is one of them being mistaken for
another.

| | Question it answers | How | Where you see it |
|---|---|---|---|
| **Keyword search** | which of my papers contain these words | arithmetic over text | the search box |
| **Vector similarity** | which of my papers are *about* the same thing | cosine distance over TF-IDF+SVD | "Similar" on a paper, and the second band under a thin search |
| **arXiv fetch** | what exists that I do not have | a request to arXiv | the Fetch button, `arxiv` command, `fetch_papers` tool |

A search only ever consults the first two. It cannot find a paper you have not
fetched, and saying so plainly is now the empty state's whole job.

---

## What the keyword search reads

Everything below, lowercased, as one bag of text per paper:

- title
- abstract
- concept tags (matched from a fixed vocabulary)
- **author names**
- **affiliations**, when arXiv has them
- **the comment field** ("Accepted at NeurIPS 2026", "12 pages")
- **journal reference**

The bolded four were added after the `nvidia` report. Author names were stored
on 1,182 of 1,443 papers and never read, so author search did not exist: a
search for an author with five papers in the library returned one result, and
that one was a coincidental word match. Affiliation, comment and journal_ref
were not even being parsed out of the arXiv feed.

### Why that still does not find "papers from NVIDIA"

Honest answer: it partly can now, and it will never do it reliably, and the
reason is upstream of this tool.

`arxiv:affiliation` is an optional element. Most authors never fill it in.
arXiv's own search does not index it dependably either. So "papers out of
NVIDIA labs" is not a query the arXiv metadata API can answer well, by anyone,
and a tool that appeared to answer it would be guessing.

What *does* work, and is worth knowing:

- searching an author by name (now)
- searching a venue via the comment field, e.g. `neurips 2026` (now)
- searching the subject the lab works on, then using the vector band below

---

## How a paper is scored

No model decides the order. The whole thing is arithmetic you can redo by hand,
and every result carries its own derivation.

```
distinctive word   a query word that is not boilerplate here      0.60
common word        a word most papers here use anyway             0.20
exact phrase       your words, verbatim, in the paper            +0.35
repetition         the words recur rather than appear once     up to +0.15
recency            fades to nothing over 30 days               up to +0.20
```

The first two are summed, divided by **how many words you searched for**, and
scaled by 0.8. Dividing by the whole query is what stops a paper matching one
word out of five from tying a paper matching all five.

`BOILERPLATE` is a hand-built inverse document frequency. Words like `learning`,
`model`, `framework` and (since the `finance ai` report) `ai`, `llm`, `machine`
are worth a third of a real subject word, because matching one tells you only
that the paper is in computer science.

### Hyphens, and one idea being one idea

`chain-of-thought` and `chain of thought` are the same idea spelled two ways, so
the text side opens compounds into their parts and a query reaches inside them.

That fix, applied naively, caused its own bug: `nvidia-labs` became two
independent words, and a paper containing only "labs" counted as a third of the
query. A search for NVIDIA-labs returned a paper about animal welfare in AI
travel agents.

A compound is therefore matched as a **group**: the paper has it written that
way, or has *all* of its parts, or does not have it. `llm-as-judge` matches a
paper writing "LLM as judge" and went from 8 hits to 24. `nvidia-labs` matches
nothing, which is correct.

### Words that match nothing are dropped, and named

A word appearing in zero papers cannot promote anything, and because ranking is
by coverage it lowers every real result by the same amount. So dead words are
removed from the query and reported:

```
read as: Searched your library for agent, memory; ignored memroy (in no paper you hold).
```

That is how you find your own typo, instead of quietly getting a worse answer.

---

## The vector side, and its one real limitation

Embeddings are built by `research-digest embed`: TF-IDF over title and abstract,
then SVD down to 384 columns, fitted over the whole library in one pass.

The limitation is structural and worth understanding, because it shapes what
this feature can ever be:

> **The fitted vectoriser is not saved. Only the output vectors are.**

So there is no way to turn *text you type* into a vector in the same space. The
store can answer "papers near this paper". It cannot answer "papers near this
query". That is why the search box has never been semantic.

### The bridge

The keyword hits are themselves papers. So they can seed the vector side:

1. search `nvidia` → 7 papers contain the word
2. take those 7, ask the vector store what sits nearest their centre
3. show the result as a **separate band**, labelled, with its similarity score

On the real library that turns 7 literal matches into a shelf of GPU
scheduling, sparse attention, kernel and inference-serving papers, none of which
use the word "nvidia". It requires nothing new to be stored and no query-time
model.

It is kept visually separate on purpose. The top list is what contains your
words; the band is what those papers sit next to. Blending them would produce a
result nobody could explain, and every ranking decision in this tool is meant to
be arguable.

### The upgrade that is not being taken, and why

Persisting the fitted `TfidfVectorizer` and `TruncatedSVD` would allow true
query-to-vector search. It is rejected for now on three grounds:

1. it means pickling scikit-learn objects, which couples the library file to a
   library version and is a deserialisation surface
2. the basis is corpus-fitted, so it must be rebuilt and re-persisted on every
   fetch or it drifts from the library it describes
3. the seeded expansion above gets most of the benefit for none of that

`minilm` (sentence-transformers) has a fixed basis and would sidestep 1 and 2
entirely, at the cost of a ~90 MB download. It is already a supported engine:
`research-digest embed --engine minilm`. If query-side semantic search becomes
worth the dependency, that is the road, not pickling a corpus fit.

### The failure this store is built around

Vectors from two different TF-IDF fits are not comparable, even at the same
width. An earlier version encoded new papers in small incremental batches and
produced 16- and 17-column vectors sitting alongside 384-column ones.

So every row records which encoder and which **basis fingerprint** produced it,
there is deliberately no append path, and a mixed store refuses to load rather
than returning confident nonsense. `doctor` reports how many papers are missing
vectors, because similarity silently ignores whatever is not embedded and the
newest papers are exactly the ones that go missing.

---

## arXiv's rate limit

Not a bug, and the single most likely reason a fetch fails.

arXiv answers a burst with **HTTP 429**, and in practice **406** when it has
decided a client is asking too often. This is a block on your machine. It is
not about your query: a request for `cat:cs.AI&max_results=1` gets the same
refusal.

What this tool does about it:

- one request at a time, 5 seconds apart, never in parallel
- a refusal is written to disk, so the next process waits instead of walking
  into the same wall
- the wait **escalates**, roughly ×4 per consecutive refusal up to an hour.
  A flat five minutes meant: wait five, ask, get refused, reset five. A client
  arXiv had decided to refuse stayed in that loop indefinitely
- only a **parsed Atom feed** clears the backoff. Not a bare 200, because arXiv
  sits behind a CDN that can serve a cached response while the origin is still
  refusing
- a block is reported as a block, never as "returned nothing", and it stays on
  screen with a live countdown rather than flashing a toast. A vanishing error
  reads as a glitch, and the reader's next move is to retry, which is what
  lengthens the block

Check it with `python -m research_digest_mcp doctor`, which makes no network
request and therefore cannot make the situation worse.

---

## What "no papers from July 2026" means

Not that anything was lost. It means **no paper in your library has a
publication date in that month**, and the reason is mechanical:

A fetch asks arXiv for the newest papers in your categories. It has no memory of
what it missed, so a month you did not fetch stays unfetched forever, and a
search over that window confidently returns nothing. "Never fetched" and "does
not exist" are indistinguishable from the outside, which is the worst answer a
research library can give.

So the Profile tab draws a bar per month across the window you actually fetch
in, an empty bar is clickable, and clicking it runs a date-bounded backfill.

The window starts where deliberate fetching started, not at your oldest paper.
Measured from the oldest, a real library reported 215 missing months back to
1995, which is not a gap. It is a library that was not running in 1995.

---

## The opinion underneath all of this

**A number on screen should be a number you can get back to.** Every score
carries its derivation, every trend line carries both counts and both
denominators, every link on the map is a count of papers you can click.

**Refusing to answer beats answering wrongly.** A compound that matches nothing
returns nothing. A concept in three papers gets no trend direction. A query of
pure typos says so instead of guessing.

**The two "I don't know"s are different and must look different.** "Your library
does not contain this" and "I could not reach arXiv" call for opposite
responses. Collapsing them into one blank result was the bug behind most of what
is written here.

**A model may help you ask. It never decides what you get.** A local model turns
your sentence into search terms, and code, not prompting, stops it re-topicing
your question or deciding to go online. Ranking stays arithmetic, always.
