import json

import pytest

from c3 import cli
from c3.models import load_pr, save_json
from c3.runners import RunResult
from test_classify import Classifier, LABELS, make_pr, model_response


def classify(dataset):
    return cli.main(["classify", "--dataset", dataset, "--reviewer", "claude"])


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_DATA_DIR", str(tmp_path))
    return tmp_path


def write_input(data_dir, pr):
    source = data_dir / "prs" / pr.dataset / f"org__example#{pr.number}.json"
    save_json(pr, source)
    return source


def test_classify_publishes_separate_d2_dataset_and_audit(data_dir, monkeypatch, capsys):
    source = write_input(data_dir, make_pr())
    original = source.read_bytes()
    write_input(data_dir, make_pr(number=2))
    save_json({"upstream": "metadata"}, source.parent / "_meta.json")
    output = data_dir / "classified" / "D2"

    runner = Classifier(model_response(), model_response([{**label, "actionable": True} for label in LABELS]))

    def make_runner(name, settings):
        assert name == "claude"
        assert settings.data_dir == data_dir
        return runner

    monkeypatch.setattr(cli, "make_runner", make_runner, raising=False)
    assert classify("D2") == 0
    assert len(runner.calls) == 2
    assert source.read_bytes() == original
    assert {path.name for path in output.iterdir()} == {"org__example#2.json", "_meta.json"}
    assert [ref.valid for ref in load_pr(output / "org__example#2.json").references] == [False, True, False]
    metadata = json.loads((output / "_meta.json").read_text())
    assert metadata["dataset"] == "D2" and metadata["reviewer"] == "claude"
    assert metadata["input_pr_count"] == 2 and metadata["retained_pr_count"] == 1
    assert metadata["dropped_pr_count"] == 1 and metadata["error_count"] == 0

    assert classify("D2") == 1
    assert len(runner.calls) == 2
    assert "already occupied" in capsys.readouterr().err


def test_classify_failure_does_not_publish_easier_records(data_dir, monkeypatch, capsys):
    write_input(data_dir, make_pr("D1"))
    write_input(data_dir, make_pr("D1", number=2))
    runner = Classifier(model_response(), RunResult("", {}, "test runner failure"))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner, raising=False)

    assert classify("D1") == 1
    assert len(runner.calls) == 2
    assert "org/example#2" in capsys.readouterr().err
    assert not (data_dir / "classified" / "D1").exists()


@pytest.mark.parametrize("dataset", ["D1", "D3"])
def test_classify_preserves_benchmark_references(data_dir, monkeypatch, dataset):
    pr = make_pr(dataset)
    write_input(data_dir, pr)
    labels = [{**label, "actionable": False} for label in LABELS]
    runner = Classifier(model_response(labels))
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner)

    assert classify(dataset) == 0
    output = load_pr(data_dir / "classified" / dataset / "org__example#1.json")
    assert [(ref.ref_id, ref.valid, ref.actionable) for ref in output.references] == [
        ("first", False, False), ("second", True, False), ("automated", False, False),
    ]


def test_classify_requires_input_pr_records(data_dir, capsys):
    save_json({}, data_dir / "prs" / "D3" / "_meta.json")
    assert classify("D3") == 1
    assert "No PR records" in capsys.readouterr().err


def test_classify_rejects_a_dataset_with_no_retained_prs(data_dir, monkeypatch, capsys):
    write_input(data_dir, make_pr())
    runner = Classifier(model_response())
    monkeypatch.setattr(cli, "make_runner", lambda *args: runner, raising=False)

    assert classify("D2") == 1
    assert "1 dropped" in capsys.readouterr().err
    assert not (data_dir / "classified" / "D2").exists()
