"""Command-line entry points for the C3 review pipeline."""

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import re
import sys
import subprocess
import tempfile
from dataclasses import asdict
from tempfile import TemporaryDirectory

import httpx

from c3.aacr import load_aacr_with_audit, summary as aacr_summary
from c3.classify import classify_references, keep_actionable
from c3.checkout import prepare_checkout
from c3.cli_flags import (
    CLAUDE_AGENT_READONLY, CLAUDE_DIFF_ONLY, CLAUDE_JSON_OUTPUT,
    CODEX_AGENT_READONLY, CODEX_JSON_OUTPUT,
)
from c3.config import load_settings
from c3.diffutil import filter_java
from c3.github_client import GitHubClient
from c3.models import CATEGORIES, PullRequest, load_pr, save_json
from c3.review import ReviewResult, build_review_prompt, run_review
from c3.runners import CachedRunner, make_runner
from c3.select import anonymize, assess_candidate, sample_prs
from c3.swepr import load_swepr_with_audit

_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")


def _check_import_output(output: Path) -> None:
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError(f"{output.name} output is already occupied. Choose a new C3_DATA_DIR.")


def _publish_import(output: Path, prs: list[PullRequest], audit: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        for pr in prs:
            save_json(pr, staged / f"{pr.pr_id.replace('/', '__')}.json")
        save_json(audit, staged / "_meta.json")
        _check_import_output(output)
        staged.rename(output)


def _load_aacr(args: argparse.Namespace) -> int:
    salt = os.environ.get("C3_ANONYMIZATION_SALT")
    if not salt or not salt.strip():
        raise ValueError("Set C3_ANONYMIZATION_SALT in the environment before loading D1")
    settings = load_settings()
    output = settings.data_dir / "prs" / "D1"
    _check_import_output(output)

    prs, audit = load_aacr_with_audit(args.positive, args.negative)
    prs, _ = anonymize(prs, salt)
    github = GitHubClient(settings.github_token or "")
    try:
        for pr in prs:
            diff = github.compare_diff(pr.repo, pr.base_sha, pr.review_sha)
            pr.diff = filter_java(diff)
            if not pr.diff:
                raise ValueError(f"No Java diff for {pr.pr_id} at its AACR commits")
    except httpx.HTTPError as exc:
        raise ValueError(f"GitHub comparison failed: {exc}") from exc
    finally:
        github.close()

    _publish_import(output, prs, {**audit, "evaluation": aacr_summary(prs)})
    return 0


def _load_swepr(args: argparse.Namespace) -> int:
    salt = os.environ.get("C3_ANONYMIZATION_SALT")
    if not salt or not salt.strip():
        raise ValueError("Set C3_ANONYMIZATION_SALT in the environment before loading D3")
    settings = load_settings()
    output = settings.data_dir / "prs" / "D3"
    _check_import_output(output)
    prs, audit = load_swepr_with_audit(
        args.path, {language.strip() for language in args.languages.split(",")},
        args.python_sample, settings.seed, evaluated_only=not args.include_unevaluated,
    )
    sanitized, _ = anonymize(prs, salt)
    _publish_import(output, sanitized, audit)
    return 0


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


def _classify(args: argparse.Namespace) -> int:
    settings = load_settings()
    source = settings.data_dir / "prs" / args.dataset
    output = settings.data_dir / "classified" / args.dataset
    _check_import_output(output)
    files = sorted(path for path in source.glob("*.json") if path.name != "_meta.json")
    if not files:
        raise ValueError(f"No PR records found in {source}")
    spec = settings.reviewers.get(args.reviewer)
    if spec is None:
        raise ValueError(f"Unknown reviewer {args.reviewer!r}")

    prs = []
    filenames = {"_meta.json"}
    for path in files:
        try:
            pr = load_pr(path)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid PR record in {path}") from exc
        if pr.dataset != args.dataset:
            raise ValueError(f"Expected dataset {args.dataset} in {path}, got {pr.dataset!r}")
        filename = f"{pr.pr_id.replace('/', '__')}.json"
        if filename in filenames:
            raise ValueError(f"Duplicate or reserved PR output filename: {filename}")
        filenames.add(filename)
        prs.append(pr)

    runner = make_runner(args.reviewer, settings)
    retained = []
    dropped = 0
    for pr in prs:
        try:
            classified = classify_references(runner, pr)
        except ValueError as exc:
            raise ValueError(f"{args.dataset} classification failed for {pr.pr_id} "
                             f"(1 error, {dropped} dropped so far): {exc}") from exc
        if pr.dataset == "D2":
            classified = keep_actionable(classified)
        if classified is None:
            dropped += 1
        else:
            retained.append(classified)
    if not retained:
        raise ValueError(f"No {args.dataset} PRs retained after classification ({dropped} dropped)")

    _publish_import(output, retained, {
        "dataset": args.dataset,
        "reviewer": args.reviewer,
        "backend": spec.backend,
        "model": spec.model,
        "input_pr_count": len(prs),
        "retained_pr_count": len(retained),
        "dropped_pr_count": dropped,
        "error_count": 0,
        "retained_pr_ids": [pr.pr_id for pr in retained],
    })
    return 0


def _record_path(source: Path, pr: PullRequest, dataset: str) -> Path:
    manifest_path = source / "_meta.json"
    if not manifest_path.is_file():
        raise ValueError(f"Missing {manifest_path} dataset manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid dataset manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid dataset manifest: {manifest_path}")
    if manifest.get("dataset") not in (None, dataset):
        raise ValueError(f"Manifest dataset does not match {dataset}: {manifest_path}")
    return manifest


def _load_review_prs(settings, dataset: str) -> tuple[Path, list[PullRequest]]:
    raw = settings.data_dir / "prs" / dataset
    classified = settings.data_dir / "classified" / dataset
    source = classified if classified.is_dir() else raw
    if dataset == "D2" and not classified.is_dir():
        raise ValueError(f"D2 review requires complete {classified} classification first")
    if not source.is_dir():
        raise ValueError(f"No {dataset} PR records found in {source}")
    manifest = _record_path(source, None, dataset)
    paths = sorted(path for path in source.glob("*.json") if path.name != "_meta.json")
    expected = (manifest.get("retained_pr_ids") if dataset == "D2"
                else manifest.get("retained_pr_ids") or manifest.get("selected_pr_ids"))
    if isinstance(expected, list) and (
        not all(isinstance(pr_id, str) and pr_id for pr_id in expected)
        or len(set(expected)) != len(expected)
    ):
        raise ValueError(f"{dataset} manifest contains invalid or duplicate PR IDs")
    if dataset == "D2" and not isinstance(expected, list):
        raise ValueError("D2 classified manifest must list retained_pr_ids")
    if source == classified:
        retained_count = manifest.get("retained_pr_count")
        if retained_count is not None and retained_count != len(expected or []):
            raise ValueError(f"{dataset} classified manifest has an inconsistent retained PR count")
    if dataset == "D2":
        raw_manifest = _record_path(raw, None, dataset)
        selected = raw_manifest.get("selected_pr_ids")
        raw_paths = sorted(path for path in raw.glob("*.json") if path.name != "_meta.json")
        selected_files = {f"{pr_id.replace('/', '__')}.json" for pr_id in selected
                          if isinstance(pr_id, str)} if isinstance(selected, list) else set()
        if (not isinstance(selected, list)
                or not all(isinstance(pr_id, str) and pr_id for pr_id in selected)
                or len(set(selected)) != len(selected)
                or {path.name for path in raw_paths} != selected_files or not selected
                or manifest.get("input_pr_count") != len(selected)
                or manifest.get("retained_pr_count") != len(expected)
                or manifest.get("error_count") != 0
                or not set(expected).issubset(set(selected))):
            raise ValueError("D2 raw and classified manifests do not describe a complete matching collection")
    if isinstance(expected, list):
        expected_set = set(expected)
        actual_files = {path.name for path in paths}
        filenames = {f"{pr_id.replace('/', '__')}.json" for pr_id in expected if isinstance(pr_id, str)}
        if len(expected_set) != len(expected) or actual_files != filenames:
            raise ValueError(f"{dataset} records do not match manifest-listed PR IDs")
    elif dataset == "D1":
        evaluation_count = manifest.get("evaluation", {}).get("prs") if isinstance(manifest.get("evaluation"), dict) else None
        if not isinstance(evaluation_count, int) or len(paths) != evaluation_count:
            raise ValueError("D1 manifest must list PR IDs or provide a matching evaluation PR count")
    elif dataset == "D3":
        raise ValueError("D3 manifest must list selected_pr_ids")
    if not paths:
        raise ValueError(f"No {dataset} PR records found in {source}")
    prs = []
    seen = set()
    for path in paths:
        try:
            pr = load_pr(path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid PR record in {path}") from exc
        if (pr.dataset != dataset or pr.pr_id in seen or not _REPO.fullmatch(pr.repo)
                or isinstance(pr.number, bool) or not isinstance(pr.number, int) or pr.number < 1
                or pr.pr_id != f"{pr.repo}#{pr.number}"):
            raise ValueError(f"Unexpected dataset or duplicate PR ID in {path}")
        if path.name != f"{pr.pr_id.replace('/', '__')}.json":
            raise ValueError(f"PR filename does not match PR ID: {path.name}")
        seen.add(pr.pr_id)
        prs.append(pr)
    if isinstance(expected, list) and set(expected) != seen:
        raise ValueError(f"{dataset} PR IDs do not match the manifest")
    return source, prs


def _probe_review_version(runner) -> str | None:
    """Probe local CLI identity only; never performs model inference."""
    version = getattr(runner, "tool_version", None)
    if version:
        return str(version)
    if type(runner).__name__ not in {"ClaudeCliRunner", "CodexCliRunner"}:
        return None
    executable = "claude" if type(runner).__name__ == "ClaudeCliRunner" else "codex"
    exec_fn = getattr(runner, "exec_fn", subprocess.run)
    try:
        result = exec_fn([executable, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    runner.tool_version = result.stdout.strip()
    return runner.tool_version or None


def _atomic_record(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_review_record(record: object, pr: PullRequest, reviewer: str,
                         mode: str, run_idx: int) -> bool:
    if not isinstance(record, dict):
        return False
    if (record.get("pr_id") != pr.pr_id or record.get("reviewer") != reviewer
            or record.get("mode") != mode or record.get("run_idx") != run_idx
            or record.get("error") is not None or record.get("parse_error") is not False
            or not isinstance(record.get("meta"), dict)
            or not isinstance(record.get("comments"), list)):
        return False
    for comment in record["comments"]:
        if not isinstance(comment, dict) or set(comment) != {"path", "line", "comment", "category"}:
            return False
        file, line = comment["path"], comment["line"]
        if (not isinstance(file, str) or not file or "\\" in file or "\x00" in file
                or PurePosixPath(file).is_absolute() or PureWindowsPath(file).drive
                or any(part in {"", ".", ".."} for part in file.split("/"))):
            return False
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            return False
        if not isinstance(comment["comment"], str) or comment["category"] not in CATEGORIES:
            return False
    return True


def _review(args: argparse.Namespace) -> int:
    if args.runs < 1:
        raise ValueError("--runs must be positive")
    settings = load_settings()
    spec = settings.reviewers.get(args.reviewer)
    if spec is None:
        raise ValueError(f"Unknown reviewer {args.reviewer!r}")
    if not spec.model.strip():
        raise ValueError(f"Set a pinned model for reviewer {args.reviewer}")
    if args.mode == "agent" and spec.backend in {"deepseek_api", "openai_api"}:
        raise ValueError(f"Agent mode is unsupported for {spec.backend}")
    source, prs = _load_review_prs(settings, args.dataset)
    runner = make_runner(args.reviewer, settings)
    tool_version = _probe_review_version(runner)
    if type(runner).__name__ in {"ClaudeCliRunner", "CodexCliRunner"} and not tool_version:
        raise ValueError(f"Cannot establish {args.reviewer} CLI version; review preflight failed")
    cache = CachedRunner(runner, settings.results_dir / "review-cache", "review")
    output = settings.data_dir / "reviews" / args.dataset / args.reviewer / args.mode
    if type(runner).__name__ == "ClaudeCliRunner":
        flags = (CLAUDE_AGENT_READONLY if args.mode == "agent" else CLAUDE_DIFF_ONLY) + CLAUDE_JSON_OUTPUT
    elif type(runner).__name__ == "CodexCliRunner":
        flags = CODEX_AGENT_READONLY + CODEX_JSON_OUTPUT if args.mode == "agent" else []
    else:
        flags = []
    with TemporaryDirectory(prefix="c3-review-") as temp_root:
        for pr in prs:
            for run_idx in range(1, args.runs + 1):
                path = output / f"run{run_idx}" / f"{pr.pr_id.replace('/', '__')}.json"
                system, user = build_review_prompt(pr, args.mode)
                fingerprint_input = {
                    "dataset": args.dataset, "pr_id": pr.pr_id, "repo": pr.repo,
                    "base_sha": pr.base_sha, "head_sha": pr.review_sha, "diff": pr.diff,
                    "mode": args.mode, "reviewer": args.reviewer,
                    "backend": spec.backend, "model": spec.model,
                    "runner": type(runner).__name__, "tool_version": tool_version,
                    "system": system, "user": user, "run_idx": run_idx,
                    "allow_read_tools": args.mode == "agent", "flags": flags,
                }
                fingerprint = sha256(json.dumps(fingerprint_input, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
                if path.exists():
                    try:
                        old = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        raise ValueError(f"Cannot resume invalid review artifact {path}") from exc
                    if not isinstance(old, dict):
                        raise ValueError(f"Invalid review artifact {path}; choose a new data directory")
                    if old.get("fingerprint") != fingerprint:
                        raise ValueError(f"Review fingerprint mismatch in {path}; choose a new data directory")
                    if _valid_review_record(old, pr, args.reviewer, args.mode, run_idx):
                        continue

                workdir = None
                if args.mode == "diff":
                    workdir = Path(tempfile.mkdtemp(prefix="diff-", dir=temp_root))
                else:
                    identity = sha256(pr.pr_id.encode()).hexdigest()[:16]
                    workdir = prepare_checkout(f"https://github.com/{pr.repo}.git", pr.review_sha,
                                               Path(temp_root) / f"agent-{identity}-{run_idx}")
                result = run_review(cache, pr, args.reviewer, args.mode, run_idx, workdir)
                record = asdict(result)
                record.update(fingerprint=fingerprint, fingerprint_input=fingerprint_input,
                              source=str(source))
                _atomic_record(record, path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="c3", description="C3 code review research pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    aacr = commands.add_parser("load-aacr", help="Load AACR-Bench v1 Java PRs into D1")
    aacr.add_argument("--positive", required=True, type=Path)
    aacr.add_argument("--negative", required=True, type=Path)
    aacr.set_defaults(run=_load_aacr)
    swepr = commands.add_parser("load-swepr", help="Load local SWE-PRBench PRs into D3")
    swepr.add_argument("--path", required=True, type=Path, help="Path to evals/eval_100.json or prs.jsonl")
    swepr.add_argument("--languages", default="Java,Python", help="Comma-separated dataset language names")
    swepr.add_argument("--python-sample", required=True, type=int, help="Maximum number of Python PRs to sample")
    swepr.add_argument("--include-unevaluated", action="store_true", help="Allow records outside the evaluated split")
    swepr.set_defaults(run=_load_swepr)
    collect = commands.add_parser("collect", help="Collect anonymized D2 PRs")
    collect.add_argument("--repos", required=True, help="Comma-separated owner/repo names")
    collect.add_argument("--since", required=True, type=date.fromisoformat)
    collect.add_argument("--until", required=True, type=date.fromisoformat)
    collect.add_argument("--n-per-repo", required=True, type=int)
    collect.set_defaults(run=_collect)
    classify = commands.add_parser("classify", help="Classify human references from prs/ into classified/")
    classify.add_argument("--dataset", required=True, choices=("D1", "D2", "D3"))
    classify.add_argument("--reviewer", required=True, help="Configured reviewer with tools disabled")
    classify.set_defaults(run=_classify)
    review = commands.add_parser("review", help="Run or resume AI code reviews")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--mode", choices=("diff", "agent"), required=True)
    review.add_argument("--runs", required=True, type=int)
    review.add_argument("--dataset", choices=("D1", "D2", "D3"), required=True)
    review.set_defaults(run=_review)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError) as exc:
        print(f"c3: {exc}", file=sys.stderr)
        return 1
