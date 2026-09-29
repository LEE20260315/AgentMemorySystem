"""T-E4 / G-E4 — query telemetry invariants (R7).

Telemetry is the ONLY write side-effect of a recall. These tests lock:
  * one well-formed JSONL line per query under `query-log/query-YYYYMMDD.jsonl`;
  * NO memory body is ever written;
  * a failing write is swallowed (recall never breaks);
  * `stats` counts per day + gives the G-E4 verdict;
  * no RETRIEVAL module reads telemetry (source scan);
  * logging does NOT touch the index.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import store, telemetry  # noqa: E402

_PKG_DIR = os.path.join(_ROOT, "recall")


class TelemetryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _results(self):
        return [{"text": "alpha memory body SENSITIVE_9f"}, {"text": "beta body"}]

    def _day_file(self, when: datetime) -> str:
        return telemetry.query_log_path(when, self.dir)

    def test_appends_one_jsonl_line_under_query_log(self):
        when = datetime(2026, 1, 10, 9, 30)
        telemetry.log_query("原子写 记忆", 5, self._results(), self.dir, when=when)
        path = self._day_file(when)
        self.assertTrue(path.endswith(os.path.join("query-log", "query-20260110.jsonl")))
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["k"], 5)
        self.assertEqual(rec["n"], 2)
        self.assertEqual(len(rec["keys"]), 12)
        self.assertIn("原子写", rec["query"])

    def test_no_memory_body_is_logged(self):
        telemetry.log_query("q", 5, self._results(), self.dir)
        blob = open(self._day_file(datetime.now()), "r", encoding="utf-8").read()
        self.assertNotIn("SENSITIVE_9f", blob)
        self.assertNotIn("alpha memory body", blob)

    def test_write_failure_is_swallowed(self):
        bad = os.path.join(self.dir, "afile")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("x")
        # base_dir points under a *file* -> creation fails -> must not raise
        telemetry.log_query("q", 5, self._results(), base_dir=os.path.join(bad, "sub"))

    def test_stats_counts_and_verdict(self):
        day = datetime(2026, 1, 10, 12, 0)
        telemetry.log_query("a", 5, self._results(), self.dir, when=day)
        telemetry.log_query("b", 5, self._results(), self.dir, when=day)
        s = telemetry.stats(days=3, base_dir=self.dir, today=day)
        self.assertEqual(s["per_day"][-1]["count"], 2, "today carries 2 queries")
        self.assertEqual(s["per_day"][-1]["date"], "2026-01-10")
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["zero_days"], 2)          # the two earlier days
        self.assertIn("已触发", telemetry.verdict(s))

    def test_stats_verdict_zero(self):
        s = telemetry.stats(days=14, base_dir=self.dir, today=datetime(2026, 1, 10))
        self.assertEqual(s["total"], 0)
        self.assertIn("未触发", telemetry.verdict(s))
        self.assertIn("止损冻结", telemetry.verdict(s))

    def test_logging_does_not_touch_index(self):
        db = os.path.join(self.dir, "idx.db")
        conn = store.connect(db)
        try:
            conn.execute("INSERT INTO entries(source_agent, source_file, title, "
                         "fingerprint, raw_text, created_at) "
                         "VALUES('a','f','t','fp','body','now')")
            conn.commit()
        finally:
            conn.close()
        before = hashlib.sha256(open(db, "rb").read()).hexdigest()
        telemetry.log_query("q", 5, self._results(), self.dir)
        after = hashlib.sha256(open(db, "rb").read()).hexdigest()
        self.assertEqual(before, after)

    def test_retrieval_path_does_not_read_telemetry(self):
        """R7(a): ingest/store/runner must not reference the query log."""
        offenders = []
        for name in ("store.py", "ingest.py", "runner.py"):
            src = open(os.path.join(_PKG_DIR, name), "r", encoding="utf-8").read()
            if "telemetry" in src or "query-log" in src or "query_log" in src:
                offenders.append(name)
        self.assertEqual(offenders, [], f"retrieval path must not read telemetry: {offenders}")


if __name__ == "__main__":
    unittest.main()
