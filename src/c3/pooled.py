"""Issue-pool construction and coverage metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass

from c3.judge import Judgment
from c3.models import PullRequest


@dataclass
class Issue:
    issue_id: str
    pr_id: str
    ref_ids: list[str]
    found_by: set[str]


def build_pool(prs: list[PullRequest], judgments: dict[str, list[Judgment]],
               new_valid_clusters: dict[str, list[list[tuple[str, int]]]]) -> list[Issue]:
    pool: list[Issue] = []
    for pr in prs:
        all_ref_ids = [ref.ref_id for ref in pr.references]
        if len(all_ref_ids) != len(set(all_ref_ids)):
            raise ValueError("duplicate reference IDs within PR")
        refs = [ref for ref in pr.references if ref.valid is True]
        issue_by_ref: dict[str, Issue] = {}
        for ref in refs:
            issue = Issue(f"{pr.pr_id}|{ref.ref_id}", pr.pr_id, [ref.ref_id],
                          {"human"} if ref.origin == "human" else set())
            pool.append(issue)
            issue_by_ref[ref.ref_id] = issue

        matched_indices: set[tuple[str, int]] = set()
        for judgment in judgments.get(pr.pr_id, []):
            if judgment.pr_id != pr.pr_id:
                raise ValueError("judgment PR ID does not match its pool key")
            matched_indices.update((judgment.reviewer, index)
                                   for index in judgment.matched_ai_indices)
            for ref_id in judgment.matched_ref_ids:
                issue = issue_by_ref.get(ref_id)
                if issue is not None:
                    issue.found_by.add(judgment.reviewer)

        cluster_indices: set[tuple[str, int]] = set()
        for cluster_idx, cluster in enumerate(new_valid_clusters.get(pr.pr_id, [])):
            if not cluster:
                raise ValueError("new issue clusters must not be empty")
            pairs = set(cluster)
            if len(pairs) != len(cluster):
                raise ValueError("duplicate AI index within new issue cluster")
            if pairs & matched_indices:
                raise ValueError("new issue cluster overlaps an already validated reference")
            if pairs & cluster_indices:
                raise ValueError("duplicate AI index across new issue clusters")
            cluster_indices.update(pairs)
            reviewers = {reviewer for reviewer, _ in pairs}
            if not all(isinstance(reviewer, str) and reviewer for reviewer in reviewers):
                raise ValueError("reviewer names must be nonempty strings")
            issue_id = f"{pr.pr_id}|new:{cluster_idx}"
            pool.append(Issue(issue_id, pr.pr_id, [], reviewers))
    return pool


def pooled_recall(pool: list[Issue], who: str) -> float:
    if not pool:
        return math.nan
    return sum(who in issue.found_by for issue in pool) / len(pool)


def human_precision(prs: list[PullRequest]) -> float | None:
    """Human precision on D1's expert-adjudicated positive/negative candidates."""
    d1_prs = [pr for pr in prs if pr.dataset == "D1"]
    positive = sum(ref.origin == "human" and ref.valid is True
                   for pr in d1_prs for ref in pr.references)
    negative = sum(ref.origin == "human" and ref.valid is False
                   for pr in d1_prs for ref in pr.references)
    if negative == 0:
        return None
    return positive / (positive + negative)
