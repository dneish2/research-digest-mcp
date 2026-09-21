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

## What it does, in one line each

| | |
|---|---|
| **Papers** | everything you have fetched, best match first. Search it, or ask it a question |
| **Digest** | a small dated pick for today, the same every time you open it |
| **Map** | which subjects your library actually holds, and which ones turn up together. Click anything for the papers behind it |
| **Trends** | what arXiv as a whole is publishing more and less of, week over week |
| **Shelf** | what you saved, with your notes |
| **Profile** | what gets fetched. Edit it here |
| **Scoring** | the ranking function, with the arithmetic shown, and a scale that says what a score is worth |

Two things it does that are worth knowing about up front:

**It uses both of arXiv's services.** arXiv runs a search API and a bulk
harvest feed on separate hosts. Search rate-limits on reputation and will refuse
one machine for an hour at a time, so fetching goes through the harvest feed and
falls back to search rather than the other way around. Whichever one is
answering, the screen says so.

**It measures the field without downloading it.** The Trends tab counts every cs
paper arXiv published, by streaming the harvest feed and keeping only the daily
totals. Three months of the whole field is a few kilobytes on disk, and your
library is not touched.

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
[Development](#development) below. An editable install needs a local clone.

### Check the install

```bash
python -m research_digest_mcp doctor
```

It names which Python answered and whether that is your working tree or a
frozen copy, whether the command is on PATH and where it is if not, whether the
data directory is writable, how many papers you hold and when the library last
grew, and it ends with the exact MCP config line to paste, built from the
interpreter you just ran it with. It makes no network request unless you pass
`--network`, so it cannot itself trip arXiv's rate limit.

Exit code is 0 when everything is green, 1 when it is usable with warnings, and
2 when something is broken, so a scheduled task can gate on it.

### If `research-digest` is "not recognized" or "command not found"

The install worked. Your shell just cannot see it.

`pip` puts the `research-digest` launcher in a per-user scripts folder that is
often missing from `PATH`, especially on Windows and with the Microsoft Store
build of Python. pip prints a warning about this, but it scrolls past in the
middle of the install output:

```
WARNING: The script research-digest.exe is installed in
'...\local-packages\Python313\Scripts' which is not on PATH.
```

Every command in this README also works in this form, which does not depend on
`PATH` at all:

```bash
python -m research_digest_mcp web       # instead of: research-digest web
python -m research_digest_mcp fetch
python -m research_digest_mcp status
```

Note the underscores: `research_digest_mcp` is the Python package, while
`research-digest` is the shortcut command. If `python` is not the right name on
your system, use `python3` or `py -3`.

Prefer the short command? Add the folder pip named in its warning to `PATH`:

```powershell
# Windows, PowerShell. Paste the path from YOUR pip warning.
$dir = "$env:LOCALAPPDATA\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts"
[Environment]::SetEnvironmentVariable(
    "PATH", [Environment]::GetEnvironmentVariable("PATH", "User") + ";$dir", "User")
```

```bash
# macOS and Linux, then reopen the shell
echo 'export PATH="$(python3 -m site --user-base)/bin:$PATH"' >> ~/.zshrc
```

Open a new terminal afterwards. The old one keeps the old `PATH`.

## Build your library

```bash
research-digest fetch      # pull recent papers from arXiv, against your profile
research-digest embed      # optional: build vectors for similarity search
research-digest status     # see what you have
research-digest profile    # what it fetches for you, and the query it sends
```

Missed a stretch of days? `fetch` finds them itself. It starts at the oldest
recent day you hold nothing from, rather than at your newest paper, because
anchoring to the newest paper steps over every hole behind it. For anything
older than three weeks, give it a window:

```bash
research-digest fetch --since 2026-07-01 --until 2026-07-31
```

Both go through arXiv's harvest feed, which takes a date range directly and is
provisioned separately from the search API, so a search rate limit does not stop
a fetch.

`fetch` is safe to run daily. It only adds papers you have not seen, and arXiv
hands back roughly the same recent batch each time, so the library grows a few
dozen papers a day, so put it on a cron job, launchd agent, or Windows scheduled
task if you want it to run itself. Run it for a week and the trends view starts
to mean something.

Already have a library from an older version of this tool, or another
machine? Load it in one shot instead of waiting on daily fetches to catch up:

```bash
research-digest import ~/old-library/archive.json
```

Safe to run more than once: papers already present are updated, not
duplicated, and stale scoring output from whatever wrote the file is dropped
rather than carried in, since the running code recomputes it on every read.

## Use it

```bash
research-digest ask "what should I read about agent memory?"
research-digest search agentic evaluation  # keywords, over what you already hold
research-digest arxiv "speculative decoding"   # searches arXiv and ADDS what it finds
research-digest digest                     # today's top picks, written to a dated file
research-digest web                        # browser interface on localhost
```

`search` reads the shelf. `arxiv` puts something on it. `ask` reads your
sentence first, and tells you what it decided before it shows you anything:

```
read as: Searched your library for agent, memory; ignored memroy (in no paper you hold).
```

That line is the point. When an answer looks wrong you need to know whether the
question was misread or the library is simply thin, and those need opposite
fixes.

A word appearing in zero papers is not searched for, because ranking is by how
much of your query a paper covers, so a dead term lowers every real result by
the same amount. That is how a typo silently costs you the answer. Instead you
are offered the nearest word the library does contain, with the number of papers
it will return:

```
Nothing matched "memroy", so this is showing results for memory.
```

The suggestion comes from your own papers and nowhere else, so it is never a
word that returns nothing. If the odd spelling was deliberate, ask for it
exactly and get the honest zero.

No model is needed for any of this. If you have a local one (Ollama, any small
model) it is used for one job, turning your sentence into search terms, and
it is held to two rules enforced in code, not asked for in a prompt: it may
re-word your question but not re-topic it, and it never decides to go online.
Nothing ranks your results except arithmetic you can read.

A full walkthrough of every command and screen is in
[docs/RUNNING.md](docs/RUNNING.md).

## Connect it to your agent

These use `python -m research_digest_mcp` rather than the short
`research-digest` command on purpose. Your agent starts this server itself, and
it often does so with a different `PATH` than your terminal has, so the short
command can fail there even when it works when you type it. Use `python3` or
`py -3` if that is what Python is called on your machine.

**Claude Code**

```bash
claude mcp add research-digest -- python -m research_digest_mcp mcp
```

**Codex CLI**, add to `~/.codex/config.toml`:

```toml
[mcp_servers.research-digest]
command = "python"
args = ["-m", "research_digest_mcp", "mcp"]
```

**GitHub Copilot CLI**, add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "research-digest": {
      "command": "python",
      "args": ["-m", "research_digest_mcp", "mcp"],
      "tools": ["*"]
    }
  }
}
```

Any other MCP client takes the same two fields: a command, and the arguments
that make it run this package with `mcp`.

Then ask in normal language: "search my library for retrieval papers", "what
have I saved about multi-agent systems".

Check it responds, before blaming your agent:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m research_digest_mcp mcp
```

