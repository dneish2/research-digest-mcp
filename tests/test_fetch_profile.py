"""Tests for the interest profile and the fetch it drives.

These cover the failure that made the library stop being useful: a fetch that
asked arXiv for whatever was newest, in a handful of categories, with no
reference to what the reader actually reads, and with no way to reach a day it
had missed.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TempHome(unittest.TestCase):
    """Each test gets its own data directory. Nothing touches the real one."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["RESEARCH_DIGEST_HOME"] = self._dir.name
        for name in list(sys.modules):
            if name.startswith("research_digest_mcp"):
                del sys.modules[name]

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)


class TestProfileShape(TempHome):

    def test_legacy_settings_still_load(self):
        """A settings.json from before tiers existed must keep working."""
        from research_digest_mcp.config import load_profile
        profile = load_profile({"categories": ["cs.AI"], "topics": ["agent"]})
        self.assertEqual(profile["tiers"]["core"]["categories"], ["cs.AI"])
        self.assertEqual(profile["tiers"]["core"]["topics"], ["agent"])
        self.assertEqual(profile["tiers"]["complementary"]["categories"], [])

    def test_tiers_are_read_and_combined(self):
        from research_digest_mcp.config import load_profile
        profile = load_profile({
            "categories": [], "topics": [],
            "profile": {
                "core": {"categories": ["cs.AI"], "topics": ["agent"]},
                "complementary": {"categories": ["cs.HC"], "topics": ["trust"]},
                "stretch": {"categories": ["stat.ME"],
                            "structural_keywords": ["confounding"]},
            },
        })
        self.assertEqual(profile["all_categories"], ["cs.AI", "cs.HC", "stat.ME"])
        self.assertIn("confounding", profile["all_topics"])

    def test_structural_keywords_are_searchable_terms(self):
        """The stretch tier matches on method, so its keywords must be queried."""
        from research_digest_mcp.config import load_profile
        profile = load_profile({
            "categories": [], "topics": [],
            "profile": {"stretch": {"categories": ["stat.ME"],
                                    "structural_keywords": ["ablation"]}},
        })
        self.assertIn("ablation", profile["tiers"]["stretch"]["keywords"])


class TestQueryConstruction(TempHome):

    def test_without_keywords_it_asks_for_the_whole_category(self):
        from research_digest_mcp.fetchers import build_query
        self.assertEqual(build_query("cs.LG"), "cat:cs.LG")

    def test_keywords_narrow_the_request_at_arxivs_end(self):
        from research_digest_mcp.fetchers import build_query
        query = build_query("cs.LG", ["agent benchmark", "calibration"])
        self.assertIn("cat:cs.LG AND (", query)
        self.assertIn('ti:"agent benchmark"', query)
        self.assertIn('abs:"calibration"', query)

    def test_a_date_range_makes_a_missed_day_reachable(self):
        from research_digest_mcp.fetchers import build_query
        query = build_query("cs.LG", None, since="2026-07-01", until="2026-07-31")
        self.assertIn("submittedDate:[202607010000 TO 202607312359]", query)

    def test_quotes_in_a_topic_cannot_break_the_query(self):
        from research_digest_mcp.fetchers import build_query
        query = build_query("cs.LG", ['agent" OR all:junk'])
        self.assertNotIn('"agent" OR all:junk"', query)
        self.assertIn('ti:"agent OR all:junk"', query)


class TestKeywordWindow(TempHome):

    def test_a_long_topic_list_is_covered_across_runs(self):
        """Truncating would mean the tail of the profile is never queried."""
        from research_digest_mcp.config import KEYWORDS_PER_QUERY
        from research_digest_mcp.fetchers import _keyword_window
        topics = [f"t{i}" for i in range(20)]
        seen = set()
        for run in range(5):
            seen.update(_keyword_window(topics, run * KEYWORDS_PER_QUERY))
        self.assertEqual(seen, set(topics))

    def test_the_window_wraps_rather_than_running_short(self):
        from research_digest_mcp.fetchers import _keyword_window
        topics = ["a", "b", "c", "d", "e", "f", "g"]
        window = _keyword_window(topics, 5)
        self.assertEqual(len(window), 6)
        self.assertEqual(window[:2], ["f", "g"])

    def test_no_keywords_is_not_an_error(self):
        from research_digest_mcp.fetchers import _keyword_window
        self.assertEqual(_keyword_window([], 3), [])


