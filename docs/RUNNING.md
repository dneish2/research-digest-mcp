# Driving it yourself

A hands-on pass through every command and every screen, in the order that makes
sense the first time. Nothing here needs an API key, an account, or a model.

Throughout: `research-digest` and `python -m research_digest_mcp` are the same
program. Use whichever works in your shell — `doctor` will tell you which.

---

## 0. Is it wired up?

```
python -m research_digest_mcp doctor
```

This is the first thing to run and the first thing to run again when something
is odd. It answers, in order: which Python is serving, whether it is your
working tree or a stale copy pip made, where the library lives and whether it
is writable, when it last grew, whether your profile is shaped right, whether
arXiv is currently refusing you, whether similarity is built, whether you have
a local model, and — at the bottom — the exact line to paste into Claude Code,
Codex or Copilot.

Exit code is 0 if clean, 1 if there are warnings, 2 if something is broken, so
it works in a script.

Add `--network` to make exactly one arXiv request and confirm it answers.
Without the flag it makes none, so the doctor can never itself get you
rate-limited.

---

## 1. Fill the shelf

```
python -m research_digest_mcp fetch
```

Goes through your profile tier by tier and reports what it asked for, not just
what came back — "fetched 50, 0 new" is useless when what you need to know is
which tier produced the zero.

Missed a stretch of days? A plain fetch cannot reach backwards, because it
always starts from the newest paper. Give it a window:

```
python -m research_digest_mcp fetch --since 2026-07-01 --until 2026-07-31
```

arXiv answers a burst with HTTP 429 and this tool takes that seriously: one
request at a time, five seconds apart, and a refusal starts a five-minute
cooldown written to disk so the next run waits instead of digging the hole
deeper. If you are cooling down, `doctor` says so and says for how long.

---

## 2. Ask it things

Three different verbs, and the difference matters:

```
python -m research_digest_mcp search agent evaluation      # keywords, your shelf
python -m research_digest_mcp ask "what should I read about agent memory?"
python -m research_digest_mcp arxiv "speculative decoding"  # arXiv, ADDS to your shelf
```

`search` reads the shelf. `ask` reads the shelf too, but reads your *sentence*
first. `arxiv` is the only one that puts something new on the shelf.

`ask` prints what it decided before it prints any results:

```
read as: Searched your library for agent, memory; terms suggested by qwen2.5:7b,
         ranking by the usual arithmetic.
```

That line is the point. When an answer is wrong you need to know whether the
question was misread or the library is simply thin, and those need opposite
fixes. It also names any word it dropped:

```
read as: Searched your library for agent, memory; ignored memroy (in no paper you hold).
```

A word that appears in zero papers cannot promote anything, and because ranking
is by how much of your query a paper covers, it drags every real result down by
the same amount. So it is dropped, and saying so is how you find your own typo.

Add `--no-llm` to force the rule-based reading even when a model is configured.
Useful for checking that the model is earning its place.

---

## 3. Point it at your own work

Optional, off by default, and nothing on disk is read until you name a folder.

```
python -m research_digest_mcp workspace ~/Code
```

It reads README and manifest files and the first lines of source files under
that folder, counts a fixed vocabulary of research terms, and ranks them by how
many *projects* mention each — a term every project mentions describes your
workspace, a term one file mentions a hundred times describes that file. Then:

```
python -m research_digest_mcp ask "anything related to what I'm building?"
```

Set the folder permanently in the Profile tab of the web UI, or in
`settings.json` as `"workspace_root"`.

Nothing read here leaves the machine. Even with a local model configured, the
model is asked about your *question*; it is never shown your files.

---

## 4. The daily read

```
python -m research_digest_mcp digest
```

Five picks, one per category where possible, written to a dated markdown file
so you can read it outside any tool. Deterministic: same library and same
topics produce the same picks, which is what makes it worth skimming once a day
rather than re-rolling.

---

## 5. The browser

```
python -m research_digest_mcp web
```

Opens on <http://127.0.0.1:8756>. Localhost only — this is a personal library,
not a service.

