"""run_sync() — the single unit of work invoked by the scheduler/daemon.

Reads nothing from OneDrive; writes only to %LOCALAPPDATA%. Each run is
summarised to the log so the "run it for a few days and observe" experiment
has a durable, queryable record.
"""

from __future__ import annotations

import time
from datetime import datetime

from . import ingest, logs, store

# Exit codes surfaced to the Windows Task Scheduler (via watch --once ->
# cli.main return -> __main__.py `raise SystemExit`). They distinguish the
# three health classes so "did nothing" / "did it with errors" / "fine" are no
# longer all reported as a silent success (G-B10).
EXIT_OK = 0        # a genuine, clean sync
EXIT_FAIL = 1      # the sync itself blew up ([SYNC FAIL])
EXIT_ERRORS = 2    # ran, but at least one file failed ([SYNC DEGRADED] errors>0)
EXIT_EMPTY = 3     # ran, but walked zero files ([SYNC DEGRADED] files=0)


def run_sync(conn=None) -> dict:
    """Run a full ingest synchronisation once. Returns the summary dict.

    Opens its own connection if none given. Commit is made at the end.
    """
    started = time.monotonic()
    created_at = datetime.now().isoformat(timespec="seconds")
    own = conn is None
    if own:
        conn = store.connect(logs.index_db_path())
    try:
        summary = ingest.ingest(conn)
        conn.commit()
    finally:
        if own:
            conn.close()

    summary["elapsed_s"] = round(time.monotonic() - started, 2)
    summary["created_at"] = created_at
    return summary


def format_summary(s: dict, *, include_files: bool = True,
                   include_errors: bool = True) -> str:
    """Render a one-line summary: `files=.. entries=.. (agent=..) errors=.. elapsed=..s`.

    `include_files` / `include_errors` let the DEGRADED log lines lead with the
    reason (`files=0` or `errors=N`) WITHOUT printing that same field twice.
    """
    per = ", ".join(f"{a}={n}" for a, n in sorted(s["by_agent"].items()))
    parts: list[str] = []
    if include_files:
        parts.append(f"files={s['files']}")
    parts.append(f"entries={s['entries']} ({per})")
    if include_errors and s.get("errors"):
        parts.append(f"errors={s['errors']}")
    parts.append(f"elapsed={s['elapsed_s']}s")
    if s.get("truncated"):
        parts.append(f"truncated={','.join(s['truncated'])}")
    return " ".join(parts)


def sync_once(log: bool = True) -> dict:
    """High-level: run one sync, grade it, and log it. Never raises.

    Grading (see EXIT_* above):
      * files == 0            -> [SYNC DEGRADED] files=0    (ok=False, code 3)
      * errors > 0 & files > 0-> [SYNC DEGRADED] errors=N   (ok=False, code 2)
      * otherwise             -> [SYNC OK]                  (ok=True,  code 0)
      * unhandled exception   -> [SYNC FAIL]                (ok=False, code 1)
    The returned dict always carries `ok`, `degraded`, `health` and `exit_code`.

    Each log line states every field exactly once: the DEGRADED lines lead with
    the reason (`files=0` / `errors=N`) and the formatter drops that same field
    from the tail.
    """
    logger = logs.get_logger()
    try:
        summary = run_sync()
    except Exception as e:  # noqa: BLE001 — a single bad run must never kill the caller
        if log:
            logger.exception("[SYNC FAIL] %r", e)
        summary = {"files": 0, "entries": 0, "errors": 1, "by_agent": {},
                   "walked_files": 0, "truncated": [], "elapsed_s": 0.0}
        summary.update(ok=False, degraded=True, health="fail",
                       exit_code=EXIT_FAIL)
        return summary

    files = summary.get("files", 0)
    errors = summary.get("errors", 0)

    if files == 0:
        if log:
            logger.warning("[SYNC DEGRADED] files=0 %s",
                           format_summary(summary, include_files=False))
        summary.update(ok=False, degraded=True, health="empty",
                       exit_code=EXIT_EMPTY)
    elif errors > 0:
        if log:
            logger.warning("[SYNC DEGRADED] errors=%d %s", errors,
                           format_summary(summary, include_errors=False))
        summary.update(ok=False, degraded=True, health="errors",
                       exit_code=EXIT_ERRORS)
    else:
        if log:
            logger.info("[SYNC OK] %s", format_summary(summary))
        summary.update(ok=True, degraded=False, health="ok",
                       exit_code=EXIT_OK)
    return summary