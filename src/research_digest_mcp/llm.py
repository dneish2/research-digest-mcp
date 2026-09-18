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
    "The question box works without it. A model only improves how your "
    "question gets turned into search terms."
)


# Anything that speaks one of these two shapes works. Between them they cover
# Ollama, LM Studio, llama.cpp's server, vLLM, OpenAI, Anthropic-compatible
# gateways, OpenRouter, Together, Groq, and whatever comes next, because they
# all settled on the same request body.
PROVIDERS = {
    "ollama": {
        "label": "Ollama (local)",
        "blurb": "A model running on this machine. Nothing is sent anywhere.",
        "default_url": DEFAULT_BASE_URL,
        "needs_key": False,
        "local": True,
        "setup": "Install Ollama from ollama.com, then run: ollama pull qwen2.5:3b",
    },
    "openai-compatible": {
        "label": "OpenAI-compatible endpoint",
        "blurb": ("Anything speaking the /v1/chat/completions shape. LM Studio, "
                  "llama.cpp, vLLM and Jan run this locally. OpenAI, OpenRouter, "
                  "Groq and Together run it as a paid service."),
        "default_url": "http://127.0.0.1:1234/v1",
        "needs_key": True,
        "local": False,
        "setup": ("Point it at the base URL your server prints, ending in /v1. "
                  "A key is only needed for a hosted service."),
    },
}

LOCAL_HOST = re.compile(r"^https?://(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(:\d+)?(/|$)")


def is_local(base_url: str) -> bool:
    """Is this endpoint on this machine? Decides what the UI is allowed to promise."""
    return bool(LOCAL_HOST.match(str(base_url or "").strip()))


def settings_block(settings: Dict[str, Any]) -> Dict[str, Any]:
    """The llm section of settings.json, with defaults filled in."""
    raw = settings.get("llm") or {}
    provider = str(raw.get("provider") or "ollama")
    if provider not in PROVIDERS:
        provider = "ollama"
    base_url = str(raw.get("base_url") or PROVIDERS[provider]["default_url"]).rstrip("/")
    return {
        "enabled": bool(raw.get("enabled", True)),
        "provider": provider,
        "base_url": base_url,
        "model": str(raw.get("model") or ""),
        "api_key": str(raw.get("api_key") or ""),
        "dismissed": bool(raw.get("dismissed", False)),
        "local": is_local(base_url),
    }


def _get_json(url: str, timeout: float, key: str = "") -> Optional[Any]:
    try:
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def list_models(config: Dict[str, Any]) -> Optional[List[str]]:
    """What the configured endpoint says it can run, or None if it did not answer."""
    base, key = config["base_url"], config.get("api_key", "")
    if config["provider"] == "ollama":
        payload = _get_json(base + "/api/tags", PROBE_TIMEOUT)
        if payload is None:
            return None
        return [str(m.get("name", "")) for m in (payload.get("models") or [])
                if m.get("name")]
    payload = _get_json(base + "/models", PROBE_TIMEOUT, key)
    if payload is None:
        return None
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [str(r.get("id", "")) for r in (rows or []) if isinstance(r, dict) and r.get("id")]


def probe(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Is a model reachable, and which one would we use?

    Never raises, never blocks for long, and separates the states that need
    different words on screen: switched off, nothing listening, listening with
    nothing installed, and ready.
    """
    config = settings_block(settings)
    provider = PROVIDERS[config["provider"]]
    out: Dict[str, Any] = {
        "status": "off",
        "enabled": config["enabled"],
        "provider": config["provider"],
        "provider_label": provider["label"],
        "base_url": config["base_url"],
        "model": config["model"],
        "models": [],
        "local": config["local"],
        "has_key": bool(config["api_key"]),
        "dismissed": config["dismissed"],
        "hint": provider["setup"],
        # The one sentence that has to be right, because it is the promise the
        # workspace feature is offered on. It follows the URL, not a claim.
        "privacy": ("Your questions stay on this machine."
                    if config["local"] else
                    f"Your questions are sent to {config['base_url']}. Your papers "
                    f"and your workspace files are not."),
    }
    if not config["enabled"]:
        out["message"] = ("Turned off. Questions are read by rule instead, "
                          "which needs no model.")
        return out

    models = list_models(config)
    if models is None:
        out["status"] = "absent"
        out["message"] = (f"Nothing answered at {config['base_url']}. "
                          f"Questions are read by rule instead, which works fine.")
        return out

    out["models"] = models
    if not models and not config["model"]:
        out["status"] = "no_models"
        out["message"] = ("The endpoint answered but lists no models. "
                          + provider["setup"])
        return out

    # A hosted endpoint may refuse to list models while happily serving the one
    # you named. A configured name is therefore taken at its word.
    if config["model"]:
        out["model"] = config["model"]
        if models and config["model"] not in models:
            fallback = pick_model(models)
            out["model"] = fallback
            out["status"] = "model_missing"
            out["message"] = (f"{config['model']} is not available here. "
                              f"Using {fallback} instead.")
            return out
    else:
        out["model"] = pick_model(models)

    out["status"] = "ready"
    out["message"] = (f"{out['model']} is reading your questions into search terms. "
                      f"It does not rank anything.")
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
    prompt = PLAN_PROMPT.format(
        question=question, topics=", ".join(topics[:20]) or "not set")
    headers = {"Content-Type": "application/json"}

    if config.get("provider", "ollama") == "ollama":
        url = base + "/api/generate"
        body = {"model": model, "prompt": prompt, "stream": False, "format": "json",
                "options": {"temperature": 0, "num_predict": 300}}
    else:
        url = base + "/chat/completions"
        body = {"model": model, "temperature": 0, "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}]}
        if config.get("api_key"):
            headers["Authorization"] = "Bearer " + config["api_key"]

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=GENERATE_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    if config.get("provider", "ollama") == "ollama":
        return _extract_json(str(payload.get("response", "")))
    try:
        return _extract_json(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        return None
