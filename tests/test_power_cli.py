import json

from c3 import cli


def test_power_cli_writes_pilot_based_plan_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("C3_RESULTS_DIR", str(tmp_path / "results"))
    args = ["power", "--p-discordant", "0.3", "--refs-per-pr", "4", "--design-effect", "1.2"]
    assert cli.main(args) == 0
    path = tmp_path / "results" / "power.json"
    plan = json.loads(path.read_text())
    assert plan["pilot"] == {"p_discordant": 0.3, "refs_per_pr": 4.0, "design_effect": 1.2}
    assert plan["assumptions"]["alpha"] == 0.05 / 3
    assert plan["n_refs"] > 0 and plan["n_prs"] > 0
    assert cli.main(args) == 0


def test_power_cli_refuses_to_overwrite_different_pilot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("C3_RESULTS_DIR", str(tmp_path / "results"))
    assert cli.main(["power", "--p-discordant", "0.3", "--refs-per-pr", "4", "--design-effect", "1.2"]) == 0
    assert cli.main(["power", "--p-discordant", "0.4", "--refs-per-pr", "4", "--design-effect", "1.2"]) == 1
    assert "different power plan" in capsys.readouterr().err
