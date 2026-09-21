"""One question, one answer path.

The browser's question box and the `ask_library` MCP tool are the same feature
reached two ways, so they are the same function. Two implementations of "what
did the user mean" would drift within a week, and the one nobody was watching
would be the one giving the wrong answer.

The pipeline, in order, and every step is reported back so the answer can be
argued with:

  1. read the question by rule        (askparse.plan -- no model, always runs)
  2. optionally improve the plan      (llm.propose_plan -> askparse.validate)
  3. resolve the scope                (library / arxiv / workspace / saved)
  4. drop terms that match nothing    (scoring.live_terms -- the typo guard)
  5. rank with the arithmetic scorer  (scoring.rank_all_query -- never a model)

Step 5 is deliberately the same scorer as everything else in this tool. A model
may help decide *what* to search for. Nothing decides the order of your results
except arithmetic you can read.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import storage
from .askparse import plan as rule_plan
from .askparse import validate
from .scoring import about_sentence, live_terms, rank_all_query


def _row(paper: Dict[str, Any], saved=(), read=(), tiers=None) -> Dict[str, Any]:
    return {
        "saved": paper["id"] in saved,
        "read": paper["id"] in read,
        "id": paper["id"],
        "title": paper.get("title", ""),
        "url": paper.get("url", "") or f"https://arxiv.org/abs/{paper['id']}",
        "published": paper.get("published", ""),
        "category": paper.get("primary_category", ""),
        "about": about_sentence(paper),
        "concepts": (paper.get("concepts") or [])[:6],
        "score": paper.get("score", 0.0),
        "why": paper.get("why", {}),
        "why_text": paper.get("why_text", ""),
        "tier": paper.get("tier") or (tiers or {}).get(paper.get("primary_category"), ""),
    }


def build_plan(question: str, settings: Dict[str, Any],
               use_llm: bool = True) -> Dict[str, Any]:
    """The query plan, from rules and — when one is available — a local model.

    The rule plan is computed first and unconditionally, so it is always there
    to validate against and to fall back to. A model that is slow, absent,
    broken or hallucinating costs the plan nothing.
    """
    base = rule_plan(question)
    if not use_llm:
        return base
    try:
        from . import llm
        config = llm.settings_block(settings)
        if not config["enabled"]:
            return base
        status = llm.probe(settings)
        if status["status"] not in ("ready", "model_missing"):
            return base
        config["model"] = status["model"]
        proposed = llm.propose_plan(question, settings.get("topics") or [], config)
        if proposed is None:
            return base
        improved = validate(proposed, base)
        improved["model"] = status["model"]
        return improved
    except Exception:
        # A question must never fail because of the optional half of the feature.
        return base


def _scope_terms(plan: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Terms the scope itself contributes, plus how it was resolved.

    The workspace scope is the interesting one: the user does not supply the
    search terms, the workspace does. "Anything related to what I am building"
    is not a keyword search the asker could have typed.
    """
    if plan["scope"] != "workspace":
        return {"terms": [], "detail": None}

    from .workspace import WorkspaceUnavailable, configured_root, scan, search_terms
    root = configured_root(settings)
    if root is None:
        return {
            "terms": [],
            "detail": {
                "status": "not_configured",
                "message": ("No workspace folder is set, so there is nothing to read "
                            "your interests off. Set one in Profile and this question "
                            "will search the library for what your own projects are "
                            "about. Nothing is read until you name a folder."),
            },
        }
    try:
        result = scan(root)
    except WorkspaceUnavailable as exc:
        return {"terms": [], "detail": {"status": "error", "message": str(exc)}}
    return {"terms": search_terms(result), "detail": result}


