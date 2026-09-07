"""Turning papers into vectors.

Two engines, one contract: given every paper, return a vector per paper, all
fitted together in a single pass.

  tfidf-svd   (default) TF-IDF over title+abstract, then SVD down to 384
              columns. Needs scikit-learn. Fits the corpus, so it is rebuilt
              whole every time.
  minilm      sentence-transformers, all-MiniLM-L6-v2. A fixed basis that does
              not depend on your corpus, at the cost of a ~90 MB download.

Both stamp a basis fingerprint into the store. Two runs of tfidf-svd over
different corpora produce different fingerprints and are never mixed.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

TARGET_DIMS = 384


class EncoderUnavailable(RuntimeError):
    """The requested engine is not installed. The message says how to get it."""


def _corpus_text(paper: Dict[str, Any]) -> str:
    return f"{paper.get('title', '')} {paper.get('abstract', '')}".strip()


def _basis_fingerprint(engine: str, ids: List[str], dims: int) -> str:
    """Identifies this particular fit: engine, corpus membership, and width."""
    digest = hashlib.sha256()
    digest.update(engine.encode())
    digest.update(str(dims).encode())
    for pid in sorted(ids):
        digest.update(pid.encode())
    return digest.hexdigest()[:16]


def _encode_tfidf(texts: List[str]) -> Tuple[Any, int]:
    try:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:
        raise EncoderUnavailable(
            "The tfidf-svd engine needs scikit-learn. Install the extra:\n"
            "    pip install 'research-digest-mcp[embeddings]'"
        ) from exc
    import numpy as np

    vectorizer = TfidfVectorizer(
        max_features=4096, stop_words="english", sublinear_tf=True,
        min_df=2, ngram_range=(1, 2),
    )
    sparse = vectorizer.fit_transform(texts)

    # SVD cannot produce more columns than the data has rank. Rather than
    # silently narrowing, we cap and report the width we actually achieved.
    width = min(TARGET_DIMS, sparse.shape[1] - 1, sparse.shape[0] - 1)
    if width < 2:
        raise EncoderUnavailable(
            f"Only {sparse.shape[0]} papers with {sparse.shape[1]} usable terms. "
            f"Fetch more papers before embedding (about 50 is enough)."
        )
    svd = TruncatedSVD(n_components=width, random_state=0)
    return svd.fit_transform(sparse).astype(np.float32), width


def _encode_minilm(texts: List[str]) -> Tuple[Any, int]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EncoderUnavailable(
            "The minilm engine needs sentence-transformers. Install it with:\n"
            "    pip install sentence-transformers"
        ) from exc
    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    vectors = model.encode(texts, batch_size=32, show_progress_bar=False)
    return vectors, int(vectors.shape[1])


ENGINES = {"tfidf-svd": _encode_tfidf, "minilm": _encode_minilm}


def embed_all(papers: List[Dict[str, Any]], engine: str = "tfidf-svd") -> Dict[str, Any]:
    """Fit one basis over every paper and return vectors plus the basis identity."""
    if engine not in ENGINES:
        raise EncoderUnavailable(
            f"Unknown engine {engine!r}. Available: {', '.join(sorted(ENGINES))}.")

    usable = [p for p in papers if p.get("id") and _corpus_text(p)]
    if not usable:
        return {"vectors": {}, "engine": engine, "basis": "", "dims": 0, "count": 0}

    ids = [p["id"] for p in usable]
    matrix, dims = ENGINES[engine]([_corpus_text(p) for p in usable])
    return {
        "vectors": {pid: matrix[i] for i, pid in enumerate(ids)},
        "engine": engine,
        "basis": _basis_fingerprint(engine, ids, dims),
        "dims": dims,
        "count": len(ids),
    }
