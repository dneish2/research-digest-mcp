# Security

## Reporting a problem

Use GitHub's private reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Please don't open a public issue for anything that
could be used against someone's machine. I'll reply within a week.

## What this tool trusts and what it doesn't

Research Digest runs on your machine with your permissions, and it is often
started by a coding agent. These are the boundaries it is built around.

**Paper text is untrusted.** Titles and abstracts come from arXiv, and anyone
can submit a paper. That text reaches your agent through the MCP tools, so it
can carry instructions aimed at the agent. The tools limit what such text could
do: nine of the twelve only read, none delete anything, and the three that write
(`save_paper`, `fetch_papers`, `get_digest`) can only add papers to the library
or write the day's digest file inside the library folder.

**The web interface is for this machine only.** It listens on 127.0.0.1 and
refuses requests whose Host is not this machine, whose Origin is another site,
or that the browser marks as cross-site. Other pages you have open cannot read
from it or write to it.

**Your LLM key stays on disk.** If you configure a model with an API key, the
key is stored in `settings.json` in your library folder and sent only to the
`base_url` you configured. The web interface reports whether a key is set but
never sends the key itself to the browser.

**No dependencies in the core.** The base install is the Python standard
library. numpy and scikit-learn are installed only if you ask for similarity
search.

**Network access goes to arXiv only**, over HTTPS with certificate checks and
timeouts, plus the model endpoint you configure, if any.

## Supported versions

Fixes go into the latest release.
