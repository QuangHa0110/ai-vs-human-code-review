"""Select and anonymize D2 candidate pull requests."""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import random
import re

from c3.comments import clean_comments
from c3.diffutil import changed_lines, filter_java
from c3.github_client import GitHubClient
from c3.models import PullRequest

_MENTION = re.compile(
    r"(?<![^\W_])(?<![@.+-])@(?P<mention>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?i:\[bot\])?)"
    r"(?![^\W_])(?![/-])"
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def assess_candidate(
    gh: GitHubClient, repo: str, number: int, created_after: str,
) -> tuple[PullRequest | None, str | None]:
    """Build a candidate or report the first eligibility criterion it fails."""
    pr = gh.get_pr(repo, number)
    if pr["merged"] is not True:
        return None, "not_merged"
    if _timestamp(pr["created_at"]) <= _timestamp(created_after):
        return None, "created_at_cutoff"

    pr_id = f"{repo}#{number}"
    author = (pr.get("user") or {}).get("login") or ""
    raw = gh.review_comments(repo, number)
    cleaned = clean_comments(raw, author, pr_id)
    if cleaned.bot_ratio > 0.3:
        return None, "bot_ratio"
    if cleaned.review_sha is None or len(cleaned.kept) < 2:
        return None, "too_few_references"

    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        raise ValueError(f"Missing merge commit for {pr_id}")
    # The target branch may already contain the reviewed commit. Use the
    # first parent of the recorded merge result as the historical anchor.
    # For rebase merges this can be a rebased PR commit.
    historical_target = gh.first_parent(repo, merge_sha)
    base_sha = gh.merge_base(repo, historical_target, cleaned.review_sha)
    if base_sha == cleaned.review_sha:
        raise ValueError(f"Cannot recover the historical review base for {pr_id}")
    diff = filter_java(gh.compare_diff(repo, base_sha, cleaned.review_sha))
    if not diff:
        return None, "empty_java_diff"
    if changed_lines(diff) > 500:
        return None, "java_diff_too_large"

    candidate = PullRequest(
        pr_id=pr_id, dataset="D2", repo=repo, number=number,
        title=pr.get("title") or "", body=pr.get("body") or "",
        base_sha=base_sha, review_sha=cleaned.review_sha, diff=diff,
        references=cleaned.kept,
    )
    logins = {(comment.get("user") or {}).get("login") for comment in raw}
    # save_json serializes only the declared dataclass fields.
    candidate._reviewer_logins = {
        login.casefold() for login in logins
        if login and login.casefold() != author.casefold()
    }
    return candidate, None


def build_candidate(
    gh: GitHubClient, repo: str, number: int, created_after: str,
) -> PullRequest | None:
    return assess_candidate(gh, repo, number, created_after)[0]


def sample_prs(
    candidates: list[PullRequest], n_per_repo: int, seed: int,
) -> list[PullRequest]:
    if n_per_repo < 0:
        raise ValueError("n_per_repo must be nonnegative")
    by_repo = defaultdict(list)
    for pr in sorted(candidates, key=lambda pr: pr.pr_id):
        by_repo[pr.repo].append(pr)
    rng = random.Random(seed)
    sampled = []
    for repo in sorted(by_repo):
        stratum = by_repo[repo]
        sampled.extend(rng.sample(stratum, min(len(stratum), n_per_repo)))
    return sorted(sampled, key=lambda pr: pr.pr_id)


def anonymize(prs: list[PullRequest], salt: str) -> tuple[list[PullRequest], dict[str, str]]:
    """Return new records and an in-memory mapping of logins to pseudonyms."""
    logins = set()
    for pr in prs:
        logins.update(getattr(pr, "_reviewer_logins", ()))
        for text in (pr.title, pr.body, *(ref.text for ref in pr.references)):
            logins.update(match["mention"].casefold() for match in _MENTION.finditer(text))

    mapping = {
        login: "reviewer_" + sha256((salt + "\0" + login).encode()).hexdigest()[:8]
        for login in sorted(logins)
    }
    if not mapping:
        return [
            replace(pr, references=[replace(ref) for ref in pr.references])
            for pr in prs
        ], mapping

    alternatives = "|".join(re.escape(name) for name in sorted(mapping, key=lambda name: (-len(name), name)))
    identities = re.compile(
        rf"{_MENTION.pattern}|(?<![A-Za-z0-9-])(?P<login>(?ai:{alternatives}))(?![A-Za-z0-9-])"
    )

    def pseudonym(match: re.Match[str]) -> str:
        if match["mention"]:
            return "@" + mapping[match["mention"].casefold()]
        return mapping[match["login"].casefold()]

    def redact(text: str) -> str:
        return identities.sub(pseudonym, text)

    return [
        replace(
            pr, title=redact(pr.title), body=redact(pr.body),
            references=[replace(ref, text=redact(ref.text)) for ref in pr.references],
        )
        for pr in prs
    ], mapping
