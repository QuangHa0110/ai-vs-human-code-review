import json
from hashlib import sha256
from pathlib import Path

import pytest

from c3 import cli
from c3.models import load_pr


MINI = Path(__file__).parent / "fixtures" / "swepr_mini.json"


@pytest.fixture
def dataset(tmp_path):
    rows = json.loads(MINI.read_bytes())
    full = tmp_path / "dataset" / "prs.jsonl"
    evaluated = full.parent / "evals" / "eval_100.json"
    annotations = full.parent / "annotations"
    evaluated.parent.mkdir(parents=True)
    annotations.mkdir()
    full.write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
    evaluated.write_text(json.dumps(rows[:-1]), encoding="utf-8")
    for row in rows:
        comment = {
            "comment_id": 21, "body": "Handle null here, @reviewer.",
            "file": "Test.java" if row["language"] == "Java" else "mod.py",
            "line": 1, "is_in_diff": True, "is_initiating_comment": True,
            "is_reply": False,
        }
        (annotations / f"{row['task_id']}_human.json").write_text(json.dumps({
            "substantive_comment_ids": [21, 22, 23, 24],
            "comments": [
                comment,
                dict(comment, comment_id=22, is_reply=True, is_initiating_comment=False),
                dict(comment, comment_id=23, is_in_diff=False),
                dict(comment, comment_id=24, line=None),
                dict(comment, comment_id=25),
            ],
        }), encoding="utf-8")
    return full, evaluated, annotations


def test_keeps_all_java(dataset):
    from c3.swepr import load_swepr

    _, evaluated, _ = dataset
    prs = load_swepr(evaluated, {"Java", "Python"}, python_sample=0, seed=42)
    assert [pr.pr_id for pr in prs] == ["acme/java#1", "acme/java#3"]


def test_python_sample_deterministic(dataset):
    from c3.swepr import load_swepr

    full, evaluated, _ = dataset
    original = load_swepr(full, {"Python"}, python_sample=2, seed=42)
    full.write_text("\n".join(reversed(full.read_text(encoding="utf-8").splitlines())), encoding="utf-8")
    assert len(original) == 2
    assert original == load_swepr(full, {"Python"}, python_sample=2, seed=42)
    assert original == load_swepr(evaluated, {"Python"}, python_sample=2, seed=42)
    assert all("a/mod.py" in pr.diff for pr in original)


def test_evaluated_only_filter(dataset):
    from c3.swepr import load_swepr_with_audit

    full, _, _ = dataset
    prs, audit = load_swepr_with_audit(full, {"Java"}, python_sample=0, seed=42)
    assert [pr.number for pr in prs] == [1, 3]
    assert audit["candidate_prs_by_language"] == {"Java": 2}
    prs, audit = load_swepr_with_audit(full, {"Java"}, python_sample=0, seed=42, evaluated_only=False)
    assert [pr.number for pr in prs] == [1, 3, 6]
    assert audit["evaluated_membership"] == {"java__1": True, "java__3": True, "java__6": False}


@pytest.mark.parametrize(("field", "value"), [
    ("head_commit", "3" * 40),
    ("diff_patch", "diff --git a/Different.java b/Different.java\n"),
])
@pytest.mark.parametrize("evaluated_only", [True, False])
def test_rejects_evaluated_task_with_different_snapshot(dataset, field, value, evaluated_only):
    from c3.swepr import load_swepr

    full, _, _ = dataset
    rows = [json.loads(line) for line in full.read_bytes().splitlines()]
    rows[0][field] = value
    full.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"java__1.*eval_100\.json.*{field}"):
        load_swepr(full, {"Java"}, python_sample=0, seed=42, evaluated_only=evaluated_only)


def test_missing_evaluation_membership_fails_closed(dataset):
    from c3.swepr import load_swepr, load_swepr_with_audit

    full, evaluated, _ = dataset
    evaluated.unlink()
    with pytest.raises(ValueError, match=r"evals/eval_100\.json"):
        load_swepr(full, {"Java"}, python_sample=0, seed=42)
    _, audit = load_swepr_with_audit(full, {"Java"}, python_sample=0, seed=42, evaluated_only=False)
    assert audit["evaluated_membership"] == {"java__1": None, "java__3": None, "java__6": None}