class TestCooldown(TempHome):

    def test_a_refusal_is_remembered_across_processes(self):
        """The process that earns a 429 exits; the next run must still know."""
        from research_digest_mcp import fetchers
        fetchers._begin_cooldown()
        self.assertGreater(fetchers.cooldown_remaining(), 0)

        # Simulate a fresh process: re-import and read the file from disk.
        for name in list(sys.modules):
            if name.startswith("research_digest_mcp"):
                del sys.modules[name]
        from research_digest_mcp import fetchers as reloaded
        self.assertGreater(reloaded.cooldown_remaining(), 0)

    def test_a_request_during_cooldown_is_refused_without_a_call(self):
        from research_digest_mcp import fetchers
        fetchers._begin_cooldown()
        with self.assertRaises(fetchers.ArxivCoolingDown):
            fetchers._get({"search_query": "cat:cs.AI"})

    def test_an_expired_cooldown_does_not_block(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import COOLDOWN_PATH
        COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOLDOWN_PATH.write_text(json.dumps({"until": time.time() - 1}), encoding="utf-8")
        self.assertEqual(fetchers.cooldown_remaining(), 0.0)

    def test_a_corrupt_cooldown_file_does_not_block_forever(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import COOLDOWN_PATH
        COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOLDOWN_PATH.write_text("not json", encoding="utf-8")
        self.assertEqual(fetchers.cooldown_remaining(), 0.0)


class TestFetchProfile(TempHome):
    """fetch_profile with the network stubbed out."""

    def _stub(self, fetchers, calls, papers_per_call=2, fail_on=None):
        def fake(category, max_results=60, keywords=None, since=None, until=None, start=0):
            calls.append({"category": category, "keywords": list(keywords or []),
                          "max_results": max_results, "since": since})
            if fail_on and category in fail_on:
                raise fetchers.ArxivUnavailable("arXiv refused this client (HTTP 429).")
            return [{"id": f"{category}.{i}", "title": f"{category} paper {i}",
                     "abstract": "x", "published": "2026-07-01", "updated": "2026-07-01",
                     "url": "u", "authors": [], "primary_category": category,
                     "categories": [category]} for i in range(papers_per_call)]
        fetchers.fetch_category = fake

    def test_every_tier_is_fetched_with_its_own_budget(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import load_profile
        calls = []
        self._stub(fetchers, calls)
        profile = load_profile({
            "categories": [], "topics": [],
            "profile": {
                "core": {"categories": ["cs.AI"], "topics": ["agent"], "per_category": 50},
                "complementary": {"categories": ["cs.HC"], "topics": ["trust"],
                                  "per_category": 25},
            },
        })
        result = fetchers.fetch_profile(profile)
        budgets = {c["category"]: c["max_results"] for c in calls}
        self.assertEqual(budgets["cs.AI"], 50)
        self.assertEqual(budgets["cs.HC"], 25)
        self.assertEqual(len(result["papers"]), 4)

    def test_the_plan_reports_what_was_actually_asked_for(self):
        """'Fetched 50, 0 new' hid which category produced the zero."""
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import load_profile
        calls = []
        self._stub(fetchers, calls)
        profile = load_profile({"categories": ["cs.AI"], "topics": ["agent"]})
        result = fetchers.fetch_profile(profile)
        row = result["plan"][0]
        self.assertEqual(row["category"], "cs.AI")
        self.assertEqual(row["keywords"], ["agent"])
        self.assertEqual(row["returned"], 2)

    def test_a_refusal_stops_the_run_instead_of_repeating_it(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import load_profile
        calls = []
        self._stub(fetchers, calls, fail_on={"cs.AI"})
        profile = load_profile({
            "categories": [], "topics": [],
            "profile": {"core": {"categories": ["cs.AI", "cs.LG", "cs.CL"],
                                 "topics": ["agent"]}},
        })
        result = fetchers.fetch_profile(profile)
        self.assertTrue(result["stopped_early"])
        self.assertEqual(len(calls), 1)

    def test_a_backfill_window_reaches_the_fetchers(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.config import load_profile
        calls = []
        self._stub(fetchers, calls)
        profile = load_profile({"categories": ["cs.AI"], "topics": ["agent"]})
        fetchers.fetch_profile(profile, since="2026-07-01", until="2026-07-31")
        self.assertEqual(calls[0]["since"], "2026-07-01")


if __name__ == "__main__":
    unittest.main()
