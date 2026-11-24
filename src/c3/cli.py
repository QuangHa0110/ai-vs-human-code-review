"""Command-line entry points for the C3 review pipeline."""

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
import os
import re
import sys

import httpx

from c3.config import load_settings
from c3.github_client import GitHubClient
from c3.models import load_pr, save_json
from c3.select import anonymize, assess_candidate, sample_prs

_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")


def _collect(args: argparse.Namespace) -> int:
    repos = sorted({repo.strip().lower() for repo in args.repos.split(",")})
    if not all(_REPO.fullmatch(repo) and repo.rsplit("/", 1)[1] not in {".", ".."} for repo in repos):
        raise ValueError("--repos must contain comma-separated owner/repo names")
    if args.since > args.until:
        raise ValueError("--since must be on or before --until")
    if args.n_per_repo <= 0:
        raise ValueError("--n-per-repo must be positive")
    salt = os.environ.get("C3_ANONYMIZATION_SALT")
    if not salt or not salt.strip():
        raise ValueError("Set C3_ANONYMIZATION_SALT in the environment before collecting D2")

    settings = load_settings()
    cutoff = args.since
    if settings.training_cutoff:
        cutoff = max(cutoff, date.fromisoformat(settings.training_cutoff))
    if cutoff > args.until:
        raise ValueError("The training cutoff must be on or before --until")
    params = {
        "repos": repos,
        "since": args.since.isoformat(),
        "until": args.until.isoformat(),
        "created_after": cutoff.isoformat(),
        "n_per_repo": args.n_per_repo,
        "seed": settings.seed,
    }
    output = settings.data_dir / "prs" / "D2"
    manifest = output / "_meta.json"
    salt_fingerprint = sha256(salt.encode()).hexdigest()
    existing = None
    if manifest.exists():
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in params.items()):
            raise ValueError("D2 already contains a different collection. Choose a new C3_DATA_DIR.")
        if existing.get("salt_fingerprint") != salt_fingerprint:
            raise ValueError("Resume with the original C3_ANONYMIZATION_SALT or choose a new C3_DATA_DIR.")
    rejects = Counter()
    candidates = []
    github = GitHubClient(settings.github_token or "")
    try:
        for repo in repos:
            seen = set()
            for item in github.search_merged_prs(repo, cutoff.isoformat(), args.until.isoformat()):
                number = item["number"]
                if number in seen:
                    continue
                seen.add(number)
                candidate, rejection = assess_candidate(github, repo, number, cutoff.isoformat())
                if candidate is not None:
                    candidates.append(candidate)
                else:
                    rejects[rejection] += 1
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ValueError(f"GitHub collection failed: {exc}") from exc
    finally:
        github.close()

    selected = sample_prs(candidates, args.n_per_repo, settings.seed)
    sanitized, _ = anonymize(selected, salt)
    eligible_counts = Counter(pr.repo for pr in candidates)
    selected_counts = Counter(pr.repo for pr in sanitized)
    selected_ids = [pr.pr_id for pr in sanitized]
    if existing is not None and existing.get("selected_pr_ids") != selected_ids:
        raise ValueError("The D2 sample changed. Choose a new C3_DATA_DIR.")
    records = [(pr, output / f"{pr.pr_id.replace('/', '__')}.json") for pr in sanitized]
    expected_paths = {path for _, path in records} if existing is not None else set()
    existing_paths = set(output.glob("*.json")) - {manifest}
    if existing_paths != expected_paths:
        raise ValueError("D2 PR files do not match the collection manifest. Choose a new C3_DATA_DIR.")
    if existing is not None:
        for pr, path in records:
            try:
                stored = load_pr(path)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"D2 cannot read {path.name}. Choose a new C3_DATA_DIR.") from exc
            if stored != pr:
                raise ValueError(f"D2 record {path.name} changed. Choose a new C3_DATA_DIR.")
        return 0

    for pr, path in records:
        save_json(pr, path)
    save_json({
        **params,
        "salt_fingerprint": salt_fingerprint,
        "reject_counts": dict(rejects),
        "eligible_counts": {repo: eligible_counts[repo] for repo in repos},
        "selected_counts": {repo: selected_counts[repo] for repo in repos},
        "shortages": {
            repo: args.n_per_repo - selected_counts[repo]
            for repo in repos if selected_counts[repo] < args.n_per_repo
        },
        "selected_pr_ids": selected_ids,
    }, manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="c3", description="C3 code review research pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Collect anonymized D2 PRs")
    collect.add_argument("--repos", required=True, help="Comma-separated owner/repo names")
    collect.add_argument("--since", required=True, type=date.fromisoformat)
    collect.add_argument("--until", required=True, type=date.fromisoformat)
    collect.add_argument("--n-per-repo", required=True, type=int)
    collect.set_defaults(run=_collect)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError) as exc:
        print(f"c3: {exc}", file=sys.stderr)
        return 1
