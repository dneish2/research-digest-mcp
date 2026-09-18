"""Turning a question into a query plan, with no model involved.

The search box accepts English. "Can you find me papers related to finance AI"
used to be tokenised as nine search terms, six of which were "can", "you",
"find", "me", "papers", "to" — 439 results led by a paper on healthcare
workforce readiness. The question words outnumbered the subject, so the subject
lost.

This module is the floor: a plain rule-based parser that strips the asking and
keeps the asked-about, decides where to look, and says in one sentence what it
decided. `llm.py` can produce a better plan when a local model is available, but
it produces *this same shape*, and it is validated against this module's output
before it is trusted. Nothing here needs a network, a key, or a model, so the
feature works identically on a machine with no LLM at all.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .scoring import _significant

# Ways people open a request. Stripped from the front of the question, longest
# first so "can you find me" wins over "find me". These are about the act of
# asking, never about the subject, so removing them cannot remove signal.
_OPENERS = (
    "can you please find me", "can you please show me", "could you find me",
    "can you find me", "can you show me", "can you find", "can you get me",
    "can you tell me", "can u find me", "can u find", "can u show me",
    "could you show me", "would you find me", "please find me", "please show me",
    "i am looking for", "i'm looking for", "im looking for", "i want to read",
    "i want to find", "i would like", "i'd like", "i want", "i need",
    "do you have", "do we have", "is there anything", "are there any",
    "what are some", "what are the", "what is the", "what's the", "whats the",
    "show me some", "show me any", "give me some", "give me", "show me",
    "find me some", "find me any", "find me", "find", "search for", "search",
    "look for", "lookup", "look up", "tell me about", "tell me",
    "how about", "what about", "anything on", "anything about", "anything",
    "any papers", "any work", "any research",
)

# Noise that survives the opener strip and describes the container, not the
# subject: "papers about X" is a search for X.
_CONNECTORS = (
    "papers related to", "papers relating to", "papers regarding",
    "research related to", "work related to", "anything related to",
    "anything relating to", "something related to", "stuff related to",
    "papers about", "papers on", "papers for", "papers in", "papers that",
    "research about", "research on", "research into", "work about", "work on",
    "articles about", "articles on", "literature on", "literature about",
    "related to", "relating to", "regarding", "about", "on the topic of",
    "papers", "paper", "research", "articles", "article", "publications",
)

# Where to look. The default is the library; these are the phrases that say
# otherwise. Matched against the whole question, before any stripping, because
# "anything new on arXiv" puts the signal in a word the opener strip would eat.
_ARXIV_CUES = (
    "on arxiv", "from arxiv", "arxiv", "newly published", "just published",
    "new papers", "newest", "latest", "recent", "recently", "this week",
    "today", "brand new", "fresh", "beyond my library", "not in my library",
    "outside my library", "grow my library", "anything new",
)
_WORKSPACE_CUES = (
    "my workspace", "my local workspace", "local workspace", "my machine",
    "my computer", "my laptop", "my code", "my codebase", "my repos",
    "my repositories", "my repo", "my projects", "my project", "what i am building",
    "what i'm building", "what im building", "what i am working on",
    "what i'm working on", "what im working on", "on disk", "locally",
)
_SAVED_CUES = (
    "my saved", "i saved", "i have saved", "my bookmarks", "bookmarked",
    "my shelf", "my reading list", "my queue",
)

# Relative date language -> a submittedDate floor. Only the unambiguous ones:
# guessing at "lately" would be inventing a number and calling it a filter.
_WINDOWS = (
    (("today",), 1),
    (("yesterday",), 2),
    (("this week", "past week", "last week", "last 7 days", "past 7 days"), 7),
    (("two weeks", "last 14 days", "past fortnight", "fortnight"), 14),
    (("this month", "past month", "last month", "last 30 days", "past 30 days"), 30),
    (("last three months", "past three months", "last 90 days", "this quarter"), 90),
    (("this year", "past year", "last year", "last 12 months"), 365),
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_PUNCT = re.compile(r"[?!.,;:\"“”‘’]+")

# Every individual word used by a scope or date cue. These words told the
# parser *where* to look; they are not things to look for. A model shown the
# question "anything on my local workspace" duly proposed "local" and
# "workspace" as search terms, and because they appear in the question the
# re-wording check let them through -- so the library was searched for the
# word "workspace". A cue is consumed by the step that reads it.
CUE_WORDS = {
    word
    for phrase in _ARXIV_CUES + _WORKSPACE_CUES + _SAVED_CUES
    for word in phrase.split()
} | {"papers", "paper", "research", "article", "articles", "publication",
     "publications", "related", "relating", "regarding", "topic", "topics",
     "read", "reading", "something", "anything", "everything", "stuff"}


def _strip_prefixes(text: str, prefixes) -> str:
    """Remove any leading phrase from `prefixes`, repeatedly.

    Repeatedly, because real questions stack them: "can you find me any papers
    about X" is an opener, then a determiner, then a connector.
    """
    changed = True
    while changed:
        changed = False
        for prefix in sorted(prefixes, key=len, reverse=True):
            if text == prefix:
                return ""
            if text.startswith(prefix + " "):
                text = text[len(prefix) + 1:].strip()
                changed = True
                break
    return text


def _month_floor(text: str, today: date) -> Optional[str]:
    """"in July", "since March 2026" -> the first of that month."""
    match = re.search(r"\b(?:in|since|from|during)\s+([a-z]{3,9})\.?\s*(\d{4})?\b", text)
    if not match:
        return None
    month = _MONTHS.get(match.group(1))
    if not month:
        return None
    year = int(match.group(2)) if match.group(2) else today.year
    # "in July" spoken in March means last July, not a July that has not happened.
    if not match.group(2) and month > today.month:
        year -= 1
    return date(year, month, 1).isoformat()


def detect_since(question: str, today: Optional[date] = None) -> Optional[str]:
    """The earliest submission date the question asks for, or None."""
    today = today or date.today()
    low = question.lower()
    explicit = re.search(r"\bsince\s+(\d{4}-\d{2}-\d{2})\b", low)
    if explicit:
        return explicit.group(1)
    for phrases, days in _WINDOWS:
        if any(p in low for p in phrases):
            return (today - timedelta(days=days)).isoformat()
    return _month_floor(low, today)


def detect_scope(question: str) -> str:
    """Where to look: 'library' (default), 'arxiv', 'workspace', or 'saved'.

    Workspace wins over arXiv when both are mentioned, because "what on arXiv
    relates to my workspace" is a workspace question first: the workspace is
    what supplies the terms, and where to search is the second step.
    """
    low = f" {question.lower()} "
    if any(cue in low for cue in _WORKSPACE_CUES):
        return "workspace"
    if any(cue in low for cue in _SAVED_CUES):
        return "saved"
    if any(cue in low for cue in _ARXIV_CUES):
        return "arxiv"
    return "library"


# Judgement words that describe how much the asker wants the thing, not what
# the thing is. "the best papers on X" and "papers on X" are the same search.
_QUALIFIERS = (
    "any", "some", "all", "the", "a", "an", "new", "recent", "best", "good",
    "great", "top", "latest", "newest", "interesting", "important", "key",
    "relevant", "useful", "notable", "seminal", "classic", "cool", "solid",
)


def subject_of(question: str) -> str:
    """The question with the asking removed: what is actually being asked about."""
    text = _PUNCT.sub(" ", question.lower())
    text = " ".join(text.split())
    text = _strip_prefixes(text, _OPENERS)
    text = _strip_prefixes(text, _QUALIFIERS)
    text = _strip_prefixes(text, _CONNECTORS)
    text = _strip_prefixes(text, _QUALIFIERS)
    # Container words in the middle too: "the best papers on X" only loses
    # "papers on" if the strip is not anchored to the front of the string.
    for connector in sorted(_CONNECTORS, key=len, reverse=True):
        text = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(connector), " ", text)
    text = " ".join(text.split())
    text = _strip_prefixes(text, _QUALIFIERS)
    # Scope and date language has done its job by now and is not subject matter.
    for cue in sorted(_ARXIV_CUES + _WORKSPACE_CUES + _SAVED_CUES, key=len, reverse=True):
        text = text.replace(cue, " ")
    for phrases, _ in _WINDOWS:
        for phrase in phrases:
            text = text.replace(phrase, " ")
    text = re.sub(r"\b(?:in|since|from|during)\s+(?:%s)\.?(?:\s+\d{4})?\b"
                  % "|".join(_MONTHS), " ", text)
    text = re.sub(r"\bsince\s+\d{4}-\d{2}-\d{2}\b", " ", text)
    return " ".join(text.split()).strip(" -")


def looks_like_a_question(text: str) -> bool:
    """Is this English asking for something, or is it just search terms?

    Used to decide whether to *offer* the question path, never to refuse the
    plain one. Getting this wrong costs a suggestion, not a result.
    """
    low = text.strip().lower()
    if not low:
        return False
    if low.endswith("?"):
        return True
    words = low.split()
    if len(words) < 4:
        return False
    first = words[0]
    if first in ("can", "could", "would", "what", "which", "who", "how", "where",
                 "when", "why", "is", "are", "do", "does", "did", "show", "find",
                 "give", "tell", "search", "look", "any", "anything", "i"):
        return True
    return any(cue in low for cue in ("related to", "papers about", "papers on",
                                      "looking for", "anything about"))


def plan(question: str, today: Optional[date] = None) -> Dict[str, Any]:
    """A query plan from a question, using rules only.

    Always returns a usable plan. When the question turns out to be nothing but
    scaffolding ("can you find me some papers"), `terms` comes back empty and
    the caller shows the profile feed rather than pretending to have searched
    for something.
    """
    question = (question or "").strip()
    scope = detect_scope(question)
    since = detect_since(question, today)
    subject = subject_of(question)
    terms = [t for t in _significant(subject.split()) if t not in CUE_WORDS]
    return {
        "question": question,
        "subject": subject,
        "terms": terms,
        "extra_terms": [],
        "scope": scope,
        "since": since,
        "source": "rules",
    }


SCOPES = ("library", "arxiv", "workspace", "saved")


def validate(candidate: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce an untrusted plan (from a local model) into a plan we will run.

    A model is allowed to *propose* which words to search for. It is not
    allowed to decide the shape of the request, invent a scope, hand back a
    date that is not a date, or return five hundred terms. Every field is
    checked against the rule-based plan that would have run anyway, and
    anything that does not check out falls back to it field by field rather
    than throwing the whole suggestion away.

    This is the difference between using a model and trusting one.
    """
    if not isinstance(candidate, dict):
        return dict(fallback)

    out = dict(fallback)
    out["source"] = "llm"

    # The invariant: a model may re-word the question, not re-topic it.
    #
    # Shown the user's standing interests for context, a model returns them as
    # "terms" -- asked about prompt injection it answered prompt, injection,
    # evaluation, reliability, hallucination. Ranking is by what fraction of
    # the query a paper covers, so those three extra words pushed every paper
    # actually about prompt injection from 2/2 down to 2/5 and the ordering
    # went soft. Words the model produces that are not in the question and not
    # in the rule plan are demoted to suggestions, which the caller offers as
    # follow-up searches rather than silently adding to the search you asked
    # for. Prompting for this would be a request; this is a mechanism.
    asked = set(_significant(re.sub(r"[^a-z0-9\- ]", " ", (fallback.get("question") or "").lower()).split()))
    asked |= set(fallback.get("terms") or [])

    terms = candidate.get("terms")
    if isinstance(terms, str):
        terms = terms.split()
    volunteered = []
    if isinstance(terms, list):
        clean = [t for t in _significant([str(t) for t in terms if str(t).strip()][:12])
                 if t not in CUE_WORDS]
        kept = [t for t in clean if t in asked or any(t in a or a in t for a in asked)]
        volunteered = [t for t in clean if t not in kept]
        if kept:
            out["terms"] = kept[:6]

    extra = candidate.get("extra_terms") or candidate.get("related_terms")
    if isinstance(extra, str):
        extra = extra.split(",")
    extra_clean = _significant([str(t) for t in (extra or []) if str(t).strip()][:12],
                               split_compounds=False)
    out["extra_terms"] = [t for t in dict.fromkeys(volunteered + extra_clean)
                          if t not in out["terms"]][:8]

    # `scope` is deliberately NOT taken from the model, whatever it returned.
    # Scope decides whether your question causes a network request to arXiv,
    # and a model asked about "finance ai" answered "arxiv" for a question
    # containing no word about anything being new. Which words to search for is
    # a judgement call worth a model; whether to go online is not, and the rule
    # parser reads that off unambiguous cues for free.

    since = str(candidate.get("since", "") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        try:
            # A model asked for "recent" will cheerfully answer with next year,
            # and a future floor silently matches nothing at all.
            if date.fromisoformat(since) <= date.today():
                out["since"] = since
        except ValueError:
            pass

    # The terms are the one thing that must survive. If the model returned a
    # plan with nothing to search for, it is not a plan.
    if not out["terms"]:
        return dict(fallback)
    return out
