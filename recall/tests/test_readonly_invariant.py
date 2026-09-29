"""G-B1 — read-only invariant.

The single iron rule of this project: ingest/recall must NEVER modify any Agent
memory source file. These tests lock that rule down and — crucially — prove the
checker itself is not a rubber stamp (mutation test): injecting a single byte
into the source tree must turn the invariant RED.

Runs under both `pytest` and `python -m unittest`. The index is always written
to a throwaway temp DB; the user's real %LOCALAPPDATA%\\recall-memory\\index.db is
never touched.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # workspace root that holds `recall/`
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import ingest, store  # noqa: E402


def build_tree(base: str) -> str:
    """Create a fake source tree with nested dirs and a few .md files."""
    src = os.path.join(base, "src")
    os.makedirs(os.path.join(src, "nested", "deep"), exist_ok=True)
    files = {
        os.path.join(src, "a.md"): "# A\n## Sec1\nhello 世界\n## Sec2\nmore\n",
        os.path.join(src, "nested", "b.md"): "## Only\ncontent b\n",
        os.path.join(src, "nested", "deep", "c.md"): "no heading here\nplain text\n",
        os.path.join(src, "notes.txt"): "ignored by *.md glob\n",
    }
    for path, content in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return src


def snapshot(root: str) -> dict:
    """Map relpath -> SHA256(content) for every file under root (byte-exact)."""
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as f:
                snap[rel] = hashlib.sha256(f.read()).hexdigest()
    return snap


class ReadOnlyInvariantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.src = build_tree(self.base)
        self.db = os.path.join(self.base, "tmp-index.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _ingest_and_search(self) -> None:
        """Run the full read-only pipeline against a TEMP db."""
        conn = store.connect(self.db)
        try:
            ingest.ingest(conn, sources=[("fake", self.src, "**/*.md")])
            conn.commit()
            store.search(conn, "世界", k=5)   # exercise the recall path too
            store.stats(conn)
        finally:
            conn.close()

    def test_source_tree_unchanged_after_ingest_and_search(self):
        before = snapshot(self.src)
        self._ingest_and_search()
        after = snapshot(self.src)
        self.assertEqual(
            before, after,
            "ingest/search must not change a single byte of the source tree")

    def test_index_written_to_temp_db_not_user_db(self):
        self._ingest_and_search()
        self.assertTrue(os.path.exists(self.db), "temp index should be created")
        # nothing was written next to the source files
        self.assertFalse(os.path.exists(os.path.join(self.src, "index.db")))

    def test_checker_detects_mutation(self):
        """Mutation test: the invariant check must be able to FAIL.

        After a normal (read-only) run the snapshot is unchanged; then we mutate
        the tree exactly like a buggy writer would and assert the checker turns
        RED. This proves the guard is not a永远-True 橡皮章.
        """
        before = snapshot(self.src)
        self._ingest_and_search()
        # 1) baseline: green
        self.assertEqual(before, snapshot(self.src))

        # 2) append ONE byte to an existing file -> must be detected
        with open(os.path.join(self.src, "a.md"), "a", encoding="utf-8") as f:
            f.write("x")
        self.assertNotEqual(
            before, snapshot(self.src), "checker failed to catch a 1-byte change")
        before2 = snapshot(self.src)

        # 3) create a brand-new file -> must be detected
        with open(os.path.join(self.src, "injected.md"), "w", encoding="utf-8") as f:
            f.write("new file\n")
        self.assertNotEqual(
            before2, snapshot(self.src), "checker failed to catch a new file")


if __name__ == "__main__":
    unittest.main()
