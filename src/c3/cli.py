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
from c3.annotation import (NOISE_CAUSES, assign_raters, calibration_items,
                           merge_labels, unmatched_items)
from c3.classify import classify_references, keep_actionable
from c3.checkout import prepare_checkout
from c3.cli_flags import (
    CLAUDE_AGENT_READONLY, CLAUDE_DIFF_ONLY, CLAUDE_JSON_OUTPUT,
    CODEX_AGENT_READONLY, CODEX_JSON_OUTPUT,
)
from c3.config import load_settings
from c3.diffutil import filter_java
from c3.github_client import GitHubClient
from c3.judge import build_judge_prompt, judge_pr, validation_subset
from c3.models import AiComment, CATEGORIES, PullRequest, load_pr, save_json
from c3.power import mcnemar_n, required_prs
from c3.review import ReviewResult, build_review_prompt, run_review
from c3.report import analyze as analyze_report
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


def _review_identity_matches(record: object, pr: PullRequest, reviewer: str,
                             mode: str, run_idx: int, source: Path) -> bool:
    if not isinstance(record, dict) or record.get("source") != str(source):
        return False
    if (record.get("pr_id") != pr.pr_id or record.get("reviewer") != reviewer
            or record.get("mode") != mode or record.get("run_idx") != run_idx
            or not isinstance(record.get("fingerprint"), str)):
        return False
    identity = record.get("fingerprint_input")
    if not isinstance(identity, dict):
        return False
    expected = {"dataset": pr.dataset, "pr_id": pr.pr_id, "repo": pr.repo,
                "base_sha": pr.base_sha, "head_sha": pr.review_sha, "diff": pr.diff,
                "mode": mode, "reviewer": reviewer, "run_idx": run_idx}
    if any(identity.get(key) != value for key, value in expected.items()):
        return False
    fingerprint = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return record.get("fingerprint") == fingerprint


