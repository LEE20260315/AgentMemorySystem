"""T01 — local index: idempotent replacement + keyword scoring.

Locks two behaviours the rest of the system depends on:
  * `store.replace_file_entries()` is idempotent (re-running never doubles rows);
  * `store.search()` finds Chinese substrings and weights a title hit (x3)
    above a body hit (x1).
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


class IndexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "idx.db")
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def test_replace_is_idempotent(self):
        entries = [
            {"title": "T", "text": "body-one", "fingerprint": "f1"},
            {"title": "U", "text": "body-two", "fingerprint": "f2"},
        ]
        n1 = store.replace_file_entries(
            self.conn, "a", "f.md", entries, "2026-01-01T00:00:00")
        n2 = store.replace_file_entries(
            self.conn, "a", "f.md", entries, "2026-01-01T00:00:00")
        self.assertEqual(n1, 2)
        self.assertEqual(n2, 2)
        self.assertEqual(self._count(), 2, "row count must not double")

    def test_replace_removes_stale_rows_for_same_file(self):
        store.replace_file_entries(self.conn, "a", "f.md",
                                   [{"title": "T", "text": "x", "fingerprint": "1"}], "t")
        store.replace_file_entries(self.conn, "a", "f.md",
                                   [{"title": "T", "text": "y", "fingerprint": "2"}], "t")
        self.assertEqual(self._count(), 1)

    def test_chinese_substring_hit(self):
        store.replace_file_entries(
            self.conn, "a", "c.md",
            [{"title": "登录", "text": "认证 流程 说明", "fingerprint": "x"}], "t")
        results = store.search(self.conn, "认证", k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "登录")

    def test_title_weight_beats_body(self):
        store.replace_file_entries(
            self.conn, "a", "title.md",
            [{"title": "OneDrive 原子写", "text": "无关正文", "fingerprint": "t"}], "t")
        store.replace_file_entries(
            self.conn, "a", "body.md",
            [{"title": "其它", "text": "这里提到 OneDrive 一次", "fingerprint": "b"}], "t")
        results = store.search(self.conn, "OneDrive", k=5)
        self.assertEqual(results[0]["file"], "title.md")

    def test_empty_query_returns_nothing(self):
        store.replace_file_entries(
            self.conn, "a", "c.md",
            [{"title": "T", "text": "hello", "fingerprint": "x"}], "t")
        self.assertEqual(store.search(self.conn, "   ", k=5), [])

    def test_multi_term_search(self):
        store.replace_file_entries(
            self.conn, "a", "c.md",
            [{"title": "原子写", "text": "OneDrive 迁移 经验", "fingerprint": "x"}], "t")
        results = store.search(self.conn, "OneDrive 迁移", k=5)
        self.assertGreaterEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
