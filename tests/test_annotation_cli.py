import json

import pytest

from c3 import cli
from c3.models import PullRequest, Reference


class Settings:
    seed = 7


def test_sample_cli_writes_blinded_rater_files_and_private_key(tmp_path, monkeypatch):
    pr = PullRequest("owner/repo#1", "D1", "owner/repo", 1, "title", "body", "base", "head",
                     "diff", [])
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    monkeypatch.setattr(cli, "_load_review_prs", lambda settings, dataset: (tmp_path / "source", [pr]))
    source = tmp_path / "source"
    source.mkdir()
    review = {"pr_id": pr.pr_id, "reviewer": "opaque", "mode": "diff", "run_idx": 1,
              "fingerprint": "review-fingerprint", "parse_error": False, "error": None,
              "meta": {}, "comments": [{"path": "A.java", "line": 4, "comment": "possible issue",
                                          "category": "testing"}], "source": str(source),
              "fingerprint_input": {"dataset": "D1", "pr_id": pr.pr_id, "repo": pr.repo,
                                    "base_sha": pr.base_sha, "head_sha": pr.review_sha,
                                    "diff": pr.diff, "mode": "diff", "reviewer": "opaque", "run_idx": 1}}
    monkeypatch.setattr(cli, "_load_annotation_artifacts", lambda *args: (
        {("opaque", pr.pr_id, "diff", 1): review},
        {("opaque", pr.pr_id, "diff", 1): {"pr_id": pr.pr_id, "matches": []}},
    ))
    output = tmp_path / "annotation"
    private = tmp_path / "annotation-private"
    rc = cli.main(["sample", "--dataset", "D1", "--reviewers", "opaque", "--raters", "one,two,three",
                   "--output-dir", str(output), "--private-dir", str(private)])
    assert rc == 0
    public = json.loads((output / "rater-01.json").read_text())
    assert set(public) == {"schema", "items"}
    assert public["items"][0]["text"] == "possible issue"
    assert "reviewer" not in json.dumps(public)
    assert "fingerprint" not in json.dumps(public)
    assert sorted(p.name for p in (output / "assignments").iterdir()) == [
        "rater-01.json", "rater-02.json", "rater-03.json"]
    assert (private / "annotation-key.json").is_file()
    private_key = json.loads((private / "annotation-key.json").read_text())
    assert "review-fingerprint" in json.dumps(private_key)
    assert private_key["raters"] == ["one", "three", "two"]
    pr.references.extend([
        Reference("positive", "A.java", 1, "valid reference", "human", True),
        Reference("negative", "B.java", 2, "negative reference", "human", False),
    ])
    calibration_output = tmp_path / "calibration"
    assert cli.main(["calibration-set", "--n", "2", "--raters", "one,two",
                     "--output-dir", str(calibration_output), "--private-dir", str(private)]) == 0
    assert (private / "annotation-key.json").is_file()
    assert (private / "calibration-key.json").is_file()
    assert cli.main(["calibration-set", "--n", "2", "--raters", "one,two",
                     "--output-dir", str(tmp_path / "calibration-again"),
                     "--private-dir", str(private)]) == 1
    assert not (tmp_path / "calibration-again").exists()
    for path in output.glob("rater-*.json"):
        payload = json.loads(path.read_text())
        assignment = json.loads((output / "assignments" / path.name).read_text())
        payload["labels"] = {item_id: {"label": "noise", "cause": "lack_context"}
                             for item_id in assignment["item_ids"]}
        path.write_text(json.dumps(payload))
    adjudication = tmp_path / "adjudication.json"
    adjudication.write_text(json.dumps({"items": {item_id: {"missed_by_human": False}
                                                for item_id in private_key["items"]}}))
    assert cli.main(["merge-labels", "--labels-dir", str(output), "--private-dir", str(private),
                     "--adjudication", str(adjudication)]) == 0
    assert cli.main(["sample", "--dataset", "D1", "--reviewers", "opaque", "--raters", "one,two,three",
                     "--output-dir", str(output), "--private-dir", str(private)]) == 1


