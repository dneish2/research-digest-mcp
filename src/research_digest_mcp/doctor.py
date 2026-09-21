"""`research-digest doctor`: check the install and print the config to paste.

Everything that went wrong with this tool's setup was a diagnosis failure, not a
configuration failure. Nothing needed installing. What was needed was a straight
answer to four questions:

  which Python is answering, and is it the copy I just edited
  is the command on PATH, and if not, where is it
  does the library have anything in it, and when did it last grow
  what exact line do I paste into Claude Code or Codex

Two installs of this package on one machine, at different commit versions, with
no way to tell from any output which one served a request, cost hours. So the
first check here is identity, and the last thing printed is a config line built
from sys.executable rather than the word "python".

Read-only and safe to run repeatedly. It makes no network request unless asked.
"""
from __future__ import annotations

import io
import json
import os
import platform
import sys
import sysconfig
import tempfile
from pathlib import Path

from . import __version__
from .config import COOLDOWN_PATH, HOME, SETTINGS_PATH, load_profile, load_settings

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


class Report:
    """Collects check results so the exit code can reflect the worst one."""

    def __init__(self) -> None:
        self.worst = 0

    def line(self, level: str, label: str, detail: str = "", remedy: str = "") -> None:
        print(f"  [{level}] {label}")
        if detail:
            for row in str(detail).splitlines():
                print(f"         {row}")
        if remedy:
            print(f"         -> {remedy}")
        if level == WARN:
            self.worst = max(self.worst, 1)
        elif level == FAIL:
            self.worst = max(self.worst, 2)


def _install_kind() -> str:
    """Editable checkout, or a frozen snapshot pip copied in."""
    package = Path(__file__).resolve().parent
    for entry in sys.path:
        try:
            for pth in Path(entry).glob("__editable__*research_digest_mcp*"):
                if pth.is_file():
                    return "editable (live source)"
        except (OSError, ValueError):
            continue
    for parent in package.parents:
        if parent.name == "site-packages":
            direct = next(parent.glob("research_digest_mcp*/direct_url.json"), None)
            if direct:
                try:
                    info = json.loads(direct.read_text(encoding="utf-8"))
                    commit = (info.get("vcs_info") or {}).get("commit_id", "")
                    if commit:
                        return f"frozen snapshot from {commit[:10]}"
                except (OSError, ValueError):
                    pass
            return "frozen snapshot (a copy, not your working tree)"
    return "source checkout"


def _mcp_config_lines() -> str:
    exe = sys.executable
    toml_exe = exe.replace("\\", "\\\\")
    return (
        "  Claude Code\n"
        f'    claude mcp add research-digest -- "{exe}" -m research_digest_mcp mcp\n'
        "\n"
        "  Codex CLI   (~/.codex/config.toml)\n"
        "    [mcp_servers.research-digest]\n"
        f'    command = "{toml_exe}"\n'
        '    args = ["-m", "research_digest_mcp", "mcp"]\n'
        "\n"
        "  GitHub Copilot CLI   (~/.copilot/mcp-config.json)\n"
        '    "research-digest": {\n'
        f'      "command": {json.dumps(exe)},\n'
        '      "args": ["-m", "research_digest_mcp", "mcp"],\n'
        '      "tools": ["*"]\n'
        "    }"
    )


