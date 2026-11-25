import csv
import hashlib
import json
from pathlib import Path

from c3.config import Settings
from c3 import cli
from c3.models import AiComment, PullRequest, Reference, save_json
from c3.report import analyze


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _pr(dataset="D2", number=1):
    repo = "owner/repo"
    return PullRequest(
        f"{repo}#{number}", dataset, repo, number, "title", "body", "base", f"head{number}",
        "diff", [Reference("h1", "A.java", 1, "human bug", "human", True, "logic_bug")],
    )


def _write_run(settings, pr, reviewer, mode="diff", run=1, *, error=None, hit=True, n_comments=1):
    source = settings.data_dir / ("classified/D2" if pr.dataset == "D2" else f"prs/{pr.dataset}")
    source.mkdir(parents=True, exist_ok=True)
    save_json(pr, source / f"{pr.pr_id.replace('/', '__')}.json")
    ids = [f"{pr.repo}#{path.name.split('#')[-1].removesuffix('.json')}" for path in source.glob("*.json") if path.name != "_meta.json"]
    if pr.dataset == "D2":
        raw = settings.data_dir / "prs/D2"
        raw.mkdir(parents=True, exist_ok=True)
        save_json(pr, raw / f"{pr.pr_id.replace('/', '__')}.json")
        save_json({"dataset": "D2", "selected_pr_ids": ids}, raw / "_meta.json")
        save_json({"dataset": "D2", "retained_pr_ids": ids, "retained_pr_count": len(ids),
                   "input_pr_count": len(ids), "error_count": 0}, source / "_meta.json")
    else:
        save_json({"dataset": pr.dataset, "selected_pr_ids": ids,
                   "evaluation": {"prs": len(ids)}}, source / "_meta.json")
    fingerprint_input = {
        "dataset": pr.dataset, "pr_id": pr.pr_id, "repo": pr.repo,
        "base_sha": pr.base_sha, "head_sha": pr.review_sha, "diff": pr.diff,
        "mode": mode, "reviewer": reviewer, "run_idx": run,
    }
    review = {
        "pr_id": pr.pr_id, "reviewer": reviewer, "mode": mode, "run_idx": run,
        "comments": [] if error else [AiComment("A.java", 1, f"found bug {i}", "logic_bug").__dict__
                                       for i in range(n_comments)],
        "meta": {}, "error": error, "parse_error": False,
        "fingerprint_input": fingerprint_input, "fingerprint": _digest(fingerprint_input),
        "source": str(source),
    }
    review_path = settings.data_dir / "reviews" / pr.dataset / reviewer / mode / f"run{run}" / f"{pr.pr_id.replace('/', '__')}.json"
    save_json(review, review_path)
    judgment_dir = settings.data_dir / "judgments" / pr.dataset / "judge" / reviewer / mode / f"run{run}"
    save_json({"dataset": pr.dataset, "judge": "judge", "reviewer": reviewer, "mode": mode,
               "run_idx": run, "selected_pr_ids": [pr.pr_id], "source": str(source)},
              judgment_dir / "_meta.json")
    if not error:
        judgment_identity = {"dataset": pr.dataset, "pr_id": pr.pr_id, "reviewer": reviewer,
                             "mode": mode, "run_idx": run, "judge": "judge", "review_fingerprint": review["fingerprint"]}
        record = {**judgment_identity, "matches": [{"ref_id": "h1", "ai_index": 0}] if hit else [],
                  "invalid": 0, "review_fingerprint": review["fingerprint"], "source": str(source),
                  "fingerprint_input": judgment_identity, "fingerprint": _digest(judgment_identity)}
        save_json(record, judgment_dir / f"{pr.pr_id.replace('/', '__')}.json")


