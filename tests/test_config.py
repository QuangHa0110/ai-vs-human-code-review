from pathlib import Path

import pytest

from c3.config import load_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("C3_") or key in {"GITHUB_TOKEN", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"}:
            monkeypatch.delenv(key)


def test_load_settings_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.seed == 20261121
    assert set(settings.reviewers) == {"claude", "gpt", "deepseek"}
    assert settings.reviewers["claude"].backend == "claude_cli"
    assert settings.reviewers["gpt"].backend == "codex_cli"
    assert settings.reviewers["deepseek"].backend == "deepseek_api"
    assert all(spec.model == "" for spec in settings.reviewers.values())
    assert settings.github_token is None
    assert settings.data_dir == Path("data")
    assert settings.results_dir == Path("results")


def test_env_file_and_environment_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("C3_MODEL_CLAUDE=pilot-model\nC3_MODEL_GPT=file-model\nC3_DATA_DIR=custom-data\nGITHUB_TOKEN=file-token\n")
    monkeypatch.setenv("C3_MODEL_GPT", "environment-model")
    settings = load_settings(env)
    assert settings.reviewers["claude"].model == "pilot-model"
    assert settings.reviewers["gpt"].model == "environment-model"
    assert settings.data_dir == Path("custom-data")
    assert settings.github_token == "file-token"


def test_extra_reviewers_parsed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_EXTRA_REVIEWERS", "pilot=claude_cli:pilot-model, calib=openai_api:calibration")
    settings = load_settings()
    assert settings.reviewers["pilot"].name == "pilot"
    assert settings.reviewers["pilot"].backend == "claude_cli"
    assert settings.reviewers["calib"].model == "calibration"


def test_gpt_backend_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_GPT_BACKEND", "openai_api")
    assert load_settings().reviewers["gpt"].backend == "openai_api"


@pytest.mark.parametrize("value", ["bad", "x=unknown:model", "claude=claude_cli:model", "x=claude_cli:"])
def test_invalid_extra_reviewers_raise(value, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("C3_EXTRA_REVIEWERS", value)
    with pytest.raises(ValueError):
        load_settings()
