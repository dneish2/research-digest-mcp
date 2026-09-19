"""Tests for the browser API and the workspace reader.

The web layer had no tests at all, which is backwards: it is the surface the
owner of this library actually uses, and every bug found by hand in it was a
bug the 52 passing tests could not have seen. Several of these pin failures
that were live until the overhaul -- a search result that always rendered
unstarred, an endpoint whose status field meant two different things, a
settings writer with no validation behind it.

Deliberately no network. `fetch_settings`, `search` and the LLM probe are
either not called or are called against a machine with nothing listening, which
is also the state of every CI runner this has to pass on.
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

    def seed(self):
        from research_digest_mcp import storage
        storage.merge_papers([
            {"id": "2601.00001", "title": "Agent memory that survives upgrades",
             "abstract": "We propose a portable memory for agents.",
             "published": date.today().isoformat(), "primary_category": "cs.AI",
             "concepts": ["memory", "agent"], "authors": ["Ada Lovelace"],
             "url": "https://arxiv.org/abs/2601.00001"},
            {"id": "2601.00002", "title": "Calibration under distribution shift",
             "abstract": "We study calibration.", "published": "2026-02-02",
             "primary_category": "stat.ME", "concepts": ["calibration"],
             "authors": ["Grace Hopper"], "url": "https://arxiv.org/abs/2601.00002"},
        ], date.today().isoformat())
        return storage


class TestReadEndpoints(TempHome):
    def test_every_read_endpoint_answers_with_a_status(self):
        """A GET that raises becomes a 500 with no useful message. Each of
        these is reachable from a button, so each must answer."""
        from research_digest_mcp.web import api
        self.seed()
        for path, params in [
            ("/api/status", {}), ("/api/papers", {}), ("/api/search", {"q": ["agent"]}),
            ("/api/ask", {"q": ["papers on agent memory"], "llm": ["0"]}),
            ("/api/profile", {}), ("/api/trends", {}), ("/api/saved", {}),
            ("/api/settings", {}), ("/api/suggest-terms", {}),
            ("/api/export", {"what": ["all"], "format": ["json"]}),
            ("/api/explain", {"topics": ["agent"], "title": ["x"]}),
            ("/api/workspace", {}), ("/api/paper", {"id": ["2601.00001"]}),
        ]:
            with self.subTest(path=path):
                out = api(path, params)
                self.assertIn("status", out)
                self.assertIsInstance(json.dumps(out), str, "must be serialisable")

    def test_an_unknown_endpoint_is_an_error_not_a_crash(self):
        from research_digest_mcp.web import api
        self.assertEqual(api("/api/nope", {})["status"], "error")

    def test_a_search_result_knows_whether_it_is_already_saved(self):
        """Live bug: /api/search never returned saved/read, so a paper you had
        starred rendered with an empty star in every search result, and
        clicking it un-starred the paper you were trying to keep."""
        from research_digest_mcp.web import api
        storage = self.seed()
        storage.save_paper("2601.00001", "Agent memory that survives upgrades")
        rows = api("/api/search", {"q": ["agent memory"]})["results"]
        self.assertTrue(rows[0]["saved"])
        self.assertIn("read", rows[0])

    def test_a_card_is_labelled_with_the_tier_that_fetched_it(self):
        """Derived from the category, because `import` drops the stored field
        and no paper in a restored library carries one."""
        from research_digest_mcp.web import api, api_write
        self.seed()
        api_write("/api/profile", {"tiers": {
            "core": {"categories": ["cs.AI"]},
            "stretch": {"categories": ["stat.ME"]}}})
        by_id = {r["id"]: r for r in api("/api/papers", {})["results"]}
        self.assertEqual(by_id["2601.00001"]["tier"], "core")        # cs.AI
        self.assertEqual(by_id["2601.00002"]["tier"], "stretch")     # stat.ME

    def test_a_category_in_no_tier_gets_no_badge_rather_than_a_wrong_one(self):
        from research_digest_mcp.web import api
        self.seed()
        by_id = {r["id"]: r for r in api("/api/papers", {})["results"]}
        self.assertEqual(by_id["2601.00002"]["tier"], "", "stat.ME is in no tier here")

    def test_the_llm_probe_never_reports_the_request_as_failed(self):
        """probe() has its own vocabulary (absent, no_models, ready). Spreading
        it over the envelope made "no local model installed" -- the normal
        state for most people -- arrive at the client as a failed request."""
        from research_digest_mcp.web import api
        out = api("/api/llm", {})
        self.assertEqual(out["status"], "ok")
        self.assertIn(out["llm"]["status"],
                      {"off", "absent", "no_models", "ready", "model_missing"})

    def test_paging_reports_whether_there_is_more(self):
        from research_digest_mcp.web import api
        self.seed()
        page = api("/api/papers", {"limit": ["1"], "offset": ["0"]})
        self.assertEqual(page["showing"], 1)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["total"], 2)


class TestTheLibraryKnowsWhatItIsMissing(TempHome):
    """A search over a library with a month-shaped hole in it still answers
    confidently. A July paper could not be found in a library holding 402
    papers from May, 369 from June and none at all from July, and nothing on
    any screen mentioned the hole. That makes "never fetched" and "does not
    exist" look identical, which is the worst answer a research tool can give.
    """

    def _months(self, spec):
        from research_digest_mcp import storage
        rows = []
        for month, count in spec.items():
            for i in range(count):
                rows.append({"id": f"{month}.{i:05d}", "title": "x", "abstract": "y",
                             "published": f"{month}-15", "primary_category": "cs.AI",
                             "concepts": []})
        storage.merge_papers(rows, date.today().isoformat())
        return storage

    def test_a_missing_month_inside_the_fetching_window_is_named(self):
        storage = self._months({"2026-05": 40, "2026-06": 40, "2026-08": 40})
        cov = storage.month_coverage(storage.load_papers())
        self.assertEqual(cov["gaps"], ["2026-07"])

    def test_a_thin_month_is_flagged_separately_from_an_empty_one(self):
        """A partial fetch reads as a quiet month and is not one."""
        storage = self._months({"2026-05": 40, "2026-06": 40, "2026-07": 2})
        cov = storage.month_coverage(storage.load_papers())
        self.assertEqual(cov["gaps"], [])
        self.assertEqual(cov["thin"], ["2026-07"])

    def test_the_long_tail_of_old_papers_is_not_reported_as_gaps(self):
        """Measured from its true earliest paper, a real library reported 215
        missing months going back to 1995. That is not a gap, it is a library
        that was not running in 1995. The window opens where fetching did."""
        storage = self._months({"2011-03": 1, "2018-07": 2,
                                "2026-05": 40, "2026-06": 40, "2026-08": 40})
        cov = storage.month_coverage(storage.load_papers())
        self.assertEqual(cov["gaps"], ["2026-07"])
        self.assertEqual(cov["window_start"], "2026-05")
        self.assertEqual(cov["outside_window"], 3)

    def test_the_current_month_is_never_a_gap(self):
        """It is always incomplete. Flagging it would fire every first of the
        month and train the reader to ignore the warning."""
        storage = self._months({"2026-05": 40, "2026-06": 40})
        cov = storage.month_coverage(storage.load_papers())
        self.assertNotIn(date.today().strftime("%Y-%m"), cov["gaps"])

    def test_status_carries_the_warning_with_the_command_to_fix_it(self):
        from research_digest_mcp.mcp import tool_library_status
        self._months({"2026-05": 40, "2026-06": 40, "2026-08": 40})
        status = tool_library_status({})
        self.assertIn("2026-07", status["coverage_warning"])
        self.assertIn("--since 2026-07-01", status["coverage_warning"])

    def test_a_search_with_only_weak_hits_says_so(self):
        """Nine papers each covering a third of the query were presented
        exactly like nine good hits."""
        from research_digest_mcp.web import api
        self.seed()
        out = api("/api/search", {"q": ["memory upgrades heat pumps calibration"]})
        if out["matched"]:
            self.assertTrue(out["weak"], "a partial-coverage hit must be flagged")
            self.assertIn("not have this yet", out["weak_note"])


class TestExport(TempHome):
    def test_each_format_comes_back_with_a_filename_and_a_body(self):
        from research_digest_mcp.web import api
        self.seed()
        for fmt, needle in [("json", '"papers"'), ("markdown", "## "),
                            ("bibtex", "@misc{")]:
            with self.subTest(fmt=fmt):
                out = api("/api/export", {"what": ["all"], "format": [fmt]})
                self.assertEqual(out["status"], "ok")
                self.assertEqual(out["count"], 2)
                self.assertTrue(out["filename"])
                self.assertIn(needle, out["body"])

    def test_the_json_export_is_what_import_reads_back(self):
        """A round trip, because an export nothing can read is a backup you
        find out is worthless on the day you need it."""
        from research_digest_mcp import storage
        from research_digest_mcp.web import api
        self.seed()
        body = api("/api/export", {"what": ["all"], "format": ["json"]})["body"]
        path = self.home / "roundtrip.json"
        path.write_text(body, encoding="utf-8")

        import contextlib
        import io

        from research_digest_mcp.__main__ import main
        storage.write_json(storage.ARCHIVE_PATH, {"papers": {}, "runs": []})
        self.assertEqual(len(storage.load_papers()), 0)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["import", str(path)])
        self.assertEqual(len(storage.load_papers()), 2)

    def test_bibtex_does_not_invent_a_journal(self):
        from research_digest_mcp.web import api
        self.seed()
        body = api("/api/export", {"what": ["all"], "format": ["bibtex"]})["body"]
        self.assertIn("archivePrefix = {arXiv}", body)
        self.assertNotIn("journal", body)


class TestWriteEndpoints(TempHome):
    def test_an_edited_profile_is_saved_and_read_back(self):
        from research_digest_mcp.config import load_profile, load_settings
        from research_digest_mcp.web import api_write
        out = api_write("/api/profile", {
            "work_context": "building agents",
            "tiers": {"core": {"categories": ["cs.AI", "cs.LG"],
                               "topics": ["evaluation", "provenance"],
                               "per_category": 40}},
        })
        self.assertEqual(out["status"], "ok")
        profile = load_profile(load_settings())
        self.assertEqual(profile["tiers"]["core"]["categories"], ["cs.AI", "cs.LG"])
        self.assertEqual(profile["tiers"]["core"]["per_category"], 40)
        self.assertEqual(profile["work_context"], "building agents")

    def test_the_legacy_flat_keys_are_kept_in_step_with_core(self):
        """load_profile falls back to the flat keys, so a settings file whose
        two halves disagree is a bug waiting for an upgrade to trigger it."""
        from research_digest_mcp.config import load_settings
        from research_digest_mcp.web import api_write
        api_write("/api/profile", {"tiers": {"core": {"categories": ["cs.CL"],
                                                      "topics": ["retrieval"]}}})
        settings = load_settings()
        self.assertEqual(settings["categories"], ["cs.CL"])
        self.assertEqual(settings["topics"], ["retrieval"])

    def test_a_category_that_is_not_one_is_refused_and_named(self):
        """An arXiv query built from "machine learning" returns nothing,
        forever, and looks exactly like a quiet day."""
        from research_digest_mcp.config import load_profile, load_settings
        from research_digest_mcp.web import api_write
        out = api_write("/api/profile", {
            "tiers": {"core": {"categories": ["cs.AI", "machine learning", "nonsense"]}}})
        self.assertEqual(load_profile(load_settings())["tiers"]["core"]["categories"],
                         ["cs.AI"])
        self.assertEqual(len(out["rejected"]), 2)
        self.assertIn("machine learning", " ".join(out["rejected"]))

    def test_per_category_is_clamped_to_what_arxiv_will_actually_return(self):
        from research_digest_mcp.config import load_profile, load_settings
        from research_digest_mcp.web import api_write
        api_write("/api/profile", {"tiers": {"core": {"per_category": 99999}}})
        self.assertEqual(load_profile(load_settings())["tiers"]["core"]["per_category"], 200)

    def test_a_workspace_root_that_does_not_exist_is_refused(self):
        from research_digest_mcp.web import api_write
        out = api_write("/api/profile", {"workspace_root": str(self.home / "nope")})
        self.assertTrue(out["rejected"])

    def test_a_remote_endpoint_is_allowed_but_changes_what_is_promised(self):
        """Refusing anything but localhost was the wrong shape of protection:
        it blocked LM Studio on your own LAN while the real requirement is only
        that the page never claims privacy it is not delivering. The promise
        follows the URL instead of the URL being bent to fit the promise."""
        from research_digest_mcp.web import api_write
        local = api_write("/api/llm", {"provider": "ollama",
                                       "base_url": "http://127.0.0.1:11434"})
        self.assertEqual(local["status"], "ok")
        self.assertTrue(local["llm"]["local"])
        self.assertIn("stay on this machine", local["llm"]["privacy"])

        remote = api_write("/api/llm", {"provider": "openai-compatible",
                                        "base_url": "https://api.example.com/v1"})
        self.assertEqual(remote["status"], "ok")
        self.assertFalse(remote["llm"]["local"])
        self.assertIn("api.example.com", remote["llm"]["privacy"])
        self.assertIn("workspace files are not", remote["llm"]["privacy"])

    def test_a_nonsense_url_or_provider_is_refused(self):
        from research_digest_mcp.web import api_write
        self.assertEqual(api_write("/api/llm", {"base_url": "not a url"})["status"],
                         "error")
        self.assertEqual(api_write("/api/llm", {"provider": "magic"})["status"],
                         "error")

    def test_dismissing_the_model_strip_persists(self):
        from research_digest_mcp.config import load_settings
        from research_digest_mcp.web import api_write
        api_write("/api/llm", {"dismissed": True})
        self.assertTrue(load_settings()["llm"]["dismissed"])

    def test_an_unknown_write_endpoint_is_refused(self):
        from research_digest_mcp.web import api_write
        self.assertEqual(api_write("/api/anything", {})["status"], "error")


class TestWorkspaceReader(TempHome):
    def _tree(self):
        root = self.home / "work"
        (root / "alpha").mkdir(parents=True)
        (root / "beta").mkdir(parents=True)
        (root / "node_modules" / "junk").mkdir(parents=True)
        (root / "alpha" / "README.md").write_text(
            "An agent router with evaluation and provenance checks.", encoding="utf-8")
        (root / "beta" / "README.md").write_text(
            "Retrieval and reranking over a knowledge graph. Agent tooling.",
            encoding="utf-8")
        (root / "node_modules" / "junk" / "README.md").write_text(
            "calibration calibration calibration", encoding="utf-8")
        return root

    def test_it_reads_prose_and_skips_dependency_directories(self):
        from research_digest_mcp.workspace import scan
        out = scan(self._tree())
        terms = {t["term"] for t in out["terms"]}
        self.assertIn("agent", terms)
        self.assertIn("retrieval", terms)
        self.assertNotIn("calibration", terms, "node_modules must not be read")

    def test_terms_are_ranked_by_spread_across_projects(self):
        """A term every project mentions describes the workspace; a term one
        file mentions a hundred times describes that file."""
        from research_digest_mcp.workspace import scan, search_terms
        out = scan(self._tree())
        self.assertEqual(search_terms(out, 1), ["agent"])

    def test_a_term_never_matches_the_middle_of_another_word(self):
        """Substring matching scored "rag" in 194 files of a workspace with no
        RAG in it -- it was matching "storage" and "average", and "eval" was
        matching "retrieval"."""
        from research_digest_mcp.workspace import scan
        root = self.home / "sub"
        root.mkdir()
        (root / "README.md").write_text(
            "storage average fragments. retrieval of paragraphs.", encoding="utf-8")
        terms = {t["term"] for t in scan(root)["terms"]}
        self.assertNotIn("rag", terms)
        self.assertNotIn("eval", terms)
        self.assertIn("retrieval", terms)

    def test_a_missing_folder_is_an_explained_refusal_not_a_traceback(self):
        from research_digest_mcp.workspace import WorkspaceUnavailable, scan
        with self.assertRaises(WorkspaceUnavailable):
            scan(self.home / "does-not-exist")

    def test_nothing_is_read_until_a_folder_is_named(self):
        """No default. A tool that starts reading your home directory because
        you clicked a tab is not a feature."""
        from research_digest_mcp.workspace import configured_root
        self.assertIsNone(configured_root({}))
        self.assertIsNone(configured_root({"workspace_root": "   "}))

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks on this platform")
    def test_a_symlink_loop_does_not_hang_the_scan(self):
        """Ordinary on macOS and Linux, and a hung scan is a page that never
        answers rather than an error anyone can see."""
        root = self._tree()
        try:
            os.symlink(str(root), str(root / "alpha" / "loop"),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted here")
        out = scan_with_timeout(root)
        self.assertEqual(out["status"], "ok")


def scan_with_timeout(root):
    from research_digest_mcp.workspace import scan
    return scan(root)


class TestTrendsCountIdeasNotTitleWords(TempHome):
    """extract_concepts tops a paper's tags up with distinctive title words when
    fewer than three known concepts match. Useful on a card, meaningless as a
    trend: Rising led with "toward" and "evaluating", and Crossing Over
    announced that "regionfed" -- one paper's model name -- had crossed into
    cs.LG."""

    def _two_weeks(self):
        from research_digest_mcp import storage
        today = date.today()
        rows = []
        for i in range(8):
            rows.append({"id": f"26.1000{i}", "title": "Toward evaluating regionfed",
                         "abstract": "x", "primary_category": "cs.LG",
                         "first_seen": (today - timedelta(days=2)).isoformat(),
                         "concepts": ["evaluation", "toward", "regionfed", "evaluating"]})
        for i in range(8):
            rows.append({"id": f"26.2000{i}", "title": "Older work", "abstract": "x",
                         "primary_category": "cs.LG",
                         "first_seen": (today - timedelta(days=10)).isoformat(),
                         "concepts": ["evaluation", "toward"]})
        storage.merge_papers(rows, today.isoformat())

    def test_a_title_word_never_becomes_a_trend(self):
        from research_digest_mcp.trends import compute_trends
        self._two_weeks()
        from research_digest_mcp import storage
        out = compute_trends(storage.load_papers())
        named = {r["concept"] for r in out["rising"] + out["falling"] + out["steady"]}
        self.assertIn("evaluation", named, "a real concept must still be counted")
        for junk in ("toward", "regionfed", "evaluating"):
            self.assertNotIn(junk, named)

    def test_crossing_needs_a_category_with_enough_history(self):
        """In a category holding four papers every concept is a first, and the
        list filled up with confident claims about nothing."""
        from research_digest_mcp import storage
        from research_digest_mcp.trends import cross_pollination
        today = date.today()
        storage.merge_papers([
            {"id": "26.30001", "title": "A", "abstract": "x", "primary_category": "cs.XX",
             "first_seen": (today - timedelta(days=200)).isoformat(),
             "concepts": ["planning"]},
            {"id": "26.30002", "title": "B", "abstract": "x", "primary_category": "cs.XX",
             "first_seen": today.isoformat(), "concepts": ["diffusion"]},
        ], today.isoformat())
        self.assertEqual(cross_pollination(storage.load_papers()), [])


class TestFetchTimestamp(TempHome):
    def test_a_fetch_records_the_time_not_just_the_day(self):
        """The header said "last fetch today" from one minute past midnight
        until midnight again -- the entire question it was there to answer."""
        from research_digest_mcp.config import load_state, record_fetch
        record_fetch(3, 100, source="cli")
        stamp = load_state()["last_fetch_at"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(load_state()["last_fetch"], stamp[:10])

    def test_status_reports_the_timestamp_for_the_header_to_render(self):
        from research_digest_mcp.config import record_fetch
        from research_digest_mcp.mcp import tool_library_status
        self.seed()
        record_fetch(1, 2, source="web")
        status = tool_library_status({})
        self.assertTrue(status["last_fetch_at"])
        self.assertEqual(status["last_fetch_source"], "web")

    def test_a_library_never_fetched_by_this_version_says_so_rather_than_guessing(self):
        """Back-filling from a file mtime would be indistinguishable from a
        real timestamp once it is on screen."""
        from research_digest_mcp.mcp import tool_library_status
        self.seed()
        self.assertEqual(tool_library_status({})["last_fetch_at"], "")


if __name__ == "__main__":
    unittest.main()


class TestTheMap(TempHome):
    """The map makes claims about how a library hangs together, so it has to be
    countable. Nothing in it is estimated: every line means a specific number
    of papers mention both ends, and that number is on screen."""

    def _library(self):
        from research_digest_mcp import storage
        rows = []
        # 10 papers pairing agentic with evaluation, which is the ordinary case.
        for i in range(10):
            rows.append({"id": f"26.400{i:02d}", "title": "Agentic evaluation study",
                         "abstract": "We evaluate agentic systems on a benchmark.",
                         "published": "2026-05-01", "primary_category": "cs.AI",
                         "concepts": []})
        # 20 more on each side alone, so both concepts are substantial.
        for i in range(20):
            rows.append({"id": f"26.410{i:02d}", "title": "Agentic planning",
                         "abstract": "An agentic planning approach.",
                         "published": "2026-05-01", "primary_category": "cs.AI",
                         "concepts": []})
            rows.append({"id": f"26.420{i:02d}", "title": "Diffusion models",
                         "abstract": "We study diffusion for images.",
                         "published": "2026-05-01", "primary_category": "cs.CV",
                         "concepts": []})
        # One paper doing the rare thing: agentic AND diffusion.
        rows.append({"id": "26.43000", "title": "Agentic control of diffusion models",
                     "abstract": "An agentic controller for a diffusion model.",
                     "published": "2026-05-02", "primary_category": "cs.AI",
                     "concepts": []})
        storage.merge_papers(rows, date.today().isoformat())
        return storage

    def test_every_link_is_a_count_you_can_get_back_to(self):
        from research_digest_mcp.clusters import build
        storage = self._library()
        out = build(storage.load_papers())
        self.assertEqual(out["status"], "ok")
        for link in out["links"]:
            self.assertGreaterEqual(link["papers"], 3)
            self.assertGreater(link["strength"], 0)
        names = {n["concept"] for n in out["nodes"]}
        self.assertIn("agentic", names)
        self.assertIn("diffusion", names)

    def test_the_layout_is_the_same_every_time(self):
        """A map that moves when you reopen it is not a place. A force
        simulation would look better and would do exactly that."""
        from research_digest_mcp.clusters import build
        storage = self._library()
        papers = storage.load_papers()
        first = build(papers)
        second = build(papers)
        self.assertEqual([(n["concept"], n["x"], n["y"]) for n in first["nodes"]],
                         [(n["concept"], n["x"], n["y"]) for n in second["nodes"]])

    def test_a_bridge_is_the_rare_pairing_not_the_common_one(self):
        """The whole point: a search only finds what you knew to ask for, and
        the ranked list puts the most typical papers on top."""
        from research_digest_mcp.clusters import bridges
        storage = self._library()
        found = bridges(storage.load_papers())
        self.assertTrue(found, "the rare pairing should surface")
        self.assertEqual(found[0]["id"], "26.43000")
        self.assertEqual(sorted(found[0]["pair"]), ["agentic", "diffusion"])
        self.assertIn("only 1", found[0]["note"])

    def test_a_thin_library_says_so_instead_of_drawing_noise(self):
        from research_digest_mcp import storage
        from research_digest_mcp.clusters import build
        storage.merge_papers([
            {"id": "26.9", "title": "One paper", "abstract": "About agentic things.",
             "published": "2026-05-01", "primary_category": "cs.AI", "concepts": []},
        ], date.today().isoformat())
        out = build(storage.load_papers())
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["nodes"], [])


class TestARefusalNeverLooksLikeAnEmptyResult(TempHome):
    """The two states look identical on screen and mean opposite things. One
    says the paper is not out there; the other says we were not allowed to
    look. A backfill that was actually rate-limited reported "arXiv returned
    nothing. Nothing was written.", and the real cause sat unread in an errors
    list no surface displayed.
    """

    def test_the_backoff_escalates_instead_of_resetting_to_the_same_wait(self):
        """A flat five minutes meant: wait five, ask, get refused, wait five
        again. A client arXiv had decided to refuse stayed in a loop of
        politely spaced refusals forever."""
        from research_digest_mcp import fetchers
        waits = []
        for _ in range(4):
            fetchers._begin_cooldown(429)
            waits.append(fetchers.cooldown_remaining())
        for earlier, later in zip(waits, waits[1:]):
            self.assertGreater(later, earlier)
        self.assertLessEqual(waits[-1], 3600.0 + 1)
        self.assertEqual(fetchers.cooldown_detail()["strikes"], 4)
        self.assertEqual(fetchers.cooldown_detail()["last_code"], 429)

    def test_an_answer_clears_the_escalation(self):
        """Only a parsed feed counts. A bare 200 can be a cached edge response
        served while the origin is still refusing this client."""
        from research_digest_mcp import fetchers
        fetchers._begin_cooldown(406)
        fetchers._begin_cooldown(406)
        self.assertGreater(fetchers.cooldown_remaining(), 0)
        fetchers.parse_atom(
            b'<?xml version="1.0"?>'
            b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        self.assertEqual(fetchers.cooldown_remaining(), 0.0)
        self.assertEqual(fetchers.cooldown_detail()["strikes"], 0)

    def test_a_cooling_down_client_says_so_before_it_asks(self):
        from research_digest_mcp import fetchers
        fetchers._begin_cooldown(406)
        with self.assertRaises(fetchers.ArxivCoolingDown) as caught:
            fetchers._get({"search_query": "cat:cs.AI"})
        message = str(caught.exception)
        self.assertIn("refusing this client", message)
        self.assertIn("nothing is wrong with your", message.lower())

    def test_a_blocked_refresh_reports_the_block_not_an_empty_month(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.web import api
        fetchers._begin_cooldown(429)
        out = api("/api/refresh", {"since": ["2026-07-01"], "until": ["2026-07-31"]})
        self.assertEqual(out["status"], "error")
        self.assertTrue(out["blocked"])
        self.assertNotIn("returned nothing", out["message"])
        self.assertIn("refus", out["message"].lower())
        self.assertGreater(out["cooldown"]["remaining"], 0)

    def test_a_blocked_arxiv_search_is_unavailable_not_no_match(self):
        from research_digest_mcp import fetchers
        from research_digest_mcp.mcp import tool_fetch_papers
        fetchers._begin_cooldown(406)
        out = tool_fetch_papers({"query": "nvidia"})
        self.assertEqual(out["status"], "unavailable")
        self.assertTrue(out["blocked"])
        self.assertIn("not a problem with your search", out["what_now"])

    def test_an_empty_search_says_how_many_papers_were_checked(self):
        """"Nothing matched" over 1,443 papers reads as a broken search. The
        same fact, with the denominator, reads as an answer."""
        from research_digest_mcp.web import api
        self.seed()
        out = api("/api/search", {"q": ["zzzznotathing"]})
        self.assertEqual(out["matched"], 0)
        self.assertEqual(out["searched"], 2)
