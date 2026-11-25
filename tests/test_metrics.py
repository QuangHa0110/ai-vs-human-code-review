import math

import pytest

from c3.judge import Judgment, Match
from c3.metrics import (
    PrOutcome, adjusted_noise_rate, adjusted_precision, bootstrap_ci,
    build_outcome, mismatch_rate, recall, recall_by_category,
)
from c3.models import AiComment, PullRequest, Reference
from c3.review import ReviewResult


def pr():
    return PullRequest("x/y#1", "D2", "x/y", 1, "", "", "a", "b", "", [
        Reference("h1", "A.java", 1, "h1", "human", True, "logic_bug"),
        Reference("h2", "A.java", 2, "h2", "human", True, "style_naming_docs"),
        Reference("a1", "A.java", 3, "a1", "ai", True, "logic_bug"),
        Reference("bad", "A.java", 4, "bad", "human", False, "testing"),
    ])


def review():
    return ReviewResult("x/y#1", "gpt", "diff", 1,
                        [AiComment("A.java", 1, "one", "logic_bug"),
                         AiComment("A.java", 2, "two", "logic_bug"),
                         AiComment("A.java", 3, "three", "logic_bug")], {})


def test_build_outcome_filters_valid_human_refs_and_deduplicates_reference_hits():
    result = build_outcome(pr(), review(), Judgment("x/y#1", "gpt", "diff", 1, "j",
                                                    [Match("h1", 0), Match("h1", 1), Match("a1", 2)], 0))
    assert result.ref_hits == {"h1": True, "h2": False}
    assert result.ref_categories == {"h1": "logic_bug", "h2": "style_naming_docs"}
    assert (result.n_ai, result.n_ai_matched) == (3, 3)


def test_build_outcome_can_include_both_origins_and_rejects_failed_reviews():
    all_refs = build_outcome(pr(), review(), Judgment("x/y#1", "gpt", "diff", 1, "j", [], 0), origin=None)
    assert set(all_refs.ref_hits) == {"h1", "h2", "a1"}
    failed = review()
    failed.parse_error = True
    with pytest.raises(ValueError, match="failed"):
        build_outcome(pr(), failed, Judgment("x/y#1", "gpt", "diff", 1, "j", [], 0))


def test_recall_micro_aggregates_reference_counts():
    assert recall([PrOutcome("1", {"a": True, "b": False}, {}, 0, 0),
                   PrOutcome("2", {"c": True, "d": True, "e": True, "f": False}, {}, 0, 0)]) == 4 / 6


def test_recall_excludes_categories():
    outcome = PrOutcome("1", {"a": True, "b": False}, {"a": "logic_bug", "b": "style_naming_docs"}, 0, 0)
    assert recall([outcome], {"style_naming_docs"}) == 1


def test_empty_denominators_are_nan():
    assert math.isnan(recall([]))
    assert math.isnan(mismatch_rate([PrOutcome("1", {}, {}, 0, 0)]))


def test_mismatch_and_adjusted_precision():
    assert mismatch_rate([PrOutcome("1", {}, {}, 10, 4)]) == pytest.approx(0.6)
    assert adjusted_noise_rate(0.5, 0.6) == pytest.approx(0.3)
    assert adjusted_precision(0.5, 0.6) == pytest.approx(0.7)


def test_bootstrap_ci_is_seeded_and_resamples_prs():
    data = [PrOutcome("1", {"a": True}, {}, 0, 0), PrOutcome("2", {"b": False}, {}, 0, 0)]
    first = bootstrap_ci(data, recall, n_boot=200, seed=17)
    assert first == bootstrap_ci(data, recall, n_boot=200, seed=17)
    assert first[0] == 0.5
    assert first[1] <= first[0] <= first[2]


def test_bootstrap_ci_skips_nan_resamples_without_retrying():
    data = [PrOutcome("empty", {}, {}, 0, 0), PrOutcome("hit", {"a": True}, {}, 0, 0)]
    result = bootstrap_ci(data, recall, n_boot=100, seed=1)
    assert result[0] == 1.0
    assert result[1:] == (1.0, 1.0)


def test_recall_by_category_returns_hits_and_totals():
    result = recall_by_category([PrOutcome("1", {"a": True, "b": False},
                                            {"a": "logic_bug", "b": "logic_bug"}, 0, 0)])
    assert result == {"logic_bug": (1, 2)}


def test_build_outcome_rejects_duplicate_reference_ids_within_pr():
    duplicate_pr = pr()
    duplicate_pr.references.append(duplicate_pr.references[0])
    with pytest.raises(ValueError, match="duplicate reference IDs"):
        build_outcome(duplicate_pr, review(), Judgment("x/y#1", "gpt", "diff", 1, "j", [], 0))
