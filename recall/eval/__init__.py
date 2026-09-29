"""recall eval basis (T04).

This sub-package is the project's "single judge": a *frozen* golden set
(`golden_pairs.yaml`) plus a scorer (`recall_eval.py`) that turns "tests are
green" into a number the value gate can be judged against.

Design rules (see RECALL_FIRST_PLAN.md §4 / §12):
* ZERO third-party deps — the golden set is JSON formatted as YAML (JSON is a
  strict subset of YAML 1.2), so it stays human-readable without PyYAML.
* STRICTLY READ-ONLY w.r.t. the index: every DB access opens the file with the
  SQLite `mode=ro` URI, so nothing here can ever write `index.db`.
* The `answer_phrase` oracle lives ONLY in the golden file — it is never written
  to the index, the run logs, or the telemetry log.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
