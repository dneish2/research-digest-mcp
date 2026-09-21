"""Finding the word you meant, out of the words your library actually contains.

A search for `evalution` used to return nothing, say so honestly, and stop. The
honesty was an improvement on guessing, and stopping there still leaves the
reader to spot their own typo in a box they have already read three times.

The correction is drawn from the library's own vocabulary and nowhere else.
There is no dictionary, no model, and no list of known misspellings. A candidate
has to be a word that appears in papers you hold, which means every suggestion
is one that will return results, and the suggestion carries the count.

Two indexes, because two mistakes account for nearly all real typos:

  transposition   memroy for memory, evalaution for evaluation. Same letters in
                  a different order, so a key of the word's sorted letters finds
                  it in one lookup.

  one edit        a missing, extra or wrong character. Indexed by deleting each
                  character of every vocabulary word in turn: a query and a word
                  that are one edit apart always share a single-deletion form,
                  so this is a lookup rather than a scan.

Scanning was the obvious first approach and it is not viable: difflib over
40,000 words runs a sequence matcher per candidate, which is seconds per query.
These are dict lookups over a bounded neighbourhood, and the whole index builds
in about a second over 24,000 papers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")

# A word has to appear in this many papers before it can be suggested. Below it
# the "correction" is usually somebody else's typo, and offering one typo as the
# fix for another is worse than saying nothing.
MIN_DF = 3

# Only words this long get a deletion index. Short words have too many
# neighbours at one edit for a suggestion to mean anything: `rag` is one edit
# from `bag`, `rig`, `ran` and `ram`, and none of those is what you meant.
MIN_LENGTH = 5

# What counts as close enough, by length. A one character change in a four
# letter word is a different word; in a twelve letter word it is a slip.
def _max_edits(term: str) -> int:
    return 1 if len(term) < 8 else 2


_cache: Dict[str, Any] = {}


def _tokens(paper: Dict[str, Any]) -> set:
    text = " ".join(str(paper.get(field) or "") for field in
                    ("title", "abstract", "comment", "journal_ref"))
    text += " " + " ".join(paper.get("concepts") or [])
    text += " " + " ".join(paper.get("authors") or [])
    return set(_WORD.findall(text.lower()))


def _deletions(word: str) -> List[str]:
    return [word[:i] + word[i + 1:] for i in range(len(word))]


def build(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The vocabulary index. Cached on the library's size, which is the only
    thing that changes it: a fetch that adds nothing cannot add a word."""
    key = f"{len(papers)}"
    if _cache.get("key") == key:
        return _cache["index"]

    df: Dict[str, int] = {}
    for paper in papers:
        for token in _tokens(paper):
            df[token] = df.get(token, 0) + 1

    vocab = {word: n for word, n in df.items() if n >= MIN_DF}
    by_letters: Dict[str, List[str]] = {}
    by_deletion: Dict[str, List[str]] = {}
    for word in vocab:
        by_letters.setdefault("".join(sorted(word)), []).append(word)
        if len(word) >= MIN_LENGTH:
            for form in _deletions(word):
                by_deletion.setdefault(form, []).append(word)

    index = {"df": vocab, "letters": by_letters, "deletions": by_deletion,
             "papers": len(papers)}
    _cache.update(key=key, index=index)
    return index


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance counting a swap of two neighbours as one mistake.

    This started as plain Levenshtein and got `memroy` wrong, which is the
    typo the whole feature was reported against. Swapping two letters is two
    Levenshtein edits, so a six letter word sat outside a one edit budget and
    the suggestion was dropped. It is one keystroke, so it costs one here.

    Written out rather than imported because the tool is standard library only,
    and it abandons the comparison once it passes `limit`, since the answer is
    only ever used as "close enough or not".
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    # Three rows, because a transposition needs the row before the previous one.
    before: Optional[List[int]] = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            cost = min(previous[j] + 1, current[j - 1] + 1,
                       previous[j - 1] + (ca != cb))
            if (before is not None and i > 1 and j > 1
                    and ca == b[j - 2] and a[i - 2] == cb):
                cost = min(cost, before[j - 2] + 1)
            current.append(cost)
        if min(current) > limit:
            return limit + 1
        before, previous = previous, current
    return previous[-1]


def suggest(term: str, papers: List[Dict[str, Any]],
            limit: int = 3) -> List[Tuple[str, int]]:
    """Words in the library close to `term`, commonest first.

    Returns (word, papers containing it). Empty when nothing is close, which is
    the right answer for a word that is simply not in this field: a search for
    `photosynthesis` in an agents library should say so, not offer `synthesis`.
    """
    term = term.lower().strip()
    index = build(papers)
    vocab = index["df"]
    if not term or term in vocab:
        return []

    candidates = set(index["letters"].get("".join(sorted(term)), []))
    if len(term) >= MIN_LENGTH - 1:
        candidates.update(index["deletions"].get(term, []))
        for form in _deletions(term):
            candidates.update(index["deletions"].get(form, []))

    allowed = _max_edits(term)
    scored = []
    for word in candidates:
        if word == term:
            continue
        gap = _distance(term, word, allowed)
        if gap <= allowed:
            # Closer beats commoner, but among equally close words the one in
            # more of your papers is the better bet.
            scored.append((gap, -vocab[word], word))
    scored.sort()
    return [(word, vocab[word]) for _gap, _df, word in scored[:limit]]


def corrections(dead_terms: List[str], papers: List[Dict[str, Any]],
                limit: int = 3) -> List[Dict[str, Any]]:
    """One suggestion per query word that matched nothing."""
    out = []
    for term in dead_terms:
        hits = suggest(term, papers, limit=limit)
        if hits:
            out.append({
                "term": term,
                "suggestion": hits[0][0],
                "papers": hits[0][1],
                "others": [{"term": w, "papers": n} for w, n in hits[1:]],
            })
    return out


def repair(terms: List[str], fixes: List[Dict[str, Any]]) -> List[str]:
    """The query with each dead word replaced by its suggestion."""
    swap = {fix["term"]: fix["suggestion"] for fix in fixes}
    return [swap.get(term, term) for term in terms]
