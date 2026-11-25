import pytest

from c3.metrics import PrOutcome
from c3.pooled import Issue
from c3.stats import (
    cochran_q, pairwise_mcnemar, paired_hits, pool_hits,
    pr_cluster_permutation,
)


def outcome(pr_id, **ref_hits):
    return PrOutcome(pr_id, ref_hits, {}, 0, 0)


def test_cochran_identical_p_one():
    assert cochran_q({"a": [1, 0, 1], "b": [1, 0, 1]}) == (0.0, 1.0)


def test_cochran_detects_difference():
    statistic, p_value = cochran_q({"a": [1, 1, 1, 1], "b": [0, 0, 0, 0], "c": [1, 0, 1, 0]})
    assert statistic > 0
    assert p_value < 0.05


def test_mcnemar_discordant_counts():
    result, = pairwise_mcnemar({"a": [1] * 5 + [0], "b": [0] * 5 + [1]})
    assert (result.only_a, result.only_b) == (5, 1)
    assert result.odds_ratio == pytest.approx(5.5 / 1.5)


def test_holm_not_smaller_than_raw():
    results = pairwise_mcnemar({"a": [1, 1, 0, 0], "b": [0, 0, 1, 1], "c": [1, 0, 1, 0]})
    assert all(item.p_holm >= item.p_raw for item in results)


def test_paired_hits_common_prs_only_and_checks_ref_sets():
    hits = paired_hits({
        "a": [outcome("p1", r1=True, r2=False), outcome("p2", r3=True)],
        "b": [outcome("p1", r1=False, r2=True), outcome("p3", r4=False)],
    })
    assert hits == {"a": [1, 0], "b": [0, 1]}
    with pytest.raises(ValueError, match="reference IDs"):
        paired_hits({"a": [outcome("p1", r1=True)], "b": [outcome("p1", r2=False)]})


def test_pool_hits_includes_human():
    pool = [Issue("i1", "p1", ["r1"], {"human", "a"}), Issue("i2", "p1", [], {"b"})]
    assert pool_hits(pool, ["human", "a", "b"]) == {
        "human": [1, 0], "a": [1, 0], "b": [0, 1]
    }


def test_pr_cluster_permutation_flips_whole_pr_blocks():
    outcomes = {
        "a": [outcome("p1", a1=True, a2=True), outcome("p2", b1=False, b2=False)],
        "b": [outcome("p1", a1=False, a2=False), outcome("p2", b1=True, b2=True)],
    }
    result = pr_cluster_permutation(outcomes, "a", "b", n_perm=1000, seed=7)
    assert result.n_prs == 2
    assert result.difference == pytest.approx(0)
    assert result.p_value == pytest.approx(1)
