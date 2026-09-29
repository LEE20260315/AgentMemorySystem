"""Build a FROZEN golden set (query -> answer_phrase) for T04 — v2 rework.

Why script-generated: a hand-picked golden set drifts toward the cases we happen
to remember and cannot be re-derived. A seeded generator over the *real* index is
reproducible (`seed=0` -> byte-identical output) and honest: it samples what is
actually indexed, not what we wish were.

--------------------------------------------------------------------------
REWORK (v2, 2026-09-22) — why v1 was invalid
--------------------------------------------------------------------------
The first frozen set was rejected by the architect, with three measured defects:
    (a) 72% of queries used an English STOP-WORD as the second word
        (`topics.md to`, `stack and`) — the query did not discriminate at all;
    (b) 34% of oracles were old-system INJECTION-MARKER fragments of the form
        `[sync:mem_…|h:…|src:…]` — unreadable and meaningless as an answer;
    (c) trae titles collide wildly (`topics.md` x26, `Lessons Learned` x22), so a
        title-derived query is unsolvable by construction.
v2 fixes the CONSTRUCTION (not the metric):

    * STOP-WORD FILTERING — the discriminator may never be a function word.
    * ORACLE FROM THE PROSE REGION — the oracle is drawn only from running prose;
      headings, blank/very-short lines and `[sync:…]` marker lines are excluded.
    * BODY-DERIVED QUERY TOKENS — the query is built from the prose *body*, never
      from the (collision-prone) title. This is `正文取词`.
    * df BAND `[k+1, 0.08*N]` — every query token must occur in at least `k+1`
      groups (so it can never uniquely identify the target) AND in at most 8% of
      all groups (so it is discriminative, i.e. not a stop-word / boilerplate
      token). On the live corpus (N=236) that is df in [6, 18].
    * PER-RECORD DIAGNOSTICS — each record carries `df` (per query token),
      `df_union` (size of the OR candidate set), `and_size` (size of the AND
      candidate set) and `title_collision_size` (# groups sharing this title), so
      the fairness of every item is auditable after the fact.

Record shape (one per chosen content-group):
    {
      "query":                str,        # 1..n prose tokens, all inside the df band
      "answer_phrase":        str,        # hidden oracle: verbatim, group-unique, prose
      "sources":              [str, ...], # every agent that carries this content
      "agent":                str,        # the source this sample is stratified under
      "title":                str,        # diagnostic only
      "key":                  str,        # content_key (diagnostic / dedup)
      "strict_unique":        bool,       # oracle unique even against supersets
      "line_safe":            bool,       # oracle lies inside one prose line
      "df":                   [int, ...], # df of each query token, in query order
      "df_union":             int,        # groups containing ANY query token
      "and_size":             int,        # groups containing ALL query tokens (>=1)
      "title_collision_size": int         # groups sharing this (stripped) title
    }

Sampling: eligible groups (text >= 120 chars, not >=80% low-info headers) are
stratified so each of the 5 live sources contributes >= 20 records; the default
M=200 splits evenly. NOT filtered by "decision value" (that is G-E1's job).
"""

from __future__ import annotations

import argparse
import collections
import os
import random
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import logs, store  # noqa: E402
from recall.eval import _common  # noqa: E402

DEFAULT_M = 200
DEFAULT_SEED = 0
SOURCE_QUOTA = ("trae", "codepilot", "workbuddy", "pi", "dsh")
MIN_REPS_PER_SOURCE = 20
MIN_TEXT = 120           # exclude very short bodies (no room for a unique oracle)
LOW_INFO_MAX = 0.8       # exclude groups that are >= 80% low-info headers
PHRASE_MIN = 20          # answer_phrase length bounds
PHRASE_MAX = 120
MIN_PROSE_CHARS = 12     # a window must carry this many real (non-space) chars

# --- v2 df band --------------------------------------------------------------
# Default bounds = the architect's spec: [k+1, 0.08*N] with k=5 -> min 6; the
# upper bound is a FRACTION of the group count and is resolved per-build.
DEFAULT_K = 5
MIN_DF_FLOOR = DEFAULT_K + 1     # 6
MAX_DF_FRAC = 0.08
DEFAULT_N_TOKENS = 2             # body words per query
# How to pick the n in-band tokens: "hi" = the most-shared in-band tokens (a
# harder, larger OR set), "lo" = the rarest qualifying ones (an easier set).
DEFAULT_PICK = "hi"

