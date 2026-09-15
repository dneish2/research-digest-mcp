# research-digest

[![tests](https://github.com/dneish2/research-digest-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/dneish2/research-digest-mcp/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)

A personal library of arXiv papers that your AI assistant can read.

It fetches papers in the categories you care about, ranks them against your
topics, and exposes the result to any MCP client. Claude Code can then answer
"what have I read about evaluation harnesses" from your own library instead of
from the open web.

Nothing leaves your machine. There is no account, no API key, and no server.

---

## Install

```bash
pip install git+https://github.com/dneish2/research-digest-mcp
```

No clone. That gives you search, saved papers, trends and the web interface
with no third-party dependencies at all.

Similarity search ("find papers like this one") needs numpy and scikit-learn:

```bash
pip install "research-digest-mcp[embeddings] @ git+https://github.com/dneish2/research-digest-mcp"
```

Rather try it without installing anything? [uv](https://docs.astral.sh/uv/)
can run it straight from GitHub:

```bash
uvx --from git+https://github.com/dneish2/research-digest-mcp research-digest fetch
```

Working on the code itself, not just using it? See
[Development](#development) below — an editable install needs a local clone.

## Build your library

```bash
research-digest fetch      # pull recent papers from arXiv
research-digest embed      # optional: build vectors for similarity search
research-digest status     # see what you have
```

`fetch` is safe to run daily. It only adds papers you have not seen, and arXiv
hands back roughly the same recent batch each time, so the library grows a few
dozen papers a day — put it on a cron job, launchd agent, or Windows scheduled
task if you want it to run itself. Run it for a week and the trends view starts
to mean something.

Already have a library from an older version of this tool, or another
machine? Load it in one shot instead of waiting on daily fetches to catch up:

```bash
research-digest import ~/old-library/archive.json
```

Safe to run more than once — papers already present are updated, not
duplicated, and stale scoring output from whatever wrote the file is dropped
rather than carried in, since the running code recomputes it on every read.

## Use it

```bash
research-digest search agentic evaluation
research-digest digest                    # today's top picks, written to a dated file
research-digest web                       # browser interface on localhost
```

## Connect it to your agent

**Claude Code**

```bash
claude mcp add research-digest -- research-digest mcp
```

**Codex CLI** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.research-digest]
command = "research-digest"
args = ["mcp"]
```

**GitHub Copilot CLI** — add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "research-digest": {
      "command": "research-digest",
      "args": ["mcp"],
      "tools": ["*"]
    }
  }
}
```

Any other MCP client takes the same two fields: run `research-digest` with the
argument `mcp`.

Then ask in normal language: "search my library for retrieval papers", "what
have I saved about multi-agent systems".

Check it responds:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | research-digest mcp
```

---

## The tools

| Tool | What it does |
|---|---|
| `search_papers` | Keyword search, with the score breakdown for every hit |
| `get_similar` | Nearest papers by embedding similarity |
| `get_trends` | Concepts rising and falling across the last two weeks |
| `get_saved` | Your bookmarked papers and notes |
| `suggest_reading` | Unread papers on a topic, best first |
| `save_paper` | Add a specific paper by arXiv id or URL and bookmark it |
| `get_digest` | Today's top picks against your topics, written to a dated file |
| `library_status` | Paper count, date range, embedding health |

---

## How ranking works

No model decides the order. Every score is arithmetic you can redo by hand,
matching your topics and your query against each paper's title, abstract and
extracted concept tags.

Say one of your topics is "reinforcement learning." A paper scores:

| | |
|---|---|
| that exact phrase appears in the title or abstract | 1.00 |
| just "reinforcement" appears, on its own | 0.60 |
| just "learning" appears, on its own | 0.20 |
| three or more of your topics matched, not just this one | +0.30 |
| exactly two matched | +0.15 |
| published today, decaying to zero over 30 days | +0.20 |

"Reinforcement" is worth three times what "learning" is, because "learning"
appears in nearly every paper in this field, so matching it alone is weak
evidence, not none. 0.60 would let it drown out real signal; 0 would throw
away the little it does carry. 0.20 is the middle ground. This only applies to
words like "learning," "model" and "training" that are common in this specific
literature. Plain English words such as "of" and "the" are handled
differently: dropped from matching entirely, covered below.

`search_papers` ranks a little differently from the standing topic profile
above: results are ordered by how much of your query each paper covers,
weighted by how distinctive each matched word is, with a bonus for the exact
phrase and for terms that recur rather than appear once. Function words like
"the" and "of" are ignored for matching but still count inside a phrase match,
and a query reaches inside hyphenated compounds — "chain of thought" finds
papers that wrote it "chain-of-thought".

Every result carries its own derivation, in the MCP response and in the web
interface. The "How scoring works" tab lets you edit a title, an abstract and
your topic list, and watch the arithmetic change.

### Is it any good?

For 5 results, precision@5 is how many of them are actually relevant, out of 5.
1.00 means every result belongs; 0.20 means one out of five does.

Two separate checks, in [`docs/EVAL.md`](docs/EVAL.md):

- **Exact-phrase queries** — 26 queries where "relevant" can be checked by a
  script (the phrase is in the paper or it is not). Precision@5: **0.769**.
  Two baselines on the same queries show this is a real result and not an
  artifact of easy questions: sorting by recency alone gets 0.031, and random
  order gets 0.000.
- **Semantic queries** — 8 queries where relevance is a judgment call, not a
  script. An LLM made that call 115 times, blind to which ranker produced
  which result. Precision@5: **0.80**.

The 26-query number is the one to trust day to day: it runs in CI on every
push, against a corpus frozen so the score cannot drift out from under a
change to the ranker. The 8-query number is directional — eight queries is not
a benchmark, and there is no held-out set behind it.

Read the limitations section in `docs/EVAL.md` before leaning on either number.
Exact-phrase positives reward a keyword matcher by construction. It also walks
through the one query where this ranker scores zero, and why no ranker could
do better on it — that's the most useful part of the document.

---

## Configuration

Settings live in `settings.json` inside your data directory:

```bash
research-digest config                          # show current settings
research-digest config --add-category cs.CV     # track another arXiv category
research-digest config --add-topic "world model"
```

| Setting | Default |
|---|---|
| `categories` | `cs.AI`, `cs.LG`, `cs.CL`, `cs.MA`, `cs.SE` |
| `topics` | agent, evaluation, reasoning, retrieval, multi-agent, reliability, interpretability |
| `max_per_fetch` | 60 |
| `encoder` | `tfidf-svd` |

Data lives in `~/.research-digest` by default. Point `RESEARCH_DIGEST_HOME`
somewhere else if you prefer:

```bash
export RESEARCH_DIGEST_HOME=~/notes/papers          # macOS, Linux
$env:RESEARCH_DIGEST_HOME = "$env:APPDATA\research-digest"   # Windows
```

The directory holds `archive.json`, `saved.json`, `settings.json` and, if you
built them, `embeddings.db`.

---

## A note on the embeddings

The default encoder is TF-IDF followed by SVD. It is fitted to your corpus,
which means the 384 columns it produces are derived from the particular set of
papers it saw. Vectors from two different fits are not comparable, even when
both are 384 wide.

So `research-digest embed` always rebuilds the whole store in one pass, and the
store records which fit produced every row. If it ever finds more than one, it
refuses to compare them and tells you to re-embed rather than returning
confident nonsense.

If you would rather have a fixed basis that survives incremental updates,
install `sentence-transformers` and run `research-digest embed --engine minilm`.
That downloads about 90 MB the first time.

---

## Development

```bash
uv venv && uv pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

Any Python installer works here — `pip install -e ".[dev]"` is the same
install without uv.

The MCP server is plain JSON-RPC over stdin and stdout, about 350 lines, with no
SDK. One JSON object per line each way, which makes it easy to drive from a
shell script when something looks wrong.

## License

MIT
