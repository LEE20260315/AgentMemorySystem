"""Tests for the T04 eval basis: golden-set build + recall@k / MRR / DOC.

Locks the fairness invariants (verbatim, group-unique, mid-section oracle; every
query token common to >=2 other entries), determinism, the DOC formula, and the
read-only guarantee (no byte of index.db changes across an eval run).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from recall import store  # noqa: E402
from recall.eval import _common, build_golden, recall_eval  # noqa: E402

_FILLER = "填充内容用于凑长度"      # 9 chars, shared by every body -> a common token


def _body(i: int) -> str:
    """A >120-char body whose mid-section carries a per-i unique marker."""
    marker = f"独特标记{i}号内容说明"
    return _FILLER * 8 + marker + _FILLER * 12


class _TmpDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "idx.db")
        self.conn = store.connect(self.db)
        self._ts = "2026-01-01T00:00:00"

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _add(self, agent: str, file: str, title: str, text: str) -> None:
        store.replace_file_entries(
            self.conn, agent, file,
            [{"title": title, "text": text, "fingerprint": f"{agent}:{file}"}],
            self._ts)

    def _seed_corpus(self, n: int = 8) -> None:
        agents = ["trae", "dsh", "pi", "workbuddy", "codepilot"]
        for i in range(n):
            a = agents[i % len(agents)]
            self._add(a, f"{a}{i}.md", "标题", _body(i))


class GoldenBuildTest(_TmpDb):
    def test_build_nonempty_and_valid(self):
        self._seed_corpus(8)
        records = build_golden.build(self.conn, m=5, seed=0)
        self.assertTrue(records, "a seeded corpus must yield records")
        self.assertLessEqual(len(records), 5)

        texts = [r[0] for r in self.conn.execute("SELECT raw_text FROM entries")]
        for rec in records:
            phrase = rec["answer_phrase"]
            # verbatim + group-unique across the whole db
            self.assertIn(phrase, " ".join(texts))
            self.assertEqual(
                sum(1 for t in texts if phrase in t), 1,
                f"answer_phrase must be unique to one group: {phrase!r}")
            self.assertGreaterEqual(len(phrase), build_golden.PHRASE_MIN)
            self.assertLessEqual(len(phrase), build_golden.PHRASE_MAX)
            self.assertTrue(rec["query"].strip())

    def test_build_deterministic(self):
        self._seed_corpus(8)
        a = build_golden.build(self.conn, m=5, seed=0)
        b = build_golden.build(self.conn, m=5, seed=0)
        self.assertEqual(a, b, "same seed must reproduce the golden set byte-for-byte")

    def test_every_query_token_is_common(self):
        self._seed_corpus(8)
        records = build_golden.build(self.conn, m=5, seed=0)
        counts = build_golden._token_group_counts(build_golden._load_groups(self.conn))
        for rec in records:
            for tok in rec["query"].split():
                self.assertTrue(
                    build_golden._common_enough(tok, counts, True),
                    f"query token {tok!r} must appear in >=2 other entries")

    def test_short_bodies_excluded(self):
        self._add("trae", "short.md", "标题", "太短了 " + _FILLER)   # < 120 chars
        for i in range(4):
            self._add("trae", f"long{i}.md", "标题", _body(i))
        records = build_golden.build(self.conn, m=5, seed=0)
        short_text = "太短了 " + _FILLER
        for rec in records:
            self.assertNotIn(short_text, rec["answer_phrase"])
            self.assertNotEqual(rec["title"], "", "sanity")


class EvalMetricTest(_TmpDb):
    def test_compute_doc_counts_duplicate_snippet_pairs(self):
        # "abc" and "ABC" normalise equal -> 1 dup pair out of C(3,2)=3
        res = [{"snippet": "abc"}, {"snippet": "ABC"}, {"snippet": "xyz"}]
        self.assertAlmostEqual(recall_eval.compute_doc(res), 1 / 3)

    def test_compute_doc_none_for_fewer_than_two(self):
        self.assertIsNone(recall_eval.compute_doc([]))
        self.assertIsNone(recall_eval.compute_doc([{"snippet": "abc"}]))

    def test_recall_and_mrr_hit(self):
        text = "独特标记X" + _FILLER * 12          # >120 chars
        self._add("trae", "a.md", "标题", text)
        rec = {"query": "标题", "answer_phrase": "独特标记X", "agent": "trae"}
        m = recall_eval.evaluate(self.conn, [rec], k=5)
        self.assertEqual(m["recall@5"], 1.0)
        self.assertEqual(m["recall@1"], 1.0)
        self.assertEqual(m["mrr"], 1.0)
        self.assertIsNone(m["doc"], "single result -> DOC undefined")

    def test_doc_detects_identical_previews(self):
        # Same 140-char preview, different tails -> not folded, but DOC sees dup
        self._add("trae", "one.md", "T", "alpha " + "x" * 200 + " DIFFERENT_ONE")
        self._add("trae", "two.md", "T", "alpha " + "x" * 200 + " DIFFERENT_TWO")
        rec = {"query": "alpha", "answer_phrase": "DIFFERENT_TWO", "agent": "trae"}
        m = recall_eval.evaluate(self.conn, [rec], k=5)
        self.assertEqual(m["recall@5"], 1.0)
        self.assertEqual(m["doc"], 1.0, "two look-alike previews -> DOC == 1.0")

    def test_evaluate_does_not_modify_index(self):
        self._seed_corpus(6)
        self.conn.commit()                       # flush the seed writes first
        before = hashlib.sha256(open(self.db, "rb").read()).hexdigest()
        records = build_golden.build(self.conn, m=5, seed=0)
        recall_eval.evaluate(self.conn, records, k=5)
        self.conn.commit()
        after = hashlib.sha256(open(self.db, "rb").read()).hexdigest()
        self.assertEqual(before, after, "eval must never write index.db")

    def test_report_section_handles_non_default_k(self):
        # regression: the report must not hard-code recall@5
        text = "独特标记Y" + _FILLER * 12
        self._add("trae", "a.md", "标题", text)
        rec = {"query": "标题", "answer_phrase": "独特标记Y", "agent": "trae"}
        m = recall_eval.evaluate(self.conn, [rec], k=3)
        lines = "\n".join(recall_eval._section("x", m))
        self.assertIn("recall@3", lines)
        self.assertNotIn("recall@5", lines)


class GoldenIoTest(_TmpDb):
    def test_roundtrip(self):
        path = os.path.join(self._tmp.name, "g.yaml")
        obj = {"version": 1, "records": [{"query": "标题 记忆", "answer_phrase": "x"}]}
        _common.dump_json_yaml(obj, path)
        self.assertEqual(_common.load_json_yaml(path), obj)

    def test_open_readonly_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            _common.open_readonly(os.path.join(self._tmp.name, "nope.db"))

    def test_open_readonly_cannot_write(self):
        self._seed_corpus(2)
        ro = _common.open_readonly(self.db)
        try:
            with self.assertRaises(Exception):
                ro.execute("INSERT INTO entries(source_agent, source_file, title, "
                           "fingerprint, raw_text, created_at) VALUES('x','y','t','f','b','c')")
        finally:
            ro.close()

    def test_tokenize_shapes(self):
        # ASCII words stay whole; CJK runs expand to 2-char bigrams
        self.assertEqual(_common.tokenize("OneDrive 原子写 v2.1"),
                         ["onedrive", "原子", "子写", "v2.1"])
        self.assertTrue(_common.has_cjk("记忆"))
        self.assertFalse(_common.has_cjk("memory"))


if __name__ == "__main__":
    unittest.main()
