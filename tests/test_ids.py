"""One paper, one id, everywhere.

Found while checking that similarity worked after a re-embed: 7 of 30 saved
papers pointed at ids no lookup could resolve. They still listed on the shelf,
because the shelf carries its own copy of the title, and opening one answered
"No paper 2609.05339v1" about a paper that was sitting in the library.
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


class TestOneIdPerPaper(TempHome):
    def test_a_record_reports_the_id_it_is_stored_under(self):
        """The archive is keyed by base id and each record carried its own
        versioned id, so the archive disagreed with itself: keyed by
        2609.19128, holding a record calling itself 2609.19128v1. 1,245 of
        23,850 records were in that state on a real library."""
        from research_digest_mcp import storage
        storage.merge_papers([{"id": "2609.19128v1", "title": "Cognitive extensions",
                               "abstract": "x", "published": "2026-09-16",
                               "primary_category": "cs.AI"}],
                             date.today().isoformat())
        paper = storage.load_papers()[0]
        self.assertEqual(paper["id"], "2609.19128")
        self.assertEqual(paper["version_id"], "2609.19128v1",
                         "the specific revision is still nameable")

    def test_a_bookmark_survives_the_paper_being_revised(self):
        """Save v1, let v2 arrive, and the bookmark must still open. This is the
        failure that started it: a shelf whose entries stop resolving is the
        worst thing a shelf can do, because being there later is its only job."""
        from research_digest_mcp import storage
        from research_digest_mcp.web import api
        storage.merge_papers([{"id": "2605.30169v1", "title": "First version",
                               "abstract": "x", "published": "2026-05-01",
                               "primary_category": "cs.AI"}],
                             date.today().isoformat())
        storage.save_paper("2605.30169v1", "First version")
        storage.merge_papers([{"id": "2605.30169v2", "title": "Second version",
                               "abstract": "x", "published": "2026-05-01",
                               "primary_category": "cs.AI"}],
                             date.today().isoformat())

        ids = {p["id"] for p in storage.load_papers()}
        self.assertEqual(ids, {"2605.30169"}, "one paper, not two")
        saved = storage.load_saved()
        self.assertEqual(list(saved), ["2605.30169"])
        for key in saved:
            self.assertIn(key, ids, "every bookmark must resolve")
        self.assertEqual(api("/api/paper", {"id": ["2605.30169"]})["status"], "ok")

    def test_an_old_shelf_is_repaired_by_being_read(self):
        """Applied on read as well as write, so a file written by an older
        version is fixed by use rather than by a migration nobody knows to run."""
        import json

        from research_digest_mcp import storage
        from research_digest_mcp.config import SAVED_PATH
        SAVED_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAVED_PATH.write_text(json.dumps({
            "2609.05339v1": {"title": "Saved before ids were normalised",
                             "note": "", "concepts": []},
        }), encoding="utf-8")
        self.assertEqual(list(storage.load_saved()), ["2609.05339"])

    def test_a_note_is_never_lost_when_two_keys_fold_together(self):
        """Both v1 and v2 present on an old shelf fold to one entry, and the one
        carrying a note wins. A note is the only thing on the shelf the reader
        wrote themselves."""
        import json

        from research_digest_mcp import storage
        from research_digest_mcp.config import SAVED_PATH
        SAVED_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAVED_PATH.write_text(json.dumps({
            "2605.30169v1": {"title": "A", "note": "why I kept this",
                             "concepts": [], "saved_at": "2026-05-01T00:00:00"},
            "2605.30169v2": {"title": "A", "note": "", "concepts": [],
                             "saved_at": "2026-06-01T00:00:00"},
        }), encoding="utf-8")
        saved = storage.load_saved()
        self.assertEqual(list(saved), ["2605.30169"])
        self.assertEqual(saved["2605.30169"]["note"], "why I kept this")

    def test_unsaving_works_whichever_form_is_passed(self):
        from research_digest_mcp import storage
        storage.save_paper("2605.30169v2", "A paper")
        self.assertTrue(storage.unsave_paper("2605.30169"))
        self.assertEqual(storage.load_saved(), {})


if __name__ == "__main__":
    unittest.main()