A JSON line listing the eleven tools means the server is fine and anything still
broken is in the client config.

---

## The tools

| Tool | What it does |
|---|---|
| `ask_library` | A question in plain English, with the reading it used |
| `search_papers` | Keyword search, with the score breakdown for every hit |
| `fetch_papers` | Searches **arXiv itself** and adds what it finds to the library |
| `get_similar` | Nearest papers by embedding similarity |
| `get_trends` | Concepts rising and falling **in your own feed**, as shares of each week's papers |
| `get_field_trends` | What **arXiv as a whole** is publishing more or less of, comparing any two windows |
| `get_saved` | Your bookmarked papers and notes |
| `suggest_reading` | Unread papers on a topic, best first |
| `suggest_profile_terms` | What you save that your fetch profile never asks for |
| `save_paper` | Add a specific paper by arXiv id or URL and bookmark it |
| `get_digest` | Today's top picks against your topics, written to a dated file |
| `library_status` | Paper count, date range, last fetch time, embedding health |

`fetch_papers` is the one that changes what an agent can do for you. Every
other tool reads a shelf; without this one, "find me something on X" answers
"nothing found" for a paper that exists and simply has not been fetched yet.

`get_trends` and `get_field_trends` answer different questions and are kept
apart on purpose. The first is what you fetched, the second is what was
published. "How many papers on RAG came out this week" answered from a personal
library is a wrong answer that looks like a right one.

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
and a query reaches inside hyphenated compounds, so "chain of thought" finds
papers that wrote it "chain-of-thought".

