"""Produce CSV research tables from fingerprinted C3 artifacts."""

from __future__ import annotations

import csv
import itertools
import json
import math
from pathlib import Path
import random
import statistics

import numpy as np

from c3.judge import Judgment, Match
from c3.metrics import (PrOutcome, bootstrap_ci, build_outcome, mismatch_rate,
                        recall, recall_by_category)
from c3.models import AiComment, PullRequest
from c3.pooled import build_pool, human_precision, pooled_recall
from c3.stats import pairwise_mcnemar, pr_cluster_permutation
from c3.variability import run_variability

TABLES = {
    "rq1_rq2": "rq1_rq2.csv", "rq3_pairwise": "rq3_pairwise.csv",
    "rq3_variability": "rq3_variability.csv", "rq4_by_category": "rq4_by_category.csv",
    "rq5_pooled": "rq5_pooled.csv", "rq6_context": "rq6_context.csv",
    "agreement": "agreement.csv", "d3_calibration": "d3_calibration.csv",
}


def _digest(value: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _valid_review(record, pr: PullRequest, reviewer: str, mode: str, run: int, source: Path) -> bool:
    if (not isinstance(record, dict) or record.get("pr_id") != pr.pr_id
            or record.get("reviewer") != reviewer or record.get("mode") != mode
            or record.get("run_idx") != run or record.get("source") != str(source)):
        return False
    identity = record.get("fingerprint_input")
    if not isinstance(identity, dict):
        return False
    expected = {"dataset": pr.dataset, "pr_id": pr.pr_id, "repo": pr.repo,
                "base_sha": pr.base_sha, "head_sha": pr.review_sha, "diff": pr.diff,
                "mode": mode, "reviewer": reviewer, "run_idx": run}
    if any(identity.get(key) != value for key, value in expected.items()):
        return False
    if record.get("fingerprint") != _digest(identity):
        return False
    if record.get("error") is not None or record.get("parse_error") is True:
        return isinstance(record.get("error"), (str, type(None))) and record.get("parse_error") in (True, False)
    return (record.get("parse_error") is False and isinstance(record.get("meta"), dict)
            and isinstance(record.get("comments"), list)
            and all(isinstance(item, dict) and set(item) == {"path", "line", "comment", "category"}
                    and isinstance(item["path"], str) and isinstance(item["comment"], str)
                    and isinstance(item["category"], str) for item in record["comments"]))


def _valid_judgment(record, pr: PullRequest, review: dict, reviewer: str, mode: str, run: int,
                    judge: str, source: Path) -> Judgment | None:
    if not isinstance(record, dict):
        return None
    identity = record.get("fingerprint_input")
    if not isinstance(identity, dict) or record.get("fingerprint") != _digest(identity):
        return None
    expected = {"dataset": pr.dataset, "pr_id": pr.pr_id, "reviewer": reviewer,
                "mode": mode, "run_idx": run, "judge": judge,
                "review_fingerprint": review.get("fingerprint")}
    if any(identity.get(key) != value for key, value in expected.items()):
        return None
    if any(record.get(key) != value for key, value in {
        "pr_id": pr.pr_id, "reviewer": reviewer, "mode": mode, "run_idx": run,
        "judge": judge, "review_fingerprint": review.get("fingerprint"), "source": str(source)
    }.items()):
        return None
    comments = review.get("comments", [])
    valid_refs = {ref.ref_id for ref in pr.references if ref.valid is True}
    matches = record.get("matches")
    if not isinstance(matches, list):
        return None
    seen = set()
    parsed = []
    for item in matches:
        if (not isinstance(item, dict) or set(item) != {"ref_id", "ai_index"}
                or item["ref_id"] not in valid_refs or isinstance(item["ai_index"], bool)
                or not isinstance(item["ai_index"], int) or not 0 <= item["ai_index"] < len(comments)
                or (item["ref_id"], item["ai_index"]) in seen):
            return None
        seen.add((item["ref_id"], item["ai_index"]))
        parsed.append(Match(item["ref_id"], item["ai_index"]))
    invalid = record.get("invalid")
    if isinstance(invalid, bool) or not isinstance(invalid, int) or invalid < 0:
        return None
    return Judgment(pr.pr_id, reviewer, mode, run, judge, parsed, invalid)


def _csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if value is None or (isinstance(value, float) and math.isnan(value)) else value
                             for key, value in row.items()})