def test_calibration_cli_keeps_expert_labels_private(tmp_path, monkeypatch):
    prs = [{"pr_id": "p1", "dataset": "D1", "repo": "owner/repo", "number": 1, "references": [
        {"ref_id": "positive", "valid": True, "path": "A.java", "line": 1, "text": "finding"},
        {"ref_id": "negative", "valid": False, "path": "B.java", "line": 2, "text": "noise"},
    ]}]
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    monkeypatch.setattr(cli, "_load_review_prs", lambda settings, dataset: (tmp_path / "source", prs))
    output, private = tmp_path / "calibration", tmp_path / "private"
    assert cli.main(["calibration-set", "--n", "2", "--raters", "one,two",
                     "--output-dir", str(output), "--private-dir", str(private)]) == 0
    public = json.loads((output / "rater-01.json").read_text())
    key = json.loads((private / "calibration-key.json").read_text())
    assert "positive" not in json.dumps(public) and "negative" not in json.dumps(public)
    assert {item["label"] for item in key["items"].values()} == {"valid", "noise"}


def _merge_handoff(tmp_path):
    output, private = tmp_path / "annotation", tmp_path / "private"
    (output / "assignments").mkdir(parents=True)
    private.mkdir()
    item = {"dataset": "D1", "pr_id": "owner/repo#1", "reviewer": "opaque", "mode": "diff",
            "run_idx": 1, "ai_index": 0, "review_fingerprint": "fp"}
    item_id = "a1" * 10
    (private / "annotation-key.json").write_text(json.dumps({"items": {item_id: item},
                                                              "assignments": {item_id: ["one", "two"]},
                                                              "raters": ["one", "two"]}))
    for index in (1, 2):
        filename = f"rater-{index:02d}.json"
        (output / "assignments" / filename).write_text(json.dumps({"item_ids": [item_id]}))
        (output / filename).write_text(json.dumps({"items": [{"item_id": item_id}],
                                                  "labels": {item_id: {"label": "noise", "cause": "lack_context"}}}))
    adjudication = tmp_path / "adjudication.json"
    adjudication.write_text(json.dumps({"items": {item_id: {"missed_by_human": False}}}))
    return output, private, item_id, adjudication


def test_merge_labels_requires_manual_fields_and_writes_report_schema(tmp_path):
    output, private, item_id, adjudication = _merge_handoff(tmp_path)
    assert cli.main(["merge-labels", "--labels-dir", str(output), "--private-dir", str(private),
                     "--adjudication", str(adjudication)]) == 0
    merged = json.loads((output / "adjudicated_labels.json").read_text())
    assert merged["schema"] == "c3-adjudication-v1"
    assert merged["items"][0]["cause"] == "lack_context"
    assert merged["items"][0]["review_fingerprint"] == "fp"


def test_merge_labels_rejects_missing_assigned_rater_file(tmp_path):
    output, private, _, adjudication = _merge_handoff(tmp_path)
    (output / "rater-02.json").unlink()
    (output / "assignments" / "rater-02.json").unlink()
    assert cli.main(["merge-labels", "--labels-dir", str(output), "--private-dir", str(private),
                     "--adjudication", str(adjudication)]) == 1
    assert not (output / "adjudicated_labels.json").exists()


def test_merge_labels_rejects_tampered_assignment_ids(tmp_path):
    output, private, _, adjudication = _merge_handoff(tmp_path)
    (output / "assignments" / "rater-01.json").write_text(json.dumps({"item_ids": []}))
    assert cli.main(["merge-labels", "--labels-dir", str(output), "--private-dir", str(private),
                     "--adjudication", str(adjudication)]) == 1
    assert not (output / "adjudicated_labels.json").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_sample_refuses_public_private_directory_overlap(tmp_path, monkeypatch, nested):
    pr = PullRequest("owner/repo#1", "D1", "owner/repo", 1, "title", "body", "base", "head", "diff", [])
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    monkeypatch.setattr(cli, "_load_review_prs", lambda settings, dataset: (tmp_path / "source", [pr]))
    monkeypatch.setattr(cli, "_load_annotation_artifacts", lambda *args: ({}, {}))
    base = tmp_path / "shared"
    output = base if not nested else base / "output"
    private = base if not nested else output / "private"

    assert cli.main(["sample", "--dataset", "D1", "--reviewers", "opaque", "--raters", "one,two",
                     "--output-dir", str(output), "--private-dir", str(private)]) == 1
    assert not base.exists()
