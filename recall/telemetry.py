"""T-E4 query telemetry: the ONLY write side-effect of a recall query.

Each `recall` query appends ONE JSON object to
`%LOCALAPPDATA%\\recall-memory\\query-log\\query-YYYYMMDD.jsonl`. This is the
evidence for the "trigger mechanism" gate (G-E4): when is memory actually reached
for? `stats --days N` summarises the window (per-day counts / zero-query days /
G-E4 verdict).

Hard constraints (RECALL_FIRST_PLAN §12, red line R7):
  (a) it does NOT participate in retrieval — `ingest` / `store` / `search` never
      read it (locked by a source-scan test); only this module and `stats` do;
  (b) ANY failure to write is SWALLOWED — telemetry must never break a recall;
  (c) it lives only under %LOCALAPPDATA%\\recall-memory, and NO memory body is
      logged — only  ts / query / k / result-count / short result-key hash.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta

from . import logs, store

QUERY_DIR_NAME = "query-log"
_PREFIX = "query-"
_SUFFIX = ".jsonl"


def query_log_dir(base_dir: str | None = None) -> str:
    """`…\\recall-memory\\query-log` (created on demand; best-effort)."""
    d = os.path.join(base_dir or logs._root(), QUERY_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def query_log_path(when: datetime | None = None,
                   base_dir: str | None = None) -> str:
    day = (when or datetime.now()).strftime("%Y%m%d")
    return os.path.join(query_log_dir(base_dir), f"{_PREFIX}{day}{_SUFFIX}")


def _result_key_hash(results) -> str:
    """Short fingerprint of the returned content-keys (no bodies stored)."""
    joined = "|".join(store._content_key(r.get("text") or "") for r in results)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()[:12]


def log_query(query: str, k: int, results, base_dir: str | None = None,
              when: datetime | None = None) -> None:
    """Append one telemetry line; NEVER raises (best-effort, swallowed)."""
    try:
        now = when or datetime.now()
        record = {
            "ts": now.isoformat(timespec="seconds"),
            "query": " ".join((query or "").split()),
            "k": int(k),
            "n": len(results or []),
            "keys": _result_key_hash(results or []),
        }
        with open(query_log_path(now, base_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never break a recall
        pass


def _count_lines(path: str) -> int:
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        return 0
    return n


def stats(days: int = 14, base_dir: str | None = None,
          today: datetime | None = None) -> dict:
    """Per-day query counts over the last `days` days (oldest -> newest)."""
    days = max(1, int(days))
    today = today or datetime.now()
    base = today.replace(hour=0, minute=0, second=0, microsecond=0)
    per_day: list[dict] = []
    for i in range(days - 1, -1, -1):
        day = base - timedelta(days=i)
        per_day.append({
            "date": day.strftime("%Y-%m-%d"),
            "count": _count_lines(query_log_path(day, base_dir)),
        })
    total = sum(d["count"] for d in per_day)
    zero_days = sum(1 for d in per_day if d["count"] == 0)
    return {
        "days": days,
        "per_day": per_day,
        "total": total,
        "zero_days": zero_days,
        "active_days": days - zero_days,
    }


def verdict(s: dict) -> str:
    """One-line G-E4 verdict for the window."""
    if s["total"] == 0:
        return (f"未触发（{s['days']} 天 0 次查询）→ G-E4 未达成；"
                f"{s['days']} 天全零即触发止损冻结")
    return (f"已触发：有查询天数 {s['active_days']}/{s['days']}，"
            f"零查询 {s['zero_days']} 天，共 {s['total']} 次")


def render_stats(s: dict) -> list[str]:
    """Human-readable block for the `stats` command."""
    first = s["per_day"][0]["date"]
    last = s["per_day"][-1]["date"]
    lines = [
        f"查询遥测（最近 {s['days']} 天：{first} ～ {last}）",
        f"  查询总数    ：{s['total']}",
        f"  有查询天数  ：{s['active_days']} / {s['days']}",
        f"  零查询天数  ：{s['zero_days']}",
        "  每日查询数：",
    ]
    for d in s["per_day"]:
        bar = "#" * min(d["count"], 40)
        lines.append(f"    {d['date']}  {d['count']:>4d}  {bar}")
    lines.append(f"  G-E4 判定   ：{verdict(s)}")
    return lines
