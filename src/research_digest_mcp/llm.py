"""An optional local model, for turning a question into a better query plan.

Three rules, in order of importance:

1. Everything works without it. The question box, the scopes, the ranking and
   every tool answer exist without a model and do not change shape when one
   appears. A model makes the *plan* better; it never makes the result
   possible. If you have no LLM, nothing here nags you and nothing is hidden.

2. It is local. The default is Ollama on this machine. Your questions, your
   library and your workspace terms do not leave the machine, which is the only
   basis on which the workspace scope is offered at all.

3. Its output is checked, not trusted. The model returns JSON; `askparse.validate`
   coerces every field against the rule-based plan and falls back per field.
   The model proposes search terms. The scorer -- arithmetic, inspectable,
   unchanged -- still decides the ranking. A model never orders your results.

Probing is cheap and is allowed to fail: one request, a short timeout, and a
status string the UI can render as a single quiet line.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
PROBE_TIMEOUT = 1.5
GENERATE_TIMEOUT = 25.0

# Ollama model names we would pick automatically, best first. Small ones on
# purpose: this job is rewriting a sentence into six search terms, and a 3B
# model does it well. Asking someone to pull 40GB to improve a search box
# would be a worse experience than the rule-based parser they already have.
PREFERRED = ("qwen2.5:7b", "qwen2.5:3b", "llama3.2:3b", "llama3.1:8b",
             "mistral:7b", "phi3.5", "gemma2:9b", "qwen2.5-coder:7b")

INSTALL_HINT = (
    "Install Ollama from ollama.com, then run 'ollama pull qwen2.5:3b'. "
    "The question box works without it — a model only improves how your "
    "question is turned into search terms."
)


def settings_block(settings: Dict[str, Any]) -> Dict[str, Any]:
    """The llm section of settings.json, with defaults filled in."""
    raw = settings.get("llm") or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "base_url": str(raw.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        "model": str(raw.get("model") or ""),
        "dismissed": bool(raw.get("dismissed", False)),
    }


def _get_json(url: str, timeout: float) -> Optional[Any]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def probe(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Is there a local model, and which one would we use?

    Never raises, never blocks for long, and distinguishes the three states
    that need different words in the UI: switched off by the user, nothing
    listening, and listening but with no models pulled.
    """
    config = settings_block(settings)
    out: Dict[str, Any] = {
        "status": "off",
        "enabled": config["enabled"],
        "base_url": config["base_url"],
        "model": config["model"],
        "models": [],
        "dismissed": config["dismissed"],
        "hint": INSTALL_HINT,
    }
    if not config["enabled"]:
        out["message"] = ("Turned off in settings. Questions are parsed by rule, "
                          "which needs no model.")
        return out

    payload = _get_json(config["base_url"] + "/api/tags", PROBE_TIMEOUT)
    if payload is None:
        out["status"] = "absent"
        out["message"] = (f"No local model server answering at {config['base_url']}. "
                          f"Questions are parsed by rule instead, which works fine.")
        return out

    models = [str(m.get("name", "")) for m in (payload.get("models") or []) if m.get("name")]
    out["models"] = models
    if not models:
        out["status"] = "no_models"
        out["message"] = ("A model server is running but has no models pulled. "
                          "Run 'ollama pull qwen2.5:3b'.")
        return out

    chosen = config["model"] if config["model"] in models else pick_model(models)
    out["model"] = chosen
    out["status"] = "ready"
    out["message"] = f"Using {chosen} to read your questions. Ranking stays arithmetic."
    if config["model"] and config["model"] not in models:
        out["status"] = "model_missing"
        out["message"] = (f"{config['model']} is configured but not pulled. "
                          f"Falling back to {chosen}.")
    return out


def pick_model(models: List[str]) -> str:
    """The best available model from PREFERRED, else the first one installed."""
    for want in PREFERRED:
        for have in models:
            if have == want or have.startswith(want + "-") or have == want + ":latest":
                return have
    for want in PREFERRED:
        base = want.split(":")[0]
        for have in models:
            if have.split(":")[0] == base:
                return have
    return models[0]


PLAN_PROMPT = """You turn a researcher's question into search terms for their \
personal arXiv library. Reply with JSON only, no prose.

Question: {question}

Their standing interests: {topics}

JSON fields:
  terms        2-5 lowercase search words taken from the QUESTION ITSELF, with \
the asking removed. Do not add subjects the question did not name, and do not \
copy the standing interests into this field.
  extra_terms  up to 6 adjacent words the asker did not say but might want next.
  scope        one of: library, arxiv, workspace, saved
  since        YYYY-MM-DD if the question asks for a time window, else ""

scope is "library" unless the question asks for something new or not yet held \
("arxiv"), asks about their own machine or code ("workspace"), or asks about \
what they bookmarked ("saved").

JSON:"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in a model's reply.

    Models add a sentence before the JSON, or wrap it in a fence, however
    firmly they are asked not to. That is exactly why the reply is parsed and
    validated rather than instructed: the instruction is a request, the parser
    is the mechanism.
    """
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:i + 1])
                except ValueError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def propose_plan(question: str, topics: List[str], config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ask the local model for a query plan. None on any failure at all.

    Returns the raw parsed object. The caller validates it — this function
    deliberately does not, so that the trust boundary sits in one place
    (askparse.validate) and cannot be bypassed by adding another caller.
    """
    model = config.get("model")
    base = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    if not model:
        return None
    body = json.dumps({
        "model": model,
        "prompt": PLAN_PROMPT.format(
            question=question, topics=", ".join(topics[:20]) or "not set"),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 300},
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=GENERATE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return _extract_json(str(payload.get("response", "")))
