"""Score recall against the frozen golden set: recall@k, MRR, and DOC.

Metrics
-------
* recall@k : fraction of queries whose answer_phrase is contained in ANY of the
             top-k result *texts*. (hit = "the memory we should surface is in
             the window", independent of exact position.)
* MRR      : mean of 1/rank of the FIRST result text containing answer_phrase
             (0 if none). Rewards putting the right memory at the very top.
* DOC      : Duplicate-Occupancy of top-k — the fraction of result PAIRS whose
             *snippet* content-keys are equal, averaged over queries with >=2
             results:

                 DOC_q = #{i<j : key(snippet_i) == key(snippet_j)} / C(n,2)

             `key` is `store._content_key` (normalised-content hash). We hash the
             SNIPPET, not the full text, because display-time folding already
             removes identical *texts*; what remains is "different memories whose
             previews look the same", which is exactly the waste DOC measures.
* PD       : Preview Distinctness (0..1, higher = better) — the GRADED
             replacement for DOC: the mean, over result pairs, of
             `1 - LCS(snippet_i, snippet_j) / min(len_i, len_j)`, computed on the
             UNDECORATED snippets. DOC only detects byte-INEQUALITY, so a window
             shifted by a single character satisfies it while the reader still
             sees the same text (measured directly in §14.6). The longest common
             SUBSTRING survives a shift, so PD grades similarity instead of
             equality.
             ⚠️ **DOC is kept as an OBSERVATION ONLY** and must not be used to
             claim preview quality improved.

Keep / rollback criteria for T05 (chunking), all three must hold:
    recall@5_new >= base  AND  MRR_new >= base - 0.005  AND  DOC_new <= 0.8 * base
Otherwise roll back to the previous ingest code and rebuild.

Read-only: the index is opened with SQLite `mode=ro`; the golden set is loaded,
never rewritten. The only file this script writes is the REPORT (--out).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import logs, store  # noqa: E402
from recall.eval import _common  # noqa: E402

DEFAULT_K = 5


# --- metric primitives -------------------------------------------------------
def _hit(results: list[dict], phrase: str) -> bool:
    """True if any result's full text contains the verbatim answer phrase."""
    if not phrase:
        return False
    return any(phrase in (r.get("text") or "") for r in results)


def _rank(results: list[dict], phrase: str) -> int:
    """1-based rank of the first hit, or 0 if none."""
    if not phrase:
        return 0
    for i, r in enumerate(results):
        if phrase in (r.get("text") or ""):
            return i + 1
    return 0


def compute_doc(results: list[dict]):
    """Duplicate-Occupancy of top-k (see module docstring).

    Returns None when there are fewer than 2 results (caller excludes these from
    the mean). Uses `store._content_key(r["snippet"])` so it is comparable
    before/after chunking.
    """
    if len(results) < 2:
        return None
    keys = [store._content_key(r.get("snippet") or "") for r in results]
    n = len(keys)
    dup = 0
    for i in range(n):
        for j in range(i + 1, n):
            if keys[i] == keys[j]:
                dup += 1
    total = n * (n - 1) // 2
    return (dup / total) if total else None


def _core(snippet: str) -> str:
    """Strip the "…" decoration so similarity is measured on the shown text."""
    return (snippet or "").strip("…")


def _lcs_len(a: str, b: str) -> int:
    """Length of the longest common SUBSTRING of a and b (classic DP, O(n*m)).

    Deliberately a SUBSTRING, not a prefix: a slid preview window still shares most
    of its characters, so a prefix-only measure would grade it as "different".
    """
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def compute_pd(results: list[dict]):
    """Preview Distinctness of top-k (graded; higher = more distinguishable).

    PD_q = mean over pairs of `1 - LCS(core_i, core_j) / min(len_i, len_j)`.
    Returns None when there are fewer than 2 results (excluded from the mean).

    Why not DOC: DOC compares content HASHES, so `a` and `a` shifted one character
    are "perfectly distinct" while looking identical. PD rewards genuinely
    different previews, so it cannot be satisfied by nudging a window.
    """
    if len(results) < 2:
        return None
    cores = [_core(r.get("snippet") or "") for r in results]
    vals = []
    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            a, b = cores[i], cores[j]
            m = min(len(a), len(b))
            if m == 0:
                continue
            vals.append(1.0 - _lcs_len(a, b) / m)
    return (sum(vals) / len(vals)) if vals else None


def evaluate(conn, records: list[dict], k: int = DEFAULT_K) -> dict:
    """Run every golden query through `store.search` and aggregate the metrics."""
    n = len(records)
    hits = {1: 0, 3: 0, k: 0}
    mrr = 0.0
    doc_vals: list[float] = []
    pd_vals: list[float] = []
    doc_queries = 0
    zero_result = 0
    per_source: dict[str, dict] = {}

    for rec in records:
        query = rec.get("query", "")
        phrase = rec.get("answer_phrase", "")
        results = store.search(conn, query, k)
        if not results:
            zero_result += 1
        rank = _rank(results, phrase)
        mrr += (1.0 / rank) if rank else 0.0
        for kk in hits:
            hits[kk] += 1 if _hit(results[:kk], phrase) else 0
        d = compute_doc(results)
        if d is not None:
            doc_vals.append(d)
            doc_queries += 1
        p = compute_pd(results)
        if p is not None:
            pd_vals.append(p)
        src = rec.get("agent", "?")
        bucket = per_source.setdefault(src, {"n": 0, "h": 0})
        bucket["n"] += 1
        bucket["h"] += 1 if _hit(results, phrase) else 0

    return {
        "n": n,
        "k": k,
        "recall@1": (hits[1] / n) if n else 0.0,
        "recall@3": (hits[3] / n) if n else 0.0,
        f"recall@{k}": (hits[k] / n) if n else 0.0,
        "mrr": (mrr / n) if n else 0.0,
        "doc": (sum(doc_vals) / len(doc_vals)) if doc_vals else None,
        "doc_queries": doc_queries,
        "pd": (sum(pd_vals) / len(pd_vals)) if pd_vals else None,
        "zero_result": zero_result,
        "cn": sum(1 for r in records if _common.has_cjk(r.get("query", ""))),
        "per_source": per_source,
    }


