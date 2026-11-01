# Releasing

Publishing is a GitHub release. The `publish` workflow runs the tests, checks
that the tag matches the package version, builds, and uploads to PyPI. There is
no API token anywhere: PyPI trusts this repository's workflow directly.

## One-time setup

1. **PyPI**: sign in at pypi.org, open *Your projects* > *Publishing*, and add a
   pending publisher:
   - PyPI project name: `research-digest-mcp`
   - Owner: `dneish2`, repository: `research-digest-mcp`
   - Workflow: `publish.yml`, environment: `pypi`
2. **GitHub**: Settings > Environments > New environment, named `pypi`.
   Optionally add yourself as a required reviewer, so every upload waits for
   one click.

## Each release

1. Set the same version in `pyproject.toml`, `src/research_digest_mcp/__init__.py`,
   `SERVER_VERSION` in `src/research_digest_mcp/mcp.py`, both places in
   `server.json`, and `CITATION.cff`. `tests/test_release.py` fails if they
   disagree.
2. Move the CHANGELOG heading from "unreleased" to the date.
3. Merge, then create the release on GitHub with tag `v<version>`
   (for example `v1.0.0`). The workflow does the rest.

## After the first upload

- Switch the install lines in the README and `EXTRAS_INSTALL` in `config.py`
  from the `git+https://` form to `pip install research-digest-mcp` and
  `uvx research-digest-mcp mcp`.
- List it in the MCP Registry: install `mcp-publisher`, then
  `mcp-publisher login github` and `mcp-publisher publish` from the repo root.
  The registry checks that the PyPI description contains the `mcp-name` line
  at the top of the README.
