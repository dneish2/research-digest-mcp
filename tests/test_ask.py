"""Tests for the question box: reading English, and refusing to trust a model.

The question path has two halves that fail in opposite directions. The rule
parser fails by keeping too much -- question words become search terms and the
subject drowns. The model fails by volunteering too much -- it answers with the
user's whole standing profile, or decides the question warrants a network
request nobody asked for. Both halves are pinned here, and the model half is
pinned without a model present, by feeding `validate` the exact shapes a real
local model returned.
"""
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


class TestReadingAQuestion(TempHome):
    def test_the_asking_is_stripped_and_the_subject_survives(self):
        """The failure that started this: nine terms, six of them question words.

        "can you find me papers related to finance ai" ranked 439 papers led by
        one on healthcare workforce readiness, because "can", "you", "find",
        "me", "papers" and "to" outnumbered "finance".
        """
        from research_digest_mcp.askparse import plan
        got = plan("can you find me papers related to finance ai")
        self.assertEqual(got["terms"], ["finance", "ai"])

    def test_judgement_words_are_not_subject_matter(self):
        from research_digest_mcp.askparse import plan
        self.assertEqual(plan("what are the best papers on retrieval")["terms"],
                         ["retrieval"])

    def test_container_words_go_even_mid_sentence(self):
        from research_digest_mcp.askparse import plan
        self.assertEqual(plan("show me the top papers about calibration")["terms"],
                         ["calibration"])

    def test_a_hyphenated_query_reaches_the_unhyphenated_paper(self):
        """The bug that made "LLM-as-judge" find 8 papers in a library of 1,443.

        _match_set opened compounds on the text side only, so the query had to
        guess the author's hyphenation exactly.
        """
        from research_digest_mcp.scoring import score_query
        paper = {"title": "Using an LLM as judge for evaluation",
                 "abstract": "We study judging with language models.", "concepts": []}
        self.assertIsNotNone(score_query(paper, ["llm-as-judge"]))

    def test_one_half_of_a_compound_is_not_a_match_regression(self):
        """The fix above, overdone. Splitting "nvidia-labs" into two independent
        terms meant a paper saying "labs" anywhere counted as a third of the
        query and came back as a result: a search for NVIDIA-labs returned a
        paper about animal welfare in AI travel agents. A compound is one idea.
        The paper has it, or has all of its parts, or does not have it."""
        from research_digest_mcp.scoring import score_query
        half = {"title": "Your AI travel agent would book you a bullfight",
                "abstract": "Animal welfare in frontier labs and their models.",
                "concepts": []}
        other_half = {"title": "GPU fault resilience in NVIDIA MPS",
                      "abstract": "We design fault-resilient MPS.", "concepts": []}
        both = {"title": "A report from the NVIDIA labs team",
                "abstract": "Work done at NVIDIA labs.", "concepts": []}
        self.assertIsNone(score_query(half, ["nvidia-labs"]))
        self.assertIsNone(score_query(other_half, ["nvidia-labs"]))
        self.assertIsNotNone(score_query(both, ["nvidia-labs"]))

    def test_scope_is_read_from_the_question(self):
        from research_digest_mcp.askparse import detect_scope
        self.assertEqual(detect_scope("anything new on arxiv about agents"), "arxiv")
        self.assertEqual(detect_scope("what relates to my local workspace"), "workspace")
        self.assertEqual(detect_scope("show me my saved papers"), "saved")
        self.assertEqual(detect_scope("agent evaluation"), "library")

    def test_a_workspace_question_is_a_workspace_question_first(self):
        """Both cues present: the workspace supplies the terms, so it wins."""
        from research_digest_mcp.askparse import detect_scope
        self.assertEqual(
            detect_scope("anything new on arxiv related to my codebase"), "workspace")

    def test_relative_dates_become_a_floor(self):
        from research_digest_mcp.askparse import detect_since
        today = date(2026, 9, 17)
        self.assertEqual(detect_since("anything from this week", today),
                         (today - timedelta(days=7)).isoformat())
        self.assertEqual(detect_since("papers since 2026-07-01", today), "2026-07-01")
        self.assertEqual(detect_since("papers in July", today), "2026-07-01")
        self.assertIsNone(detect_since("papers about agents", today))

    def test_a_month_that_has_not_happened_means_last_year(self):
        from research_digest_mcp.askparse import detect_since
        self.assertEqual(detect_since("work in December", date(2026, 3, 1)),
                         "2025-12-01")

    def test_scope_words_never_become_search_terms(self):
        """"my local workspace" told us where to look, not what to look for.

        Without this the library was searched for the literal word "workspace".
        """
        from research_digest_mcp.askparse import plan
        terms = plan("anything related to whats on my local workspace")["terms"]
        for leaked in ("local", "workspace", "related", "anything"):
            self.assertNotIn(leaked, terms)

    def test_a_question_with_no_subject_says_so(self):
        from research_digest_mcp.askparse import plan
        self.assertEqual(plan("can you find me some papers")["terms"], [])