def _stat_row(dataset, reviewer, mode, metric, value=None, low=None, high=None,
              status="ok", **extra):
    return {"dataset": dataset, "reviewer": reviewer, "mode": mode, "metric": metric,
            "value": value, "ci_low": low, "ci_high": high, "status": status, **extra}


def _load_labels(labels_dir: Path | None) -> dict:
    if labels_dir is None:
        return {}
    path = labels_dir / "adjudicated_labels.json"
    payload = _read(path)
    if (not isinstance(payload, dict) or payload.get("schema") != "c3-adjudication-v1"
            or not isinstance(payload.get("items", []), list)):
        return {}
    return payload


def _label_metrics(labels: dict, group: tuple[str, str, str], reviews: dict,
                   outcomes: list[PrOutcome], entries: list) -> dict:
    """Use only complete per-comment adjudications tied to saved review fingerprints."""
    if not labels:
        return {}
    dataset, reviewer, mode = group
    relevant_reviews = {identity: record for identity, record in reviews.items()
                        if identity[:3] == group}
    expected = {}
    for identity, record in relevant_reviews.items():
        for index, _ in enumerate(record.get("comments", [])):
            expected[(*identity, index)] = record.get("fingerprint")
    supplied = {}
    for item in labels.get("items", []):
        if not isinstance(item, dict):
            continue
        if (item.get("dataset"), item.get("reviewer"), item.get("mode")) != group:
            continue
        identity = (dataset, reviewer, mode, item.get("run_idx"), item.get("pr_id"), item.get("ai_index"))
        if (identity not in expected or item.get("review_fingerprint") != expected[identity]
                or item.get("label") not in {"valid", "noise"}
                or not isinstance(item.get("missed_by_human"), bool) or identity in supplied):
            return {}
        supplied[identity] = item
    if not expected or set(supplied) != set(expected):
        return {}
    noise_count = sum(item["label"] == "noise" for item in supplied.values())
    valid = [item for item in supplied.values() if item["label"] == "valid"]
    mismatch = mismatch_rate(outcomes)
    if math.isnan(mismatch):
        return {}
    return {"true_noise_share": noise_count / len(supplied),
            "missed_by_human_share": sum(item["missed_by_human"] for item in valid) / len(valid) if valid else None,
            "adjusted_precision": 1 - mismatch * noise_count / len(supplied)}


def _d1_new_issue_clusters(labels: dict, reviews: dict, judgments: dict) -> tuple[dict, bool]:
    """Return run1/diff clusters only from complete, fingerprint-bound D1 labels."""
    if not labels:
        return {}, False
    relevant = {identity: record for identity, record in reviews.items()
                if identity[0] == "D1" and identity[2] == "diff" and identity[3] == 1}
    expected = {}
    for identity, review in relevant.items():
        if identity not in judgments:
            return {}, False
        for ai_index in range(len(review.get("comments", []))):
            expected[(*identity, ai_index)] = review["fingerprint"]
    supplied = {}
    for item in labels.get("items", []):
        if not isinstance(item, dict) or (item.get("dataset"), item.get("mode"), item.get("run_idx")) != ("D1", "diff", 1):
            continue
        identity = ("D1", item.get("reviewer"), "diff", 1, item.get("pr_id"), item.get("ai_index"))
        if (identity not in expected or item.get("review_fingerprint") != expected[identity]
                or item.get("label") not in {"valid", "noise"}
                or not isinstance(item.get("missed_by_human"), bool) or identity in supplied):
            return {}, False
        supplied[identity] = item
    if not expected or set(supplied) != set(expected):
        return {}, False
    grouped: dict[str, dict[str, list[tuple[str, int]]]] = {}
    seen_pairs = set()
    for identity, item in supplied.items():
        dataset, reviewer, mode, run, pr_id, ai_index = identity
        issue_id = item.get("issue_id")
        judgment = judgments[(dataset, reviewer, mode, run, pr_id)]
        matched = ai_index in judgment.matched_ai_indices
        if item["label"] == "valid" and not matched and (
                not isinstance(issue_id, str) or not issue_id.strip()):
            return {}, False
        if issue_id is None:
            continue
        if (item["label"] != "valid" or matched or not isinstance(issue_id, str)
                or not issue_id.strip() or (pr_id, reviewer, ai_index) in seen_pairs):
            return {}, False
        seen_pairs.add((pr_id, reviewer, ai_index))
        grouped.setdefault(pr_id, {}).setdefault(issue_id.strip(), []).append((reviewer, ai_index))
    # A label issue ID must identify one cluster per PR and no AI comment can appear twice.
    clusters = {pr_id: list(issue_map.values()) for pr_id, issue_map in grouped.items()}
    return clusters, True


