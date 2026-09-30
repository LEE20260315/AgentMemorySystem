"""Local SQLite index + substring keyword search (CJK-correct).

Phase 0 note: SQLite FTS5 `trigram` tokenizer only emits tokens for runs of
>=3 chars, which misses 2-char Chinese words (the common case). At this scale
(few thousand entries) a substring (LIKE) multi-term scorer is both correct
and fast, so we use it and drop FTS entirely.
"""

from __future__ import annotations

import hashlib
import math
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


def _span(length: int, pos: int, width: int, lead: int) -> tuple[int, int]:
    """The [start,end) slice a preview would show for a hit at `pos`."""
    start = max(0, pos - lead)
    end = min(length, start + width)
    if end - start < width:            # near the tail: pull the window back
        start = max(0, end - width)
    return start, end


def _slide_starts(length: int, pos: int, width: int, lead: int) -> list[int]:
    """Candidate preview starts, best-first: default centring, then slides.

    Every start keeps the hit at `pos` INSIDE its window, so a slid preview never
    loses the term it matched on — sliding only changes which surrounding text is
    shown. This is how a preview escapes being byte-identical to one already on
    screen: two memories whose shared boilerplate prefix contains the term would
    otherwise render as the same 140 chars (measured on the v2 baseline: 67.0% of
    duplicate-preview pairs differ only AFTER the first 300 chars).
    """
    if length <= width:
        return [0]
    default, _ = _span(length, pos, width, lead)
    lo = max(0, pos - width + 1)
    hi = min(pos, length - width)
    step = max(1, width // 7)
    starts = [default]
    s = default
    while s + step <= hi:
        s += step
        starts.append(s)
    starts.append(hi)          # the extremes must be tried: a fixed step can skip
    s = default                # exactly the shift that reaches distinguishing text
    while s - step >= lo:
        s -= step
        starts.append(s)
    starts.append(lo)
    seen: set[int] = set()
    out: list[int] = []
    for s in starts:
        if lo <= s <= hi and s not in seen:
            seen.add(s)
            out.append(s)
    return out or [default]


def _leading_common(a: str, b: str) -> int:
    """Length of the shared leading run — what a reader perceives as "the same"."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _snippet(text: str, terms: list[str], width: int = 140, lead: int = 40,
             avoid: list[str] | None = None) -> str:
    """Return a window of `text` anchored on the earliest body hit of any term.

    If no term occurs in the body (e.g. the match was only in the title), falls
    back to the head of the body (previous behaviour).

    `avoid` holds the previews already shown for this result set. `DOC` is a
    BINARY metric (byte-equality), so a window shifted by a single character
    already counts as "distinct" while still looking identical to a reader — the
    first version of this fix exploited exactly that and was rejected on visual
    inspection. So this does not merely break equality: among all slide positions
    it picks the one that MAXIMISES the visible difference, i.e. minimises the
    longest common leading run against every already-shown preview. The hit at
    `pos` is always kept inside the window, and nothing is rewritten — only the
    window moves.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    low = flat.lower()
    pos = -1
    for t in terms:
        i = low.find((t or "").lower())
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return flat if len(flat) <= width else flat[: width - 1] + "…"

    def _render(start: int) -> str:
        end = min(len(flat), start + width)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(flat) else ""
        return prefix + flat[start:end] + suffix

    default, _ = _span(len(flat), pos, width, lead)
    if not avoid:
        return _render(default)

    def _core(start: int) -> str:
        return flat[start:min(len(flat), start + width)]

    # Compare the UNDECORATED windows. Every slid window starts with an "…", so
    # comparing rendered strings would score a zero common prefix for ALL of them
    # and turn the rule below into a no-op — which is exactly what the first
    # version did (caught on visual inspection, not by the metric).
    best_start = default
    best_worst = max(_leading_common(_core(default), a) for a in avoid)
    for start in _slide_starts(len(flat), pos, width, lead):
        worst = max(_leading_common(_core(start), a) for a in avoid)
        if worst < best_worst:
            best_start, best_worst = start, worst
            if worst == 0:                 # differs from the very first char
                break
    return _render(best_start)


def search(conn: sqlite3.Connection, query: str, k: int = 5) -> list[dict]:
    """Multi-term substring search: AND-first ranking + cross-source folding.

    Ranking (per row): `matched` = number of distinct query terms found in the
    title/body. Rows are ordered by (all terms matched, matched, score, id) desc,
    so rows that contain EVERY term rank above partial (OR) hits.

    `score` is a BM25-shaped sum: saturating term frequency (`_tf`) normalised by
    document length, times a candidate-set IDF, with the title component scaled
    DOWN by how many candidates share that exact title (`title_w`). Ties break
    toward newer rows (higher id).

    Folding: rows with byte-identical normalised content (`_content_key`) are
    merged into ONE result — the highest-ranked row is the representative, and
    the result carries the full `sources` list + `n_sources`. Top-K therefore
    yields K DISTINCT contents instead of K copies of the same memory.

    Previews: every shown window is made distinguishable from the other windows in
    the same result set (see `_snippet(avoid=…)`). DOC exists because two
    different memories can otherwise render as one identical 140-char preview.

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
    cand_low = [((title or "").lower(), (text or "").lower())
                for _id, _a, _f, title, text in rows]
    n_cand = len(cand_low)

    # --- candidate-set df (IDF proxy) ----------------------------------------
    # Measured over the OR candidate set rather than the whole table: no extra
    # query, and it is exactly the population being ranked. A floor keeps a
    # single-term query (where df == N) from collapsing every score to zero.
    df: dict[str, int] = {}
    for _orig, tl in lowered:
        df[tl] = sum(1 for t_low, b_low in cand_low if tl in t_low or tl in b_low)

    def _idf(tl: str) -> float:
        d = df.get(tl, 0)
        if n_cand and d:
            return max(0.15, math.log(1.0 + (n_cand - d + 0.5) / (d + 0.5)))
        return 0.15

    # --- title-collision discount --------------------------------------------
    # trae repeats a few titles across dozens of files (`topics.md` x26,
    # `Lessons Learned` x22; max 61 in the v2 golden set). A flat title bonus then
    # floods top-k with same-titled rows, so the title component is scaled by how
    # many candidates share that exact title.
    title_counts: dict[str, int] = {}
    for t_low, _b_low in cand_low:
        key = t_low.strip()
        if key:
            title_counts[key] = title_counts.get(key, 0) + 1

    # BM25 defaults. `dl` is measured in CHARACTERS (CJK makes token counts
    # meaningless here) and normalised by the candidate-set average.
    K1, B = 1.2, 0.75
    avgdl = ((sum(len(a) + len(b) for a, b in cand_low) / n_cand)
             if n_cand else 1.0) or 1.0

    def _tf(x: int, denom: float) -> float:
        """Saturating, length-normalised term frequency (BM25 shape)."""
        return (x * (K1 + 1.0)) / (x + K1 * denom) if x else 0.0

    scored = []
    for eid, agent, file, title, text in rows:
        t_low = (title or "").lower()
        b_low = (text or "").lower()
        denom = 1.0 - B + B * ((len(t_low) + len(b_low)) / avgdl)
        title_w = 1.0 / (1.0 + math.log1p(max(0, title_counts.get(t_low.strip(), 1) - 1)))
        score = 0.0
        matched = 0
        matched_terms = []
        for orig, tl in lowered:
            tf_t = t_low.count(tl)
            tf_b = b_low.count(tl)
            if not (tf_t or tf_b):
                continue
            matched += 1
            matched_terms.append(orig)
            # Binary presence (the old rule) let a 4000-char blob mentioning a term
            # once outrank a short focused memory; density signals relevance, so tf
            # is saturated and normalised by document length.
            score += _idf(tl) * (3.0 * title_w * _tf(tf_t, denom) + _tf(tf_b, denom))
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
    shown: list[str] = []
    for key in order[:k]:
        g = groups[key]
        rep = g["rep"]
        snip = _snippet(rep["text"], terms, avoid=shown)
        # `avoid` holds the UNDECORATED window bodies: the "…" decoration is what
        # defeats a common-prefix comparison (see _snippet).
        shown.append(snip.lstrip("…").rstrip("…"))
        results.append({
            "id": rep["id"],
            "agent": rep["agent"],
            "file": rep["file"],
            "title": rep["title"],
            "text": rep["text"],
            "sources": g["sources"],
            "n_sources": len(g["sources"]),
            "snippet": snip,
            "matched_terms": rep["matched_terms"],
        })
    return results


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    by_agent = conn.execute(
        "SELECT source_agent, COUNT(*) FROM entries GROUP BY source_agent "
        "ORDER BY 2 DESC").fetchall()
    return {"total": total, "by_agent": by_agent}