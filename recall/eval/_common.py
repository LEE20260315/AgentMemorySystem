"""Shared helpers for the recall eval basis (T04).

Kept deliberately small: tokenization (CJK-correct, matching the substring
retriever's real behaviour), JSON-as-YAML I/O, and read-only DB opening. Nothing
here writes to the index.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

# --- paths (all inside the repo, next to the code) ---------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(_HERE, "golden_pairs.yaml")
MANUAL_PATH = os.path.join(_HERE, "golden_pairs_manual.yaml")  # optional, hand-added
REPORT_PATH = os.path.join(_HERE, "REPORT.md")

# --- tokenization ------------------------------------------------------------
# Two token shapes, mirroring how a human actually types a CJK/English keyword
# query into a substring retriever:
#   * ASCII words of length >= 2 (letters/digits/._-): e.g. `OneDrive`, `v2.1`
#   * CJK runs, split into 2-char sliding BIGRAMS (CJK has no spaces, so the
#     maximal run would be one giant near-unique token — bigrams are the unit
#     Chinese keyword search actually uses, and they are naturally shared).
# Single ASCII alphanumerics and lone CJK chars are deliberately NOT tokens.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]{2,}|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Split `text` into query tokens, order-preserving and de-duplicated.

    ASCII tokens are lower-cased (the retriever matches case-insensitively); CJK
    runs expand to overlapping 2-char bigrams.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if _CJK_RE.search(tok):
            if len(tok) < 2:
                continue
            parts = [tok[i:i + 2] for i in range(len(tok) - 1)]
        else:
            parts = [tok.lower()]
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def has_cjk(text: str) -> bool:
    """True if `text` contains at least one CJK ideograph."""
    return bool(text) and bool(_CJK_RE.search(text))


# --- JSON-as-YAML I/O --------------------------------------------------------
def dump_json_yaml(obj, path: str) -> None:
    """Write `obj` as JSON (a strict subset of YAML 1.2), atomically.

    Atomicity matters for the frozen golden set: a crash must never leave a
    half-written file that a later run would silently trust.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_json_yaml(path: str):
    """Read a JSON-as-YAML document. Raises OSError/JSONDecodeError on trouble."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open the index READ-ONLY so eval can never mutate `index.db`.

    Uses the SQLite URI `mode=ro`, which fails loudly (rather than creating a
    file) if the path is missing and forbids any write. This is what lets §12.4
    assert `sha256(index.db)` is unchanged across an eval run.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"index not found: {db_path}")
    uri = Path(db_path).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)