def _paired_noise_difference(labels: dict, group: tuple[str, str], pairs: list[tuple[str, int]],
                             reviews: dict, seed: int) -> tuple[float | None, float | None, float | None, int, str]:
    """Mean same-PR agent-minus-diff noise delta with PR-cluster bootstrap CI."""
    if not labels:
        return None, None, None, 0, "labels_not_supplied"
    dataset, reviewer = group
    deltas_by_pr = {}
    excluded_zero = 0
    for pr_id, run in pairs:
        rates = {}
        for mode in ("diff", "agent"):
            identity = (dataset, reviewer, mode, run, pr_id)
            review = reviews.get(identity)
            if review is None:
                return None, None, None, 0, "missing_fingerprint_bound_review"
            items = {}
            for item in labels.get("items", []):
                if not isinstance(item, dict) or (item.get("dataset"), item.get("reviewer"), item.get("mode"),
                                                  item.get("run_idx"), item.get("pr_id")) != identity:
                    continue
                index = item.get("ai_index")
                if (isinstance(index, bool) or not isinstance(index, int)
                        or not 0 <= index < len(review.get("comments", []))
                        or item.get("review_fingerprint") != review.get("fingerprint")
                        or item.get("label") not in {"valid", "noise"}
                        or not isinstance(item.get("missed_by_human"), bool) or index in items):
                    return None, None, None, 0, "labels_incomplete_or_unusable"
                items[index] = item
            if len(items) != len(review.get("comments", [])):
                return None, None, None, 0, "labels_incomplete_or_unusable"
            rates[mode] = (sum(item["label"] == "noise" for item in items.values()) / len(items)
                           if items else None)
        if rates["diff"] is None or rates["agent"] is None:
            excluded_zero += 1
            continue
        deltas_by_pr.setdefault(pr_id, []).append(rates["agent"] - rates["diff"])
    pr_deltas = [statistics.mean(deltas) for deltas in deltas_by_pr.values()]
    n_pairs = sum(map(len, deltas_by_pr.values()))
    if not pr_deltas:
        return None, None, None, 0, "undefined_comment_denominator"
    estimate = statistics.mean(pr_deltas)
    rng = random.Random(seed)
    samples = [statistics.mean(pr_deltas[rng.randrange(len(pr_deltas))] for _ in pr_deltas)
               for _ in range(2000)]
    low, high = np.quantile(samples, [0.025, 0.975])
    status = "ok_with_zero_comment_pairs_excluded" if excluded_zero else "ok"
    return estimate, float(low), float(high), n_pairs, status


