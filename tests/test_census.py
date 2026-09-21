"""Counting the field without downloading it.

The reported ask: "frankly I'm more interested in industry trends, for example
from 1 week or 1 month ago how many papers are being reported on rag or
evaluation or memory", together with the constraint that the whole of arXiv must
not land on the laptop.

Nothing here touches the network. The walk is the only part that does, and what
it feeds is a Tally, which is what these tests drive directly.
"""
from __future__ import annotations

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

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)


class TestWordBoundaries(TempHome):
    def test_a_short_term_is_not_matched_inside_a_longer_word(self):
        """The measurement that forced this whole module.

        Concept matching has always been a substring test. Over one real day of
        cs, `rag` matched 262 of 1,140 papers; the word-bounded count is 17. The
        other 245 are words like leverages and storage. A trend line through 94%
        noise is worse than none, because it is confident.
        """
        from research_digest_mcp.census import Tally
        tally = Tally()
        tally.observe({"published": "2026-09-01", "title": "Storage leverages",
                       "abstract": "This averages over fragments."})
        self.assertEqual(tally.days["2026-09-01"]["terms"].get("rag"), None)

        tally.observe({"published": "2026-09-01", "title": "RAG for code",
                       "abstract": "retrieval-augmented generation"})
        self.assertEqual(tally.days["2026-09-01"]["terms"]["rag"], 1)

    def test_one_subject_spelled_three_ways_counts_once_per_paper(self):
        """The field does not agree on hyphens. Counting only one spelling
        undercounts the subject, which looks exactly like the subject being
        smaller than it is; counting each spelling separately double counts one
        paper that hedges."""
        from research_digest_mcp.census import Tally
        tally = Tally()
        tally.observe({"published": "2026-09-01", "title": "A multi agent system",
                       "abstract": "multiagent coordination and multi-agent debate"})
        self.assertEqual(tally.days["2026-09-01"]["terms"]["multi-agent"], 1)

    def test_every_day_carries_its_own_denominator(self):
        """"17 papers on rag" means nothing without "of 1,140 that day". Without
        the denominator a week arXiv published more in is indistinguishable from
        a week a subject rose in."""
        from research_digest_mcp.census import Tally
        tally = Tally()
        for i in range(5):
            tally.observe({"published": "2026-09-01", "title": f"Paper {i}",
                           "abstract": "memory" if i < 2 else "nothing here"})
        bucket = tally.days["2026-09-01"]
        self.assertEqual(bucket["papers"], 5)
        self.assertEqual(bucket["terms"]["memory"], 2)


class TestComparing(TempHome):
    def seed(self, today):
        """Two windows, with a subject that doubles its share and one that holds."""
        from research_digest_mcp import census
        days = {}
        for offset in range(3, 25):
            day = (today - timedelta(days=offset)).isoformat()
            recent = offset <= 10
            days[day] = {"papers": 100, "terms": {
                "rag": 8 if recent else 4,
                "memory": 5,
            }}
        census.save({"days": days, "terms": ["rag", "memory"], "sets": ["cs"],
                     "updated": today.isoformat()})

    def test_a_share_that_doubles_reads_as_a_rise(self):
        from research_digest_mcp.census import compare
        today = date(2026, 9, 20)
        self.seed(today)
        out = compare(window=7, offset=0, against=7, against_offset=7, today=today)
        self.assertEqual(out["status"], "ok")
        rows = {r["term"]: r for r in out["rows"]}
        self.assertEqual(rows["rag"]["share_before"], 4.0)
        self.assertEqual(rows["rag"]["share_now"], 8.0)
        self.assertEqual(rows["rag"]["change_pts"], 4.0)
        self.assertEqual(rows["memory"]["change_pts"], 0.0)

    def test_the_newest_days_are_left_out_of_both_windows(self):
        """They are not missing, they are not announced. arXiv publishes on a
        delay and refuses a future `until` outright, so the newest days are
        always short; counting them makes every subject look like it fell off a
        cliff this week."""
        from research_digest_mcp.census import REPORTING_LAG, compare
        today = date(2026, 9, 20)
        self.seed(today)
        out = compare(window=7, today=today)
        self.assertEqual(out["recent"]["end"],
                         (today - timedelta(days=REPORTING_LAG)).isoformat())

    def test_windows_of_different_lengths_compare_by_rate_not_by_total(self):
        """A week against a month on raw counts reports every subject as having
        collapsed. The share and the per-day rate both survive the mismatch."""
        from research_digest_mcp.census import compare
        today = date(2026, 9, 20)
        self.seed(today)
        out = compare(window=7, offset=0, against=14, against_offset=7, today=today)
        rows = {r["term"]: r for r in out["rows"]}
        self.assertGreater(rows["rag"]["of_before"], rows["rag"]["of_now"])
        self.assertEqual(rows["memory"]["share_now"], rows["memory"]["share_before"])
        self.assertEqual(rows["memory"]["per_day_now"], rows["memory"]["per_day_before"])

    def test_a_missing_census_says_so_instead_of_reporting_zeroes(self):
        from research_digest_mcp.census import compare
        out = compare(today=date(2026, 9, 20))
        self.assertEqual(out["status"], "no_data")
        self.assertIn("not been built", out["reason"])
        self.assertEqual(out["rows"], [])

    def test_a_second_pass_over_a_day_replaces_it_rather_than_adding(self):
        """Recounting the same papers is not more papers. Adding would double
        every number with nothing on screen to say it had happened."""
        from research_digest_mcp import census
        first = census.Tally()
        first.observe({"published": "2026-09-01", "title": "Memory", "abstract": "x"})
        census.merge(first, ["cs"], ("2026-09-01", "2026-09-01"))
        again = census.Tally()
        again.observe({"published": "2026-09-01", "title": "Memory", "abstract": "x"})
        data = census.merge(again, ["cs"], ("2026-09-01", "2026-09-01"))
        self.assertEqual(data["days"]["2026-09-01"]["papers"], 1)

    def test_a_window_reports_how_many_of_its_days_it_actually_holds(self):
        """A day the census never walked and a day arXiv published nothing look
        identical in a total and mean opposite things."""
        from research_digest_mcp import census
        today = date(2026, 9, 20)
        tally = census.Tally()
        tally.observe({"published": (today - timedelta(days=4)).isoformat(),
                       "title": "Memory", "abstract": "x"})
        census.merge(tally, ["cs"], ("", ""))
        out = census.compare(window=7, today=today)
        self.assertEqual(out["recent"]["asked"], 7)
        self.assertEqual(out["recent"]["present"], 1)


if __name__ == "__main__":
    unittest.main()
