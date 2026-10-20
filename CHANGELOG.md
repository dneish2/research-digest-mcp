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
