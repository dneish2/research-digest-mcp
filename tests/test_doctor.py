"""What `doctor` tells a new user to paste into their agent's config."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_digest_mcp import doctor  # noqa: E402


class TestTheConfigLineSurvivesTheInstall(unittest.TestCase):
    def test_a_normal_install_points_at_its_own_python(self):
        with mock.patch.object(doctor, "_uvx_source", return_value=""):
            text = doctor._mcp_config_lines()
        self.assertIn("-m research_digest_mcp mcp", text)
        self.assertNotIn("uvx", text)

    def test_a_uvx_run_is_told_to_configure_uvx_not_a_cached_python(self):
        # Pointing at a python inside uv's cache breaks when the cache is cleaned.
        source = "git+https://github.com/dneish2/research-digest-mcp"
        with mock.patch.object(doctor, "_uvx_source", return_value=source):
            text = doctor._mcp_config_lines()
        self.assertIn(f'uvx --from "{source}" research-digest mcp', text)
        self.assertNotIn(sys.executable, text)

    def test_a_windows_path_is_escaped_in_toml(self):
        with mock.patch.object(doctor, "_uvx_source", return_value=r"C:\Users\me\rd"):
            text = doctor._mcp_config_lines()
        self.assertIn(r'args = ["--from", "C:\\Users\\me\\rd"', text)

    def test_a_python_in_uvs_cache_is_recognised(self):
        cached = Path.home() / ".cache" / "uv" / "archive-v0" / "abc123"
        with mock.patch.object(doctor.sys, "prefix", str(cached)):
            self.assertTrue(doctor._uvx_source())
        with mock.patch.object(doctor.sys, "prefix", str(Path.home() / ".venv")):
            self.assertEqual(doctor._uvx_source(), "")


class TestCounts(unittest.TestCase):
    def test_one_is_singular(self):
        self.assertEqual(doctor._count(1, "category", "categories"), "1 category")
        self.assertEqual(doctor._count(3, "category", "categories"), "3 categories")


if __name__ == "__main__":
    unittest.main()