_SEP = "\x00"            # joins texts for the uniqueness scan (never in a phrase)

# Old-system injection marker: `[sync:mem_…|h:…|src:…]`. Any line carrying it —
# or any candidate window containing it — is NOT prose and is never an oracle.
_MARKER = "[sync:"

# English function words (the v1 defect (a) source). A query discriminator drawn
# from this set is meaningless, so it is refused outright (the df upper bound
# would usually also drop them, but we make the intent explicit and portable).
STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to", "in",
    "on", "at", "by", "for", "with", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "it", "its", "this", "that", "these", "those", "not",
    "no", "do", "does", "did", "done", "has", "have", "had", "will", "would",
    "can", "could", "should", "may", "might", "must", "i", "you", "he", "she",
    "we", "they", "me", "him", "her", "us", "them", "my", "your", "his", "their",
    "our", "so", "up", "out", "about", "into", "over", "after", "before", "than",
    "too", "very", "just", "also", "only", "more", "most", "some", "any", "all",
    "each", "both", "few", "many", "much", "other", "such", "nor", "per", "via",
    "vs", "etc", "eg", "ie", "null", "none", "true", "false", "yes", "ok", "okay",
    # structural / markdown noise that is never a real discriminator
    "md", "txt", "json", "yaml", "yml", "rfc", "todo", "note", "notes", "section",
})


# --- prose detection ---------------------------------------------------------
def _low_info_ratio(text: str) -> float:
    """Fraction of the text that is low-information (headings / tiny lines).

    A line is "low info" if it is blank, a markdown heading (`#...`), or shorter
    than 6 chars. Groups at or above LOW_INFO_MAX are excluded — they are the
    "just a header, no real memory" stubs that make recall meaningless.
    """
    lines = text.splitlines()
    total = 0
    info = 0
    for ln in lines:
        s = ln.strip()
        total += len(s) + 1
        if not s or s.startswith("#") or len(s) < 6:
            continue
        info += len(s) + 1
    if total == 0:
        return 1.0
    return 1.0 - (info / total)


def _is_prose_line(line: str) -> bool:
    """A line counts as PROSE iff it is running text, not structure.

    Excluded: blank, markdown headings (`#`), very short lines (< 6 chars), and
    old-system injection-marker lines (containing `[sync:`). This is the v2
    "oracle from the prose region" rule — it removes defect (b) at the source.
    """
    s = line.strip()
    if not s or s.startswith("#") or len(s) < 6:
        return False
    if _MARKER in s:
        return False
    return True


def _prose_runs(text: str) -> list[list[str]]:
    """Split `text` into maximal runs of CONSECUTIVE prose lines.

    Each run is joined by "\n"; because the original lines were contiguous in
    `text`, every run (and any window inside it) is a verbatim substring of
    `text`. A one-line run is `line_safe`; a multi-line run is the documented
    cross-line fallback.
    """
    runs: list[list[str]] = []
    cur: list[str] = []
    for ln in text.split("\n"):
        if _is_prose_line(ln):
            cur.append(ln)
        else:
            if cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)
    return runs


def _prose_text(text: str) -> str:
    """The concatenation of all prose lines (used for query-token extraction)."""
    return "\n".join("\n".join(run) for run in _prose_runs(text))


# --- grouping / counting -----------------------------------------------------
def _load_groups(conn) -> list[dict]:
    """Collapse the index into content-groups (folding identical contents).

    Mirrors the display-time folding in `store.search`: identical normalised
    content is ONE group carrying every source. Reading order is by row id so the
    result is deterministic.
    """
    rows = conn.execute(
        "SELECT id, source_agent, source_file, title, raw_text FROM entries "
        "ORDER BY id").fetchall()
    groups: dict[str, dict] = {}
    order: list[str] = []
    for eid, agent, _file, title, text in rows:
        key = store._content_key(text)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "key": key, "text": text, "title": title or "",
                "agents": set(), "rep_id": eid,
            }
            order.append(key)
        g["agents"].add(agent)
    return [groups[k] for k in order]


def _group_token_sets(groups: list[dict]) -> list[set]:
    """Per-group sets of tokens from `title + text` (the retriever's haystack)."""
    return [set(_common.tokenize(g["title"] + " " + g["text"])) for g in groups]