Every result carries its own derivation, in the MCP response and in the web
interface. The "How scoring works" tab lets you edit a title, an abstract and
your topic list, and watch the arithmetic change.

### Is it any good?

For 5 results, precision@5 is how many of them are actually relevant, out of 5.
1.00 means every result belongs; 0.20 means one out of five does.

Two separate checks, in [`docs/EVAL.md`](docs/EVAL.md):

- **Exact-phrase queries**: 26 queries where "relevant" can be checked by a
  script (the phrase is in the paper or it is not). Precision@5: **0.769**.
  Two baselines on the same queries show this is a real result and not an
  artifact of easy questions: sorting by recency alone gets 0.031, and random
  order gets 0.000.
- **Semantic queries**: 8 queries where relevance is a judgment call, not a
  script. An LLM made that call 115 times, blind to which ranker produced
  which result. Precision@5: **0.80**.

The 26-query number is the one to trust day to day: it runs in CI on every
push, against a corpus frozen so the score cannot drift out from under a
change to the ranker. The 8-query number is directional. Eight queries is not
a benchmark, and there is no held-out set behind it.

Read the limitations section in `docs/EVAL.md` before leaning on either number.
Exact-phrase positives reward a keyword matcher by construction. It also walks
through the one query where this ranker scores zero, and why no ranker could
do better on it, which is the most useful part of the document.

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
| `encoder` | `tfidf-svd` |

### Your interest profile

`categories` and `topics` above are the simple shape, and they keep working. If
you want the tool to fetch outside your own subject, add a `profile` block with
three tiers:

```json
{
  "profile": {
    "work_context": "One paragraph on what you build. Not used for scoring; it is there so you can read your own profile back.",
    "core":          { "categories": ["cs.AI", "cs.LG"], "topics": ["agent benchmark", "eval harness"], "per_category": 60 },
    "complementary": { "categories": ["cs.HC", "cs.IR"], "topics": ["trust", "explainability"], "per_category": 30 },
    "stretch":       { "categories": ["stat.ME", "econ.EM"], "structural_keywords": ["confounding", "identification"], "per_category": 20 }
  }
}
```

**core** is the subject you work in, fetched deepest. **complementary** is the
adjacent lanes, fetched shallower so they season the feed rather than flood it.
**stretch** is for fields you do not work in, matched on `structural_keywords`,
which are about method rather than subject: a `stat.ME` paper on identification
strategy is worth reading for how it argues, not for what it is about.

`per_category` is how many papers that tier asks arXiv for, per category, per
run. This is the number that decides whether your library grows. Set it too low
and each run sees only the last hour of submissions.

Run `research-digest profile` to see what the tool believes about you, how many
papers you actually hold in each category, and the exact arXiv query it sends.
A category listed there holding zero papers is configured and not delivering.

Topic lists longer than six are covered across runs rather than truncated: each
fetch asks about the next six and the window advances, so a forty-topic profile
is fully covered in about a week of daily runs.

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

Any Python installer works here. `pip install -e ".[dev]"` is the same
install without uv.

The MCP server is plain JSON-RPC over stdin and stdout, about 350 lines, with no
SDK. One JSON object per line each way, which makes it easy to drive from a
shell script when something looks wrong.

## License

MIT
