"""Vector storage and cosine similarity.

Why this file is careful about one thing:

TF-IDF+SVD is fitted to a corpus. Each run derives its own 384 directions from
whatever documents it was given, so vectors from two runs are not comparable
even when both have 384 columns. The upstream version encoded new papers in
small incremental batches, which produced 16- and 17-column vectors alongside
384-column ones and made the whole index raise on load. Clamping the width would
have hidden that: same width, different meaning, silently wrong answers.

So the store records which encoder and which fit produced every row, and refuses
to mix. Rebuilding is cheap; a wrong answer you cannot see is not.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import EMBEDDINGS_DB, ensure_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    paper_id  TEXT PRIMARY KEY,
    vector    BLOB    NOT NULL,
    dims      INTEGER NOT NULL,
    encoder   TEXT    NOT NULL,
    basis     TEXT    NOT NULL,
    coded_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS vectors_basis ON vectors(encoder, basis);
"""


class MixedBasis(RuntimeError):
    """The store holds vectors from more than one fit. Re-embed to fix."""


class EmbeddingStore:
    def __init__(self, path: Optional[Path] = None):
        ensure_home()
        self.path = Path(path or EMBEDDINGS_DB)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        """Commit on success, and always close.

        sqlite3's own context manager commits but leaves the handle open, which
        on Windows keeps a lock on the file for the life of the process.
        """
        conn = sqlite3.connect(str(self.path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def bases(self) -> List[Tuple[str, str, int, int]]:
        """(encoder, basis, dims, rows) for every distinct fit present."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT encoder, basis, dims, COUNT(*) FROM vectors "
                "GROUP BY encoder, basis, dims ORDER BY COUNT(*) DESC"
            ).fetchall()

    def replace_all(self, vectors: Dict[str, "object"], encoder: str, basis: str) -> int:
        """Swap in a complete freshly-fitted set. The old fit is dropped entirely.

        This is the only way to write. There is deliberately no append: appending
        to a corpus-fitted encoder is the bug this store exists to prevent.
        """
        import numpy as np

        rows = [
            (pid, np.asarray(v, dtype=np.float32).tobytes(),
             int(np.asarray(v).shape[0]), encoder, basis)
            for pid, v in vectors.items()
        ]
        with self._connect() as conn:
            conn.execute("DELETE FROM vectors")
            conn.executemany(
                "INSERT INTO vectors (paper_id, vector, dims, encoder, basis) "
                "VALUES (?,?,?,?,?)", rows)
        return len(rows)

    def load_matrix(self):
        """Every vector as one matrix, or (None, []) when empty.

        Raises MixedBasis rather than returning a matrix that cannot be compared.
        """
        import numpy as np

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT paper_id, vector, dims, encoder, basis FROM vectors "
                "ORDER BY paper_id").fetchall()
        if not rows:
            return None, []

        fits = {(r[3], r[4], r[2]) for r in rows}
        if len(fits) > 1:
            raise MixedBasis(
                f"The vector store holds {len(fits)} different fits "
                f"({sorted((f[0], f[1][:8], f[2]) for f in fits)}). They are not "
                f"comparable. Run 'research-digest embed' to rebuild in one pass."
            )

        ids = [r[0] for r in rows]
        matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        return matrix, ids


class SimilaritySearch:
    """Cosine similarity by hand. numpy only, no scikit-learn at query time."""

    def __init__(self, store: Optional[EmbeddingStore] = None):
        self.store = store or EmbeddingStore()
        self._matrix = None
        self._ids: List[str] = []
        self._unit = None

    def _ensure(self) -> bool:
        if self._matrix is not None:
            return True
        matrix, ids = self.store.load_matrix()
        if matrix is None:
            return False
        import numpy as np
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix, self._ids, self._unit = matrix, ids, matrix / norms
        return True

    def find_similar(self, paper_id: str, k: int = 5) -> List[Tuple[str, float]]:
        if not self._ensure() or paper_id not in self._ids:
            return []
        import numpy as np
        idx = self._ids.index(paper_id)
        scores = self._unit @ self._unit[idx]
        order = np.argsort(scores)[::-1]
        out = []
        for i in order:
            if self._ids[i] == paper_id:
                continue
            out.append((self._ids[i], round(float(scores[i]), 4)))
            if len(out) >= k:
                break
        return out

    def find_similar_to_group(self, paper_ids: List[str], k: int = 5) -> List[Tuple[str, float]]:
        """Nearest papers to the centre of a group. Used for reading suggestions."""
        if not self._ensure():
            return []
        import numpy as np
        idxs = [self._ids.index(p) for p in paper_ids if p in self._ids]
        if not idxs:
            return []
        centroid = self._unit[idxs].mean(axis=0)
        norm = np.linalg.norm(centroid) or 1.0
        scores = self._unit @ (centroid / norm)
        exclude = set(paper_ids)
        out = []
        for i in np.argsort(scores)[::-1]:
            if self._ids[i] in exclude:
                continue
            out.append((self._ids[i], round(float(scores[i]), 4)))
            if len(out) >= k:
                break
        return out