def answer(question: str, settings: Dict[str, Any], limit: int = 25,
           use_llm: bool = True) -> Dict[str, Any]:
    """Answer a plain-English question from the library.

    Never raises on an ordinary miss: a question that names nothing, matches
    nothing, or names a workspace that is not configured all come back as a
    status the caller can render, with the plan attached so the reader can see
    what was actually searched for.
    """
    question = (question or "").strip()
    if not question:
        return {"status": "error", "message": "Ask me something.", "results": []}

    papers = storage.load_papers()
    plan = build_plan(question, settings, use_llm=use_llm)
    scope = _scope_terms(plan, settings)

    if scope["detail"] and scope["detail"].get("status") in ("not_configured", "error"):
        return {
            "status": "needs_workspace",
            "question": question, "plan": plan,
            "message": scope["detail"]["message"],
            "workspace": scope["detail"],
            "results": [],
        }

    # Workspace terms lead, because in a workspace question they are the
    # subject; whatever the asker typed alongside them narrows rather than
    # replaces.
    #
    # extra_terms are deliberately NOT searched. A model asked for related
    # words returns the user's whole standing profile, and because ranking is
    # by what fraction of the query a paper covers, every added word lowers
    # every real result by the same amount: "prompt injection this week" became
    # a nine-term query and the papers actually about prompt injection scored
    # 2/9. They are offered as follow-up searches instead, which is what a
    # suggestion should be -- a thing you can take, not a thing already done
    # to your query.
    candidates: List[str] = []
    for source in (scope["terms"], plan["terms"]):
        for term in source:
            if term not in candidates:
                candidates.append(term)
    # Capped for the same reason extra_terms are not searched: ranking is by
    # coverage, so a long term list makes every result look like a weak match
    # and flattens the ordering. Six is the point past which added terms cost
    # more precision than the recall they buy.
    candidates = candidates[:6]

    if not candidates:
        return {
            "status": "no_subject",
            "question": question, "plan": plan,
            "message": ("That reads as a question with no subject in it. Try naming "
                        "the thing you want: 'agent evaluation', 'prompt injection'."),
            "results": [],
        }

    if not papers:
        return {
            "status": "empty_library",
            "question": question, "plan": plan, "terms": candidates,
            "message": "The library is empty. Fetch some papers first.",
            "results": [],
        }

    live, dead = live_terms(papers, candidates)
    if not live:
        return {
            "status": "no_subject",
            "question": question, "plan": plan, "ignored_terms": dead,
            "message": (f"None of {', '.join(dead)} appears in any paper you hold. "
                        f"If that is a typo, fix it; if it is a real subject, arXiv "
                        f"search can go and get it."),
            "results": [],
        }

    from .config import load_profile, tier_index
    saved, read = storage.load_saved(), storage.load_read()
    tiers = tier_index(load_profile(settings))

    ranked = rank_all_query(papers, live)
    if plan.get("since"):
        floor = plan["since"]
        ranked = [p for p in ranked
                  if str(p.get("published") or p.get("first_seen") or "")[:10] >= floor]

    return {
        "status": "ok" if ranked else "no_match",
        "question": question,
        "plan": plan,
        "terms": live,
        "ignored_terms": dead,
        "scope": plan["scope"],
        "since": plan.get("since"),
        "workspace": scope["detail"],
        "searched": len(papers),
        "matched": len(ranked),
        "reading": reading_of(plan, live, dead, scope["detail"]),
        "related": [t for t in (plan.get("extra_terms") or []) if t not in live][:6],
        "results": [_row(p, saved, read, tiers) for p in ranked[:limit]],
    }


def reading_of(plan: Dict[str, Any], terms: List[str], dead: List[str],
               workspace: Optional[Dict[str, Any]]) -> str:
    """One sentence saying what the question was taken to mean.

    This is the part that makes an English search box trustworthy rather than
    magic: when the answer is wrong, the reader can see whether the question
    was misread or the library is simply thin, and those need opposite fixes.
    """
    bits = []
    if plan["scope"] == "workspace" and workspace:
        projects = ", ".join((workspace.get("projects") or [])[:3])
        bits.append(f"Read {workspace.get('files_read', 0)} files under "
                    f"{workspace.get('root', 'your workspace')}"
                    + (f" ({projects}…)" if projects else ""))
        bits.append(f"searched your library for {', '.join(terms)}")
    else:
        bits.append(f"Searched your library for {', '.join(terms)}")
    if plan.get("since"):
        bits.append(f"published on or after {plan['since']}")
    if dead:
        bits.append(f"ignored {', '.join(dead)} (in no paper you hold)")
    if plan.get("source") == "llm":
        bits.append(f"terms suggested by {plan.get('model', 'a local model')}, "
                    f"ranking by the usual arithmetic")
    return "; ".join(bits) + "."
