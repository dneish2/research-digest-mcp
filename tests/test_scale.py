"""What a score is worth, which the number alone cannot say.

Reported as: "if you're at the least telling them to interact and see it change,
we have to explain what's the difference between 0.1 and 0.9 and make it
intuitive and easy to use."
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
        """A spread: papers matching everything, one topic, and nothing."""
        from research_digest_mcp import storage
        papers = []
        for i in range(10):
            papers.append({"id": f"a{i}", "title": "Agent evaluation and memory",
                           "abstract": "agent evaluation memory",
                           "published": "2026-01-01", "primary_category": "cs.AI"})
        for i in range(30):
            papers.append({"id": f"b{i}", "title": "Agent systems",
                           "abstract": "one agent topic only",
                           "published": "2026-01-01", "primary_category": "cs.AI"})
        for i in range(60):
            papers.append({"id": f"c{i}", "title": "Photosynthesis in plants",
                           "abstract": "nothing relevant at all",
                           "published": "2026-01-01", "primary_category": "q-bio.NC"})
        storage.merge_papers(papers, date.today().isoformat())
        return storage.load_papers()


class TestScale(TempHome):
    def test_a_score_is_placed_against_real_papers(self):
        from research_digest_mcp.scoring import percentile_of, score_distribution
        papers = self.library()
        topics = ["agent", "evaluation", "memory"]
        dist = score_distribution(papers, topics)
        self.assertEqual(dist["library"], 100)
        # 60 of the 100 papers match nothing, so anything above zero already
        # beats them. That is the fact the bare number could never convey.
        self.assertGreaterEqual(percentile_of(0.01, dist), 60.0)
        self.assertEqual(percentile_of(0.0, dist), 0.0)

    def test_the_landmarks_show_a_scale_that_is_not_a_percentage(self):
        """The whole point. Scores cluster near the bottom, so equal steps on
        the scale are nothing like equal steps in rank."""
        from research_digest_mcp.scoring import score_distribution
        dist = score_distribution(self.library(), ["agent", "evaluation", "memory"])
        marks = {m["score"]: m["above"] for m in dist["landmarks"]}
        self.assertLessEqual(marks[0.1], marks[0.5])
        self.assertLessEqual(marks[0.5], marks[0.9])
        self.assertEqual(marks[0.9], 100.0, "nothing here should reach 0.9")

    def test_the_distribution_is_cached_per_topic_list(self):
        """It costs 1.7 seconds over 24,000 papers and the page recomputes on
        every keystroke."""
        from research_digest_mcp.scoring import _DIST_CACHE, score_distribution
        papers = self.library()
        score_distribution(papers, ["agent"])
        first = _DIST_CACHE.get((("agent",), len(papers)))
        self.assertIsNotNone(first)
        self.assertIs(score_distribution(papers, ["agent"]), first)
        score_distribution(papers, ["memory"])
        self.assertIsNone(_DIST_CACHE.get((("agent",), len(papers))),
                          "a new topic list replaces the old one")

    def test_the_explain_endpoint_carries_the_scale(self):
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/explain", {
            "topics": ["agent, evaluation, memory"],
            "title": ["Agent evaluation and memory"],
            "abstract": ["agent evaluation memory"],
        })
        self.assertEqual(out["status"], "ok")
        self.assertIsNotNone(out["scale"])
        self.assertGreater(out["percentile"], 0)
        self.assertNotIn("_scores", out["scale"], "the raw list must not be shipped")

    def test_examples_come_from_the_library_not_from_a_blank_box(self):
        """"Who knows the full title all the time" was the report. These fill
        the title, abstract and date from a paper actually held."""
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/explain/examples", {})
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["results"])
        first = out["results"][0]
        self.assertTrue(first["title"])
        self.assertTrue(first["abstract"])

    def test_examples_can_be_searched_by_title(self):
        from research_digest_mcp.web import api
        self.library()
        out = api("/api/explain/examples", {"q": ["photosynthesis"]})
        self.assertTrue(out["results"])
        for row in out["results"]:
            self.assertIn("photosynthesis", row["title"].lower())


if __name__ == "__main__":
    unittest.main()


class TestScoringIsPinnable(TempHome):
    """A frozen corpus scored against the real clock is not frozen.

    Found by noticing the committed regression number had moved with nothing
    committed in between: 0.7692 on 2026-09-21, 0.7615 on 2026-09-23. Part of
    every score is a recency bonus decaying over 30 days against `date.today()`,
    so the eval's fixture aged out from under it and one phrase lost a relevant
    paper from its top five. Left alone it slides until it trips the CI floor and
    reads as a ranker regression.
    """

    def test_a_ranking_can_be_asked_to_use_a_fixed_date(self):
        from datetime import date

        from research_digest_mcp.scoring import rank_all_query
        papers = [
            {"id": "old", "title": "Agent memory", "abstract": "agent memory",
             "published": "2026-01-01"},
            {"id": "new", "title": "Agent memory", "abstract": "agent memory",
             "published": "2026-06-01"},
        ]
        # As of the day after the newer paper, recency separates them.
        fresh = rank_all_query(papers, ["agent", "memory"], today=date(2026, 6, 2))
        self.assertEqual(fresh[0]["id"], "new")
        self.assertGreater(fresh[0]["score"], fresh[1]["score"])

        # Long after both, recency is worth nothing to either and they tie.
        later = rank_all_query(papers, ["agent", "memory"], today=date(2027, 6, 2))
        self.assertEqual(later[0]["score"], later[1]["score"])

    def test_the_eval_pins_its_clock_to_the_corpus(self):
        """Derived from the corpus so it needs no maintenance: re-freezing the
        corpus brings its own reference date."""
        import importlib.util
        from datetime import date
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "eval" / "eval-regression.py"
        spec = importlib.util.spec_from_file_location("eval_regression", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.corpus_today([{"published": "2026-09-04"},
                                 {"published": "2026-08-01"}]),
            date(2026, 9, 5))
