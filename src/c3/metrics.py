"""Pure evaluation metrics for AI review outcomes."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from c3.judge import Judgment
from c3.models import PullRequest
from c3.review import ReviewResult


@dataclass(frozen=True)
class PrOutcome:
    pr_id: str
    ref_hits: dict[str, bool]
    ref_categories: dict[str, str]
    n_ai: int
    n_ai_matched: int


def build_outcome(pr: PullRequest, review: ReviewResult, judgment: Judgment,
                  origin: str | None = "human") -> PrOutcome:
    """Create a PR-level outcome from a successful review and its judgment."""
    if review.error is not None or review.parse_error:
        raise ValueError("cannot build an outcome from a failed review result")
    if pr.pr_id != review.pr_id or pr.pr_id != judgment.pr_id:
        raise ValueError("PR, review, and judgment IDs must match")
    if review.reviewer != judgment.reviewer or review.mode != judgment.mode or review.run_idx != judgment.run_idx:
        raise ValueError("review and judgment run metadata must match")
    all_ref_ids = [ref.ref_id for ref in pr.references]
    if len(all_ref_ids) != len(set(all_ref_ids)):
        raise ValueError("duplicate reference IDs within PR")
    refs = [ref for ref in pr.references
            if ref.valid is True and (origin is None or ref.origin == origin)]
    hits = judgment.matched_ref_ids
    ref_hits = {ref.ref_id: ref.ref_id in hits for ref in refs}
    ref_categories = {ref.ref_id: ref.category for ref in refs if ref.category is not None}
    matched_indices = {index for index in judgment.matched_ai_indices
                       if 0 <= index < len(review.comments)}
    return PrOutcome(pr.pr_id, ref_hits, ref_categories,
                     len(review.comments), len(matched_indices))


def recall(outcomes: Iterable[PrOutcome], exclude_categories=frozenset()) -> float:
    hits = total = 0
    for outcome in outcomes:
        for ref_id, was_hit in outcome.ref_hits.items():
            if outcome.ref_categories.get(ref_id) in exclude_categories:
                continue
            total += 1
            hits += bool(was_hit)
    return hits / total if total else math.nan


def mismatch_rate(outcomes: Iterable[PrOutcome]) -> float:
    total_ai = sum(outcome.n_ai for outcome in outcomes)
    unmatched = sum(outcome.n_ai - outcome.n_ai_matched for outcome in outcomes)
    return unmatched / total_ai if total_ai else math.nan


def adjusted_noise_rate(mismatch: float, true_noise_share: float) -> float:
    return mismatch * true_noise_share


def adjusted_precision(mismatch: float, true_noise_share: float) -> float:
    return 1 - adjusted_noise_rate(mismatch, true_noise_share)


def bootstrap_ci(outcomes: Sequence[PrOutcome], statistic: Callable,
                 n_boot: int = 10_000, seed: int = 20261121,
                 alpha: float = 0.05) -> tuple[float, float, float]:
    """Percentile CI from PR-level resampling; nan replicates are discarded."""
    estimate = float(statistic(outcomes))
    if not outcomes:
        return estimate, math.nan, math.nan
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    rng = random.Random(seed)
    samples: list[float] = []
    count = len(outcomes)
    for _ in range(n_boot):
        sample = [outcomes[rng.randrange(count)] for _ in range(count)]
        value = float(statistic(sample))
        if not math.isnan(value):
            samples.append(value)
    if not samples:
        return estimate, math.nan, math.nan
    low, high = np.quantile(samples, [alpha / 2, 1 - alpha / 2])
    return estimate, float(low), float(high)


def recall_by_category(outcomes: Iterable[PrOutcome]) -> dict[str, tuple[int, int]]:
    counts: dict[str, list[int]] = {}
    for outcome in outcomes:
        for ref_id, was_hit in outcome.ref_hits.items():
            category = outcome.ref_categories.get(ref_id)
            if category is None:
                continue
            counts.setdefault(category, [0, 0])
            counts[category][0] += bool(was_hit)
            counts[category][1] += 1
    return {category: (values[0], values[1]) for category, values in counts.items()}
