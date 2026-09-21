"""Tests for bulk harvesting, against a real OAI response shape.

The fixture below is trimmed from an actual oaipmh.arxiv.org reply. No test
here touches the network: the point of the harvester is what it does with the
bytes, and the endpoint's availability is not something a test suite can pin.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
 <ListRecords>
  <record>
   <header><identifier>oai:arXiv.org:2607.00001</identifier>
           <datestamp>2026-07-05</datestamp>
           <setSpec>cs:cs:AI</setSpec></header>
   <metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
     <id>2607.00001</id>
     <created>2026-07-05</created>
     <updated>2026-07-09</updated>
     <authors>
       <author><keyname>Lovelace</keyname><forenames>Ada</forenames></author>
       <author><keyname>Hopper</keyname><forenames>Grace</forenames></author>
     </authors>
     <title>An agent that evaluates itself</title>
     <categories>cs.AI cs.LG</categories>
     <comments>Accepted at NeurIPS 2026</comments>
     <journal-ref>JMLR 2026</journal-ref>
     <doi>10.1234/example</doi>
     <license>http://arxiv.org/licenses/nonexclusive-distrib/1.0/</license>
     <abstract>We propose a self-evaluating agent.</abstract>
    </arXiv>
   </metadata>
  </record>
  <record>
   <header><identifier>oai:arXiv.org:1803.07225</identifier>
           <datestamp>2026-07-05</datestamp></header>
   <metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
     <id>1803.07225</id>
     <created>2018-03-20</created>
     <updated>2026-07-05</updated>
     <authors><author><keyname>Nielsen</keyname><forenames>Frank</forenames></author></authors>
     <title>Monte Carlo Information Geometry</title>
     <categories>cs.LG stat.ML</categories>
     <abstract>An older paper, revised in July.</abstract>
    </arXiv>
   </metadata>
  </record>
  <record>
   <header status="deleted"><identifier>oai:arXiv.org:2607.99999</identifier>
           <datestamp>2026-07-05</datestamp></header>
  </record>
 </ListRecords>
</OAI-PMH>"""


class TempHome(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["RESEARCH_DIGEST_HOME"] = self._dir.name
        for mod in [m for m in list(sys.modules) if m.startswith("research_digest_mcp")]:
            del sys.modules[mod]

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)

    def _harvest(self, **kw):
        """Run a harvest with the network replaced by the fixture."""
        from research_digest_mcp import harvest
        harvest._request = lambda params, timeout=90: FEED.encode("utf-8")
        return harvest

    def test_it_keeps_the_fields_the_search_api_throws_away(self):
        h = self._harvest()
        papers = list(h.harvest("cs", "2026-07-01"))
        first = papers[0]
        self.assertEqual(first["doi"], "10.1234/example")
        self.assertEqual(first["journal_ref"], "JMLR 2026")
        self.assertIn("NeurIPS", first["comment"])
        self.assertTrue(first["license"])
        self.assertEqual(first["authors"], ["Ada Lovelace", "Grace Hopper"])

    def test_created_and_updated_are_kept_apart(self):
        """The search API conflates them, so a revision looks like a new paper."""
        h = self._harvest()
        first = list(h.harvest("cs", "2026-07-01"))[0]
        self.assertEqual(first["published"], "2026-07-05")
        self.assertEqual(first["updated"], "2026-07-09")

    def test_a_deleted_record_is_skipped_not_crashed_on(self):
        h = self._harvest()
        self.assertEqual(len(list(h.harvest("cs", "2026-07-01"))), 2)

    def test_the_datestamp_trap_is_filterable(self):
        """`from`/`until` filter on last-modified, not publication. Harvesting
        July returns papers created in July AND older papers revised in July.
        A backfill aimed at a hole in July must filter on `created` or it fills
        the hole with 2018 papers."""
        h = self._harvest()
        profile = {"all_categories": ["cs.AI", "cs.LG"]}

        everything = h.harvest_profile(profile, "2026-07-01", "2026-07-31")
        self.assertEqual(len(everything["papers"]), 2)

        july_only = h.harvest_profile(profile, "2026-07-01", "2026-07-31",
                                      published_only=True)
        self.assertEqual([p["id"] for p in july_only["papers"]], ["2607.00001"])

    def test_only_your_categories_are_kept(self):
        h = self._harvest()
        out = h.harvest_profile({"all_categories": ["stat.ML"]}, "2026-07-01")
        self.assertEqual([p["id"] for p in out["papers"]], ["1803.07225"])

    def test_harvested_papers_are_stamped_with_their_origin(self):
        h = self._harvest()
        out = h.harvest_profile({"all_categories": ["cs.AI"]}, "2026-07-01")
        self.assertEqual(out["papers"][0]["source"], "oai")

    def test_fine_grained_categories_map_to_the_archives_oai_serves(self):
        """arXiv's OAI sets are archives. There is no cs.AI set, and asking for
        one returns nothing at all."""
        from research_digest_mcp.harvest import sets_for_categories
        self.assertEqual(
            sets_for_categories(["cs.AI", "cs.LG", "stat.ME", "q-fin.TR"]),
            ["cs", "stat", "q-fin"])
        self.assertEqual(sets_for_categories(["quant-ph"]), ["physics:quant-ph"])

    def test_no_records_is_an_answer_not_an_outage(self):
        from research_digest_mcp import harvest
        harvest._request = lambda params, timeout=90: (
            b'<?xml version="1.0"?><OAI-PMH '
            b'xmlns="http://www.openarchives.org/OAI/2.0/">'
            b'<error code="noRecordsMatch">nothing</error></OAI-PMH>')
        self.assertEqual(list(harvest.harvest("cs", "2030-01-01")), [])

    def test_a_real_error_is_raised_with_its_code(self):
        from research_digest_mcp import harvest
        harvest._request = lambda params, timeout=90: (
            b'<?xml version="1.0"?><OAI-PMH '
            b'xmlns="http://www.openarchives.org/OAI/2.0/">'
            b'<error code="badArgument">nope</error></OAI-PMH>')
        with self.assertRaises(harvest.HarvestUnavailable) as caught:
            list(harvest.harvest("cs", "2026-07-01"))
        self.assertIn("badArgument", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
