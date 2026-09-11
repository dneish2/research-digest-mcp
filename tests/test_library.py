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


class TestQueryScoring(TempHome):
    """score_query / rank_all_query: search ranks differently from the standing
    profile scorer on purpose — see the docstring on score_query."""

    def test_every_term_must_match_at_least_one(self):
        from research_digest_mcp.scoring import score_query
        paper = {"title": "a study of agentic systems", "abstract": "", "published": ""}
        self.assertIsNone(score_query(paper, ["evaluation"]))  # matches nothing
        self.assertIsNotNone(score_query(paper, ["agentic"]))

    def test_full_coverage_beats_partial_coverage(self):
        from research_digest_mcp.scoring import score_query
        both = {"title": "agentic evaluation of language models", "abstract": "",
                "published": ""}
        one = {"title": "agentic control of robot arms", "abstract": "", "published": ""}
        full = score_query(both, ["agentic", "evaluation"])
        partial = score_query(one, ["agentic", "evaluation"])
        self.assertGreater(full["score"], partial["score"])

    def test_stopwords_do_not_count_as_a_match_regression(self):
        """The bug a preliminary eval caught: "of" is not in BOILERPLATE (that
        list is field jargon, not general English), so it scored as a
        *distinctive* word — 0.6 credit — and matched almost every paper.
        "chain of thought faithfulness" scored 0.00 precision@5 because of it."""
        from research_digest_mcp.scoring import score_query
        unrelated = {"title": "A Deep Generative Model for Synthesizing Labeled "
                               "Wireless Signals", "abstract": "", "published": ""}
        self.assertIsNone(score_query(unrelated, "chain of thought faithfulness".split()))

    def test_glancing_mention_does_not_beat_an_off_topic_hvac_paper_regression(self):
        """The bug this guards: query terms were fed to score_paper as if they
        were the standing profile, so an HVAC paper that only said "evaluation"
        once could still outrank the paper actually about the query."""
        from research_digest_mcp.scoring import rank_all_query
        on_topic = {"id": "a", "title": "Agentic Evaluation of Multi-Agent Reasoning",
                    "abstract": "We evaluate agentic systems on reasoning benchmarks.",
                    "published": ""}
        off_topic = {"id": "b", "title": "Large Language Models for HVAC Operations",
                     "abstract": "We touch briefly on evaluation of the control loop.",
                     "published": ""}
        ranked = rank_all_query([off_topic, on_topic], ["agentic", "evaluation"])
        self.assertEqual(ranked[0]["id"], "a")


class TestAboutSentence(TempHome):
    def test_prefers_the_contribution_sentence(self):
        from research_digest_mcp.scoring import about_sentence
        paper = {"abstract": "Background varies widely across the field. "
                              "We propose a verifier-guided framework for the task. "
                              "Results improve substantially over baselines."}
        self.assertTrue(about_sentence(paper).startswith("We propose"))

    def test_falls_back_to_first_sentence_with_no_cue(self):
        from research_digest_mcp.scoring import about_sentence
        paper = {"abstract": "Reward hacking remains a persistent failure mode. "
                              "Later sections cover mitigations."}
        self.assertTrue(about_sentence(paper).startswith("Reward hacking"))

    def test_empty_abstract_is_empty_not_an_error(self):
        from research_digest_mcp.scoring import about_sentence
        self.assertEqual(about_sentence({"abstract": ""}), "")

    def test_long_sentence_is_clipped_on_a_word_boundary(self):
        from research_digest_mcp.scoring import about_sentence
        long_sentence = "We propose " + ("a very thorough method " * 20) + "for the task."
        result = about_sentence({"abstract": long_sentence}, max_chars=100)
        self.assertLessEqual(len(result), 101)  # + the ellipsis character
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("  ", result)


