"""Characterization tests for ``evaluation.stats.show_results``.

``show_results`` had zero prior test coverage (unlike most of the rest of this repo).
These pin its current printed summary table -- per-domain rows, per-difficulty sub-rows,
and the final "Overall" row -- across a small synthetic dataset mixing two domains,
multiple difficulties, and one crashed result, ahead of extracting the repeated
``n_finished, n_crashed, lower, excluding, upper = _compute_metrics(entries); table_rows
.append([label, n_finished, n_crashed, lower, excluding, upper])`` pattern (duplicated at
the per-domain, per-difficulty, and overall call sites) into a shared ``_metrics_row(label,
entries) -> list`` helper.
"""

import json

from navi_bench.base import DatasetItem, FinalResult
from evaluation.stats import Crashed, show_results


def _item(task_id: str, domain: str, difficulty: str | None) -> DatasetItem:
    return DatasetItem(
        task_id=task_id,
        task_generation_config_json=json.dumps({"_target_": "x.y.z"}),
        env="real",
        domain=domain,
        l1_category="food",
        suggested_difficulty=difficulty,
    )


def _run_show_results(monkeypatch) -> list[str]:
    logged: list[str] = []
    monkeypatch.setattr("evaluation.stats.logger.info", logged.append)
    monkeypatch.setattr("evaluation.stats.logger.error", logged.append)

    dataset = [
        _item("t/opentable/0", "opentable", "easy"),
        _item("t/opentable/1", "opentable", "hard"),
        _item("t/resy/0", "resy", "easy"),
        _item("t/resy/1", "resy", None),
    ]
    results = [
        FinalResult(score=1.0),
        Crashed(score=0.0, exception="boom"),
        FinalResult(score=0.5),
        FinalResult(score=0.0),
    ]
    show_results(dataset, results)
    return logged


class TestShowResultsSummaryTable:
    def test_per_domain_and_overall_rows_pinned(self, monkeypatch):
        logged = _run_show_results(monkeypatch)
        table_lines = [line for line in logged if line.strip()]

        # Header + per-domain block (opentable, then resy, alphabetically) + Overall.
        assert "opentable              1            1           0.50             1.00           1.00" in table_lines
        assert "└─ easy                1            0           1.00             1.00           1.00" in table_lines
        assert "└─ hard                0            1           0.00              N/A           1.00" in table_lines
        assert "resy                   2            0           0.25             0.25           0.25" in table_lines
        assert "└─ easy                1            0           0.50             0.50           0.50" in table_lines
        assert "└─ unknown             1            0           0.00             0.00           0.00" in table_lines
        assert "Overall                3            1           0.38             0.50           0.62" in table_lines

    def test_per_task_detail_lines_pinned(self, monkeypatch):
        logged = _run_show_results(monkeypatch)
        assert "  [  0] t/opentable/0                                                | easy   | score = 1.00" in logged
        assert (
            "  [  1] t/opentable/1                                                | hard   | score = 0.00 (crashed)"
            in logged
        )
        assert "  [  2] t/resy/0                                                     | easy   | score = 0.50" in logged
        assert "  [  3] t/resy/1                                                     | unknown | score = 0.00" in logged