def run(check_network: bool = False) -> int:
    from . import storage

    report = Report()
    print("\nresearch-digest doctor\n")

    # 1. Which Python, and which copy of this package, is answering.
    print("Install")
    report.line(OK, f"python {platform.python_version()} on {platform.system()}",
                sys.executable)
    kind = _install_kind()
    level = WARN if kind.startswith("frozen") else OK
    report.line(level, f"research_digest_mcp {__version__}: {kind}",
                str(Path(__file__).resolve().parent),
                "This is a copy, not your working tree. Edits to the repo will not "
                "take effect here: reinstall with 'pip install -e .'"
                if level == WARN else "")

    # 2. Is the short command reachable, and if not, exactly where is it.
    from shutil import which
    found = which("research-digest")
    if found:
        report.line(OK, "'research-digest' is on PATH", found)
    else:
        scripts = {sysconfig.get_path("scripts"),
                   sysconfig.get_path("scripts", f"{os.name}_user")}
        where = "\n".join(sorted(s for s in scripts if s))
        report.line(WARN, "'research-digest' is not on PATH", where,
                    "Use 'python -m research_digest_mcp ...' instead, or add the "
                    "directory above to PATH. Nothing is broken either way.")

    # 3. The data directory.
    print("\nData")
    if "~" in str(HOME):
        report.line(FAIL, "RESEARCH_DIGEST_HOME contains a literal ~", str(HOME),
                    "A JSON or TOML env block is not shell-expanded. Write the full path.")
    elif not HOME.exists():
        report.line(WARN, "data directory does not exist yet", str(HOME),
                    "It is created on the first fetch.")
    else:
        try:
            # mkstemp hands back an OPEN descriptor. On Windows an open file
            # cannot be unlinked, so probing writability by unlinking without
            # closing reports every healthy directory as unwritable.
            handle, name = tempfile.mkstemp(dir=str(HOME), suffix=".probe")
            os.close(handle)
            Path(name).unlink()
            report.line(OK, "data directory is writable", str(HOME))
        except OSError as exc:
            report.line(FAIL, "data directory is not writable", f"{HOME}: {exc}")

    # 4. Does the library have anything in it, and is it still growing.
    try:
        papers = storage.load_papers()
    except Exception as exc:                      # a corrupt archive must not crash the doctor
        papers = []
        report.line(FAIL, "could not read the archive", str(exc))

    if not papers:
        report.line(WARN, "library is empty", "0 papers",
                    "Run 'python -m research_digest_mcp fetch'")
    else:
        archive = storage.read_json(storage.ARCHIVE_PATH, {}) or {}
        runs = archive.get("runs") or []
        last = runs[-1] if runs else None
        last = last.get("date") if isinstance(last, dict) else last
        detail = f"{len(papers)} papers, {len(runs)} runs recorded"
        if last:
            from datetime import date, datetime
            try:
                age = (date.today() - datetime.strptime(str(last)[:10], "%Y-%m-%d").date()).days
            except ValueError:
                age = None
            if age is not None and age > 7:
                report.line(WARN, f"last run was {age} days ago", detail,
                            "A scheduled fetch that stopped looks exactly like one that "
                            "is working. Run fetch, and check the scheduler.")
            else:
                report.line(OK, f"library last ran {last}", detail)
        else:
            report.line(WARN, "no run history recorded", detail)

    # 5. Settings and the interest profile.
    print("\nProfile")
    try:
        settings = load_settings()
    except ValueError as exc:
        report.line(FAIL, "settings.json is not readable", str(exc))
        settings = {}
    if settings:
        profile = load_profile(settings)
        tiered = bool(settings.get("profile"))
        shape = "three tiers" if tiered else "flat legacy shape (core tier only)"
        report.line(OK if tiered else WARN,
                    f"{len(profile['all_categories'])} categories, "
                    f"{len(profile['all_topics'])} topics, {shape}",
                    str(SETTINGS_PATH),
                    "" if tiered else
                    "Add a 'profile' block with core/complementary/stretch tiers to "
                    "fetch outside your own subject. See 'research-digest profile'.")
        empty = [c for c in profile["all_categories"]
                 if not any(p.get("primary_category") == c for p in papers)]
        if empty:
            report.line(WARN, f"{len(empty)} categories hold no papers",
                        ", ".join(empty),
                        "Configured and not delivering. Try "
                        "'fetch --since' to backfill, or drop them.")

    # 6. arXiv state. No request unless asked, so the doctor cannot itself throttle you.
    print("\narXiv")
    from .fetchers import cooldown_detail
    cool = cooldown_detail()
    left = cool["remaining"]
    if left > 0:
        report.line(WARN,
                    f"arXiv is refusing this machine, {left / 60:.0f} minutes left",
                    f"HTTP {cool.get('last_code')} at {cool.get('since')}, "
                    f"{cool.get('strikes')} refusal(s) in a row.\n"
                    f"{COOLDOWN_PATH}",
                    "This is a limit on how often this machine may ask arXiv. It is "
                    "not a problem with your library, your profile or your query: a "
                    "request for a single paper gets the same answer. Waiting is the "
                    "fix, and the wait lengthens each time it is retried early.")
    else:
        report.line(OK, "no cooldown active")
    if check_network:
        from .fetchers import ArxivUnavailable, fetch_category
        try:
            got = fetch_category("cs.AI", 1)
            report.line(OK, f"arXiv answered ({len(got)} paper)")
        except ArxivUnavailable as exc:
            report.line(WARN, "arXiv did not answer", str(exc))
    else:
        report.line(OK, "network check skipped", "", "Pass --network to make one request.")

    # 7. Embeddings.
    print("\nSimilarity")
    try:
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
        from .similarity import EmbeddingStore
        store = EmbeddingStore()
        vectors = store.count()
        bases = store.bases()
        if not vectors:
            report.line(WARN, "no vectors yet", "",
                        "Run 'python -m research_digest_mcp embed'")
        elif len(bases) > 1:
            # Two fits produce two incompatible 384-column spaces.
            report.line(WARN, f"{vectors} vectors from {len(bases)} different fits",
                        "Vectors from different fits are not comparable.",
                        "Run 'python -m research_digest_mcp embed' to rebuild in one pass")
        else:
            encoder, _basis, dims, _n = bases[0]
            detail = f"encoder {encoder}"
            behind = len(papers) - vectors
            if behind > 0:
                # Similarity search silently ignores whatever is not embedded,
                # so the newest papers are the ones missing from it.
                report.line(WARN, f"{vectors} vectors for {len(papers)} papers",
                            f"{behind} papers have no vector and cannot be found "
                            f"by similarity search.",
                            "Run 'python -m research_digest_mcp embed'")
            else:
                report.line(OK, f"{vectors} vectors, {dims} columns", detail)
    except ImportError:
        report.line(OK, "numpy/scikit-learn not installed",
                    "Everything except similarity search works without them.")
    except Exception as exc:
        report.line(WARN, "could not read the embedding store", str(exc))

    # 8. The optional local model. Optional means optional: not having one is
    #    an OK line, not a warning, because every feature works without it.
    print("\nQuestions")
    try:
        from . import llm
        state = llm.probe(settings or {})
        if state["status"] == "ready":
            report.line(OK, f"local model {state['model']}", state["base_url"],
                        "Used to read questions into search terms. It never orders results.")
        elif state["status"] == "model_missing":
            report.line(WARN, "the configured model is not pulled", state["message"])
        elif state["status"] == "no_models":
            report.line(WARN, "a model server is running with no models",
                        state["base_url"], "ollama pull qwen2.5:3b")
        elif state["status"] == "off":
            report.line(OK, "local model turned off", "",
                        "Questions are read by rule, which needs no model.")
        else:
            report.line(OK, "no local model", state["base_url"],
                        "Questions are read by rule instead. This is not a problem; "
                        "a model only improves how a question becomes search terms.")
    except Exception as exc:
        report.line(WARN, "could not check for a local model", str(exc))

    root = (settings or {}).get("workspace_root")
    if not root:
        report.line(OK, "no workspace folder set", "",
                    "Optional. Set one in the Profile tab to ask 'what relates to "
                    "what I am building?'. Nothing on disk is read until you do.")
    elif not Path(root).is_dir():
        report.line(WARN, "workspace folder does not exist", str(root),
                    "Fix it in the Profile tab, or clear it.")
    else:
        report.line(OK, "workspace folder set", str(root))

    # 9. The encoding bug that has bitten this project twice, pinned.
    print("\nEncoding")
    try:
        sample = {"t": "Schrödinger … 変分 — café"}
        raw = json.dumps(sample, ensure_ascii=False).encode("utf-8")
        assert json.loads(raw.decode("utf-8")) == sample
        stream = getattr(sys.stdout, "encoding", "") or ""
        if stream.lower().replace("-", "") == "utf8":
            report.line(OK, "stdout is UTF-8", stream)
        else:
            report.line(WARN, f"stdout reports {stream!r}", "",
                        "force_utf8_streams should have set this. A non-UTF-8 "
                        "stdout corrupts the MCP JSON-RPC stream silently.")
    except Exception as exc:
        report.line(FAIL, "encoding self-test failed", str(exc))

    print("\n" + "-" * 66)
    if report.worst == 0:
        print("Everything checks out. Paste this into your agent:\n")
    elif report.worst == 1:
        print("Usable, with the warnings above. Config for your agent:\n")
    else:
        print("Something is broken. Fix the FAIL lines first. For reference:\n")
    print(_mcp_config_lines())
    print()
    return report.worst
