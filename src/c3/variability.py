"""Run-to-run recall and reference-hit consistency."""

from __future__ import annotations

import itertools
import math
import statistics

from c3.metrics import PrOutcome, recall


def run_variability(outcomes_by_run: dict[int, list[PrOutcome]]) -> dict:
    if not outcomes_by_run:
        raise ValueError("at least one run is required")
    run_ids = sorted(outcomes_by_run)
    ids_by_run = {run: {outcome.pr_id for outcome in outcomes_by_run[run]} for run in run_ids}
    expected = ids_by_run[run_ids[0]]
    if any(ids != expected for ids in ids_by_run.values()):
        raise ValueError("all runs must contain the same PRs")
    by_pr = {run: {outcome.pr_id: outcome for outcome in outcomes_by_run[run]}
             for run in run_ids}
    if any(len(by_pr[run]) != len(outcomes_by_run[run]) for run in run_ids):
        raise ValueError("each run must contain each PR once")

    recalls = {run: recall(outcomes_by_run[run]) for run in run_ids}
    recall_values = list(recalls.values())
    recall_sd = statistics.pstdev(recall_values) if len(recall_values) > 1 else 0.0
    jaccards = []
    for pr_id in sorted(expected):
        for left_run, right_run in itertools.combinations(run_ids, 2):
            left = {ref for ref, hit in by_pr[left_run][pr_id].ref_hits.items() if hit}
            right = {ref for ref, hit in by_pr[right_run][pr_id].ref_hits.items() if hit}
            union = left | right
            jaccards.append(len(left & right) / len(union) if union else 1.0)
    return {"recall_by_run": recalls,
            "recall_sd": recall_sd,
            "hit_jaccard_mean": statistics.mean(jaccards) if jaccards else 1.0}
