"""Controlled autonomous scheduler for Recall.

The queue is intentionally a closed whitelist: it cannot execute shell commands
or arbitrary Python.  State lives below the local recall runtime root, never in
an Agent memory source or OneDrive.  Windows Task Scheduler invokes ``run_once``;
per-task intervals decide what is due during that tick.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import config, logs, runner, store

ALLOWED_TASKS = frozenset({"sync", "health", "self_test", "eval"})
DEFAULT_TASKS = (
    {"id": "sync", "type": "sync", "interval_minutes": 180, "enabled": True,
     "last_run": None},
    {"id": "health", "type": "health", "interval_minutes": 60, "enabled": True,
     "last_run": None},
    {"id": "self-test", "type": "self_test", "interval_minutes": 360,
     "enabled": True, "last_run": None},
    {"id": "eval", "type": "eval", "interval_minutes": 1440, "enabled": True,
     "last_run": None},
)


def root_dir(base_dir: str | None = None) -> str:
    root = base_dir or os.path.join(logs._root(), "autopilot")
    os.makedirs(root, exist_ok=True)
    return root


def queue_path(base_dir: str | None = None) -> str:
    return os.path.join(root_dir(base_dir), "queue.json")


def state_path(base_dir: str | None = None) -> str:
    return os.path.join(root_dir(base_dir), "state.json")


def stop_path(base_dir: str | None = None) -> str:
    return os.path.join(root_dir(base_dir), "stop.flag")


def lock_path(base_dir: str | None = None) -> str:
    return os.path.join(root_dir(base_dir), "run.lock")


def _atomic_json(path: str, obj: dict) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".autopilot-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def save_queue(queue: dict, base_dir: str | None = None) -> None:
    _atomic_json(queue_path(base_dir), queue)


def load_queue(base_dir: str | None = None) -> dict:
    path = queue_path(base_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        if isinstance(value, dict) and isinstance(value.get("tasks"), list):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    queue = {"version": 1, "tasks": [dict(task) for task in DEFAULT_TASKS]}
    save_queue(queue, base_dir)
    return queue


def load_state(base_dir: str | None = None) -> dict:
    try:
        with open(state_path(base_dir), "r", encoding="utf-8") as f:
            value = json.load(f)
        if isinstance(value, dict):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"runs": 0, "last_status": "never", "last_run": None,
            "last_results": []}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _due(task: dict, now: datetime) -> bool:
    if not task.get("enabled", True):
        return False
    last = _parse_time(task.get("last_run"))
    if last is None:
        return True
    try:
        minutes = max(1, int(task.get("interval_minutes", 60)))
    except (TypeError, ValueError):
        minutes = 60
    return now >= last + timedelta(minutes=minutes)


def _sync_task() -> dict:
    summary = runner.sync_once()
    return {"ok": bool(summary.get("ok")), "health": summary.get("health"),
            "files": summary.get("files", 0), "entries": summary.get("entries", 0),
            "errors": summary.get("errors", 0), "elapsed_s": summary.get("elapsed_s", 0)}


def _health_task() -> dict:
    conn = store.connect(logs.index_db_path())
    try:
        stats = store.stats(conn)
    finally:
        conn.close()
    existing = sum(1 for _agent, root, _glob in config.sources()
                   if os.path.isdir(config._expand(root)))
    ok = stats["total"] > 0 and existing >= 2
    return {"ok": ok, "entries": stats["total"], "configured_sources": len(config.sources()),
            "existing_sources": existing}


def _self_test_task() -> dict:
    conn = store.connect(logs.index_db_path())
    try:
        total = store.stats(conn)["total"]
        probes = {q: len(store.search(conn, q, 5)) for q in ("OneDrive", "Agent")}
        readonly = conn.execute("PRAGMA query_only").fetchone() is not None
    finally:
        conn.close()
    # This deliberately calls store.search directly: no telemetry is written and
    # synthetic health probes cannot inflate the Phase-0 real-use evidence.
    ok = total > 0 and any(probes.values()) and readonly
    return {"ok": ok, "entries": total, "probes": probes, "db_readable": readonly}


def _eval_task() -> dict:
    from .eval import _common, recall_eval
    records = recall_eval.load_golden(_common.GOLDEN_PATH)
    conn = _common.open_readonly(logs.index_db_path())
    try:
        metrics = recall_eval.evaluate(conn, records, k=5)
    finally:
        conn.close()
    return {"ok": metrics["n"] > 0, "n": metrics["n"],
            "recall@5": metrics.get("recall@5", 0.0), "mrr": metrics["mrr"],
            "doc": metrics["doc"]}


def default_executors() -> dict[str, Callable[[], dict]]:
    return {"sync": _sync_task, "health": _health_task,
            "self_test": _self_test_task, "eval": _eval_task}


def _acquire_lock(base_dir: str | None, now: datetime) -> bool:
    path = lock_path(base_dir)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = datetime.now().timestamp() - os.path.getmtime(path)
            if age > 3600:
                os.remove(path)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                return False
        except OSError:
            return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(now.isoformat())
    return True


def run_once(base_dir: str | None = None, *, max_tasks: int = 4,
             now: datetime | None = None,
             executors: dict[str, Callable[[], dict]] | None = None) -> dict:
    """Run due whitelisted tasks once, with a hard per-tick task budget."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    logger = logs.get_logger()
    result = {"stopped": False, "locked": False, "executed": 0, "failed": 0,
              "skipped": 0, "rejected": 0, "results": []}
    if os.path.exists(stop_path(base_dir)):
        result["stopped"] = True
        logger.warning("[AUTOPILOT STOPPED] stop.flag present")
        return result
    if not _acquire_lock(base_dir, now):
        result["locked"] = True
        logger.warning("[AUTOPILOT SKIP] another run holds the lock")
        return result

    queue = load_queue(base_dir)
    funcs = default_executors()
    if executors:
        funcs.update({k: v for k, v in executors.items() if k in ALLOWED_TASKS})
    try:
        budget = max(1, min(int(max_tasks), len(ALLOWED_TASKS)))
        for task in queue.get("tasks", []):
            task_type = task.get("type")
            if task_type not in ALLOWED_TASKS:
                result["rejected"] += 1
                result["results"].append({"id": task.get("id"), "type": task_type,
                                           "ok": False, "error": "task type not allowed"})
                continue
            if not _due(task, now):
                result["skipped"] += 1
                continue
            if result["executed"] >= budget:
                result["skipped"] += 1
                continue
            item = {"id": task.get("id"), "type": task_type, "ok": False}
            try:
                detail = funcs[task_type]()
                if not isinstance(detail, dict):
                    detail = {"ok": bool(detail), "detail": str(detail)}
                item.update(detail)
                item["ok"] = bool(detail.get("ok", True))
                if not item["ok"]:
                    result["failed"] += 1
            except Exception as exc:  # one task must not abort the remaining queue
                item["error"] = f"{type(exc).__name__}: {exc}"
                result["failed"] += 1
            task["last_run"] = now.isoformat()
            result["executed"] += 1
            result["results"].append(item)
        save_queue(queue, base_dir)
        previous = load_state(base_dir)
        status = "ok" if result["failed"] == 0 and result["rejected"] == 0 else "degraded"
        state = {"runs": int(previous.get("runs", 0)) + 1,
                 "last_status": status, "last_run": now.isoformat(),
                 "last_results": result["results"],
                 "executed": result["executed"], "failed": result["failed"],
                 "rejected": result["rejected"]}
        _atomic_json(state_path(base_dir), state)
        logger.info("[AUTOPILOT %s] executed=%d failed=%d skipped=%d rejected=%d",
                    status.upper(), result["executed"], result["failed"],
                    result["skipped"], result["rejected"])
        return result
    finally:
        try:
            os.remove(lock_path(base_dir))
        except OSError:
            pass


def set_stopped(stopped: bool, base_dir: str | None = None) -> None:
    path = stop_path(base_dir)
    if stopped:
        with open(path, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def status(base_dir: str | None = None) -> dict:
    queue = load_queue(base_dir)
    state = load_state(base_dir)
    return {"root": root_dir(base_dir), "stopped": os.path.exists(stop_path(base_dir)),
            "queue": queue, "state": state}
