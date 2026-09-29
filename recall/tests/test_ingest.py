"""T01 — markdown extractor behaviour (section split, fallback, truncation).

`extract_entries()` is the segmenter that turns one markdown file into memory
entries. We lock its current behaviour so later tasks (T05: marker-bearing
truncation, paragraph splitting) get a regression guard.

NOTE (T05): paragraph-level chunking was implemented and measured against the
frozen golden set but FAILED the keep gate (recall@5 52.0% -> 30.0%), so it was
rolled back. The extractor is again "one entry per `##` section".
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

from recall import ingest as ingest_mod  # noqa: E402
from recall.config import MAX_SECTION_CHARS  # noqa: E402


class ExtractEntriesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_splits_on_double_hash(self):
        path = self._write("m.md", "## Alpha\nbody A\n## Beta\nbody B\n")
        entries = ingest_mod.extract_entries(path)
        self.assertEqual([e["title"] for e in entries], ["Alpha", "Beta"])
        self.assertEqual([e["text"] for e in entries], ["body A", "body B"])

    def test_leading_preamble_becomes_basename_section(self):
        # text before the first `##` is flushed as a section titled by basename
        path = self._write("p.md", "preamble\n## Alpha\nbody A\n")
        entries = ingest_mod.extract_entries(path)
        self.assertEqual([e["title"] for e in entries], ["p.md", "Alpha"])
        self.assertEqual(entries[0]["text"], "preamble")

    def test_single_hash_is_not_a_separator(self):
        # only `##` splits; `#` and `###` stay inside their section body
        path = self._write("h.md", "# Top\n## Sec\n### Sub\nbody\n")
        entries = ingest_mod.extract_entries(path)
        self.assertEqual([e["title"] for e in entries], ["h.md", "Sec"])
        self.assertIn("### Sub", entries[1]["text"])

    def test_empty_file_yields_nothing(self):
        path = self._write("empty.md", "")
        self.assertEqual(ingest_mod.extract_entries(path), [])

    def test_whitespace_only_file_yields_nothing(self):
        path = self._write("ws.md", "   \n\n  \n")
        self.assertEqual(ingest_mod.extract_entries(path), [])

    def test_no_heading_uses_whole_file_fallback(self):
        path = self._write("whole.md", "just body text\nline two\n")
        entries = ingest_mod.extract_entries(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "whole.md")  # basename fallback
        self.assertIn("just body text", entries[0]["text"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(
            ingest_mod.extract_entries(os.path.join(self.dir, "nope.md")), [])

    def test_section_truncated_at_max_section_chars(self):
        """Silent truncation is LOCKED here so a future T05 (marker-bearing cut)
        has a regression guard: an over-long `##` body is clipped to the cap."""
        body = "A" * (MAX_SECTION_CHARS + 500)
        path = self._write("long.md", "## Big\n" + body + "\n")
        entries = ingest_mod.extract_entries(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["text"]), MAX_SECTION_CHARS)

    def test_truncation_with_small_cap(self):
        original = ingest_mod.MAX_SECTION_CHARS
        ingest_mod.MAX_SECTION_CHARS = 20
        try:
            path = self._write("small.md", "## S\n" + "B" * 100 + "\n")
            entries = ingest_mod.extract_entries(path)
            self.assertEqual(len(entries[0]["text"]), 20)
        finally:
            ingest_mod.MAX_SECTION_CHARS = original


class WalkTest(unittest.TestCase):
    """T-摄取性能: bare filename glob must NOT walk the whole tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, rel: str) -> str:
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        return p

    def test_bare_glob_is_root_only(self):
        self._touch("MEMORY.md")
        self._touch(os.path.join("sub", "deep", "MEMORY.md"))   # must be ignored
        found = ingest_mod._walk_md(self.dir, "MEMORY.md")
        self.assertEqual(len(found), 1, "bare glob must only scan the root dir")
        self.assertEqual(os.path.dirname(found[0]), self.dir)

    def test_path_glob_recurses(self):
        self._touch("a.md")
        self._touch(os.path.join("sub", "b.md"))
        found = ingest_mod._walk_md(self.dir, "**/*.md")
        self.assertEqual(len(found), 2)

    def test_path_glob_skips_excluded_dirs(self):
        self._touch("keep.md")
        self._touch(os.path.join("node_modules", "skip.md"))
        found = ingest_mod._walk_md(self.dir, "**/*.md")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].endswith("keep.md"))


if __name__ == "__main__":
    unittest.main()
