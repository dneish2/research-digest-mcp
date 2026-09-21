"""Reading your own workspace for terms, so the library can be searched by it.

The question "is there anything here related to what I am actually building"
has no answer in a keyword box, because the answer depends on words you would
have to already know to type. This reads them off disk instead.

What it does NOT do matters as much as what it does:

  * it reads only a directory you named, never a default guess at your home
  * it reads prose and manifests -- markdown, pyproject, package.json, the
    first lines of source files -- never whole source trees
  * nothing it reads leaves the machine. The terms are matched locally against
    a fixed vocabulary and the counts are all that is returned. Even with a
    local model configured, the model is asked about your *question*, never
    handed your files.

The vocabulary is fixed on purpose. Free-form keyword extraction from source
code returns variable names, and a library search for "utils" is worse than no
search at all.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .scoring import CONCEPT_PATTERNS

# Directories that are never a description of what you are building.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "target",
    ".next", ".nuxt", ".cache", "site-packages", ".idea", ".vscode", "coverage",
    ".tox", ".gradle", "vendor", "Pods", ".terraform", "bin", "obj",
}

# Files that describe a project rather than implement it. Read in full (up to
# the byte cap); everything else is read only for its first few lines, where a
# module docstring or header comment lives.
PROSE_NAMES = {
    "readme.md", "readme.rst", "readme.txt", "readme",
    "claude.md", "agents.md", "contributing.md", "architecture.md",
    "design.md", "roadmap.md", "notes.md", "plan.md", "spec.md",
    "pyproject.toml", "package.json", "cargo.toml", "go.mod",
    "requirements.txt", "setup.py", "description.md", "overview.md",
}
PROSE_SUFFIXES = {".md", ".rst", ".txt"}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
                 ".rb", ".c", ".h", ".cpp", ".cs", ".swift", ".kt", ".sql", ".sh"}

MAX_FILES = 900
MAX_BYTES_PER_FILE = 60_000
CODE_HEAD_BYTES = 1_200
MAX_DEPTH = 6

# Vocabulary. CONCEPT_PATTERNS is what the papers are tagged with, so a term
# found here is a term that can actually match something; the additions are
# systems vocabulary that shows up in a working repo and in a methods paper
# but is not a paper "concept" tag.
EXTRA_VOCABULARY = (
    "agent", "agents", "router", "routing", "evaluation", "eval", "trace",
    "telemetry", "observability", "embedding", "embeddings", "vector",
    "retrieval", "rag", "llm", "prompt", "prompt injection", "guardrail",
    "policy", "sandbox", "provenance", "attribution", "citation", "grounding",
    "hallucination", "abstention", "calibration", "latency",
    "quantization", "inference", "fine-tune", "dataset", "annotation",
    "backtest", "portfolio", "options", "volatility", "earnings", "forecasting",
    "time series", "knowledge graph", "ontology", "entity resolution",
    "orchestration", "scheduler", "mcp", "tool use", "tool calling",
    "function calling", "reranking", "similarity", "clustering",
    "classification", "summarization", "translation", "speech", "vision",
    "multimodal", "reinforcement learning", "reward", "preference", "rlhf",
    "distillation", "compression", "pruning", "federated",
    "differential privacy", "simulation", "digital twin", "optimization",
    "causal inference", "significance", "power analysis", "provenance",
)

# Words a working repository is full of that say nothing about what you are
# researching. "search", "cache", "index" and "pipeline" led the first scan of
# a real workspace, and a library search for them is worse than no search.
GENERIC = {
    "search", "cache", "index", "pipeline", "graph", "training", "policy",
    "workflow", "streaming", "ranking", "experiment", "privacy", "solver",
    "heuristic", "encryption", "authentication", "authorization", "scraping",
    "crawler", "compression", "regression", "labeling", "confidence",
    "throughput", "caching", "vector",
}

VOCABULARY = tuple(t for t in dict.fromkeys(list(CONCEPT_PATTERNS) + list(EXTRA_VOCABULARY))
                   if t not in GENERIC)

# Word-boundary matching, built once. A plain `term in text` substring test is
# how "rag" scored 194 files on a workspace that has no RAG in it: it was
# matching "storage" and "average", and "eval" was matching "retrieval". A
# term that matches the middle of unrelated words is a term that will fetch
# unrelated papers, which is exactly the failure this feature exists to avoid.
_MATCHERS = {
    term: re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term))
    for term in VOCABULARY
}


class WorkspaceUnavailable(RuntimeError):
    """No workspace root is configured, or the configured one cannot be read."""


def configured_root(settings: Dict[str, Any]) -> Optional[Path]:
    """The workspace directory the user opted into, or None.

    Deliberately has no default. A tool that starts reading your home directory
    because you clicked a tab is not a feature.
    """
    raw = (settings or {}).get("workspace_root") or ""
    raw = str(raw).strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _readable(path: Path, limit: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except (OSError, ValueError):
        return ""


def _relevant_files(root: Path) -> List[Path]:
    """Prose and manifests first, then code heads, breadth-first and capped.

    Breadth-first so that a wide workspace of many projects contributes a
    little from each, rather than everything from whichever one sorts first.
    """
    found: List[Path] = []
    frontier = [(root, 0)]
    # Resolved directories already walked. A symlink pointing at an ancestor is
    # an infinite walk, and they are ordinary on macOS and Linux (and turn up
    # on Windows as junctions) -- a scan that never returns is a hung page.
    seen_dirs = set()
    while frontier and len(found) < MAX_FILES:
        current, depth = frontier.pop(0)
        if depth > MAX_DEPTH:
            continue
        try:
            key = current.resolve()
        except (OSError, RuntimeError):
            key = current
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if len(found) >= MAX_FILES:
                break
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    if entry.is_symlink():
                        continue
                    frontier.append((entry, depth + 1))
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            name = entry.name.lower()
            suffix = entry.suffix.lower()
            if name in PROSE_NAMES or suffix in PROSE_SUFFIXES or suffix in CODE_SUFFIXES:
                found.append(entry)
    return found


def scan(root: Path, limit: int = 25) -> Dict[str, Any]:
    """Count vocabulary terms across a workspace. Returns terms, not contents."""
    root = Path(root).expanduser()
    if not root.exists():
        raise WorkspaceUnavailable(f"{root} does not exist.")
    if not root.is_dir():
        raise WorkspaceUnavailable(f"{root} is not a directory.")

    files = _relevant_files(root)
    counts: Dict[str, int] = {}
    where: Dict[str, set] = {}
    projects: Dict[str, int] = {}
    read = 0

    for path in files:
        name = path.name.lower()
        is_prose = name in PROSE_NAMES or path.suffix.lower() in PROSE_SUFFIXES
        text = _readable(path, MAX_BYTES_PER_FILE if is_prose else CODE_HEAD_BYTES)
        if not text:
            continue
        read += 1
        low = text.lower()
        try:
            parts = path.relative_to(root).parts
            # A file sitting directly in the root is not its own project; it is
            # a note about the workspace. Counting it as one made "SYSTEM_BRIEF.md"
            # show up in the project list next to real repositories.
            project = parts[0] if len(parts) > 1 else "(workspace root)"
        except ValueError:
            project = path.parent.name
        hit_here = False
        for term in VOCABULARY:
            if _MATCHERS[term].search(low):
                counts[term] = counts.get(term, 0) + 1
                where.setdefault(term, set()).add(project)
                hit_here = True
        if hit_here:
            projects[project] = projects.get(project, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    terms = [{
        "term": term,
        "files": hits,
        # How spread out a term is matters more than how often it appears: a
        # term in one project is that project's jargon, a term across six is
        # what this workspace is actually about.
        "projects": sorted(where.get(term, ()))[:6],
        "spread": len(where.get(term, ())),
    } for term, hits in ranked[:limit]]

    return {
        "status": "ok",
        "root": str(root),
        "files_seen": len(files),
        "files_read": read,
        "projects": sorted(projects, key=lambda p: -projects[p])[:12],
        "terms": terms,
        "how": (f"Counted {len(VOCABULARY)} research terms across {read} prose files "
                f"and source headers under {root}. Nothing was sent anywhere; only "
                f"the counts below left the files."),
    }


def search_terms(result: Dict[str, Any], limit: int = 6) -> List[str]:
    """The workspace terms worth putting into a library search.

    Ordered by spread across projects first, then by file count. A term that
    every project mentions describes the workspace; a term one file mentions
    a hundred times describes that file.
    """
    terms = sorted(result.get("terms", []),
                   key=lambda t: (-t.get("spread", 0), -t.get("files", 0), t["term"]))
    return [t["term"] for t in terms[:limit]]
