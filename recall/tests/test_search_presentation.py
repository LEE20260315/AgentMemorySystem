"""Presentation-layer tests for store.search(): cross-source folding, hit
anchoring, and AND-first ranking.

These lock the display behaviour added for the "top-5 wasted on identical
copies" fix. Nothing here may write to the index — folding is display-time only.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import store  # noqa: E402


class SearchPresentationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "idx.db")
        self.conn = store.connect(self.db)
        self._ts = "2026-01-01T00:00:00"

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _add(self, agent: str, file: str, title: str, text: str) -> None:
        store.replace_file_entries(
            self.conn, agent, file,
            [{"title": title, "text": text, "fingerprint": f"{agent}:{file}"}],
            self._ts)

    def _count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    # 1) identical content from 3 agents -> folded to 1 result with 3 sources
    def test_fold_identical_content(self):
        text = "koa-connect wrapper caused subtle ctx.state data loss; native rewrite"
        for agent in ("pi", "dsh", "trae"):
            self._add(agent, f"{agent}.md", "Lessons Learned", text)
        results = store.search(self.conn, "koa-connect", k=5)
        self.assertEqual(len(results), 1, "identical content must fold to one result")
        self.assertEqual(results[0]["n_sources"], 3)
        self.assertEqual(len(results[0]["sources"]), 3)
        agents = {s["agent"] for s in results[0]["sources"]}
        self.assertEqual(agents, {"pi", "dsh", "trae"})

    # whitespace/case normalisation still folds; genuinely different text does not
    def test_fold_normalized_but_not_different(self):
        self._add("a", "a.md", "T", "Alpha   Beta\nGamma")
        self._add("b", "b.md", "T", "alpha beta gamma")           # same after norm
        self._add("c", "c.md", "T", "alpha beta gamma DELTA")     # different
        results = store.search(self.conn, "alpha", k=5)
        self.assertEqual(len(results), 2, "normalised-dupes fold, real diffs don't")
        ns = sorted(r["n_sources"] for r in results)
        self.assertEqual(ns, [1, 2])

    # 2) different content -> not folded
    def test_no_fold_for_distinct_content(self):
        self._add("a", "a.md", "One", "unique content one about 冲突")
        self._add("b", "b.md", "Two", "totally different two about 冲突")
        results = store.search(self.conn, "冲突", k=5)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["n_sources"] == 1 for r in results))

    # 3) anchoring: term buried at char ~3000 must appear in the snippet
    def test_snippet_anchors_to_hit(self):
        body = ("x" * 3000) + " 托盘 " + ("y" * 1000)
        self._add("a", "deep.md", "Deep", body)
        results = store.search(self.conn, "托盘", k=5)
        self.assertEqual(len(results), 1)
        snippet = results[0]["snippet"]
        self.assertIn("托盘", snippet, f"snippet must contain the hit; got: {snippet!r}")
        self.assertTrue(snippet.startswith("…"))
        self.assertTrue(snippet.endswith("…"))

    # 3b) title-only hit -> snippet falls back to head of body
    def test_snippet_fallback_when_only_title_hits(self):
        self._add("a", "t.md", "标题含 触发词", "正文完全没有那个词")
        results = store.search(self.conn, "触发词", k=5)
        self.assertEqual(len(results), 1)
        self.assertIn("触发词", results[0]["matched_terms"])
        self.assertIn("正文完全没有那个词", results[0]["snippet"])

    # 4) AND-first: a row with both terms outranks a row with only one
    def test_and_first_ordering(self):
        self._add("a", "both.md", "B", "alpha and beta together")
        self._add("b", "one.md", "O", "alpha only here")
        results = store.search(self.conn, "alpha beta", k=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["file"], "both.md",
                         "row matching BOTH terms must rank first")
        self.assertEqual(results[0]["matched_terms"], ["alpha", "beta"])

    # folding & snippet are read-only: the index row count is unchanged
    def test_search_does_not_write(self):
        self._add("a", "a.md", "T", "hello 世界")
        self._add("b", "b.md", "T", "hello 世界")
        before = self._count()
        store.search(self.conn, "世界", k=5)
        self.assertEqual(self._count(), before, "search must not modify the index")

    # 5) DOC regression guard: two DIFFERENT memories sharing a long boilerplate
    #    prefix must not render as one identical preview.
    def test_shared_prefix_previews_become_distinguishable(self):
        """Measured on the v2 baseline: 67.0% of duplicate-preview pairs differed
        only AFTER the first 300 body chars, so the 140-char default window was
        identical for two distinct memories. The window must slide to escape."""
        shared = ("b" * 200) + "task" + ("z" * 100)     # len 304, hit at 200
        self._add("a", "a.md", "One", shared + " ALPHA 独特甲")
        self._add("b", "b.md", "Two", shared + " BETA 独特乙")
        results = store.search(self.conn, "task", k=5)
        self.assertEqual(len(results), 2)
        snippets = [r["snippet"] for r in results]
        self.assertNotEqual(snippets[0], snippets[1],
                            "previews of distinct memories must differ")
        for one in snippets:
            self.assertIn("task", one, "a slid preview must keep the matched term")


if __name__ == "__main__":
    unittest.main()