def load_golden(path: str) -> list[dict]:
    """Load records from a golden file (object-with-records or bare list)."""
    data = _common.load_json_yaml(path)
    if isinstance(data, dict):
        return list(data.get("records", []))
    if isinstance(data, list):
        return list(data)
    raise ValueError(f"unrecognised golden format: {path}")


# --- reporting ---------------------------------------------------------------
def _fmt_pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt(x, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _section(title: str, metrics: dict) -> list[str]:
    """Render one metrics block as a markdown section."""
    k = metrics["k"]
    lines = [
        f"### {title}",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 样本数 M | {metrics['n']} |",
        f"| k | {k} |",
        f"| recall@1 | {_fmt_pct(metrics['recall@1'])} |",
        f"| recall@3 | {_fmt_pct(metrics['recall@3'])} |",
        f"| recall@{k} | {_fmt_pct(metrics[f'recall@{k}'])} |",
        f"| MRR | {_fmt(metrics['mrr'])} |",
        f"| **PD 预览区分度** (1 − LCS/len，越高越好) | {_fmt(metrics.get('pd'))} |",
        f"| DOC (top-k 重复占用，**仅为观察项**) | {_fmt(metrics.get('doc'))} |",
        f"| DOC 有效查询数 (n>=2) | {metrics['doc_queries']} |",
        f"| 零结果查询数 | {metrics['zero_result']} |",
        "",
        f"各来源 recall@{k}：",
        "",
        "| 来源 | 样本数 | recall@k |",
        "|---|---|---|",
    ]
    for src, b in sorted(metrics["per_source"].items()):
        pct = (b["h"] / b["n"] * 100) if b["n"] else 0.0
        lines.append(f"| {src} | {b['n']} | {pct:.1f}% |")
    lines.append("")
    return lines


def write_report(out_path: str, *, db_path: str, golden_path: str,
                 metrics: dict, label: str = "基线 (pre-T05)") -> None:
    """Write / append a metrics block to REPORT.md under the given label."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = [
        f"## {label}",
        "",
        f"- 生成时间：{now}",
        f"- 索引库：`{db_path}`",
        f"- 冻结黄金集：`{golden_path}`",
        "",
    ]
    block += _section("指标", metrics)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            existing = f.read()
        with open(out_path, "a", encoding="utf-8") as f:
            if not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(block) + "\n")
    else:
        header = [
            "# Recall 评测报告（T04 基座 / T05 切分对照）",
            "",
            "> 本报告由 `recall/eval/recall_eval.py` 生成。**唯一裁判**：冻结黄金集 "
            "`golden_pairs.yaml`（seed 固定，构建后不再变动）。",
            "> 指标定义见脚本 docstring：recall@k、MRR、DOC（top-k 重复占用）。",
            "> 只读：索引以 SQLite `mode=ro` 打开，评测不改动 `index.db`；",
            "> `answer_phrase` 仅存在于黄金集，绝不进入索引 / 日志 / 遥测。",
            "",
            "**T05 保留判据（三条同时成立才保留切分）**：",
            "",
            "- `recall@5_new >= recall@5_base`",
            "- `MRR_new >= MRR_base - 0.005`",
            "- `DOC_new <= 0.8 * DOC_base`",
            "",
            "---",
            "",
        ]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(header) + "\n".join(block) + "\n")
    print(f"报告已写入：{out_path}  [{label}]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="recall_eval",
        description="Score recall against the frozen golden set (T04)")
    ap.add_argument("--db", default=logs.index_db_path())
    ap.add_argument("--golden", default=_common.GOLDEN_PATH)
    ap.add_argument("--out", default=_common.REPORT_PATH)
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--label", default="基线 (pre-T05)")
    ap.add_argument("--build", action="store_true",
                    help="重建冻结黄金集（等价于 build_golden；需 --force 覆盖）")
    ap.add_argument("--m", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-supersets", action="store_true",
                    help="配合 --build：严格模式，禁止超集组包含 oracle（样本更少）")
    ap.add_argument("--force", action="store_true",
                    help="配合 --build：覆盖已存在的冻结黄金集")
    args = ap.parse_args(argv)

    if args.build:
        from recall.eval import build_golden
        rc = build_golden.main(["--db", args.db, "--out", args.golden,
                                "--m", str(args.m), "--seed", str(args.seed)]
                               + (["--force"] if args.force else [])
                               + (["--no-supersets"] if args.no_supersets else []))
        if rc != 0:
            return rc

    if not os.path.exists(args.golden):
        print(f"[错误] 黄金集不存在：{args.golden}\n"
              f"        请先运行： python -m recall.eval.recall_eval --build")
        return 2

    records = load_golden(args.golden)
    conn = _common.open_readonly(args.db)
    try:
        metrics = evaluate(conn, records, k=args.k)
    finally:
        conn.close()

    write_report(args.out, db_path=args.db, golden_path=args.golden,
                 metrics=metrics, label=args.label)
    print(f"  M={metrics['n']}  recall@{args.k}="
          f"{_fmt_pct(metrics[f'recall@{args.k}'])}  "
          f"MRR={_fmt(metrics['mrr'])}  DOC={_fmt(metrics['doc'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