def _judge(args: argparse.Namespace) -> int:
    settings = load_settings()
    spec = settings.reviewers.get(args.judge)
    if spec is None:
        raise ValueError(f"Unknown judge {args.judge!r}")
    if not spec.model.strip():
        raise ValueError(f"Set a pinned model for judge {args.judge}")
    source, prs = _load_review_prs(settings, args.dataset)
    if args.run_idx < 1:
        raise ValueError("--run must be positive")
    selected = validation_subset([pr.pr_id for pr in prs], 0.2, settings.seed) if args.validation_only else [
        pr.pr_id for pr in prs
    ]
    by_id = {pr.pr_id: pr for pr in prs}
    output = settings.data_dir / "judgments" / args.dataset / args.judge / args.reviewer / args.mode / f"run{args.run_idx}"
    review_dir = settings.data_dir / "reviews" / args.dataset / args.reviewer / args.mode / f"run{args.run_idx}"
    skipped = Counter()
    runner = None
    summary_identity = {"dataset": args.dataset, "judge": args.judge, "reviewer": args.reviewer,
                        "mode": args.mode, "run_idx": args.run_idx, "selected_pr_ids": selected,
                        "source": str(source)}
    summary_fingerprint = sha256(json.dumps(summary_identity, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    summary_path = output / "_meta.json"
    if summary_path.exists():
        try:
            old_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot resume invalid judgment summary {summary_path}") from exc
        if (not isinstance(old_summary, dict)
                or any(old_summary.get(key) != value for key, value in summary_identity.items())
                or old_summary.get("fingerprint") != summary_fingerprint):
            raise ValueError(f"Judgment fingerprint mismatch in {summary_path}; choose a new data directory")

    for pr_id in selected:
        pr = by_id[pr_id]
        review_path = review_dir / f"{pr.pr_id.replace('/', '__')}.json"
        if not review_path.is_file():
            raise ValueError(f"Missing saved review artifact: {review_path}")
        try:
            review_record = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid saved review artifact: {review_path}") from exc
        if not _review_identity_matches(review_record, pr, args.reviewer, args.mode, args.run_idx, source):
            raise ValueError(f"Review fingerprint or PR identity mismatch in {review_path}")
        judgment_path = output / f"{pr.pr_id.replace('/', '__')}.json"
        if review_record.get("error") is not None:
            if judgment_path.exists():
                raise ValueError(f"Review failed but an existing judgment artifact remains at {judgment_path}")
            skipped["run_error"] += 1
            continue
        if review_record.get("parse_error") is True:
            if judgment_path.exists():
                raise ValueError(f"Review parse failed but an existing judgment artifact remains at {judgment_path}")
            skipped["parse_error"] += 1
            continue
        if not _valid_review_record(review_record, pr, args.reviewer, args.mode, args.run_idx):
            raise ValueError(f"Invalid saved review artifact: {review_path}")
        review = ReviewResult(pr.pr_id, args.reviewer, args.mode, args.run_idx,
                              [AiComment(**item) for item in review_record["comments"]],
                              review_record["meta"])
        system, user = build_judge_prompt(pr, review)
        if review.comments and runner is None:
            runner = make_runner(args.judge, settings)
        fingerprint_input = {"dataset": args.dataset, "pr_id": pr.pr_id, "reviewer": args.reviewer,
                             "mode": args.mode, "run_idx": args.run_idx, "judge": args.judge,
                             "backend": spec.backend, "model": spec.model,
                             "runner": type(runner).__name__ if runner is not None else None,
                             "tool_version": getattr(runner, "tool_version", None),
                             "system": system, "user": user,
                             "review_fingerprint": review_record["fingerprint"]}
        judgment_fingerprint = sha256(json.dumps(fingerprint_input, sort_keys=True,
                                                 ensure_ascii=False).encode()).hexdigest()
        if judgment_path.exists():
            try:
                old = json.loads(judgment_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"Cannot resume invalid judgment artifact {judgment_path}") from exc
            valid_refs = {ref.ref_id for ref in pr.references if ref.valid is True}
            valid_matches = isinstance(old, dict) and isinstance(old.get("matches"), list)
            seen_matches = set()
            if valid_matches:
                for match in old["matches"]:
                    if (not isinstance(match, dict) or set(match) != {"ref_id", "ai_index"}
                            or not isinstance(match["ref_id"], str)
                            or match["ref_id"] not in valid_refs
                            or isinstance(match["ai_index"], bool)
                            or not isinstance(match["ai_index"], int)
                            or not 0 <= match["ai_index"] < len(review.comments)
                            or (match["ref_id"], match["ai_index"]) in seen_matches):
                        valid_matches = False
                        break
                    seen_matches.add((match["ref_id"], match["ai_index"]))
            invalid_count = old.get("invalid") if isinstance(old, dict) else None
            expected_record_identity = {
                "pr_id": pr.pr_id, "reviewer": args.reviewer, "mode": args.mode,
                "run_idx": args.run_idx, "judge": args.judge,
                "review_fingerprint": review_record["fingerprint"], "source": str(source),
            }
            old_input = old.get("fingerprint_input") if isinstance(old, dict) else None
            old_digest = (sha256(json.dumps(old_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                          if isinstance(old_input, dict) else None)
            if (not isinstance(old, dict)
                    or any(old.get(key) != value for key, value in expected_record_identity.items())
                    or old.get("fingerprint") != judgment_fingerprint
                    or old_input != fingerprint_input or old_digest != old.get("fingerprint")
                    or not valid_matches
                    or isinstance(invalid_count, bool) or not isinstance(invalid_count, int)
                    or invalid_count < 0):
                raise ValueError(f"Invalid existing judgment artifact {judgment_path}; choose a new data directory")
            if old.get("pr_id") == pr.pr_id and old.get("review_fingerprint") == review_record["fingerprint"]:
                continue
        result = judge_pr(runner, pr, review, args.judge)
        record = asdict(result)
        record.update(fingerprint=judgment_fingerprint, fingerprint_input=fingerprint_input,
                      review_fingerprint=review_record["fingerprint"], source=str(source))
        _atomic_record(record, judgment_path)
    summary = {**summary_identity, "fingerprint": summary_fingerprint,
               "selected_count": len(selected), "skipped_run_error": skipped["run_error"],
               "skipped_parse_error": skipped["parse_error"]}
    _atomic_record(summary, summary_path)
    print(json.dumps({"judged": len(selected) - sum(skipped.values()), **{
        key: value for key, value in summary.items() if key.startswith("skipped_")
    }}))
    return 0


def _power(args: argparse.Namespace) -> int:
    """Plan sample size from user-supplied pilot estimates; do not infer a pilot."""
    settings = load_settings()
    pilot = {"p_discordant": args.p_discordant,
             "refs_per_pr": args.refs_per_pr,
             "design_effect": args.design_effect}
    alpha, delta, target_power = 0.05 / 3, args.delta, args.target_power
    n_refs = mcnemar_n(pilot["p_discordant"], delta, alpha, target_power)
    n_prs = required_prs(n_refs, pilot["refs_per_pr"], pilot["design_effect"])
    record = {
        "pilot": pilot,
        "assumptions": {"alpha": alpha, "delta": delta, "power": target_power,
                        "multiplicity": "Bonferroni alpha=0.05/3 for three pairwise comparisons"},
        "n_refs": n_refs,
        "n_prs": n_prs,
    }
    output = settings.results_dir / "power.json"
    if output.exists():
        try:
            old = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot resume invalid power plan {output}") from exc
        if old != record:
            raise ValueError("results/power.json contains a different power plan; choose a new C3_RESULTS_DIR")
        return 0
    _atomic_record(record, output)
    print(json.dumps({"n_refs": n_refs, "n_prs": n_prs}))
    return 0


def _analyze(args: argparse.Namespace) -> int:
    settings = load_settings()
    outputs = analyze_report(settings, args.labels_dir)
    print(json.dumps({name: str(path) for name, path in outputs.items()}, ensure_ascii=False))
    return 0


def _load_annotation_artifacts(settings, dataset, reviewers, source, prs):
    """Load only fingerprint-bound, successful review/judgment pairs."""
    from c3.report import _valid_judgment
    by_pr = {pr.pr_id: pr for pr in prs}
    reviews, judgments = {}, {}
    for reviewer in sorted(reviewers):
        review_root = settings.data_dir / "reviews" / dataset / reviewer
        for mode in ("diff",):
            mode_root = review_root / mode
            if not mode_root.is_dir():
                continue
            for run_dir in sorted(mode_root.glob("run*")):
                match = re.fullmatch(r"run([1-9][0-9]*)", run_dir.name)
                if not match:
                    continue
                run_idx = int(match.group(1))
                if run_idx != 1:
                    continue
                for pr in prs:
                    review_path = run_dir / f"{pr.pr_id.replace('/', '__')}.json"
                    if not review_path.is_file():
                        continue
                    try:
                        record = json.loads(review_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if (not _review_identity_matches(record, pr, reviewer, mode, run_idx, source)
                            or record.get("error") is not None or record.get("parse_error") is not False
                            or not _valid_review_record(record, pr, reviewer, mode, run_idx)):
                        continue
                    key = (reviewer, pr.pr_id, mode, run_idx)
                    valid_judgments = []
                    judgment_root = settings.data_dir / "judgments" / dataset
                    for judge_dir in sorted(judgment_root.iterdir()) if judgment_root.is_dir() else []:
                        judgment_path = (judge_dir / reviewer / mode / f"run{run_idx}"
                                         / f"{pr.pr_id.replace('/', '__')}.json")
                        if not judgment_path.is_file():
                            continue
                        try:
                            judgment_record = json.loads(judgment_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        judgment = _valid_judgment(judgment_record, pr, record, reviewer, mode,
                                                   run_idx, judge_dir.name, source)
                        if judgment is not None:
                            valid_judgments.append((judge_dir.name, judgment_record))
                    if not valid_judgments:
                        continue
                    # Identical validated judgments from different judges represent one matching set.
                    matches = valid_judgments[0][1]["matches"]
                    if any(item[1]["matches"] != matches for item in valid_judgments[1:]):
                        continue
                    reviews[key] = record
                    judgments[key] = {"pr_id": pr.pr_id, "matches": matches}
    return reviews, judgments


def _safe_annotation_paths(output_dir: Path, private_dir: Path, key_name: str) -> None:
    output_path, private_path = output_dir.resolve(), private_dir.resolve()
    if (output_path == private_path or output_path in private_path.parents
            or private_path in output_path.parents):
        raise ValueError("public and private annotation directories must be separate and non-nested")
    if output_dir.is_symlink() or (output_dir.exists()
                                   and (not output_dir.is_dir() or any(output_dir.iterdir()))):
        raise ValueError(f"{output_dir} output is occupied; choose a new annotation directory")
    if private_dir.is_symlink() or (private_dir.exists() and not private_dir.is_dir()):
        raise ValueError(f"{private_dir} is not a safe private directory")
    private_key = private_dir / key_name
    if private_key.is_symlink() or private_key.exists():
        raise ValueError(f"{private_key} already exists; choose a new private key filename")


def _write_annotation_handoff(items, raters, seed, output_dir, private_dir, key_name, private_key):
    _safe_annotation_paths(output_dir, private_dir, key_name)
    assignments = assign_raters([item.item_id for item in items], raters, seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    for index, rater in enumerate(sorted(raters), 1):
        filename = f"rater-{index:02d}.json"
        assigned = set(item_id for item_id, pair in assignments.items() if rater in pair)
        payload = {"schema": "c3-annotation-v1",
                   "items": [asdict(item) for item in items if item.item_id in assigned]}
        _atomic_record(payload, output_dir / filename)
        _atomic_record({"item_ids": sorted(assigned)}, output_dir / "assignments" / filename)
    _atomic_record({"items": private_key, "assignments": assignments,
                    "raters": sorted(raters)}, private_dir / key_name)


def _sample_annotations(args: argparse.Namespace) -> int:
    settings = load_settings()
    source, prs = _load_review_prs(settings, args.dataset)
    reviewers = [part.strip() for part in args.reviewers.split(",") if part.strip()]
    raters = [part.strip() for part in args.raters.split(",") if part.strip()]
    if not reviewers or len(set(reviewers)) != len(reviewers) or not raters or len(set(raters)) != len(raters):
        raise ValueError("reviewers and raters must be unique nonempty comma-separated names")
    if args.dataset == "D2" and args.per_reviewer is None:
        raise ValueError("--per-reviewer is required for D2")
    if args.dataset == "D1" and args.per_reviewer is not None:
        raise ValueError("D1 annotations use all eligible comments; omit --per-reviewer")
    reviews, judgments = _load_annotation_artifacts(settings, args.dataset, reviewers, source, prs)
    items, private_key = unmatched_items(prs, reviews, judgments, reviewers,
                                         args.per_reviewer, args.seed if args.seed is not None else settings.seed)
    _write_annotation_handoff(items, raters, args.seed if args.seed is not None else settings.seed,
                              args.output_dir, args.private_dir, "annotation-key.json", private_key)
    print(json.dumps({"items": len(items), "raters": len(raters), "output_dir": str(args.output_dir)}))
    return 0


def _calibration_set(args: argparse.Namespace) -> int:
    settings = load_settings()
    _, prs = _load_review_prs(settings, "D1")
    items, labels = calibration_items(prs, args.n, args.seed if args.seed is not None else settings.seed)
    raters = [part.strip() for part in args.raters.split(",") if part.strip()]
    if not raters or len(set(raters)) != len(raters):
        raise ValueError("raters must be unique nonempty comma-separated names")
    private_key = {item_id: {"label": label} for item_id, label in labels.items()}
    _write_annotation_handoff(items, raters, args.seed if args.seed is not None else settings.seed,
                              args.output_dir, args.private_dir, "calibration-key.json", private_key)
    print(json.dumps({"items": len(items), "raters": len(raters), "output_dir": str(args.output_dir)}))
    return 0


def _merge_annotation_labels(args: argparse.Namespace) -> int:
    private_path = args.private_dir / "annotation-key.json"
    private_payload = json.loads(private_path.read_text(encoding="utf-8"))
    private_key = private_payload.get("items") if isinstance(private_payload, dict) else None
    private_assignments = private_payload.get("assignments") if isinstance(private_payload, dict) else None
    configured_raters = private_payload.get("raters") if isinstance(private_payload, dict) else None
    if (not isinstance(private_key, dict) or not isinstance(private_assignments, dict)
            or set(private_key) != set(private_assignments)
            or not isinstance(configured_raters, list)
            or not configured_raters
            or any(not isinstance(rater, str) or not rater for rater in configured_raters)):
        raise ValueError("invalid private annotation key")
    if len(configured_raters) != len(set(configured_raters)):
        raise ValueError("invalid private annotation key")
    if any(not isinstance(raters, list) or any(not isinstance(rater, str) or not rater for rater in raters)
           for raters in private_assignments.values()):
        raise ValueError("invalid private assignment schedule")
    rater_names = configured_raters
    if (len(rater_names) < 2 or any(not isinstance(raters, list) or len(raters) != 2
                                    or len(set(raters)) != 2 or any(r not in rater_names for r in raters)
                                    for raters in private_assignments.values())):
        raise ValueError("private assignment schedule must assign two distinct raters to every item")
    names_by_file = {f"rater-{index:02d}.json": name
                     for index, name in enumerate(rater_names, 1)}
    expected_files = set(names_by_file)
    actual_files = {path.name for path in args.labels_dir.glob("rater-*.json")}
    actual_assignments = {path.name for path in (args.labels_dir / "assignments").glob("rater-*.json")}
    if actual_files != expected_files or actual_assignments != expected_files:
        raise ValueError("rater files must exactly match the private assignment schedule")
    expected_ids = {name: set() for name in rater_names}
    for item_id, assigned in private_assignments.items():
        for rater in assigned:
            expected_ids[rater].add(item_id)
    rater_labels = {}
    assignments_dir = args.labels_dir / "assignments"
    for path in sorted(args.labels_dir.glob("rater-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assignment_path = assignments_dir / path.name
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        ids = assignment.get("item_ids") if isinstance(assignment, dict) else None
        labels = payload.get("labels") if isinstance(payload, dict) else None
        rater = names_by_file[path.name]
        public_items = payload.get("items") if isinstance(payload, dict) else None
        public_ids = ({item.get("item_id") for item in public_items if isinstance(item, dict)}
                      if isinstance(public_items, list) else set())
        if (not isinstance(ids, list) or len(ids) != len(set(ids))
                or set(ids) != expected_ids[rater] or not isinstance(labels, dict)
                or set(labels) != expected_ids[rater] or public_ids != expected_ids[rater]):
            raise ValueError(f"{path.name} must contain a label for each assigned item ID")
        rater_labels[rater] = labels
    manual = json.loads(args.adjudication.read_text(encoding="utf-8")) if args.adjudication else {}
    manual = manual.get("items", manual) if isinstance(manual, dict) else None
    if not isinstance(manual, dict):
        raise ValueError("adjudication file must be an item ID mapping")
    # Every final report item needs a human-reviewed missed_by_human value.
    merged = merge_labels(rater_labels, private_key, manual)
    if any(not isinstance(item.get("missed_by_human"), bool) for item in merged["items"]):
        raise ValueError("adjudication must manually supply boolean missed_by_human for every item")
    if any(item["label"] == "noise" and item.get("cause") not in NOISE_CAUSES for item in merged["items"]):
        raise ValueError("every noise label needs a cause from the annotation guide")
    if any(item["label"] == "insufficient" for item in merged["items"]):
        raise ValueError("insufficient items must be resolved or excluded before report merge")
    output = args.labels_dir / "adjudicated_labels.json"
    if output.exists():
        raise ValueError(f"{output} already exists; choose a new labels directory")
    _atomic_record(merged, output)
    print(str(output))
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
    judge = commands.add_parser("judge", help="Match saved AI reviews to human references")
    judge.add_argument("--judge", required=True)
    judge.add_argument("--dataset", choices=("D1", "D2", "D3"), required=True)
    judge.add_argument("--reviewer", required=True)
    judge.add_argument("--mode", choices=("diff", "agent"), required=True)
    judge.add_argument("--run", dest="run_idx", required=True, type=int)
    judge.add_argument("--validation-only", action="store_true")
    judge.set_defaults(run=_judge)
    power = commands.add_parser("power", help="Plan paired sample size from pilot estimates")
    power.add_argument("--p-discordant", type=float, required=True)
    power.add_argument("--refs-per-pr", type=float, required=True)
    power.add_argument("--design-effect", type=float, required=True)
    power.add_argument("--delta", type=float, default=0.10)
    power.add_argument("--target-power", type=float, default=0.8)
    power.set_defaults(run=_power)
    report = commands.add_parser("analyze", help="Analyze saved reviews and judgments")
    report.add_argument("--labels-dir", type=Path, help="Optional directory containing adjudicated_labels.json")
    report.set_defaults(run=_analyze)
    sample = commands.add_parser("sample", help="Export blinded items for manual noise annotation")
    sample.add_argument("--dataset", choices=("D1", "D2"), required=True)
    sample.add_argument("--reviewers", required=True, help="Comma-separated configured reviewer IDs")
    sample.add_argument("--per-reviewer", type=int, help="D2 sample size per reviewer")
    sample.add_argument("--raters", required=True, help="Comma-separated annotator names")
    sample.add_argument("--seed", type=int)
    sample.add_argument("--output-dir", type=Path, default=Path("results/annotation"))
    sample.add_argument("--private-dir", type=Path, default=Path("results/annotation-private"))
    sample.set_defaults(run=_sample_annotations)
    calibration = commands.add_parser("calibration-set", help="Export blinded D1 expert-reference calibration items")
    calibration.add_argument("--n", required=True, type=int, help="Even total, balanced across expert valid/negative refs")
    calibration.add_argument("--raters", required=True, help="Comma-separated annotator names")
    calibration.add_argument("--seed", type=int)
    calibration.add_argument("--output-dir", type=Path, default=Path("results/calibration"))
    calibration.add_argument("--private-dir", type=Path, default=Path("results/annotation-private"))
    calibration.set_defaults(run=_calibration_set)
    merge = commands.add_parser("merge-labels", help="Merge manually supplied rater and adjudication labels")
    merge.add_argument("--labels-dir", type=Path, required=True)
    merge.add_argument("--private-dir", type=Path, default=Path("results/annotation-private"))
    merge.add_argument("--adjudication", type=Path, required=True,
                       help="JSON item-ID map with adjudicated label, missed_by_human, and optional issue_id/cause")
    merge.set_defaults(run=_merge_annotation_labels)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError) as exc:
        print(f"c3: {exc}", file=sys.stderr)
        return 1