def analyze(settings, labels_dir: Path | None = None) -> dict[str, Path]:
    """Analyze only artifacts whose PR, source, and review fingerprints agree."""
    from c3.cli import _load_review_prs

    labels = _load_labels(labels_dir)
    rq5_rows = []
    datasets: dict[str, tuple[Path, list[PullRequest]]] = {}
    for dataset in ("D1", "D2", "D3"):
        raw = settings.data_dir / "prs" / dataset
        classified = settings.data_dir / "classified" / dataset
        if raw.is_dir() or classified.is_dir():
            datasets[dataset] = _load_review_prs(settings, dataset)

    # key -> (outcome, judgment, PR, review record, run index)
    outcomes: dict[tuple[str, str, str], list[tuple[PrOutcome, Judgment, PullRequest, dict, int]]] = {}
    counts: dict[tuple[str, str, str], dict[str, int]] = {}
    valid_reviews = {}
    valid_judgments = {}
    by_id = {dataset: {pr.pr_id: pr for pr in prs} for dataset, (_, prs) in datasets.items()}
    for dataset, (source, prs) in datasets.items():
        review_root = settings.data_dir / "reviews" / dataset
        for review_dir in sorted(p for p in review_root.glob("*/*") if p.is_dir()) if review_root.exists() else []:
            reviewer, mode = review_dir.parent.name, review_dir.name
            key = (dataset, reviewer, mode)
            run_dirs = sorted(p for p in review_dir.glob("run*") if p.is_dir())
            bucket = outcomes.setdefault(key, [])
            counts[key] = {"n_run_error": 0, "n_parse_error": 0, "n_missing_review": 0,
                           "n_valid_judgment": 0}
            for run_dir in run_dirs:
                try:
                    run = int(run_dir.name.removeprefix("run"))
                except ValueError:
                    continue
                for pr in prs:
                    path = run_dir / f"{pr.pr_id.replace('/', '__')}.json"
                    review = _read(path)
                    if not _valid_review(review, pr, reviewer, mode, run, source):
                        counts[key]["n_missing_review"] += 1
                        continue
                    if review.get("error") is not None:
                        counts[key]["n_run_error"] += 1
                        continue
                    if review.get("parse_error"):
                        counts[key]["n_parse_error"] += 1
                        continue
                    valid_reviews[(dataset, reviewer, mode, run, pr.pr_id)] = review
                    judge_root = settings.data_dir / "judgments" / dataset
                    if not judge_root.exists():
                        continue
                    for judge_dir in judge_root.iterdir():
                        jpath = judge_dir / reviewer / mode / run_dir.name / path.name
                        judgment = _valid_judgment(_read(jpath), pr, review, reviewer, mode, run,
                                                   judge_dir.name, source)
                        if judgment is None:
                            continue
                        comments = [AiComment(**item) for item in review["comments"]]
                        from c3.review import ReviewResult
                        result = ReviewResult(pr.pr_id, reviewer, mode, run, comments, review["meta"])
                        bucket.append((build_outcome(pr, result, judgment), judgment, pr, review, run))
                        valid_judgments.setdefault((dataset, reviewer, mode, run, pr.pr_id), judgment)
                        counts[key]["n_valid_judgment"] += 1

    rq_rows = []
    category_rows, pair_rows, variability_rows, context_rows = [], [], [], []
    for key, entries in outcomes.items():
        dataset, reviewer, mode = key
        successful = _majority_outcomes([entry[0] for entry in entries])
        count = counts[key]
        for metric, statistic in (("recall_all", lambda xs: recall(xs)),
                                  ("recall_no_style", lambda xs: recall(xs, {"style_naming_docs"})),
                                  ("mismatch_rate", mismatch_rate)):
            if successful:
                value, low, high = bootstrap_ci(successful, statistic, n_boot=2000, seed=settings.seed)
                status = "ok" if not math.isnan(value) else "undefined_denominator"
            else:
                value = low = high = None
                status = "no_valid_judgments"
            rq_rows.append(_stat_row(dataset, reviewer, mode, metric, value, low, high, status))
        for metric, value in count.items():
            rq_rows.append(_stat_row(dataset, reviewer, mode, metric, value, status="ok"))
        label_metrics = _label_metrics(labels, key, valid_reviews, successful, entries)
        for metric in ("true_noise_share", "missed_by_human_share", "adjusted_precision"):
            value = label_metrics.get(metric)
            status = "ok" if value is not None else ("labels_not_supplied" if not labels else "labels_incomplete_or_unusable")
            rq_rows.append(_stat_row(dataset, reviewer, mode, metric, value, status=status))
        for category, (hit, total) in recall_by_category(successful).items():
            category_rows.append({"dataset": dataset, "reviewer": reviewer, "mode": mode,
                                  "category": category, "hits": hit, "references": total,
                                  "recall": hit / total if total else None})
        # Variability is reported only for complete, matching PR sets in each run.
        runs = {}
        for outcome, _, _, _, run in entries:
            runs.setdefault(run, []).append(outcome)
        if len(runs) >= 2 and len({tuple(sorted(x.pr_id for x in values)) for values in runs.values()}) == 1:
            variability = run_variability(runs)
            variability_rows.append({"dataset": dataset, "reviewer": reviewer, "mode": mode,
                                     "runs": len(runs), "recall_sd": variability["recall_sd"],
                                     "hit_jaccard_mean": variability["hit_jaccard_mean"], "status": "ok"})
        else:
            variability_rows.append({"dataset": dataset, "reviewer": reviewer, "mode": mode,
                                     "runs": len(runs), "status": "incomplete_or_single_run"})

    # RQ3 pairwise PR-block permutation is primary; reference McNemar is exploratory.
    for dataset in datasets:
        for mode in sorted({key[2] for key in outcomes if key[0] == dataset}):
            grouped = {}
            for (ds, reviewer, md), entries in outcomes.items():
                if ds != dataset or md != mode:
                    continue
                grouped[reviewer] = _majority_outcomes([item[0] for item in entries])
            for a, b in itertools.combinations(sorted(grouped), 2):
                result = pr_cluster_permutation(grouped, a, b)
                pair_rows.append({"dataset": dataset, "mode": mode, "reviewer_a": a, "reviewer_b": b,
                                  "recall_difference": result.difference, "p_value": result.p_value,
                                  "n_paired_prs": result.n_prs, "primary": "pr_cluster_permutation",
                                  "status": "ok" if result.n_prs else "no_paired_prs"})
                try:
                    hits = _paired_reference_hits(grouped)
                    mc = next(item for item in pairwise_mcnemar(hits) if (item.a, item.b) == (a, b))
                    pair_rows.append({"dataset": dataset, "mode": mode, "reviewer_a": a, "reviewer_b": b,
                                      "only_a": mc.only_a, "only_b": mc.only_b, "p_value": mc.p_raw,
                                      "p_holm": mc.p_holm, "odds_ratio": mc.odds_ratio,
                                      "primary": "exploratory_reference_mcnemar", "status": "ok"})
                except (ValueError, StopIteration):
                    pass

    # RQ5 pools expert references as the baseline. New issues require adjudication labels.
    if "D1" in datasets:
        _, prs = datasets["D1"]
        human_p = human_precision(prs)
        new_clusters, labels_complete = _d1_new_issue_clusters(labels, valid_reviews, valid_judgments)
        for mode in sorted({key[2] for key in outcomes if key[0] == "D1"} or {"all"}):
            judgments_by_pr = {}
            for (ds, reviewer, md), entries in outcomes.items():
                if ds == "D1" and md == mode:
                    for _, judgment, pr, _, _ in entries:
                        judgments_by_pr.setdefault(pr.pr_id, []).append(judgment)
            mode_clusters = new_clusters if mode == "diff" and labels_complete else {}
            pool = build_pool(prs, judgments_by_pr, mode_clusters)
            for who in ["human"] + sorted({name for ds, name, md in outcomes if ds == "D1" and md == mode}):
                rq5_status = ("adjudicated" if mode == "diff" and labels_complete else
                              "baseline_only_labels_not_supplied" if not labels else
                              "baseline_only_labels_incomplete_or_unusable")
                rq5row = {"dataset": "D1", "mode": mode, "party": who,
                          "pooled_recall": pooled_recall(pool, who), "n_issues": len(pool),
                          "human_precision": human_p if who == "human" else None,
                          "status": "ok" if pool else "no_pool_issues", "pool_status": rq5_status}
                rq5_rows.append(rq5row)

    for dataset in datasets:
        reviewers = sorted({key[1] for key in outcomes if key[0] == dataset})
        for reviewer in reviewers:
            diff = outcomes.get((dataset, reviewer, "diff"), [])
            agent = outcomes.get((dataset, reviewer, "agent"), [])
            by_diff = {(x[0].pr_id, x[4]): x[0] for x in diff}
            by_agent = {(x[0].pr_id, x[4]): x[0] for x in agent}
            common = sorted(set(by_diff) & set(by_agent))
            if not common:
                context_rows.append({"dataset": dataset, "reviewer": reviewer, "n_paired_pr_runs": 0,
                                     "mismatch_status": "missing_agent_pair",
                                     "noise_status": "missing_agent_pair",
                                     "status": "missing_agent_pair" if not agent else "no_common_pr_runs"})
                continue
            recall_diffs = [recall([by_agent[item]]) - recall([by_diff[item]]) for item in common]
            recall_defined = all(not math.isnan(value) for value in recall_diffs)
            n_ai_diffs = [by_agent[item].n_ai - by_diff[item].n_ai for item in common]
            mismatch_diffs = []
            mismatch_defined = True
            for item in common:
                old, new = by_diff[item], by_agent[item]
                if old.n_ai == 0 or new.n_ai == 0:
                    mismatch_defined = False
                    break
                mismatch_diffs.append((new.n_ai - new.n_ai_matched) / new.n_ai
                                      - (old.n_ai - old.n_ai_matched) / old.n_ai)
            noise_difference, noise_low, noise_high, noise_pairs, noise_status = _paired_noise_difference(
                labels, (dataset, reviewer), common, valid_reviews, settings.seed)
            context_rows.append({"dataset": dataset, "reviewer": reviewer, "n_paired_pr_runs": len(common),
                                 "recall_agent_minus_diff": statistics.mean(recall_diffs) if recall_defined else None,
                                 "n_ai_agent_minus_diff": statistics.mean(n_ai_diffs),
                                 "mismatch_agent_minus_diff": statistics.mean(mismatch_diffs) if mismatch_defined else None,
                                 "mismatch_status": "ok" if mismatch_defined else "undefined_denominator",
                                 "true_noise_share_agent_minus_diff": noise_difference,
                                 "noise_delta_ci_low": noise_low, "noise_delta_ci_high": noise_high,
                                 "n_noise_pairs": noise_pairs,
                                 "noise_status": noise_status,
                                 "status": "ok" if recall_defined else "undefined_recall_denominator"})
            diff_reviews = {(identity[4], identity[3]) for identity in valid_reviews
                            if identity[0] == dataset and identity[1] == reviewer and identity[2] == "diff"}
            agent_reviews = {(identity[4], identity[3]) for identity in valid_reviews
                             if identity[0] == dataset and identity[1] == reviewer and identity[2] == "agent"}
            for pr_id, run in sorted(diff_reviews - set(by_agent)):
                context_rows.append({"dataset": dataset, "reviewer": reviewer, "pr_id": pr_id,
                                     "run_idx": run, "n_paired_pr_runs": 0,
                                     "status": "missing_agent_judgment" if (pr_id, run) in agent_reviews
                                     else "missing_agent_pair"})
            for pr_id, run in sorted(agent_reviews - set(by_diff)):
                context_rows.append({"dataset": dataset, "reviewer": reviewer, "pr_id": pr_id,
                                     "run_idx": run, "n_paired_pr_runs": 0,
                                     "status": "missing_diff_judgment" if (pr_id, run) in diff_reviews
                                     else "missing_diff_pair"})

    rq1path = settings.results_dir / TABLES["rq1_rq2"]
    _csv(rq1path, rq_rows, ["dataset", "reviewer", "mode", "metric", "value", "ci_low", "ci_high", "status"])
    output_paths = {name: settings.results_dir / filename for name, filename in TABLES.items()}
    output_paths["rq1_rq2"] = rq1path
    _csv(output_paths["rq3_pairwise"], pair_rows, ["dataset", "mode", "reviewer_a", "reviewer_b", "recall_difference", "only_a", "only_b", "p_value", "p_holm", "odds_ratio", "n_paired_prs", "primary", "status"])
    _csv(output_paths["rq3_variability"], variability_rows, ["dataset", "reviewer", "mode", "runs", "recall_sd", "hit_jaccard_mean", "status"])
    _csv(output_paths["rq4_by_category"], category_rows, ["dataset", "reviewer", "mode", "category", "hits", "references", "recall"])
    _csv(output_paths["rq5_pooled"], rq5_rows, ["dataset", "mode", "party", "pooled_recall", "n_issues", "human_precision", "status", "pool_status"])
    _csv(output_paths["rq6_context"], context_rows, ["dataset", "reviewer", "n_paired_pr_runs",
         "recall_agent_minus_diff", "n_ai_agent_minus_diff", "mismatch_agent_minus_diff",
         "mismatch_status", "true_noise_share_agent_minus_diff", "noise_delta_ci_low",
         "noise_delta_ci_high", "n_noise_pairs", "noise_status", "pr_id", "run_idx", "status"])
    agreement = labels.get("agreement", []) if labels else []
    _csv(output_paths["agreement"], agreement if agreement else [{"status": "labels_not_supplied" if not labels else "labels_unusable"}], ["dataset", "reviewer", "mode", "metric", "value", "meets_threshold", "status"])
    _write_calibration(output_paths["d3_calibration"], labels, outcomes)
    plot = settings.results_dir / "rq1_recall.png"
    if rq_rows and any(row["metric"] == "recall_all" and row["value"] is not None for row in rq_rows):
        _plot_recall(plot, rq_rows)
        output_paths["plot"] = plot
    return output_paths