def _token_group_counts(groups: list[dict]) -> dict[str, int]:
    """token -> number of GROUPS whose title+text contains it (case-insensitive)."""
    counts: dict[str, int] = {}
    for g in groups:
        for tok in set(_common.tokenize(g["title"] + " " + g["text"])):
            counts[tok] = counts.get(tok, 0) + 1
    return counts


def _common_enough(tok: str, counts: dict[str, int], two_other_groups: bool = False) -> bool:
    """Return whether a token is shared beyond a single target group.

    The legacy eval test passes ``True`` to require at least two *other* groups,
    hence a total document-group frequency of three. The default accepts a
    token present in two groups for the general fairness check.
    """
    minimum = 3 if two_other_groups else 2
    return counts.get(tok, 0) >= minimum


def _in_band(tok: str, dfmap: dict[str, int], min_df: int, max_df: int) -> bool:
    """True iff `tok` is a usable discriminator: not a stop-word, df in band."""
    if tok in STOPWORDS or len(tok) < 2:
        return False
    return min_df <= dfmap.get(tok, 0) <= max_df


# --- oracle ------------------------------------------------------------------
def _scan_region(region: str, forbidden: str) -> str | None:
    """Find a verbatim, disambiguating, 20..120-char window inside `region`.

    Windows are scanned outward from the region's middle (a mid-section oracle is
    most robust), preferring longer windows, keeping the FIRST accepted hit so the
    result is deterministic. A window is accepted iff it carries >= 12 real chars
    and does not occur in `forbidden`.
    """
    n = len(region)
    if n < PHRASE_MIN:
        return None
    mid = n // 2
    for ln in (64, 56, 48, 40, 32, 24, PHRASE_MIN):
        if ln > PHRASE_MAX or ln > n:
            continue
        span = n - ln
        step = max(3, span // 150)        # <=150 stops -> covers the whole span
        seen: set[int] = set()
        tried = 0
        d = 0
        while d <= span and tried < 150:
            for i in (mid - d, mid + d):
                if i < 0 or i > span or i in seen:
                    continue
                seen.add(i)
                tried += 1
                cand = region[i:i + ln]
                if len(cand.strip()) >= MIN_PROSE_CHARS and cand not in forbidden:
                    return cand
                if tried >= 150:
                    break
            d += step
    return None


def _answer_phrase(text: str, forbidden: str):
    """Pick (phrase, line_safe) from the PROSE region, or (None, None).

    Single-line prose runs are tried first (`line_safe=True`) so the oracle
    survives paragraph-level chunking; multi-line runs are the fallback and are
    flagged `line_safe=False`. Never returns an injection-marker fragment.
    """
    runs = _prose_runs(text)
    single = [r for r in runs if len(r) == 1]
    multi = [r for r in runs if len(r) > 1]
    for regions, line_safe in ((single, True), (multi, False)):
        for run in regions:
            phrase = _scan_region("\n".join(run), forbidden)
            if phrase is not None:
                return phrase, line_safe
    return None, None


# --- one record --------------------------------------------------------------
def _query_tokens(g: dict, dfmap: dict[str, int], min_df: int, max_df: int,
                  n: int, pick: str) -> list[str] | None:
    """Deterministically pick `n` prose tokens of `g` inside the df band.

    The candidates come from the PROSE body only (v2 `正文取词`), stripped of
    stop-words, then restricted to the df band. `pick="hi"` takes the most-shared
    qualifying tokens (a harder query), `pick="lo"` the rarest (an easier one).
    """
    cand = [t for t in _common.tokenize(_prose_text(g["text"]))
            if t not in STOPWORDS and min_df <= dfmap.get(t, 0) <= max_df]
    band = list(dict.fromkeys(cand))          # de-dup, keep first-seen order
    # Small synthetic corpora (and sparse real sources) may not have enough
    # fair discriminators for the requested query width. Keep the available
    # in-band tokens instead of discarding an otherwise valid oracle; the
    # fairness invariant is per token, not a hard query-width requirement.
    if not band:
        return None
    if pick == "lo":
        band.sort(key=lambda t: (dfmap[t], t))
    else:
        band.sort(key=lambda t: (-dfmap[t], t))
    return band[:n]


def _make_record(g: dict, dfmap: dict[str, int], toksets: list[set],
                 groups: list[dict], hays: list[str], agent_label: str,
                 allow_supersets: bool, min_df: int, max_df: int,
                 n_tokens: int, pick: str, title_counts: dict[str, int]):
    """Build one golden record for group `g`, or None if it is not fair enough."""
    target_text = g["text"]

    # Groups whose ENTIRE text contains this group's text are "supersets": a hit
    # on one of them is still a correct hit (it literally carries this memory),
    # so under the relaxation they are allowed to contain the oracle. Every other
    # group must NOT — the oracle still points only at memories that truly carry
    # this content.
    forbidden_parts: list[str] = []
    contained = False
    for g2, hay2 in zip(groups, hays):
        if g2["key"] == g["key"]:
            continue
        if target_text in hay2:
            contained = True
            if allow_supersets:
                continue
        forbidden_parts.append(hay2)
    forbidden = _SEP.join(forbidden_parts)

    # (1) prose-only query tokens, all inside the df band
    toks = _query_tokens(g, dfmap, min_df, max_df, n_tokens, pick)
    if not toks:
        return None

    # (2) the hidden oracle — prose region, single-line preferred
    phrase, line_safe = _answer_phrase(target_text, forbidden)
    if phrase is None:
        return None

    # (3) diagnostics
    df_union = 0
    and_size = 0
    for ts in toksets:
        hit_any = any(t in ts for t in toks)
        hit_all = all(t in ts for t in toks)
        df_union += 1 if hit_any else 0
        and_size += 1 if hit_all else 0

    return {
        "query": " ".join(toks),
        "answer_phrase": phrase,
        "sources": sorted(g["agents"]),
        "agent": agent_label,
        "title": g["title"],
        "key": g["key"],
        "strict_unique": not contained,
        "line_safe": line_safe,
        "df": [dfmap.get(t, 0) for t in toks],
        "df_union": df_union,
        "and_size": and_size,
        "title_collision_size": title_counts.get(g["title"].strip(), 1),
    }


def _resolve_bounds(groups: list[dict], min_df: int | None,
                    max_df_frac: float) -> tuple[int, int]:
    """Resolve the concrete df band for THIS corpus.

    min_df defaults to k+1 (6); max_df is `floor(max_df_frac * N)` but is never
    allowed below min_df (so a tiny corpus still functions — used by unit tests).
    """
    n = len(groups)
    lo = MIN_DF_FLOOR if min_df is None else int(min_df)
    hi = int(max_df_frac * n)
    if hi < lo:
        # A tiny corpus cannot satisfy both the minimum-sharing rule and the
        # production 8% ceiling. In that regime the ceiling is not informative;
        # allow corpus-wide tokens so smoke corpora can still exercise the
        # oracle and determinism invariants.
        hi = n if n <= lo + 2 else lo
    return lo, hi


def _assert_fair(records: list[dict], dfmap: dict[str, int],
                 min_df: int, max_df: int) -> None:
    """Hard build-time invariant: every query token is a non-stop, in-band token."""
    for rec in records:
        toks = rec["query"].split()
        if not toks:
            raise AssertionError(f"empty query: {rec!r}")
        for tok in toks:
            if not (min_df <= dfmap.get(tok, 0) <= max_df):
                raise AssertionError(
                    f"query token {tok!r} df={dfmap.get(tok, 0)} outside "
                    f"[{min_df},{max_df}]: {rec['query']!r}")
            if tok in STOPWORDS:
                raise AssertionError(
                    f"query token {tok!r} is a stop-word: {rec['query']!r}")


def build(conn, m: int = DEFAULT_M, seed: int = DEFAULT_SEED,
          allow_supersets: bool = True, min_df: int | None = None,
          max_df_frac: float = MAX_DF_FRAC, n_tokens: int = DEFAULT_N_TOKENS,
          pick: str = DEFAULT_PICK,
          min_and_size: int = 1) -> list[dict]:
    """Deterministically sample up to `m` golden records from the index `conn`.

    `min_df`/`max_df_frac` control the df band (defaults = the spec: [6, 0.08*N]).
    `min_and_size` optionally demands the query share its full token set with at
    least that many groups (a fairness guard against a uniquely-identifying
    query; default 1 keeps the raw construction available for diagnostics).
    """
    groups = _load_groups(conn)
    if not groups:
        return []
    hays = [g["title"] + _SEP + g["text"] for g in groups]
    toksets = _group_token_sets(groups)
    dfmap = _token_group_counts(groups)
    lo_df, hi_df = _resolve_bounds(groups, min_df, max_df_frac)
    title_counts = collections.Counter(g["title"].strip() for g in groups)

    eligible = [g for g in groups
                if len(g["text"]) >= MIN_TEXT and _low_info_ratio(g["text"]) < LOW_INFO_MAX]

    def _mk(g, label):
        rec = _make_record(g, dfmap, toksets, groups, hays, label,
                           allow_supersets, lo_df, hi_df, n_tokens, pick,
                           title_counts)
        if rec is not None and rec["and_size"] < min_and_size:
            return None
        return rec

    by_source: dict[str, list[dict]] = {a: [] for a in SOURCE_QUOTA}
    for g in eligible:
        for a in g["agents"]:
            if a in by_source:
                by_source[a].append(g)

    rng = random.Random(seed)
    chosen: set[str] = set()
    records: list[dict] = []
    per = max(MIN_REPS_PER_SOURCE, m // len(SOURCE_QUOTA))

    # Round-robin across sources (NOT source-by-source): a content-group can be
    # shared by several agents (legacy-fused duplicates), so filling one source
    # to completion first would starve the later ones. One valid pick per source
    # per round keeps the snapshot evenly stratified (each source ~ m/5).
    pools = {a: list(by_source[a]) for a in SOURCE_QUOTA}
    for a in SOURCE_QUOTA:
        rng.shuffle(pools[a])
    idx = {a: 0 for a in SOURCE_QUOTA}
    got = {a: 0 for a in SOURCE_QUOTA}

    progress = True
    while len(records) < m and progress:
        progress = False
        for a in SOURCE_QUOTA:
            if got[a] >= per:
                continue
            while idx[a] < len(pools[a]):
                g = pools[a][idx[a]]
                idx[a] += 1
                if g["key"] in chosen:
                    continue
                rec = _mk(g, a)
                if rec is None:
                    continue
                chosen.add(g["key"])
                records.append(rec)
                got[a] += 1
                progress = True
                break

    # Top up (or fill if a source was thin) deterministically.
    if len(records) < m:
        rest = [g for g in eligible if g["key"] not in chosen]
        rest.sort(key=lambda g: g["key"])
        rng.shuffle(rest)
        for g in rest:
            if len(records) >= m:
                break
            rec = _mk(g, sorted(g["agents"])[0])
            if rec is None:
                continue
            chosen.add(g["key"])
            records.append(rec)

    records = records[:m]
    _assert_fair(records, dfmap, lo_df, hi_df)
    return records


def summarize(records: list[dict]) -> dict:
    """Small build summary for the console / report header."""
    cn = sum(1 for r in records if _common.has_cjk(r["query"]))
    per_source: dict[str, int] = {}
    strict = 0
    line_safe = 0
    dfs: list[int] = []
    unions: list[int] = []
    ands: list[int] = []
    colls: list[int] = []
    for r in records:
        per_source[r["agent"]] = per_source.get(r["agent"], 0) + 1
        if r.get("strict_unique", True):
            strict += 1
        if r.get("line_safe", True):
            line_safe += 1
        dfs.extend(r.get("df", []))
        unions.append(r.get("df_union", 0))
        ands.append(r.get("and_size", 0))
        colls.append(r.get("title_collision_size", 1))

    def _avg(xs):
        return (sum(xs) / len(xs)) if xs else 0.0

    return {
        "m": len(records),
        "cn": cn,
        "en": len(records) - cn,
        "strict": strict,
        "line_safe": line_safe,
        "per_source": per_source,
        "df_min": min(dfs) if dfs else 0,
        "df_max": max(dfs) if dfs else 0,
        "df_avg": _avg(dfs),
        "df_union_avg": _avg(unions),
        "and_avg": _avg(ands),
        "and_min": min(ands) if ands else 0,
        "title_coll_avg": _avg(colls),
        "title_coll_max": max(colls) if colls else 0,
    }


def _write_golden(records: list[dict], path: str, *, seed: int, m: int,
                  allow_supersets: bool, min_df: int, max_df: int,
                  n_tokens: int, pick: str, min_and_size: int) -> None:
    s = summarize(records)
    doc = {
        "_format": "json-as-yaml (JSON is a subset of YAML 1.2); no PyYAML needed",
        "_note": "answer_phrase is the hidden oracle and lives ONLY in this file",
        "version": 2,
        "rework": "2026-09-22: stopword filter + prose oracle + body tokens + df band",
        "seed": seed,
        "m": m,
        "built": len(records),
        "min_df": min_df,
        "max_df": max_df,
        "n_tokens": n_tokens,
        "pick": pick,
        "min_and_size": min_and_size,
        "strict_unique": s["strict"],
        "line_safe": s["line_safe"],
        "allow_supersets": allow_supersets,
        "df_avg": round(s["df_avg"], 3),
        "df_union_avg": round(s["df_union_avg"], 3),
        "and_avg": round(s["and_avg"], 3),
        "and_min": s["and_min"],
        "title_coll_avg": round(s["title_coll_avg"], 3),
        "title_coll_max": s["title_coll_max"],
        "records": records,
    }
    _common.dump_json_yaml(doc, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_golden",
                                 description="Build a frozen recall golden set (T04/v2)")
    ap.add_argument("--db", default=logs.index_db_path())
    ap.add_argument("--out", default=_common.GOLDEN_PATH)
    ap.add_argument("--m", type=int, default=DEFAULT_M)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--min-df", type=int, default=None,
                    help=f"df lower bound (default k+1={MIN_DF_FLOOR})")
    ap.add_argument("--max-df-frac", type=float, default=MAX_DF_FRAC,
                    help="df upper bound as a FRACTION of the group count (default 0.08)")
    ap.add_argument("--n-tokens", type=int, default=DEFAULT_N_TOKENS,
                    help="body tokens per query (default 2)")
    ap.add_argument("--pick", choices=("hi", "lo"), default=DEFAULT_PICK,
                    help="prefer the most-shared (hi) or rarest (lo) in-band tokens")
    ap.add_argument("--min-and-size", type=int, default=1,
                    help="require the query's full token set to match >= N groups")
    ap.add_argument("--no-supersets", action="store_true",
                    help="strict: forbid a superset group from containing the oracle "
                         "(yields fewer records on this corpus)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing frozen golden set")
    args = ap.parse_args(argv)

    if os.path.exists(args.out) and not args.force:
        print(f"[拒绝] 冻结黄金集已存在：{args.out}\n"
              f"        如确要重建，请显式加 --force（会改变评测基准）")
        return 2

    allow_supersets = not args.no_supersets
    conn = _common.open_readonly(args.db)
    try:
        groups = _load_groups(conn)
        lo_df, hi_df = _resolve_bounds(groups, args.min_df, args.max_df_frac)
        records = build(conn, m=args.m, seed=args.seed,
                        allow_supersets=allow_supersets, min_df=lo_df,
                        max_df_frac=args.max_df_frac, n_tokens=args.n_tokens,
                        pick=args.pick, min_and_size=args.min_and_size)
    finally:
        conn.close()

    _write_golden(records, args.out, seed=args.seed, m=args.m,
                  allow_supersets=allow_supersets, min_df=lo_df, max_df=hi_df,
                  n_tokens=args.n_tokens, pick=args.pick,
                  min_and_size=args.min_and_size)
    s = summarize(records)
    print(f"黄金集已生成：{args.out}")
    print(f"  m={s['m']}  中文查询={s['cn']}  英文查询={s['en']}  "
          f"strict_unique={s['strict']}  line_safe={s['line_safe']}  seed={args.seed}")
    print(f"  df band=[{lo_df},{hi_df}]  n_tokens={args.n_tokens}  pick={args.pick}  "
          f"min_and_size={args.min_and_size}")
    print(f"  df avg={s['df_avg']:.1f} (min {s['df_min']}..max {s['df_max']})  "
          f"df_union avg={s['df_union_avg']:.1f}  and_size avg={s['and_avg']:.1f} "
          f"(min {s['and_min']})  title_coll avg={s['title_coll_avg']:.1f} "
          f"(max {s['title_coll_max']})")
    print("  各来源： " + ", ".join(f"{a}={n}" for a, n in sorted(s["per_source"].items())))
    if s["m"] < args.m:
        print(f"  [警告] 仅取到 {s['m']}/{args.m} 条（合格样本不足）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
