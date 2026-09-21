# Where the data comes from, and where it could come from

Every number in this document was measured on 2026-09-19, from this machine,
while the arXiv **search** API was refusing us with HTTP 406. That detail is
the point: the thing that felt like a wall turned out to be one door out of
several, and nobody had tried the others.

---

## 1. What we measured

| Source | Auth | Status when tested | What one request returned |
|---|---|---|---|
| `export.arxiv.org/api/query` (search) | none | **406, blocked** | nothing, for hours |
| `oaipmh.arxiv.org/oai` (harvest) | none | **200, working** | 1,058 cs records for one day, 3.2 MB |
| `api.openalex.org` | none (email = higher limit) | **200** | 10,037 NVIDIA-affiliated works |
| `api.semanticscholar.org` | none | **429** | rate-limited unauthenticated; free key available |

Two findings change what this product can be.

### Finding 1: arXiv has two APIs and we were only using the expensive one

They are separately provisioned services on different hosts.

```
export.arxiv.org/api/query    built for search   ~200 records   rate-limited hard
oaipmh.arxiv.org/oai          built for bulk    ~1,000 records  designed to be walked
```

While a request for **one** paper was being refused by search, the harvest
endpoint served **1,058 records in 3.2 MB**, and a full profile harvest across
six archives scanned **1,907 records and kept 726 in 8.5 seconds.**

The harvest feed also carries more per paper. Search drops these; OAI has them:

| Field | Search API | OAI harvest |
|---|---|---|
| title, abstract, authors | yes | yes, with forename/keyname split |
| DOI | no | yes |
| journal reference | no | yes |
| licence | no | yes |
| comment ("Accepted at NeurIPS") | no | yes |
| created vs updated | conflated | separate |

**One gotcha, and it matters.** OAI's `from`/`until` filter on *datestamp*, the
date the record last changed, not publication date. Harvesting July returns
papers created in July **plus** every older paper revised in July. That is a
superset of what you want for filling a hole, so `harvest --published-only`
filters on `created`.

### Finding 2: OpenAlex answers the question arXiv structurally cannot

"Papers out of NVIDIA" is not answerable from arXiv metadata. `arxiv:affiliation`
is optional and usually blank.

OpenAlex resolves affiliations to institutions with stable IDs:

```
raw_affiliation_strings.search:NVIDIA                     → 10,037 works
  + published 2026 or later                               →  2,247 works
  + where arXiv is the primary source                     →    758 works
```

It also carries `cited_by_count`, referenced works, concepts, open-access
status and funding. No key, no cost.

---

## 2. What is already built

`research-digest harvest --since 2026-07-01 --until 2026-07-31 --published-only`

Walks the OAI feed for every archive your profile touches, keeps only your
categories, honours the 503/`Retry-After` back-pressure the protocol defines,
and merges into your library. It is the answer to the July gap and it works
while search is blocked.

Two properties worth stating, because they are design choices and not accidents:

**It is not keyword-filtered.** The daily fetch asks arXiv for papers matching
your topic words, which is why it is cheap and why it leaves holes: a paper
outside your word list is invisible forever. A harvest takes everything in your
categories for a window. Topics then decide what ranks highly, not what is
allowed to exist. That distinction is the difference between a feed and a
library.

**It filters before accumulating.** A cs harvest is every cs paper. Filtering
inside the walk means a month costs memory for the papers you asked for.

---

## 3. The ranked plan

Ordered by value delivered per unit of work, with the reasoning attached.

### Tier 1: do these

**1. Harvest-first backfill.** *(built)*
Rewire the gap-fill button and `fetch --since` to use OAI instead of search.
The button currently calls the blocked endpoint. **Why:** it is the only path
that works right now, it is 5× denser per request, and it carries four extra
fields. **Cost:** an afternoon of wiring, already prototyped.

**2. Provenance on every paper.**
Store `source` (`oai` / `search` / `saved-by-id` / `openalex`) and
`first_seen`, and show it. **Why:** the entire trust problem in this tool has
been "where did this number come from". A library assembled from four sources
where you cannot tell which is which will produce exactly the same confusion
again, one level up. **Cost:** one field, one badge. Do it before adding
sources, not after.

**3. Citation counts via OpenAlex.**
One batched request per 50 papers, cached, refreshed weekly. **Why:** this is
the single biggest new *ranking signal* available. Right now the scorer knows
what a paper says about itself and nothing about how the field received it.
"Highly cited relative to its age, on your topics" is a different and often
better question than "matches your words". **Caveat to design around:** recent
preprints have near-zero citations, so this must never become the primary sort
or the digest turns into a greatest-hits reel. Keep it as a column and a
filter, not a default order.

**4. Institution search.**
Import OpenAlex institution IDs, offer "papers from NVIDIA / DeepMind / FAIR /
your own university". **Why:** you asked for this twice and arXiv cannot do it.
OpenAlex can, exactly.

### Tier 2: high value, more work

**5. The citation graph as a reading surface.**
Once OpenAlex is in: "papers that cite the ones you saved", "the paper everyone
in your shelf cites that you do not have". **Why:** this is the strongest
recommendation signal in all of bibliometrics and it needs no model. Your own
shelf plus a reference list is a better recommender than any embedding over
abstracts. It turns a personal library into a research map.

**6. Full text, not just abstracts.**
arXiv offers bulk PDF/LaTeX on S3 (requester-pays). **Why:** abstracts are
marketing copy; methods sections are where the transferable mechanism lives,
which is precisely what your profile says you read for. **Why not yet:** it is
the first thing here that costs money and storage, and the value is unproven
against a cheaper proxy. Test the proxy first: search within abstracts for
method words is already live via structural keywords.

