"""A release names one version everywhere, or the publish workflow uploads a
package whose registry entry and citation point at a different one."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OneVersion(unittest.TestCase):
    def test_every_place_that_states_the_version_agrees(self):
        from research_digest_mcp import __version__, mcp
        pyproject = re.search(r'^version = "([^"]+)"',
                              (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        citation = re.search(r"^version: (\S+)",
                             (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.M).group(1)
        found = {"pyproject.toml": pyproject, "__init__": __version__,
                 "mcp.SERVER_VERSION": mcp.SERVER_VERSION, "server.json": server["version"],
                 "server.json package": server["packages"][0]["version"],
                 "CITATION.cff": citation}
        self.assertEqual(len(set(found.values())), 1, found)

    def test_the_registry_name_is_in_the_readme(self):
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"mcp-name: {server['name']}", readme)


if __name__ == "__main__":
    unittest.main()
