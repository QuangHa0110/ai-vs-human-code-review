import pytest

from c3.metrics import PrOutcome
from c3.variability import run_variability


def test_identical_runs_have_zero_recall_sd_and_jaccard_one():
    a = PrOutcome("1", {"r1": True, "r2": False}, {}, 0, 0)
    b = PrOutcome("2", {"r3": True}, {}, 0, 0)
    result = run_variability({1: [a, b], 2: [a, b]})
    assert result["recall_sd"] == 0
    assert result["hit_jaccard_mean"] == 1


def test_variability_uses_each_pr_pair_and_empty_sets_jaccard_one():
    result = run_variability({1: [PrOutcome("1", {"a": True}, {}, 0, 0),
                                 PrOutcome("2", {"b": False}, {}, 0, 0)],
                              2: [PrOutcome("1", {"a": False}, {}, 0, 0),
                                  PrOutcome("2", {"b": False}, {}, 0, 0)]})
    assert result["hit_jaccard_mean"] == pytest.approx(0.5)


def test_variability_rejects_missing_pr_alignment():
    with pytest.raises(ValueError, match="same PRs"):
        run_variability({1: [PrOutcome("1", {"a": True}, {}, 0, 0)],
                         2: [PrOutcome("2", {"a": True}, {}, 0, 0)]})


def test_variability_reports_recall_by_run_and_single_run_sd_zero():
    result = run_variability({1: [PrOutcome("1", {"a": True, "b": False}, {}, 0, 0)]})
    assert result["recall_by_run"] == {1: 0.5}
    assert result["recall_sd"] == 0
    assert result["hit_jaccard_mean"] == 1