def test_reference_comes_from_substantive_initiating_annotation(dataset):
    from c3.swepr import load_swepr_with_audit

    _, evaluated, _ = dataset
    prs, audit = load_swepr_with_audit(evaluated, {"Java"}, python_sample=0, seed=42)
    pr = prs[0]
    ref, = pr.references
    assert (ref.ref_id, ref.path, ref.line, ref.text) == (
        "acme/java#1:21", "Test.java", 1, "Handle null here, @reviewer.",
    )
    assert ref.origin == "human" and ref.valid is True
    assert (pr.dataset, pr.base_sha, pr.review_sha, pr.diff) == (
        "D3", "1" * 40, "2" * 40, json.loads(evaluated.read_bytes())[0]["diff_patch"],
    )
    assert audit["raw_substantive_references"] == 8
    assert audit["retained_references"] == 2
    assert audit["dropped_references"] == {"reply_or_non_initiating": 2, "outside_diff": 2, "missing_line": 2}


def test_missing_annotations_fails_without_falling_back_to_raw_comments(dataset):
    from c3.swepr import load_swepr

    _, evaluated, annotations = dataset
    (annotations / "java__1_human.json").unlink()
    with pytest.raises(FileNotFoundError, match="java__1_human.json"):
        load_swepr(evaluated, {"Java"}, python_sample=0, seed=42)


def test_unknown_substantive_id_fails(dataset):
    from c3.swepr import load_swepr

    _, evaluated, annotations = dataset
    annotation = annotations / "java__1_human.json"
    payload = json.loads(annotation.read_bytes())
    payload["substantive_comment_ids"].append(999)
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="999"):
        load_swepr(evaluated, {"Java"}, python_sample=0, seed=42)


def test_audit_lists_prs_without_retained_references(dataset):
    from c3.swepr import load_swepr_with_audit

    _, evaluated, annotations = dataset
    annotation = annotations / "java__1_human.json"
    payload = json.loads(annotation.read_bytes())
    payload["substantive_comment_ids"] = [22]
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    prs, audit = load_swepr_with_audit(evaluated, {"Java"}, python_sample=0, seed=42)
    assert prs[0].references == []
    assert audit["prs_without_references"] == ["acme/java#1"]


def run_import(path):
    return cli.main(["load-swepr", "--path", str(path), "--languages", "Java,Python", "--python-sample", "1"])


@pytest.fixture
def output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_ANONYMIZATION_SALT", "fixture-salt")
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("C3_SEED", "42")
    return tmp_path / "data" / "prs" / "D3"


def test_cli_writes_anonymized_d3_and_audit(dataset, output):
    full, evaluated, annotations = dataset
    assert run_import(full) == 0
    records = list(output.glob("acme__*.json"))
    assert len(records) == 3
    assert "@reviewer." not in load_pr(output / "acme__java#1.json").references[0].text
    meta = json.loads((output / "_meta.json").read_bytes())
    assert meta["inputs"]["prs"]["sha256"] == sha256(full.read_bytes()).hexdigest()
    assert meta["inputs"]["evaluated"]["sha256"] == sha256(evaluated.read_bytes()).hexdigest()
    assert meta["inputs"]["annotations"]["java__1"]["sha256"] == sha256(
        (annotations / "java__1_human.json").read_bytes(),
    ).hexdigest()
    assert meta["seed"] == 42
    assert meta["selected_prs_by_language"] == {"Java": 2, "Python": 1}
    assert len(meta["evaluated_membership"]) == 3
    assert all(meta["evaluated_membership"].values())


def test_cli_preserves_occupied_output(dataset, output):
    output.mkdir(parents=True)
    existing = output / "original.json"
    existing.write_text("original", encoding="utf-8")
    assert run_import(dataset[0]) == 1
    assert {item.name for item in output.iterdir()} == {existing.name}
    assert existing.read_text(encoding="utf-8") == "original"


def test_cli_leaves_no_partial_export_when_annotations_missing(dataset, output):
    full, _, annotations = dataset
    (annotations / "java__3_human.json").unlink()
    assert run_import(full) == 1
    assert not output.exists()


def test_cli_requires_salt(dataset, output, monkeypatch):
    monkeypatch.delenv("C3_ANONYMIZATION_SALT")
    assert run_import(dataset[0]) == 1
    assert not output.exists()
