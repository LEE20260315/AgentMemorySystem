"""T08 / G-B10 — health-signal grading.

Proves the three health classes are now distinguishable in BOTH the log text and
the exit code, and that the daemon loop still survives a degraded run.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import runner, watch  # noqa: E402


class _FakeLogger:
    """Captures (level, message) tuples instead of writing to disk/stderr."""

    def __init__(self):
        self.lines = []

    def _add(self, level, msg, args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._add("INFO", msg, args)

    def warning(self, msg, *args):
        self._add("WARNING", msg, args)

    def error(self, msg, *args):
        self._add("ERROR", msg, args)

    def exception(self, msg, *args):
        self._add("ERROR", msg, args)

    def has(self, level, needle):
        return any(lv == level and needle in m for lv, m in self.lines)

    def messages(self, level):
        return [m for lv, m in self.lines if lv == level]


class HealthGradingTest(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeLogger()
        self._orig_logger = runner.logs.get_logger
        runner.logs.get_logger = lambda name="recall": self.fake
        self._orig_run = runner.run_sync

    def tearDown(self):
        runner.logs.get_logger = self._orig_logger
        runner.run_sync = self._orig_run

    def _force_summary(self, **over):
        base = {"files": 0, "entries": 0, "errors": 0, "by_agent": {},
                "walked_files": 0, "truncated": [], "elapsed_s": 0.1,
                "created_at": "2026-01-01T00:00:00"}
        base.update(over)
        runner.run_sync = lambda *a, **k: dict(base)

    def test_ok(self):
        self._force_summary(files=48, entries=250,
                            by_agent={"trae": 158, "dsh": 92})
        s = runner.sync_once()
        self.assertTrue(s["ok"])
        self.assertFalse(s["degraded"])
        self.assertEqual(s["exit_code"], runner.EXIT_OK)
        msg = self._one("INFO", "[SYNC OK]")
        # exactly one of each field
        self.assertEqual(msg.count("files="), 1)
        self.assertEqual(msg.count("entries="), 1)
        self.assertTrue(msg.startswith("[SYNC OK] files=48 entries=250"))

    def test_degraded_files_zero(self):
        self._force_summary(files=0, entries=0)
        s = runner.sync_once()
        self.assertFalse(s["ok"])
        self.assertTrue(s["degraded"])
        self.assertEqual(s["exit_code"], runner.EXIT_EMPTY)  # 3
        msg = self._one("WARNING", "[SYNC DEGRADED]")
        # reason leads, and `files=0` appears EXACTLY once (no duplication)
        self.assertTrue(msg.startswith("[SYNC DEGRADED] files=0 "))
        self.assertEqual(msg.count("files=0"), 1)
        self.assertNotIn("files=0 files=0", msg)

    def test_degraded_errors(self):
        self._force_summary(files=10, entries=5, errors=2, by_agent={"x": 5})
        s = runner.sync_once()
        self.assertFalse(s["ok"])
        self.assertTrue(s["degraded"])
        self.assertEqual(s["exit_code"], runner.EXIT_ERRORS)  # 2
        msg = self._one("WARNING", "[SYNC DEGRADED]")
        # reason leads, and `errors=2` appears EXACTLY once (no duplication)
        self.assertTrue(msg.startswith("[SYNC DEGRADED] errors=2 "))
        self.assertEqual(msg.count("errors=2"), 1)
        self.assertNotIn("errors=2 errors=2", msg)

    def _one(self, level, needle):
        hits = [m for m in self.fake.messages(level) if needle in m]
        self.assertEqual(len(hits), 1, f"expected exactly one {needle} line: {hits}")
        return hits[0]

    def test_fail_on_exception(self):
        def boom(*a, **k):
            raise RuntimeError("db locked")
        runner.run_sync = boom
        s = runner.sync_once()
        self.assertFalse(s["ok"])
        self.assertEqual(s["exit_code"], runner.EXIT_FAIL)  # 1
        self.assertTrue(self.fake.has("ERROR", "[SYNC FAIL]"))

    def test_truncated_source_is_visible(self):
        self._force_summary(files=1, entries=3000, by_agent={"big": 3000},
                            truncated=["big"])
        s = runner.sync_once()
        self.assertTrue(s["ok"])  # a capped source alone is not DEGRADED
        # but the truncation must show up in the summary text
        self.assertTrue(any("truncated=big" in m for _lv, m in self.fake.lines))

    def test_watch_once_returns_graded_codes(self):
        orig = runner.sync_once
        try:
            runner.sync_once = lambda log=True: {"ok": True, "exit_code": 0}
            self.assertEqual(watch.watch_once(), 0)
            runner.sync_once = lambda log=True: {"ok": False, "exit_code": 3}
            self.assertEqual(watch.watch_once(), 3)
            runner.sync_once = lambda log=True: {"ok": False, "exit_code": 2}
            self.assertEqual(watch.watch_once(), 2)
            runner.sync_once = lambda log=True: {"ok": False, "exit_code": 1}
            self.assertEqual(watch.watch_once(), 1)
        finally:
            runner.sync_once = orig

    def test_watch_loop_survives_a_degraded_run(self):
        """A single failing/degraded run must never stop the daemon."""
        calls = {"n": 0}

        def failing_sync(*a, **k):
            calls["n"] += 1
            raise RuntimeError("transient failure")

        orig_sync = runner.sync_once
        orig_sleep = watch.time.sleep

        def stop_after_first(_seconds):
            raise KeyboardInterrupt

        runner.sync_once = failing_sync
        watch.time.sleep = stop_after_first
        try:
            code = watch.watch_loop(1)
        finally:
            runner.sync_once = orig_sync
            watch.time.sleep = orig_sleep

        self.assertEqual(code, 0, "loop should exit cleanly on Ctrl+C only")
        self.assertGreaterEqual(calls["n"], 1, "loop must run at least once")


if __name__ == "__main__":
    unittest.main()