class TestTrustingAModel(TempHome):
    """`validate` is the trust boundary. These are real replies from qwen2.5."""

    def _base(self, question="anything new on prompt injection this week"):
        from research_digest_mcp.askparse import plan
        return plan(question)

    def test_a_model_may_reword_the_question_not_retopic_it(self):
        """The observed failure: asked about prompt injection, the model
        answered with the user's whole standing profile. Ranking is by
        coverage, so those extra words pushed the papers actually about prompt
        injection from 2/2 down to 2/5."""
        from research_digest_mcp.askparse import validate
        base = self._base()
        out = validate({"terms": ["prompt", "injection", "evaluation",
                                  "reliability", "hallucination"]}, base)
        self.assertEqual(out["terms"], ["prompt", "injection"])
        for volunteered in ("evaluation", "reliability", "hallucination"):
            self.assertIn(volunteered, out["extra_terms"])

    def test_a_model_cannot_decide_to_go_online(self):
        """Scope decides whether a question causes a network request. Asked
        about "finance ai", the model answered scope=arxiv for a question
        containing no word about anything being new."""
        from research_digest_mcp.askparse import validate
        base = self._base("can you find me papers related to finance ai")
        self.assertEqual(base["scope"], "library")
        out = validate({"terms": ["finance"], "scope": "arxiv"}, base)
        self.assertEqual(out["scope"], "library")

    def test_a_future_date_is_refused(self):
        from research_digest_mcp.askparse import validate
        base = self._base()
        future = (date.today() + timedelta(days=400)).isoformat()
        self.assertEqual(validate({"terms": ["prompt"], "since": future}, base)["since"],
                         base["since"])

    def test_junk_falls_back_to_the_rule_plan(self):
        from research_digest_mcp.askparse import validate
        base = self._base()
        for junk in (None, [], "not json", {"terms": []}, {"terms": ["zzzz"]},
                     {"scope": "everything"}):
            out = validate(junk, base)
            self.assertEqual(out["terms"], base["terms"], junk)

    def test_a_reply_wrapped_in_prose_or_a_fence_still_parses(self):
        """Models add a sentence before the JSON however firmly asked not to.
        That is why the reply is parsed, not merely requested."""
        from research_digest_mcp.llm import _extract_json
        self.assertEqual(
            _extract_json('Sure! ```json\n{"terms": ["agent"]}\n``` hope that helps'),
            {"terms": ["agent"]})
        self.assertEqual(_extract_json('{"terms": ["a"], "nested": {"x": 1}} trailing'),
                         {"terms": ["a"], "nested": {"x": 1}})
        self.assertIsNone(_extract_json("no json at all"))
        self.assertIsNone(_extract_json('{"broken": '))


class TestAnsweringWithoutAModel(TempHome):
    """Every one of these runs with use_llm=False: the feature must not need one."""

    def _library(self):
        from research_digest_mcp import storage
        storage.merge_papers([
            {"id": "2601.00001", "title": "Agent memory that survives upgrades",
             "abstract": "We study memory portability across model upgrades.",
             "published": date.today().isoformat(), "primary_category": "cs.AI",
             "concepts": ["memory", "agent"]},
            {"id": "2601.00002", "title": "A survey of heat pumps",
             "abstract": "We review HVAC systems.", "published": "2026-01-02",
             "primary_category": "eess.SY", "concepts": []},
        ], date.today().isoformat())

    def test_a_question_is_answered_with_no_model_configured(self):
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("what should I read about agent memory?", {}, use_llm=False)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["results"][0]["id"], "2601.00001")
        self.assertEqual(out["plan"]["source"], "rules")

    def test_a_typo_is_dropped_and_reported_not_silently_diluted(self):
        """A term in zero papers cannot promote anything, and because ranking
        is by coverage it drags every real result down by the same amount."""
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("anything about agent memroy", {}, use_llm=False)
        self.assertIn("memroy", out["ignored_terms"])
        self.assertIn("memroy", out["reading"])
        self.assertEqual(out["results"][0]["id"], "2601.00001")

    def test_a_question_of_pure_typos_refuses_rather_than_guesses(self):
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("anything about zzqq wwxx", {}, use_llm=False)
        self.assertEqual(out["status"], "no_subject")
        self.assertEqual(out["results"], [])

    def test_the_date_window_is_applied_not_just_reported(self):
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("anything about heat pumps this week", {}, use_llm=False)
        self.assertEqual(out["matched"], 0, "a January paper is not from this week")

    def test_a_workspace_question_with_no_folder_set_explains_itself(self):
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("what relates to my local workspace?", {}, use_llm=False)
        self.assertEqual(out["status"], "needs_workspace")
        self.assertIn("workspace", out["message"].lower())

    def test_the_reading_line_names_what_was_actually_searched(self):
        """The line that makes an English search box arguable instead of magic."""
        from research_digest_mcp.ask import answer
        self._library()
        out = answer("papers on agent memory", {}, use_llm=False)
        self.assertIn("agent", out["reading"])
        self.assertIn("memory", out["reading"])


