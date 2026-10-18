"""The README is the install guide, so what it says about the tool is tested."""
import re
import unittest
from pathlib import Path

from research_digest_mcp import mcp

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
WORDS = {9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen"}


class ReadmeMatchesTheServer(unittest.TestCase):
    def test_every_tool_is_in_the_table(self):
        names = [t["name"] for t in mcp.TOOLS]
        section = README.split("## The tools", 1)[1].split("\n---", 1)[0]
        listed = re.findall(r"^\| `(\w+)` \|", section, flags=re.M)
        self.assertEqual(sorted(names), sorted(listed))

    def test_the_tool_count_it_states_is_the_real_one(self):
        # It said "eleven" while the server listed twelve.
        self.assertIn(f"listing the {WORDS[len(mcp.TOOLS)]} tools", README)


if __name__ == "__main__":
    unittest.main()
