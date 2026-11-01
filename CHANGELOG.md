# Changelog

## 1.0.0 (unreleased)

The first tagged release. Everything from the first commit (2026-09-07) up to
here, in short:

**Library and search**
- Fetch from arXiv's search API or its bulk harvest feed, with per-service
  rate-limit handling, and import a library from another machine.
- Keyword search that explains every score, with typo suggestions drawn only
  from words your library contains.
- `ask` for plain-English questions, optionally with a local model that may
  reword a question but never change its topic.
- A dated, reproducible daily digest.
- Similarity search over TF-IDF+SVD vectors (optional `[embeddings]` extra).
- Field trends counted over all of arXiv cs without downloading it.

**Agent tools**
- Twelve MCP tools over stdio. Nine read; `save_paper`, `fetch_papers` and
  `get_digest` write, and say so in their annotations. Result counts are capped.

**Measurement**
- A ranking eval on a frozen corpus with the clock pinned: precision@5 0.761,
  against 0.031 for newest-first. CI fails below 0.75.

**Security fixes before release** (found in a review on 2026-10-18)
- The digest date is validated, so it can no longer be used to write a file
  outside the library folder.
- The web interface refuses requests from other sites on every endpoint and
  rejects any Host that is not this machine (DNS rebinding).
- `/api/settings` no longer returns the LLM API key.
- `get_similar` accepts both `2610.22280` and `2610.22280v1`.
- An imported library can no longer carry a `javascript:` link into the page.
- `settings.json` is readable only by you on macOS and Linux, since it can hold
  an API key.

**Smaller fixes**
- `doctor` run through uvx prints a uvx config, instead of a path into uv's
  cache that breaks when the cache is cleaned.
- A harvest no longer crashes when arXiv sends Retry-After as a date.
- Unknown `/api/` paths return 404 instead of 200.
- "1 categories" reads "1 category".
