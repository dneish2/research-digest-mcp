"""What a web page in another tab, or a paper abstract an agent reads, can do.

Found by a security pass on 2026-10-18, each one proven with a working probe
before it was fixed:

- the digest date became a filename unchecked, so `date=../../notes` wrote a
  markdown file anywhere on disk, from a URL or from an agent's tool call;
- GET endpoints that save, fetch or write were open to an <img> tag on any site;
- the Origin check trusted whatever Host the request named, so a DNS-rebinding
  page passed it and could read the LLM key out of /api/settings.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TempHome(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        os.environ["RESEARCH_DIGEST_HOME"] = self._dir.name
        for mod in [m for m in list(sys.modules) if m.startswith("research_digest_mcp")]:
            del sys.modules[mod]
        from research_digest_mcp import storage
        storage.merge_papers([{"id": "2610.22280v1", "title": "Agent evaluation under load",
                               "abstract": "We evaluate agents on evaluation tasks.",
                               "published": "2026-10-15", "primary_category": "cs.AI",
                               "url": "https://arxiv.org/abs/2610.22280v1"}], "2026-10-18")

    def tearDown(self):
        self._dir.cleanup()
        os.environ.pop("RESEARCH_DIGEST_HOME", None)


class TestTheDigestDateIsADate(TempHome):
    def test_a_path_in_the_date_is_refused_and_nothing_is_written(self):
        from research_digest_mcp.digest import build_digest
        outside = Path(self._dir.name) / "planted.md"
        for bad in ["../planted", "../../planted", "C:/planted", "2026-10-18/../../x", "tomorrow"]:
            with self.subTest(date=bad), self.assertRaises(ValueError):
                build_digest([], ["evaluation"], for_date=bad)
        self.assertFalse(outside.exists())

    def test_an_impossible_calendar_date_is_refused(self):
        from research_digest_mcp.digest import check_date
        with self.assertRaises(ValueError):
            check_date("2026-13-40")
        self.assertEqual(check_date("2026-10-18"), "2026-10-18")

    def test_the_agent_gets_an_error_it_can_read_not_a_crash(self):
        from research_digest_mcp.mcp import tool_get_digest
        name = f"planted-{os.getpid()}-{id(self)}"
        out = tool_get_digest({"date": f"../../{name}"})
        self.assertEqual(out["status"], "error")
        self.assertIn("YYYY-MM-DD", out["message"])
        self.assertFalse((Path(self._dir.name).parent / f"{name}.md").exists())

    def test_the_web_endpoint_refuses_it_too(self):
        from research_digest_mcp.web import api
        out = api("/api/digest", {"date": ["../../notes"]})
        self.assertEqual(out["status"], "error")


class TestOnlyThisPageCanCallTheServer(TempHome):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        from research_digest_mcp.web import Handler
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest(method, path, skip_host=True)
        for key, value in {"Host": f"127.0.0.1:{self.port}", **(headers or {})}.items():
            conn.putheader(key, value)
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_the_page_itself_still_works(self):
        status, _ = self.request("GET", "/api/settings", {"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)
        status, _ = self.request("GET", "/")
        self.assertEqual(status, 200)

    def test_a_rebinding_host_is_refused(self):
        status, _ = self.request("GET", "/api/settings", {"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/llm", {
            "Host": f"evil.example:{self.port}", "Origin": f"http://evil.example:{self.port}",
            "Content-Type": "application/json", "Content-Length": "2"}, b"{}")
        self.assertEqual(status, 403)

    def test_a_cross_site_get_cannot_save_a_paper(self):
        from research_digest_mcp import storage
        status, _ = self.request("GET", "/api/save?id=2610.22280",
                                 {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(storage.load_saved(), {})

    def test_a_foreign_origin_is_refused_on_get(self):
        status, _ = self.request("GET", "/api/status", {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_settings_never_carry_the_key(self):
        from research_digest_mcp.config import load_settings, save_settings
        settings = load_settings()
        settings["llm"] = {"provider": "openai", "api_key": "sk-test-secret"}
        save_settings(settings)
        status, data = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertNotIn(b"sk-test-secret", data)
        self.assertTrue(json.loads(data)["llm"]["has_key"])

    def test_a_negative_length_is_rejected_instead_of_hanging(self):
        status, _ = self.request("POST", "/api/profile", {"Content-Length": "-1"})
        self.assertEqual(status, 400)


class TestSimilarAcceptsEitherIdForm(TempHome):
    def setUp(self):
        super().setUp()
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy is the optional [embeddings] extra")

    def test_a_versioned_id_finds_a_paper_stored_bare_and_the_reverse(self):
        import numpy as np
        from research_digest_mcp.similarity import SimilaritySearch

        class Store:
            def __init__(self, ids):
                self.ids = ids

            def load_matrix(self):
                return np.eye(3, dtype=np.float32) + 0.1, self.ids

        bare = SimilaritySearch(Store(["2610.1", "2610.2", "2610.3"]))
        self.assertEqual(len(bare.find_similar("2610.1v1", 2)), 2)
        versioned = SimilaritySearch(Store(["2610.1v2", "2610.2v1", "2610.3v1"]))
        hits = versioned.find_similar("2610.1", 2)
        self.assertEqual(len(hits), 2)
        self.assertNotIn("2610.1v2", [pid for pid, _ in hits])


class TestToolsSayWhatTheyTouch(unittest.TestCase):
    def test_exactly_the_three_writing_tools_are_marked_as_writing(self):
        from research_digest_mcp.mcp import TOOLS
        writing = sorted(t["name"] for t in TOOLS if not t["annotations"]["readOnlyHint"])
        self.assertEqual(writing, ["fetch_papers", "get_digest", "save_paper"])
        self.assertFalse(any(t["annotations"].get("destructiveHint") for t in TOOLS))

    def test_a_huge_or_junk_limit_is_kept_in_range(self):
        from research_digest_mcp.mcp import _limit
        self.assertEqual(_limit({"limit": 10**9}, 10), 100)
        self.assertEqual(_limit({"limit": -5}, 10), 1)
        self.assertEqual(_limit({"limit": "lots"}, 10), 10)


if __name__ == "__main__":
    unittest.main()