- **Search box** — type keywords and press Enter. Type a *question* and press
  Enter and it will read it as one; Ctrl/⌘+Enter always does. Press `/` from
  anywhere to jump to the box; the little `/` badge in the box says so.
- **Papers / Digest / Trends / Queue / Profile / Scoring** — keys `1`–`6`.
- **`?`** — the full keyboard list.
- **Concept tags** on a card are buttons. Clicking one searches for it; it is
  the fastest way to move sideways through the library.
- **Tier badge** on a card says which tier of your profile brought it in.
- **Profile** is editable. Categories, topics, per-tier depth, your work
  context, your workspace folder. It writes `settings.json`, and the next fetch
  uses it.
- **Scoring** is a playground over the real scorer — change the topics or the
  abstract and watch the arithmetic move. Every paper's "why?" link lands here
  pre-filled with that paper.

---

## 6. Get your papers back out

```
python -m research_digest_mcp export --what saved --format bibtex -o refs.bib
python -m research_digest_mcp export --what queue --format markdown -o queue.md
python -m research_digest_mcp export --what all  --format json     -o backup.json
```

`--what` is `saved`, `queue` or `all`. The JSON form is what `import` reads, so
it is a real backup and moving a library between machines is two commands:

```
python -m research_digest_mcp export --what all --format json -o backup.json
python -m research_digest_mcp import backup.json     # on the other machine
```

The Queue tab and the Profile tab have the same three buttons.

---

## 7. Similarity (optional)

```
pip install "research-digest-mcp[embeddings]"
python -m research_digest_mcp embed
```

Fits one TF-IDF+SVD basis over the whole library and powers the "Similar" list
on each paper. Vectors from two different fits are never mixed — if it detects
more than one, similarity disables itself and tells you to re-embed rather than
quietly returning nonsense.

Re-run `embed` after a fetch adds papers, or the newest papers are the ones
missing from similarity search. `doctor` counts the gap for you.

---

## 8. A local model (optional)

Everything above works without one. A model does one job here: turning your
sentence into search terms. It never ranks anything.

```
# install ollama from ollama.com, then
ollama pull qwen2.5:3b
```

That is all — the tool finds it on `127.0.0.1:11434` by itself. `doctor` says
which model it picked.

The model server must be on this machine, and that is enforced in code, not
asked for in the settings copy: your questions and your workspace terms do not
go off-box.

Three guarantees hold whether or not you have one:

1. The rule-based reading always runs first, so a model that is slow, missing
   or broken costs the answer nothing.
2. A model may re-word your question. It may not re-topic it — words it adds
   that you did not say become *suggestions*, not part of your search.
3. A model never decides to go online. Whether a question hits arXiv is read
   off your own words.

To turn it off: `"llm": {"enabled": false}` in `settings.json`, or dismiss the
one-line strip in the UI.

---

## 9. Connect it to an agent

`doctor` prints the exact line, built from `sys.executable` rather than the
word "python", because two installs of this on one machine with no way to tell
them apart costs an afternoon.

```
claude mcp add research-digest -- "/path/from/doctor/python" -m research_digest_mcp mcp
```

Eleven tools. The three worth knowing about:

- `ask_library` — a question in English, same code path as the web box
- `fetch_papers` — searches arXiv itself and **adds** what it finds; this is
  the one that lets an agent grow your shelf instead of only reading it
- `suggest_profile_terms` — what you save that your profile never asks for

---

## 10. Running the tests

```
python -m unittest discover -s tests -v
```

No pytest, no plugins, no network. CI runs this on Ubuntu, Windows and macOS
against Python 3.9 and 3.12, and then runs it a second time with numpy,
scikit-learn and scipy uninstalled, because the core is supposed to work with
no third-party packages at all and it is easy to break that by accident.

The ranker has its own track:

```
python eval/eval-regression.py --assert-min 0.75
```

It reads a frozen corpus and a pinned phrase set from `eval/fixtures/`, never
your live library, so a drop is a ranker change and nothing else. CI fails the
build if precision@5 falls below the floor. See `docs/EVAL.md`.
