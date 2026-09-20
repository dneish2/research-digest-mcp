"""Finding the word you meant, without inventing one.

The reported complaint: a search for a real subject works, "but if I do a typo
it doesn't correct me or show me things which probably I was looking for".
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TempHome(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["RESEARCH_DIGEST_HOME"] = self._dir.name
        for mod in [m for m in list(sys.modules) if m.startswith("research_digest_mcp")]:
            del sys.modules[mod]

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)

    def library(self):
        """Enough repetition to clear MIN_DF, which is the point of the bar: a
        word appearing once is usually somebody else's typo."""
        from research_digest_mcp import storage
        papers = []
        for i in range(6):
            papers.append({
                "id": f"26.{i}", "title": f"Agent memory and evaluation, part {i}",
                "abstract": "We study retrieval augmented memory for agents and "
                            "the evaluation of reasoning under interpretability "
                            "constraints.",
                "published": "2026-09-01", "primary_category": "cs.AI",
                "concepts": ["memory", "evaluation"], "authors": ["Ada Lovelace"],
            })
        storage.merge_papers(papers, date.today().isoformat())
        return storage.load_papers()


class TestSuggestions(TempHome):
    def test_a_transposition_is_one_mistake_not_two(self):
        """The typo the feature was reported against.

        Swapping two letters costs two edits in plain Levenshtein, so `memroy`
        sat outside a one edit budget for a six letter word and the suggestion
        was dropped. It is one keystroke and it costs one here.
        """
        from research_digest_mcp.spelling import suggest
        papers = self.library()
        self.assertEqual([w for w, _n in suggest("memroy", papers)][:1], ["memory"])
        self.assertEqual([w for w, _n in suggest("evalaution", papers)][:1],
                         ["evaluation"])

    def test_a_suggestion_carries_the_papers_it_will_return(self):
        """Every candidate comes from the library's own vocabulary, so the count
        is a promise that can be kept rather than a guess."""
        from research_digest_mcp.spelling import suggest
        papers = self.library()
        word, count = suggest("evalution", papers)[0]
        self.assertEqual(word, "evaluation")
        self.assertEqual(count, 6)

    def test_a_word_this_field_does_not_use_gets_no_suggestion(self):
        """Refusing beats reaching. Offering `synthesis` for `photosynthesis`
        would answer a question nobody asked."""
        from research_digest_mcp.spelling import suggest
        papers = self.library()
        self.assertEqual(suggest("photosynthesis", papers), [])
        self.assertEqual(suggest("zzzqqq", papers), [])

    def test_a_word_that_is_already_right_is_left_alone(self):
        from research_digest_mcp.spelling import suggest
        papers = self.library()
        self.assertEqual(suggest("memory", papers), [])
        self.assertEqual(suggest("evaluation", papers), [])

    def test_a_rare_word_cannot_be_the_correction(self):
        """A word in one paper is as likely to be a typo as the query is."""
        from research_digest_mcp import storage
        from research_digest_mcp.spelling import suggest
        self.library()
        storage.merge_papers([{"id": "26.99", "title": "Memoryy", "abstract": "x",
                               "published": "2026-09-01",
                               "primary_category": "cs.AI"}],
                             date.today().isoformat())
        self.assertNotIn("memoryy",
                         [w for w, _n in suggest("memoryyy", storage.load_papers())])


class TestSearchBehaviour(TempHome):
    def test_a_query_of_only_typos_is_corrected_and_says_so(self):
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/search", {"q": ["memroy"]})
        self.assertGreater(out["matched"], 0)
        self.assertEqual(out["corrected_from"], "memroy")
        self.assertEqual(out["query"], "memory")

    def test_a_partly_wrong_query_keeps_its_real_results(self):
        """The papers matching the words that landed are real answers. Replacing
        them with a corrected search would throw away a good result to fix a
        word the reader may not have meant."""
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/search", {"q": ["agent memroy"]})
        self.assertGreater(out["matched"], 0)
        self.assertEqual(out["corrected_from"], "", "results were real, so no swap")
        self.assertEqual([f["suggestion"] for f in out["corrections"]], ["memory"])

    def test_the_reader_can_insist_on_their_own_spelling(self):
        """Sometimes the word is right and the library is what is missing, and
        that is a more useful fact than a near miss."""
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/search", {"q": ["memroy"], "exact": ["1"]})
        self.assertEqual(out["matched"], 0)
        self.assertEqual(out["corrected_from"], "")
        self.assertEqual([f["suggestion"] for f in out["corrections"]], ["memory"])


if __name__ == "__main__":
    unittest.main()
