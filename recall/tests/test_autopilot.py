"""Controlled autonomous loop: persistent queue, whitelist, stop switch, state."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import autopilot  # noqa: E402


class AutopilotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_bootstrap_queue_contains_only_whitelisted_recurring_tasks(self):
        queue = autopilot.load_queue(self.root)
        self.assertEqual({t["type"] for t in queue["tasks"]},
                         {"sync", "health", "self_test", "eval"})
        self.assertTrue(all(t["type"] in autopilot.ALLOWED_TASKS
                            for t in queue["tasks"]))
        self.assertTrue(os.path.exists(autopilot.queue_path(self.root)))

    def test_unknown_task_is_rejected_without_execution(self):
        called = []
        queue = {"version": 1, "tasks": [{
            "id": "bad", "type": "shell", "interval_minutes": 1,
            "enabled": True, "last_run": None,
        }]}
        autopilot.save_queue(queue, self.root)
        result = autopilot.run_once(
            self.root, executors={"shell": lambda: called.append(True)})
        self.assertEqual(called, [])
        self.assertEqual(result["rejected"], 1)

    def test_stop_flag_skips_all_work(self):
        open(autopilot.stop_path(self.root), "w", encoding="utf-8").close()
        result = autopilot.run_once(self.root)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["executed"], 0)

    def test_due_task_runs_and_updates_queue_and_state(self):
        queue = {"version": 1, "tasks": [{
            "id": "sync", "type": "sync", "interval_minutes": 180,
            "enabled": True, "last_run": None,
        }]}
        autopilot.save_queue(queue, self.root)
        now = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        result = autopilot.run_once(
            self.root, now=now,
            executors={"sync": lambda: {"ok": True, "detail": "done"}})
        self.assertEqual(result["executed"], 1)
        saved = json.load(open(autopilot.queue_path(self.root), encoding="utf-8"))
        self.assertEqual(saved["tasks"][0]["last_run"], now.isoformat())
        state = json.load(open(autopilot.state_path(self.root), encoding="utf-8"))
        self.assertEqual(state["last_status"], "ok")
        self.assertEqual(state["runs"], 1)

    def test_not_due_task_is_skipped(self):
        queue = {"version": 1, "tasks": [{
            "id": "sync", "type": "sync", "interval_minutes": 180,
            "enabled": True, "last_run": "2026-01-01T08:30:00+00:00",
        }]}
        autopilot.save_queue(queue, self.root)
        result = autopilot.run_once(
            self.root, now=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
            executors={"sync": lambda: self.fail("not due")})
        self.assertEqual(result["executed"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_task_failure_is_recorded_and_does_not_run_unbounded(self):
        queue = {"version": 1, "tasks": [{
            "id": "health", "type": "health", "interval_minutes": 1,
            "enabled": True, "last_run": None,
        }]}
        autopilot.save_queue(queue, self.root)
        result = autopilot.run_once(
            self.root, max_tasks=1,
            executors={"health": lambda: (_ for _ in ()).throw(RuntimeError("boom"))})
        self.assertEqual(result["executed"], 1)
        self.assertEqual(result["failed"], 1)
        state = json.load(open(autopilot.state_path(self.root), encoding="utf-8"))
        self.assertEqual(state["last_status"], "degraded")
        self.assertIn("boom", state["last_results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
