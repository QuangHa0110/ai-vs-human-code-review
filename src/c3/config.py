"""Environment configuration without mutating the process environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from dotenv import dotenv_values

Backend = Literal["claude_cli", "codex_cli", "deepseek_api", "openai_api"]
BACKENDS = {"claude_cli", "codex_cli", "deepseek_api", "openai_api"}


@dataclass
class ReviewerSpec:
    name: str
    backend: Backend
    model: str


def _default_reviewers() -> dict[str, ReviewerSpec]:
    return {
        "claude": ReviewerSpec("claude", "claude_cli", ""),
        "gpt": ReviewerSpec("gpt", "codex_cli", ""),
        "deepseek": ReviewerSpec("deepseek", "deepseek_api", ""),
    }


@dataclass
class Settings:
    github_token: str | None = None
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    data_dir: Path = Path("data")
    results_dir: Path = Path("results")
    seed: int = 20261121
    reviewers: dict[str, ReviewerSpec] = field(default_factory=_default_reviewers)
    judge_primary: str = ""
    judge_secondary: str = ""
    training_cutoff: str = ""


def load_settings(env_file: Path | None = None) -> Settings:
    """Load a local .env (or explicit file), with process variables taking priority."""
    values = {
        key: value for key, value in dotenv_values(env_file or Path(".env")).items()
        if value is not None
    }
    values.update(os.environ)
    reviewers = _default_reviewers()
    for name, spec in reviewers.items():
        spec.model = values.get(f"C3_MODEL_{name.upper()}", "")
    gpt_backend = values.get("C3_GPT_BACKEND", "codex_cli")
    if gpt_backend not in {"codex_cli", "openai_api"}:
        raise ValueError("C3_GPT_BACKEND must be codex_cli or openai_api")
    reviewers["gpt"].backend = cast(Backend, gpt_backend)
    for entry in values.get("C3_EXTRA_REVIEWERS", "").split(","):
        if not entry.strip():
            continue
        name, equals, remainder = entry.strip().partition("=")
        backend, colon, model = remainder.partition(":")
        name, backend, model = name.strip(), backend.strip(), model.strip()
        if not equals or not colon or not name or not model or backend not in BACKENDS:
            raise ValueError(f"Invalid C3_EXTRA_REVIEWERS entry: {entry!r}")
        if name in reviewers:
            raise ValueError(f"Duplicate reviewer name: {name!r}")
        reviewers[name] = ReviewerSpec(name, cast(Backend, backend), model)
    return Settings(
        github_token=values.get("GITHUB_TOKEN") or None,
        deepseek_api_key=values.get("DEEPSEEK_API_KEY") or None,
        openai_api_key=values.get("OPENAI_API_KEY") or None,
        data_dir=Path(values.get("C3_DATA_DIR", "data")),
        results_dir=Path(values.get("C3_RESULTS_DIR", "results")),
        seed=int(values.get("C3_SEED", "20261121")),
        reviewers=reviewers,
        judge_primary=values.get("C3_JUDGE_PRIMARY", ""),
        judge_secondary=values.get("C3_JUDGE_SECONDARY", ""),
        training_cutoff=values.get("C3_TRAINING_CUTOFF", ""),
    )
