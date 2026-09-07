"""Tests for the parts that have burned us before.

Each of these corresponds to a real failure: a Windows encoding crash that
surfaced as an empty library, an embedding store that mixed two incompatible
fits, and a trends view that reported an outage as a 100% decline.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TempHome(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["RESEARCH_DIGEST_HOME"] = self._dir.name
        for mod in [m for m in list(sys.modules) if m.startswith("research_digest_mcp")]:
            del sys.modules[mod]
        self.home = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)


class TestEncoding(TempHome):
    def test_non_ascii_archive_loads(self):
        """A paper with an accented author must not make the library look empty.

        The original bug: read_text() with no encoding used cp1252 on Windows and
        raised, and a broad except turned that into zero results.
        """
        from research_digest_mcp import storage
        papers = {
            "2601.00001v1": {
                "id": "2601.00001v1",
                "title": "Evaluation of agentic systems",
                "abstract": "By Björn Andrés and Lucrèce Émilie — 中文 too.",
                "published": "2026-01-01",
                "concepts": ["evaluation"],
            }
        }
        (self.home / "archive.json").write_text(
            json.dumps({"papers": papers, "runs": []}, ensure_ascii=False),
            encoding="utf-8")
        loaded = storage.load_papers()
        self.assertEqual(len(loaded), 1)
        self.assertIn("Björn", loaded[0]["abstract"])

    def test_corrupt_json_raises_rather_than_empties(self):
        from research_digest_mcp import storage
        (self.home / "archive.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            storage.load_papers()


class TestScoring(TempHome):
    def test_common_words_score_less_than_distinctive(self):
        from research_digest_mcp.scoring import score_paper
        paper = {"title": "learning agentic control", "abstract": "", "published": ""}
        common = score_paper(paper, ["learning"])
        distinct = score_paper(paper, ["agentic"])
        self.assertLess(common["score"], distinct["score"])

    def test_phrase_beats_single_word(self):
        from research_digest_mcp.scoring import score_paper
        paper = {"title": "a study of world model agents", "abstract": "", "published": ""}
        phrase = score_paper(paper, ["world model"])
        single = score_paper(paper, ["agents"])
        self.assertGreater(phrase["score"], single["score"])

    def test_every_score_carries_its_derivation(self):
        from research_digest_mcp.scoring import score_paper
        result = score_paper(
            {"title": "agentic evaluation", "abstract": "", "published": ""},
            ["agentic", "evaluation"])
        self.assertEqual(len(result["why"]["matched"]), 2)
        parts = result["why"]["components"]
        total = parts["base"] + parts["breadth_bonus"] + parts["recency"]
        self.assertAlmostEqual(result["score"], min(total, 1.0), places=4)

    def test_match_count_is_not_the_display_limit(self):
        from research_digest_mcp.scoring import rank, rank_all
        papers = [{"id": str(i), "title": "agentic study", "abstract": "", "published": ""}
                  for i in range(30)]
        self.assertEqual(len(rank_all(papers, ["agentic"])), 30)
        self.assertEqual(len(rank(papers, ["agentic"], limit=5)), 5)


class TestTrends(TempHome):
    def _papers(self, this_week, prev_week):
        today = date.today()
        out = []
        for i in range(this_week):
            out.append({"id": f"a{i}", "title": "x", "abstract": "",
                        "first_seen": (today - timedelta(days=2)).isoformat(),
                        "concepts": ["agentic"]})
        for i in range(prev_week):
            out.append({"id": f"b{i}", "title": "x", "abstract": "",
                        "first_seen": (today - timedelta(days=9)).isoformat(),
                        "concepts": ["agentic"]})
        return out

    def test_empty_week_is_no_data_not_a_100_percent_decline(self):
        """The bug this guards: 0 papers this week reported every concept at -100%."""
        from research_digest_mcp.trends import compute_trends
        result = compute_trends(self._papers(0, 40))
        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["falling"], [])
        self.assertIn("how recently you fetched", result["reason"])

    def test_real_comparison_reports_change(self):
        from research_digest_mcp.trends import compute_trends
        result = compute_trends(self._papers(20, 8))
        self.assertEqual(result["status"], "ok")
        rising = {r["concept"] for r in result["rising"]}
        self.assertIn("agentic", rising)


class TestEmbeddingStore(TempHome):
    def setUp(self):
        super().setUp()
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")

    def test_mixed_fits_refuse_to_load(self):
        """Two fits in one store must raise, not return an uncomparable matrix."""
        import numpy as np
        from research_digest_mcp.similarity import EmbeddingStore, MixedBasis
        store = EmbeddingStore()
        store.replace_all({"a": np.ones(8, dtype=np.float32)}, "tfidf-svd", "fit-one")
        import sqlite3
        conn = sqlite3.connect(str(store.path))
        try:
            conn.execute(
                "INSERT INTO vectors (paper_id, vector, dims, encoder, basis) VALUES (?,?,?,?,?)",
                ("b", np.ones(5, dtype=np.float32).tobytes(), 5, "tfidf-svd", "fit-two"))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(MixedBasis):
            store.load_matrix()

    def test_replace_all_drops_the_previous_fit(self):
        import numpy as np
        from research_digest_mcp.similarity import EmbeddingStore
        store = EmbeddingStore()
        store.replace_all({f"old{i}": np.ones(4, dtype=np.float32) for i in range(5)},
                          "tfidf-svd", "fit-one")
        store.replace_all({f"new{i}": np.ones(6, dtype=np.float32) for i in range(3)},
                          "tfidf-svd", "fit-two")
        self.assertEqual(store.count(), 3)
        self.assertEqual(len(store.bases()), 1)

    def test_similarity_ranks_identical_vectors_first(self):
        import numpy as np
        from research_digest_mcp.similarity import EmbeddingStore, SimilaritySearch
        store = EmbeddingStore()
        store.replace_all({
            "seed": np.array([1, 0, 0, 0], dtype=np.float32),
            "same": np.array([2, 0, 0, 0], dtype=np.float32),
            "other": np.array([0, 1, 0, 0], dtype=np.float32),
        }, "tfidf-svd", "fit")
        hits = SimilaritySearch(store).find_similar("seed", 2)
        self.assertEqual(hits[0][0], "same")
        self.assertAlmostEqual(hits[0][1], 1.0, places=3)


class TestMcpProtocol(TempHome):
    def test_initialize_and_tools_list(self):
        from research_digest_mcp.mcp import handle
        init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "research-digest")
        tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tools["result"]["tools"]}
        self.assertEqual(names, {
            "search_papers", "get_similar", "get_trends",
            "get_saved", "suggest_reading", "library_status"})

    def test_empty_library_explains_itself(self):
        """An empty library must say so, not return [] as if nothing matched."""
        from research_digest_mcp.mcp import handle
        out = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "search_papers", "arguments": {"query": "agents"}}})
        payload = json.loads(out["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "empty_library")
        self.assertIn("research-digest fetch", payload["message"])

    def test_search_returns_derivations(self):
        from research_digest_mcp import storage
        from research_digest_mcp.mcp import handle
        storage.merge_papers([{
            "id": "2601.1v1", "title": "Agentic evaluation harnesses",
            "abstract": "We evaluate agents.", "published": date.today().isoformat(),
            "concepts": ["evaluation"], "url": "https://arxiv.org/abs/2601.1v1",
        }], date.today().isoformat())
        out = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "search_papers",
                                 "arguments": {"query": "agentic evaluation"}}})
        payload = json.loads(out["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["matched"], 1)
        self.assertIn("why", payload["results"][0])

    def test_unknown_tool_is_an_error_not_a_crash(self):
        from research_digest_mcp.mcp import handle
        out = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "nope", "arguments": {}}})
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
