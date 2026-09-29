"""watch — run sync once or on a loop for unattended observation.

Usage modes:
    memory watch --once                 # single sync, then exit (for cron/schtasks)
    memory watch --interval-minutes M   # loop every M minutes (daemon)
"""

from __future__ import annotations

import time

from . import logs, runner


def watch_once() -> int:
    """Run a single sync and return its graded exit code (G-B10).

    0 = clean, 1 = crash, 2 = ran-with-errors, 3 = walked nothing. The code is
    carried to the Windows Task Scheduler via `raise SystemExit(main())`.
    """
    summary = runner.sync_once()
    default = runner.EXIT_OK if summary.get("ok") else runner.EXIT_FAIL
    return int(summary.get("exit_code", default))


def watch_loop(interval_minutes: float) -> int:
    logger = logs.get_logger()
    if interval_minutes <= 0:
        logger.warning("interval must be > 0; using 60")
        interval_minutes = 60
    sleep_s = interval_minutes * 60
    logger.info("[WATCH] starting daemon, interval=%s min (Ctrl+C to quit)",
                interval_minutes)
    n = 0
    while True:
        n += 1
        logger.info("[WATCH] run #%s", n)
        try:
            # A single DEGRADED run must NEVER stop the daemon: sync_once grades
            # and returns instead of raising, and this guard also swallows any
            # unexpected exception so the loop keeps living (unchanged semantics).
            runner.sync_once()
        except Exception:  # noqa: BLE001 — never let a sync kill the loop
            logger.exception("[WATCH] unhandled error in run #%s", n)
        try:
            time.sleep(sleep_s)
        except KeyboardInterrupt:
            logger.info("[WATCH] interrupted by user after %s runs", n)
            return 0


def main(args) -> int:
    if getattr(args, "once", False):
        return watch_once()
    return watch_loop(args.interval_minutes)