"""Local SQLite index + substring keyword search (CJK-correct).

Phase 0 note: SQLite FTS5 `trigram` tokenizer only emits tokens for runs of
>=3 chars, which misses 2-char Chinese words (the common case). At this scale
(few thousand entries) a substring (LIKE) multi-term scorer is both correct
and fast, so we use it and drop FTS entirely.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from typing import Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_agent  TEXT NOT NULL,
  source_file   TEXT NOT NULL,
  title         TEXT,
  fingerprint   TEXT NOT NULL,
  raw_text      TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_file ON entries(source_file);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and init) the index. Local-only, never on a synced dir."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def replace_file_entries(
    conn: sqlite3.Connection,
    agent: str,
    path: str,
    entries: Iterable[dict],
    created_at: str,
) -> int:
    """Idempotently replace all entries of one source file (delete-then-insert).

    Each entry dict: {title: str, text: str, fingerprint: str}.
    Returns the number of entries inserted.
    """
    conn.execute("DELETE FROM entries WHERE source_file = ?", (path,))
    n = 0
    for e in entries:
        conn.execute(
            "INSERT INTO entries(source_agent, source_file, title, fingerprint, "
            "raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (agent, path, e["title"], e["fingerprint"], e["text"], created_at),
        )
        n += 1
    return n


def _content_key(text: str) -> str:
    """Normalised content identity for cross-source folding.

    Only EXACT duplicates collapse: whitespace is removed and case is folded,
    then SHA256. No fuzzy / similarity matching — we would rather fold too
    little than fold two different memories (the memory files legitimately
    contain near-identical boilerplate).
    """
    norm = re.sub(r"\s+", "", (text or "").lower())
    return hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()


def _snippet(text: str, terms: list[str], width: int = 140, lead: int = 40) -> str:
    """Return a window of `text` centred on the FIRST body hit of any term.

    Looks for the earliest case-insensitive occurrence of any `terms`; if found,
    returns a `width`-char window starting ~`lead` chars before it, adding `…`
    on either side as needed. If no term is in the body (e.g. the match was only
    in the title), falls back to the head of the body (previous behaviour).
    """
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    low = flat.lower()
    pos = -1
    for t in terms:
        i = low.find(t.lower())
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return flat if len(flat) <= width else flat[: width - 1] + "…"
    start = max(0, pos - lead)
    end = min(len(flat), start + width)
    if end - start < width:            # near the tail: pull the window back
        start = max(0, end - width)
    frag = flat[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return prefix + frag + suffix


def search(conn: sqlite3.Connection, query: str, k: int = 5) -> list[dict]:
    """Multi-term substring search: AND-first ranking + cross-source folding.

    Ranking (per row): `matched` = number of distinct query terms found in the
    title/body. Rows are ordered by (all terms matched, matched, score, id) desc,
    so rows that contain EVERY term rank above partial (OR) hits; `score` is
    still title-hit*3 + body-hit*1, ties break toward newer rows (higher id).

    Folding: rows with byte-identical normalised content (`_content_key`) are
    merged into ONE result — the highest-ranked row is the representative, and
    the result carries the full `sources` list + `n_sources`. Top-K therefore
    yields K DISTINCT contents instead of K copies of the same memory.

    The index itself is never modified: this is a read-only, display-time merge.
    """
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if t]
    if not terms:
        return []

    # Candidate SQL: any term appearing (case-insensitive) in title or body.
    # NOTE: kept as OR (never hard AND) so we never drop to zero results; the
    # AND-first ordering above is what surfaces full matches.
    conds = []
    args = []
    for t in terms:
        like = f"%{t}%"
        conds.append("(title LIKE ? OR raw_text LIKE ?)")
        args.extend([like, like])
    rows = conn.execute(
        "SELECT id, source_agent, source_file, title, raw_text FROM entries WHERE "
        + " OR ".join(conds),
        args,
    ).fetchall()

    lowered = [(t, t.lower()) for t in terms]
    scored = []
    for eid, agent, file, title, text in rows:
        t_low = (title or "").lower()
        b_low = (text or "").lower()
        score = 0
        matched = 0
        matched_terms = []
        for orig, tl in lowered:
            in_title = tl in t_low
            in_body = tl in b_low
            if in_title or in_body:
                matched += 1
                matched_terms.append(orig)
            score += (3 if in_title else 0) + (1 if in_body else 0)
        scored.append({
            "id": eid, "agent": agent, "file": file, "title": title, "text": text,
            "score": score, "matched": matched, "matched_terms": matched_terms,
        })

    # AND-first, then more terms, then score, then newer id.
    scored.sort(key=lambda r: (r["matched"] == len(terms), r["matched"],
                               r["score"], r["id"]), reverse=True)

    # Fold identical contents, preserving rank order; first row wins (=best).
    groups: dict[str, dict] = {}
    order: list[str] = []
    for r in scored:
        key = _content_key(r["text"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"rep": r, "sources": [], "seen": set()}
            order.append(key)
        src = (r["agent"], r["file"])
        if src not in g["seen"]:
            g["seen"].add(src)
            g["sources"].append({"agent": r["agent"], "file": r["file"]})

    results = []
    for key in order[:k]:
        g = groups[key]
        rep = g["rep"]
        results.append({
            "id": rep["id"],
            "agent": rep["agent"],
            "file": rep["file"],
            "title": rep["title"],
            "text": rep["text"],
            "sources": g["sources"],
            "n_sources": len(g["sources"]),
            "snippet": _snippet(rep["text"], terms),
            "matched_terms": rep["matched_terms"],
        })
    return results


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    by_agent = conn.execute(
        "SELECT source_agent, COUNT(*) FROM entries GROUP BY source_agent "
        "ORDER BY 2 DESC").fetchall()
    return {"total": total, "by_agent": by_agent}