def _majority_outcomes(outcomes: list[PrOutcome]) -> list[PrOutcome]:
    grouped = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.pr_id, []).append(outcome)
    result = []
    for pr_id, values in grouped.items():
        refs = set().union(*(set(item.ref_hits) for item in values))
        hits = {ref: sum(ref in item.ref_hits and item.ref_hits[ref] for item in values) * 2 >=
                sum(ref in item.ref_hits for item in values) for ref in refs}
        cats = {ref: category for item in values for ref, category in item.ref_categories.items()}
        result.append(PrOutcome(pr_id, hits, cats, round(statistics.mean(x.n_ai for x in values)),
                                round(statistics.mean(x.n_ai_matched for x in values))))
    return result


def _paired_reference_hits(outcomes):
    from c3.stats import paired_hits
    return paired_hits(outcomes)


def _write_calibration(path: Path, labels: dict, outcomes: dict) -> None:
    config = labels.get("d3_benchmark_ci") if labels else None
    rows = []
    if isinstance(config, dict):
        for reviewer, interval in config.items():
            if not isinstance(interval, dict):
                continue
            low, high = interval.get("recall_low"), interval.get("recall_high")
            if not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in (low, high)) or low > high:
                continue
            values = [x[0] for key, entries in outcomes.items() if key[0] == "D3" and key[1] == reviewer for x in entries]
            estimate = recall(values) if values else math.nan
            rows.append({"dataset": "D3", "reviewer": reviewer, "recall": estimate,
                         "ci_low": low, "ci_high": high,
                         "within_ci": low <= estimate <= high if not math.isnan(estimate) else None,
                         "status": "ok" if not math.isnan(estimate) else "no_valid_reviews"})
    if not rows:
        rows = [{"dataset": "D3", "status": "benchmark_ci_not_supplied"}]
    _csv(path, rows, ["dataset", "reviewer", "recall", "ci_low", "ci_high", "within_ci", "status"])


def _plot_recall(path: Path, rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = [row for row in rows if row["metric"] == "recall_all" and row["value"] is not None]
    fig, axis = plt.subplots(figsize=(8, 4))
    labels = [f"{row['dataset']} {row['reviewer']} {row['mode']}" for row in data]
    axis.bar(labels, [row["value"] for row in data])
    axis.set_ylabel("Recall")
    axis.set_ylim(0, 1)
    axis.tick_params(axis="x", labelrotation=45)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