if __name__ == "__main__":
    unittest.main()


class TestWhatSearchIsAllowedToRead(TempHome):
    """Author names sat on 1,182 of 1,443 papers and were never read, so author
    search did not exist. Affiliation, comment and journal_ref were not even
    parsed out of the feed, and affiliation is the only field that can answer
    "papers out of NVIDIA"."""

    def _paper(self, **over):
        base = {"id": "26.1", "title": "A study of widgets",
                "abstract": "We propose a widget.", "concepts": [],
                "authors": ["Ada Lovelace", "Grace Hopper"],
                "affiliations": ["NVIDIA"], "comment": "Accepted at NeurIPS 2026",
                "journal_ref": "JMLR 2026"}
        base.update(over)
        return base

    def test_an_author_is_findable(self):
        from research_digest_mcp.scoring import score_query
        self.assertIsNotNone(score_query(self._paper(), ["lovelace"]))
        self.assertIsNotNone(score_query(self._paper(), ["grace", "hopper"]))

    def test_an_affiliation_is_findable_when_arxiv_supplies_one(self):
        from research_digest_mcp.scoring import score_query
        self.assertIsNotNone(score_query(self._paper(), ["nvidia"]))
        self.assertIsNone(score_query(self._paper(affiliations=[]), ["nvidia"]))

    def test_a_venue_is_findable_through_the_comment_field(self):
        from research_digest_mcp.scoring import score_query
        self.assertIsNotNone(score_query(self._paper(), ["neurips"]))

    def test_the_feed_parser_keeps_the_fields_search_needs(self):
        """They were being dropped on the floor at parse time, so no amount of
        work in the scorer could have found them."""
        from research_digest_mcp.fetchers import parse_atom
        feed = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            '      xmlns:arxiv="http://arxiv.org/schemas/atom">'
            '<entry>'
            '<id>http://arxiv.org/abs/2601.00001v1</id>'
            '<title>Fast widgets</title><summary>We make widgets fast.</summary>'
            '<published>2026-01-01T00:00:00Z</published>'
            '<updated>2026-01-02T00:00:00Z</updated>'
            '<author><name>Ada Lovelace</name>'
            '  <arxiv:affiliation>NVIDIA</arxiv:affiliation></author>'
            '<author><name>Grace Hopper</name></author>'
            '<arxiv:comment>Accepted at NeurIPS 2026</arxiv:comment>'
            '<arxiv:journal_ref>JMLR 2026</arxiv:journal_ref>'
            '<arxiv:primary_category term="cs.AI"/>'
            '<category term="cs.AI"/>'
            '</entry></feed>'
        ).encode("utf-8")
        paper = parse_atom(feed)[0]
        self.assertEqual(paper["authors"], ["Ada Lovelace", "Grace Hopper"])
        self.assertEqual(paper["affiliations"], ["NVIDIA"])
        self.assertIn("NeurIPS", paper["comment"])
        self.assertIn("JMLR", paper["journal_ref"])

    def test_a_paper_with_none_of_these_fields_still_works(self):
        """Every paper fetched before this change lacks all four."""
        from research_digest_mcp.scoring import paper_text, score_query
        bare = {"id": "26.2", "title": "Widgets", "abstract": "About widgets."}
        self.assertIn("widgets", paper_text(bare))
        self.assertIsNotNone(score_query(bare, ["widgets"]))