def _rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_analyze_writes_all_tables_and_marks_unlabeled_metrics_unavailable(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr()
    _write_run(settings, pr, "claude")

    outputs = analyze(settings)

    expected = {"rq1_rq2.csv", "rq3_pairwise.csv", "rq3_variability.csv", "rq4_by_category.csv",
                "rq5_pooled.csv", "rq6_context.csv", "agreement.csv", "d3_calibration.csv"}
    assert expected <= {path.name for path in outputs.values()}
    rows = _rows(outputs["rq1_rq2"])
    assert any(row["metric"] == "recall_all" and row["value"] == "1.0" for row in rows)
    assert all(row["value"] == "" for row in rows
               if row["metric"] in {"true_noise_share", "missed_by_human_share", "adjusted_precision"})
    assert any(row["metric"] == "true_noise_share" and row["status"] == "labels_not_supplied" and row["value"] == ""
               for row in rows)
    assert all(path.exists() for path in outputs.values())


def test_analyze_rejects_judgment_that_does_not_match_saved_review(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr()
    _write_run(settings, pr, "claude")
    review_path = settings.data_dir / "reviews" / "D2" / "claude" / "diff" / "run1" / "owner__repo#1.json"
    review = json.loads(review_path.read_text())
    review["fingerprint"] = "wrong"
    review_path.write_text(json.dumps(review))

    outputs = analyze(settings)

    rows = _rows(outputs["rq1_rq2"])
    assert not any(row["metric"] == "recall_all" and row["value"] for row in rows)
    assert any(row["metric"] == "recall_all" and row["status"] == "no_valid_judgments" for row in rows)


def test_analyze_pools_shared_adjudicated_novel_issue_once(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr("D1")
    _write_run(settings, pr, "claude", hit=False)
    _write_run(settings, pr, "gpt", hit=False)
    labels = []
    for reviewer in ("claude", "gpt"):
        review_path = settings.data_dir / "reviews" / "D1" / reviewer / "diff" / "run1" / "owner__repo#1.json"
        review = json.loads(review_path.read_text())
        labels.append({"dataset": "D1", "pr_id": pr.pr_id, "reviewer": reviewer, "mode": "diff",
                       "run_idx": 1, "ai_index": 0, "review_fingerprint": review["fingerprint"],
                       "label": "valid", "missed_by_human": True, "issue_id": "novel-bug-17"})
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": labels}))

    outputs = analyze(settings, label_dir)

    pooled = _rows(outputs["rq5_pooled"])
    claude = next(row for row in pooled if row["party"] == "claude")
    assert claude["n_issues"] == "2"
    assert claude["pooled_recall"] == "0.5"
    assert claude["pool_status"] == "adjudicated"


def test_unmatched_sample_merge_activates_d1_report_with_matched_comment_present(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results", seed=11)
    pr = _pr("D1")
    _write_run(settings, pr, "claude", hit=True, n_comments=2)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    output, private = settings.results_dir / "annotation", settings.results_dir / "annotation-private"
    assert cli.main(["sample", "--dataset", "D1", "--reviewers", "claude", "--raters", "alice,bob",
                     "--output-dir", str(output), "--private-dir", str(private)]) == 0
    private_key = json.loads((private / "annotation-key.json").read_text())
    assert len(private_key["items"]) == 1
    item_id = next(iter(private_key["items"]))
    for path in output.glob("rater-*.json"):
        payload = json.loads(path.read_text())
        payload["labels"] = {item_id: {"label": "valid"}}
        path.write_text(json.dumps(payload))
    adjudication = output / "adjudication.json"
    adjudication.write_text(json.dumps({"items": {
        item_id: {"label": "valid", "missed_by_human": True, "issue_id": "novel-1"}}}))
    assert cli.main(["merge-labels", "--labels-dir", str(output), "--private-dir", str(private),
                     "--adjudication", str(adjudication)]) == 0

    outputs = analyze(settings, output)

    pooled = next(row for row in _rows(outputs["rq5_pooled"]) if row["party"] == "claude")
    noise = next(row for row in _rows(outputs["rq1_rq2"])
                 if row["dataset"] == "D1" and row["metric"] == "true_noise_share")
    assert pooled["pool_status"] == "adjudicated"
    assert noise["status"] == "ok"
    assert noise["n_labeled"] == noise["n_unmatched"] == "1"


def test_d2_sampled_noise_is_reported_as_estimate_and_rq6_requires_complete_pairs(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr("D2")
    _write_run(settings, pr, "claude", "diff", hit=True, n_comments=3)
    _write_run(settings, pr, "claude", "agent", hit=False, n_comments=1)
    diff_path = settings.data_dir / "reviews/D2/claude/diff/run1/owner__repo#1.json"
    agent_path = settings.data_dir / "reviews/D2/claude/agent/run1/owner__repo#1.json"
    diff_review, agent_review = json.loads(diff_path.read_text()), json.loads(agent_path.read_text())
    labels = [
        {"dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": "diff", "run_idx": 1,
         "ai_index": 1, "review_fingerprint": diff_review["fingerprint"], "label": "noise",
         "missed_by_human": False},
        {"dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": "agent", "run_idx": 1,
         "ai_index": 0, "review_fingerprint": agent_review["fingerprint"], "label": "valid",
         "missed_by_human": True},
    ]
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": labels}))

    outputs = analyze(settings, label_dir)

    noise = next(row for row in _rows(outputs["rq1_rq2"])
                 if row["dataset"] == "D2" and row["mode"] == "diff" and row["metric"] == "true_noise_share")
    context = next(row for row in _rows(outputs["rq6_context"]) if row.get("reviewer") == "claude")
    assert noise["status"] == "sampled_estimate"
    assert noise["n_labeled"] == "1" and noise["n_unmatched"] == "2"
    assert noise["ci_low"] == noise["ci_high"] == ""
    assert noise["ci_status"] == "insufficient_pr_clusters"
    assert context["noise_status"] == "labels_incomplete_or_unusable"
    assert context["true_noise_share_agent_minus_diff"] == ""


def test_partial_d2_adjusted_precision_is_unavailable_across_mixed_runs(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    prs = [_pr("D2", 1), _pr("D2", 2)]
    for pr in prs:
        _write_run(settings, pr, "claude", "diff", run=1, hit=False, n_comments=2 if pr.number == 1 else 3)
        _write_run(settings, pr, "claude", "diff", run=2, hit=True, n_comments=4)
    pr = prs[0]
    review_path = settings.data_dir / "reviews/D2/claude/diff/run1/owner__repo#1.json"
    review = json.loads(review_path.read_text())
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": [{
        "dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": "diff", "run_idx": 1,
        "ai_index": 0, "review_fingerprint": review["fingerprint"], "label": "noise",
        "missed_by_human": False}]}))

    outputs = analyze(settings, label_dir)

    rows = _rows(outputs["rq1_rq2"])
    noise = next(row for row in rows if row["dataset"] == "D2" and row["metric"] == "true_noise_share")
    adjusted = next(row for row in rows if row["dataset"] == "D2" and row["metric"] == "adjusted_precision")
    assert noise["status"] == "sampled_estimate" and noise["value"] == "1.0"
    assert noise["n_labeled"] == "1" and noise["n_unmatched"] == "5"
    assert adjusted["value"] == ""
    assert adjusted["status"] == "unavailable_sample_mismatch_scope"


def test_sampled_comment_shares_use_pr_cluster_bootstrap_intervals(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results", seed=29)
    prs = [_pr("D2", 1), _pr("D2", 2), _pr("D2", 3)]
    for pr, count in zip(prs, (4, 1, 1)):
        _write_run(settings, pr, "claude", "diff", hit=False, n_comments=count)
    labels = []
    for pr, review_count, label in ((prs[0], 4, "noise"), (prs[1], 1, "valid")):
        path = settings.data_dir / "reviews/D2/claude/diff/run1" / f"{pr.pr_id.replace('/', '__')}.json"
        review = json.loads(path.read_text())
        for index in range(review_count):
            labels.append({"dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": "diff",
                           "run_idx": 1, "ai_index": index, "review_fingerprint": review["fingerprint"],
                           "label": label, "missed_by_human": label == "valid"})
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": labels}))

    outputs = analyze(settings, label_dir)

    rows = _rows(outputs["rq1_rq2"])
    noise = next(row for row in rows if row["dataset"] == "D2" and row["metric"] == "true_noise_share")
    missed = next(row for row in rows if row["dataset"] == "D2" and row["metric"] == "missed_by_human_share")
    assert noise["value"] == "0.8" and noise["status"] == "sampled_estimate"
    assert noise["ci_low"] == "0.0" and noise["ci_high"] == "1.0"
    assert noise["ci_status"] == "ok"
    assert missed["ci_low"] == missed["ci_high"] == ""
    assert missed["ci_status"] == "insufficient_pr_clusters"


def test_d1_pool_is_baseline_only_if_valid_unmatched_comment_lacks_issue_id(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr("D1")
    _write_run(settings, pr, "claude", hit=False)
    review_path = settings.data_dir / "reviews" / "D1" / "claude" / "diff" / "run1" / "owner__repo#1.json"
    review = json.loads(review_path.read_text())
    item = {"dataset": "D1", "pr_id": pr.pr_id, "reviewer": "claude", "mode": "diff",
            "run_idx": 1, "ai_index": 0, "review_fingerprint": review["fingerprint"],
            "label": "valid", "missed_by_human": True}
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": [item]}))

    outputs = analyze(settings, label_dir)

    claude = next(row for row in _rows(outputs["rq5_pooled"]) if row["party"] == "claude")
    assert claude["n_issues"] == "1"
    assert claude["pool_status"] == "baseline_only_labels_incomplete_or_unusable"


def test_analyze_reports_paired_context_and_label_noise_differences(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr("D2")
    _write_run(settings, pr, "claude", "diff", hit=True, n_comments=2)
    _write_run(settings, pr, "claude", "agent", hit=False, n_comments=1)
    items = []
    for mode, labeled_indices in (("diff", [(1, "noise")]), ("agent", [(0, "valid")])):
        review_path = settings.data_dir / "reviews" / "D2" / "claude" / mode / "run1" / "owner__repo#1.json"
        review = json.loads(review_path.read_text())
        for index, label in labeled_indices:
            items.append({"dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": mode,
                          "run_idx": 1, "ai_index": index, "review_fingerprint": review["fingerprint"],
                          "label": label, "missed_by_human": label == "valid"})
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": items}))

    outputs = analyze(settings, label_dir)

    context = _rows(outputs["rq6_context"])
    row = next(item for item in context if item["dataset"] == "D2" and item["reviewer"] == "claude"
               and item["status"] == "ok")
    assert float(row["n_ai_agent_minus_diff"]) == -1.0
    assert float(row["mismatch_agent_minus_diff"]) == 0.5
    assert float(row["true_noise_share_agent_minus_diff"]) == -1.0


def test_analyze_context_label_noise_is_unavailable_without_labels(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results")
    pr = _pr("D2")
    _write_run(settings, pr, "claude", "diff", hit=True, n_comments=2)
    _write_run(settings, pr, "claude", "agent", hit=False, n_comments=1)

    outputs = analyze(settings)

    row = next(item for item in _rows(outputs["rq6_context"]) if item["status"] == "ok")
    assert float(row["n_ai_agent_minus_diff"]) == -1.0
    assert row["true_noise_share_agent_minus_diff"] == ""
    assert row["noise_status"] == "labels_not_supplied"


def test_rq6_noise_uses_paired_pr_deltas_and_seeded_pr_bootstrap(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", results_dir=tmp_path / "results", seed=123)
    prs = [_pr("D2", 1), _pr("D2", 2), _pr("D2", 3)]
    sizes = {1: {"diff": 9, "agent": 1}, 2: {"diff": 1, "agent": 9},
             3: {"diff": 0, "agent": 0}}
    noise = {1: {"diff": [False] * 9, "agent": [True]},
             2: {"diff": [True], "agent": [True] * 9},
             3: {"diff": [], "agent": []}}
    items = []
    for pr in prs:
        for mode in ("diff", "agent"):
            _write_run(settings, pr, "claude", mode, n_comments=sizes[pr.number][mode], hit=False)
            review_path = (settings.data_dir / "reviews" / "D2" / "claude" / mode / "run1"
                           / f"{pr.pr_id.replace('/', '__')}.json")
            review = json.loads(review_path.read_text())
            for index, is_noise in enumerate(noise[pr.number][mode]):
                items.append({"dataset": "D2", "pr_id": pr.pr_id, "reviewer": "claude", "mode": mode,
                              "run_idx": 1, "ai_index": index, "review_fingerprint": review["fingerprint"],
                              "label": "noise" if is_noise else "valid", "missed_by_human": False})
    label_dir = tmp_path / "labels"
    label_dir.mkdir()
    (label_dir / "adjudicated_labels.json").write_text(json.dumps({"schema": "c3-adjudication-v1", "items": items}))

    first_path = analyze(settings, label_dir)["rq6_context"]
    first = next(row for row in _rows(first_path) if row["status"] == "ok")
    second = next(row for row in _rows(analyze(settings, label_dir)["rq6_context"])
                  if row["status"] == "ok")

    assert first["n_noise_pairs"] == "2"
    assert float(first["true_noise_share_agent_minus_diff"]) == 0.5
    assert first["noise_status"] == "ok_with_zero_comment_pairs_excluded"
    assert first["noise_delta_ci_low"] == second["noise_delta_ci_low"]
    assert first["noise_delta_ci_high"] == second["noise_delta_ci_high"]

