"""Read-only ingestion: scan markdown memory files, split into entries, index."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from functools import lru_cache

from . import config, store
from .config import EXCLUDE_DIRS, MAX_FILE_BYTES, MAX_SECTIONS_PER_FILE, MAX_SECTION_CHARS

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _fingerprint(path: str, title: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(path.encode("utf-8", "replace"))
    h.update(b"|")
    h.update((title or "").encode("utf-8", "replace"))
    h.update(b"|")
    h.update(text.encode("utf-8", "replace"))
    return h.hexdigest()


def extract_entries(path: str) -> list[dict]:
    """Split one markdown file into memory entries (one per `##` section).

    Leading text before the first `##` becomes a section titled by basename.
    Strictly read-only.

    NOTE (T05, 2026-09-22): paragraph-level chunking was implemented and measured
    against the frozen golden set but FAILED the keep gate (recall@5 52.0% ->
    30.0%), so it was rolled back and this extractor restored unchanged.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read(MAX_FILE_BYTES)
    except OSError:
        return []

    lines = raw.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_title = None
    cur_body: list[str] = []

    def flush():
        nonlocal cur_title, cur_body
        if cur_title is not None or cur_body:
            title = cur_title or os.path.basename(path)
            body = "\n".join(cur_body).strip()
            if body:
                sections.append((title, [body]))
        cur_title = None
        cur_body = []

    for line in lines:
        m = _HEADING_RE.match(line)
        if m and m.group(1) in ("##",):
            flush()
            cur_title = m.group(2).strip()
        else:
            cur_body.append(line)

    flush()

    if not sections:
        body = raw.strip()
        if body:
            sections.append((os.path.basename(path), [body]))

    entries = []
    for title, texts in sections:
        for text in texts:
            text = text[:MAX_SECTION_CHARS]
            if not text.strip():
                continue
            entries.append({
                "title": title,
                "text": text,
                "fingerprint": _fingerprint(path, title, text),
            })
        if len(entries) >= MAX_SECTIONS_PER_FILE:
            break
    return entries


@lru_cache(maxsize=64)
def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a glob pattern into a compiled regex (T02).

    Differences from `fnmatch` (which we deliberately avoid, because it lets `*`
    cross directory separators and treats a leading `**/` as garbage):
      * `**/` -> zero or more directory segments (`(?:.*/)?`), so `**/*.md`
        matches both `topics.md` and `projects/x/topics.md`;
      * a lone `*` -> `[^/]*` (never crosses a separator);
      * `?` -> `[^/]`; everything else is matched literally.
    """
    i, n = 0, len(pattern)
    parts: list[str] = []
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def _matches(pattern: str, relpath: str, name: str) -> bool:
    """True if `name`/`relpath` matches `pattern`.

    A pattern with a path separator is matched against the root-relative POSIX
    path; a bare pattern is matched against the file name alone. This is what
    lets `MEMORY.md` pick exactly one well-known file inside a huge directory.
    """
    rx = _glob_to_regex(pattern)
    if "/" in pattern or "\\" in pattern:
        return bool(rx.match(relpath))
    return bool(rx.match(name))


def _walk_md(root: str, pattern: str = "**/*.md") -> list[str]:
    """Return all files under root matching `pattern`, skipping EXCLUDE_DIRS.

    Two very different shapes (T-摄取性能):
    * **Bare pattern** (`MEMORY.md`, no path separator): a well-known file lives
      DIRECTLY in the root — we only `scandir` the root, never recurse. Recursing
      a huge tree just to find one file was 95% of sync time (`~/.workbuddy` =
      ~150k files -> 2.65s walk to match 1 file).
    * **Path pattern** (`**/*.md`): recursive `os.walk` with in-place pruning of
      EXCLUDE_DIRS, so we never descend into node_modules/.git/cache/skills...

    `root` is expanded; scanning is READ-ONLY. Returns absolute paths, sorted.
    """
    root = config._expand(root)
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    if "/" not in pattern and "\\" not in pattern:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        if entry.is_file() and _matches(pattern, entry.name, entry.name):
                            out.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            return []
        out.sort()
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if _matches(pattern, rel, fn):
                out.append(full)
    out.sort()
    return out


def ingest(conn: sqlite3.Connection, sources: list[tuple] | None = None) -> dict:
    """Scan all configured sources and index them. Returns a summary dict.

    Each source is (agent, root, glob). `glob` is now enforced (T02). Entries
    are capped per source at `config.MAX_ENTRIES_PER_SOURCE`; a source that hits
    the cap is listed in `summary["truncated"]` so callers can flag DEGRADED.
    This function is strictly READ-ONLY w.r.t. the source tree — it only ever
    writes to the local SQLite index (`conn`).
    """
    sources = sources if sources is not None else config.sources()
    created_at = datetime.now().isoformat(timespec="seconds")
    cap = config.MAX_ENTRIES_PER_SOURCE
    summary = {
        "files": 0,            # files actually read
        "walked_files": 0,     # files matched by the walk (before the cap)
        "entries": 0,
        "errors": 0,
        "by_agent": {},
        "files_by_agent": {},
        "truncated": [],       # agents that hit MAX_ENTRIES_PER_SOURCE
    }

    for agent, root, pattern in sources:
        paths = _walk_md(root, pattern)
        summary["walked_files"] += len(paths)
        summary["files_by_agent"][agent] = (
            summary["files_by_agent"].get(agent, 0) + len(paths))
        agent_entries = 0
        truncated = False
        for path in paths:
            if agent_entries >= cap:
                truncated = True
                break
            summary["files"] += 1
            try:
                entries = extract_entries(path)
                remaining = cap - agent_entries
                if len(entries) > remaining:
                    entries = entries[:remaining]
                n = store.replace_file_entries(conn, agent, path, entries, created_at)
            except Exception:  # noqa: BLE001 — ingest must never die on one bad file
                summary["errors"] += 1
                continue
            agent_entries += n
            summary["entries"] += n
            summary["by_agent"][agent] = summary["by_agent"].get(agent, 0) + n
        if truncated:
            summary["truncated"].append(agent)
    return summary