class TestImport(TempHome):
    def test_import_brings_in_new_papers_and_their_own_history(self):
        from research_digest_mcp import storage
        cleaned = [{"id": "2501.00001v1", "title": "Old paper", "abstract": "",
                    "published": "2025-01-01", "first_seen": "2025-01-02"}]
        stats = storage.import_papers(cleaned, run_dates=["2025-01-02", "2025-01-03"])
        self.assertEqual(stats, {"added": 1, "updated": 0, "total": 1})
        archive = storage.load_archive()
        self.assertEqual(archive["papers"]["2501.00001v1"]["first_seen"], "2025-01-02")
        self.assertIn("2025-01-02", archive["runs"])
        self.assertIn("2025-01-03", archive["runs"])

    def test_importing_twice_updates_rather_than_duplicates(self):
        from research_digest_mcp import storage
        paper = {"id": "2501.00001v1", "title": "Old paper", "abstract": "",
                 "published": "2025-01-01"}
        storage.import_papers([paper], run_dates=["2025-01-02"])
        stats = storage.import_papers([dict(paper, title="Old paper (revised)")],
                                       run_dates=["2025-01-02"])
        self.assertEqual(stats, {"added": 0, "updated": 1, "total": 1})
        self.assertEqual(len(storage.load_papers()), 1)

    def test_an_imported_paper_does_not_fake_a_trends_spike(self):
        """Old papers must fall outside the trends windows, or importing a
        year of history would look like every concept exploded this week."""
        from research_digest_mcp import storage
        from research_digest_mcp.trends import compute_trends
        old = {"id": "2501.00001v1", "title": "agentic evaluation", "abstract": "",
               "published": "2025-01-01", "concepts": ["agentic"]}
        storage.import_papers([old], run_dates=["2025-01-02"])
        result = compute_trends(storage.load_papers())
        self.assertEqual(result["status"], "no_data")  # nothing in either recent window


class TestSavePaper(TempHome):
    def test_saving_a_paper_already_in_the_library_needs_no_network(self):
        from research_digest_mcp import storage
        from research_digest_mcp.mcp import tool_save_paper
        storage.merge_papers([{"id": "2609.05339v1", "title": "Does Memory Survive",
                                "abstract": "", "published": "2026-09-04",
                                "concepts": ["agent"]}], "2026-09-04")
        result = tool_save_paper({"id_or_url": "https://arxiv.org/abs/2609.05339v1",
                                   "note": "for the memory review"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["already_in_library"])
        saved = storage.load_saved()
        self.assertIn("2609.05339v1", saved)
        self.assertEqual(saved["2609.05339v1"]["note"], "for the memory review")

    def test_an_unparseable_id_is_an_error_not_a_network_call(self):
        from research_digest_mcp.mcp import tool_save_paper
        result = tool_save_paper({"id_or_url": "not an arxiv id"})
        self.assertEqual(result["status"], "error")


class TestServeEncoding(TempHome):
    def test_serve_writes_utf8_even_when_stdout_defaults_to_cp1252(self):
        """The Windows bug: without reconfigure(), a cp1252 stdout either raises
        UnicodeEncodeError on a CJK title or silently writes bytes that are not
        valid UTF-8, corrupting the JSON-RPC stream a client is parsing."""
        import io
        from research_digest_mcp import storage
        from research_digest_mcp.mcp import serve

        papers = {"2601.00002v1": {
            "id": "2601.00002v1", "title": "评估 agentic systems — Björn's take",
            "abstract": "非常好", "published": "2026-01-01", "concepts": ["evaluation"],
        }}
        (self.home / "archive.json").write_text(
            json.dumps({"papers": papers, "runs": []}, ensure_ascii=False), encoding="utf-8")

        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "search_papers", "arguments": {"query": "evaluation"}}})
        stdin = io.StringIO(request + "\n")
        raw_out = io.BytesIO()
        stdout = io.TextIOWrapper(raw_out, encoding="cp1252", newline="\n")

        serve(stdin=stdin, stdout=stdout)  # must not raise

        text = raw_out.getvalue().decode("utf-8")  # would mismatch if still cp1252
        self.assertIn("评估", text)
        self.assertIn("Björn", text)


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
            "search_papers", "get_similar", "get_trends", "get_saved",
            "suggest_reading", "save_paper", "get_digest", "library_status"})

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