**7. A second preprint server.** bioRxiv and medRxiv both expose free APIs with
the same shape. **Why:** only if your interests move that way. Adding sources
you do not read is how a feed becomes noise.

### Tier 3: considered and declined, with reasons

**Kaggle's arXiv snapshot** (~4 GB, all metadata). Fast to load, but it is a
periodic dump that goes stale, and OAI gives the same data live with date
ranges. The dump wins only for a cold start of the *entire* archive, which is
not what a personal library is.

**Papers With Code.** Code links and benchmark tables would be genuinely
useful. Status uncertain after 2024 and I did not verify it is still serving,
so it stays out until someone checks rather than being written in on memory.

**Scraping arXiv's HTML listings.** Would work. It is also the thing that gets
you blocked, and we have two sanctioned bulk APIs. No.

**A hosted vector database.** The library is 1,443 papers and similarity runs
in milliseconds over a 3 MB SQLite file. This would add a dependency, a
service and a bill to solve a problem nobody has.

---

## 4. Making the source transparent

The principle this tool already lives by, applied one level up. Today every
*score* explains itself. Tomorrow every *paper* should explain where it came
from.

Concretely:

- a small source badge on each card: `harvested` / `searched` / `saved by you` /
  `via citation`
- on the paper panel: first seen, which run, which query or which set, and
  which fields came from which service
- on the Profile page, a stacked bar of the library by source, so "where do my
  papers actually come from" is a glance
- in the month chart, colour the bars by source, so a harvested month looks
  different from a drip-fed one

**Why this earns its place:** the moment citation counts arrive from OpenAlex
and abstracts from arXiv, a paper is an assembly of two services with different
freshness and different coverage. Silently blending them is how you get a
number nobody can defend. Same failure as trends counting title words, one
level up.

---

## 5. Visualisation and learning, planned

What exists: the concept map, the bridge list, the month chart, the trend bars
with their denominators, the scoring walkthrough.

What would add most next, in order:

**A timeline you can scrub.** The month chart shows volume. It should show
*composition*: a stacked area of concepts over time, so "when did agent
evaluation take over my feed" is answerable by looking. Same deterministic
counting as the map, one more axis. **Why first:** it reuses machinery that
already exists and answers a question the current UI cannot.

**A citation-flow view.** Once OpenAlex lands: your saved papers as a column,
what they cite on the left, what cites them on the right. **Why:** it makes the
shape of a literature visible, and it is the natural next click from any paper.

**"Explain this to me" on any concept.** Click `retrieval-augmented` and get:
how many of your papers use it, when it first appeared in your library, the
three earliest and three most recent, the concepts it travels with, and one
paragraph assembled from those facts. **Why:** the tool currently helps you
find papers about things you already know the name of. This is the first
feature that helps you learn a name. No model needed for any of it except the
paragraph, and that paragraph should be a template over counts, not generation,
so it cannot be wrong.

**A coverage honesty panel.** What fraction of arXiv's cs.AI output for a given
month you actually hold. The OAI `completeListSize` gives the denominator for
free. **Why:** it turns "my library" into "my library, which is 4% of what was
published, selected this way" — which is the single most important thing a
reader of your trends page needs to know and currently has to be told in prose.

---

## 6. Other points of view

**As a working researcher.** The bottleneck is not finding papers, it is
deciding what not to read. The features that pay are the ones that *cut*:
bridges between things you keep apart, citation weight, "this is the paper
everyone on your shelf cites". More volume without better cutting makes the
tool worse. This argues for Tier 1 item 3 and Tier 2 item 5 over item 6.

**As someone who has never used arXiv.** The category codes were a wall until
last week. The next wall is knowing which topics are worth having. Starter
profiles helped; the missing piece is showing what a profile *yields* before
you commit to it, which the harvest feed can do cheaply since it can count a
month without downloading it.

**As the person paying the bills.** Everything in Tier 1 is free and keyless.
The first real cost is full text on S3, and it is correctly last. A tool whose
entire value proposition is "local, no account, no key" should spend that
property carefully.

**As an operator.** The failure mode of this product is a quiet one: a
scheduled fetch that stopped, a month that never arrived, a source that went
stale. That is why `doctor` and the month chart exist and why provenance is
Tier 1 rather than Tier 2. Adding sources multiplies the ways it can be quietly
wrong, so the instrumentation has to lead the sources, not follow them.

**As a sceptic.** The honest case against all of this: the library already
holds 1,443 papers and the owner has read a fraction. More data may be the
wrong axis entirely, and the real win might be that nothing here helps you
*finish* anything. If that is true, the queue, notes and a "what did I learn
this month" summary matter more than any new source. **That is a real
possibility and it should be tested before Tier 2 gets built** — the cheap test
is to look at how many saved papers ever get marked read.

---

## 7. What this does not solve

- **Paywalled and non-arXiv literature.** OpenAlex indexes it, but you will get
  a title and a DOI, not an abstract you can search.
- **Organisation search stays imperfect.** OpenAlex affiliation matching is good
  and not exhaustive; small labs and mixed appointments blur.
- **Citation counts lag.** A paper from last week has none, regardless of
  quality. Any surface using them must say so or it will mislead.
- **The harvest gotcha is permanent.** Datestamp is not publication date, and
  no amount of interface work changes that. It gets a flag and a sentence, not
  a fix.
