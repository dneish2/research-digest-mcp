# research-digest

A personal library of arXiv papers that your AI assistant can read.

It fetches papers in the categories you care about, ranks them against your
topics, and exposes the result to any MCP client. Claude Code can then answer
"what have I read about evaluation harnesses" from your own library instead of
from the open web.

Nothing leaves your machine. There is no account, no API key, and no server.

---

## Install

```bash
git clone https://github.com/dneish2/research-digest-mcp
cd research-digest-mcp
pip install -e .
```

That gives you search, saved papers, trends and the web interface with no
third-party dependencies at all.

Similarity search ("find papers like this one") needs numpy and scikit-learn:

```bash
pip install -e ".[embeddings]"
```

## Build your library

```bash
research-digest fetch      # pull recent papers from arXiv
research-digest embed      # optional: build vectors for similarity search
research-digest status     # see what you have
```

`fetch` is safe to run daily. It only adds papers you have not seen. Run it a
few times over a week and the trends view starts to mean something.

## Use it

```bash
research-digest search agentic evaluation
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
| `library_status` | Paper count, date range, embedding health |

---

## How ranking works

No model decides the order. The score is arithmetic you can check:

| | |
|---|---|
| a multi-word topic appearing verbatim | 1.00 |
| a single distinctive word | 0.60 |
| a single common word | 0.20 |
| three or more of your topics matched | +0.30 |
| exactly two matched | +0.15 |
| published today, decaying to zero over 30 days | +0.20 |

The 0.20 for common words is the part that matters. A word like "learning"
appears in nearly every paper in this field, so matching it tells you almost
nothing. Without that discount the ranking fills up with noise.

Every result carries its own derivation, in the MCP response and in the web
interface. The "How scoring works" tab lets you edit a title, an abstract and
your topic list, and watch the arithmetic change.

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
pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

The MCP server is plain JSON-RPC over stdin and stdout, about 300 lines, with no
SDK. One JSON object per line each way, which makes it easy to drive from a
shell script when something looks wrong.

## License

MIT
