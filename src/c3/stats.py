"""Paired tests for review hits, with PR-aware inference helpers."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
import random

import numpy as np
from statsmodels.stats.contingency_tables import cochrans_q as _cochrans_q, mcnemar
from statsmodels.stats.multitest import multipletests

from c3.metrics import PrOutcome
from c3.pooled import Issue


@dataclass(frozen=True)
class PairResult:
    a: str
    b: str
    only_a: int
    only_b: int
    p_raw: float
    p_holm: float
    odds_ratio: float


@dataclass(frozen=True)
class ClusterPermutationResult:
    a: str
    b: str
    difference: float
    p_value: float
    n_prs: int


def _validated_hits(hits: dict[str, list[int]]) -> list[str]:
    if len(hits) < 2 or any(not isinstance(name, str) or not name for name in hits):
        raise ValueError("at least two named parties are required")
    lengths = {len(values) for values in hits.values()}
    if len(lengths) != 1 or next(iter(lengths)) == 0:
        raise ValueError("hit vectors must have the same nonzero length")
    if any(value not in (0, 1, False, True) for values in hits.values() for value in values):
        raise ValueError("hits must be binary")
    return list(hits)


def cochran_q(hits: dict[str, list[int]]) -> tuple[float, float]:
    """Cochran Q over references; use PR-block inference when PRs have multiple refs."""
    parties = _validated_hits(hits)
    matrix = np.asarray([hits[name] for name in parties], dtype=int).T
    # The statistic is undefined when every row is unanimous across parties.
    if np.all(matrix == matrix[:, :1]):
        return 0.0, 1.0
    result = _cochrans_q(matrix)
    return float(result.statistic), float(result.pvalue)


def pairwise_mcnemar(hits: dict[str, list[int]]) -> list[PairResult]:
    """Exact reference-level McNemar tests; multi-reference PRs make these exploratory."""
    parties = _validated_hits(hits)
    raw_results = []
    for a, b in itertools.combinations(parties, 2):
        only_a = sum(bool(x) and not bool(y) for x, y in zip(hits[a], hits[b]))
        only_b = sum(not bool(x) and bool(y) for x, y in zip(hits[a], hits[b]))
        table = [[sum(bool(x) and bool(y) for x, y in zip(hits[a], hits[b])), only_a],
                 [only_b, sum(not bool(x) and not bool(y) for x, y in zip(hits[a], hits[b]))]]
        p_value = float(mcnemar(table, exact=True).pvalue) if only_a + only_b else 1.0
        raw_results.append((a, b, only_a, only_b, p_value,
                            (only_a + 0.5) / (only_b + 0.5)))
    adjusted = multipletests([row[4] for row in raw_results], method="holm")[1]
    return [PairResult(*row[:5], float(p_holm), row[5])
            for row, p_holm in zip(raw_results, adjusted)]


def _outcomes_by_pr(outcomes: list[PrOutcome], party: str) -> dict[str, PrOutcome]:
    by_pr = {item.pr_id: item for item in outcomes}
    if len(by_pr) != len(outcomes):
        raise ValueError(f"duplicate PR outcome for {party}")
    return by_pr


def paired_hits(outcomes_by_reviewer: dict[str, list[PrOutcome]]) -> dict[str, list[int]]:
    if len(outcomes_by_reviewer) < 2:
        raise ValueError("at least two reviewers are required")
    indexed = {party: _outcomes_by_pr(outcomes, party)
               for party, outcomes in outcomes_by_reviewer.items()}
    common_prs = set.intersection(*(set(items) for items in indexed.values()))
    ordered: list[tuple[str, str]] = []
    for pr_id in sorted(common_prs):
        reference_sets = [set(items[pr_id].ref_hits) for items in indexed.values()]
        if any(refs != reference_sets[0] for refs in reference_sets[1:]):
            raise ValueError(f"reference IDs differ for common PR {pr_id}")
        ordered.extend((pr_id, ref_id) for ref_id in sorted(reference_sets[0]))
    return {party: [int(items[pr_id].ref_hits[ref_id]) for pr_id, ref_id in ordered]
            for party, items in indexed.items()}


def pool_hits(pool: list[Issue], parties: list[str]) -> dict[str, list[int]]:
    if len(parties) < 2 or len(set(parties)) != len(parties):
        raise ValueError("parties must contain at least two unique names")
    return {party: [int(party in issue.found_by) for issue in pool] for party in parties}


def pr_cluster_permutation(outcomes_by_reviewer: dict[str, list[PrOutcome]], a: str, b: str,
                           n_perm: int = 10_000, seed: int = 20261121) -> ClusterPermutationResult:
    """Two-sided sign-flip test over PR-level recall differences (PR is the block)."""
    if a == b or a not in outcomes_by_reviewer or b not in outcomes_by_reviewer:
        raise ValueError("select two distinct reviewers present in the outcomes")
    if n_perm < 1:
        raise ValueError("n_perm must be positive")
    first, second = (_outcomes_by_pr(outcomes_by_reviewer[name], name) for name in (a, b))
    shared = sorted(set(first) & set(second))
    differences = []
    for pr_id in shared:
        refs_a, refs_b = first[pr_id].ref_hits, second[pr_id].ref_hits
        if set(refs_a) != set(refs_b):
            raise ValueError(f"reference IDs differ for common PR {pr_id}")
        if not refs_a:
            continue
        differences.append(sum(refs_a[key] for key in refs_a) / len(refs_a)
                           - sum(refs_b[key] for key in refs_b) / len(refs_b))
    if not differences:
        return ClusterPermutationResult(a, b, math.nan, math.nan, 0)
    observed = abs(sum(differences) / len(differences))
    if len(differences) <= 16:
        signs = itertools.product((-1, 1), repeat=len(differences))
        permuted = [abs(sum(x * sign for x, sign in zip(differences, row)) / len(differences))
                    for row in signs]
        p_value = sum(value >= observed - 1e-12 for value in permuted) / len(permuted)
    else:
        rng = random.Random(seed)
        exceed = sum(abs(sum(x * rng.choice((-1, 1)) for x in differences) / len(differences))
                     >= observed - 1e-12 for _ in range(n_perm))
        p_value = (exceed + 1) / (n_perm + 1)
    return ClusterPermutationResult(a, b, sum(differences) / len(differences), p_value, len(differences))
