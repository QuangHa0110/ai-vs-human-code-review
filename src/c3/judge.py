"""Match AI review comments to valid human references."""

import json
import random
from dataclasses import dataclass

from c3.models import PullRequest
from c3.review import ReviewResult


@dataclass(frozen=True)
class Match:
    ref_id: str
    ai_index: int


@dataclass
class Judgment:
    pr_id: str
    reviewer: str
    mode: str
    run_idx: int
    judge: str
    matches: list[Match]
    invalid: int

    @property
    def matched_ref_ids(self) -> set[str]:
        return {match.ref_id for match in self.matches}

    @property
    def matched_ai_indices(self) -> set[int]:
        return {match.ai_index for match in self.matches}


SYSTEM_PROMPT = """Decide which AI review comments identify the same issue as a human reference.
Return only a JSON array of pairs. Each pair must be [ref_id, ai_index], where ai_index
is zero-based. Use only the supplied references and comments. Return [] when none match."""


def build_judge_prompt(pr: PullRequest, review: ReviewResult) -> tuple[str, str]:
    refs = [
        {"ref_id": ref.ref_id, "text": ref.text, "path": ref.path, "line": ref.line}
        for ref in pr.references if ref.valid is True
    ]
    comments = [
        {"ai_index": index, "text": comment.comment, "path": comment.path, "line": comment.line}
        for index, comment in enumerate(review.comments)
    ]
    user = "Human references:\n" + json.dumps(refs, ensure_ascii=False)
    user += "\n\nAI comments:\n" + json.dumps(comments, ensure_ascii=False)
    return SYSTEM_PROMPT, user


def _parse_matches(text: str) -> list[list[object]]:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid judge output: expected a JSON array of pairs") from exc
    if not isinstance(payload, list) or any(
        not isinstance(pair, list) or len(pair) != 2 for pair in payload
    ):
        raise ValueError("invalid judge output: expected a JSON array of pairs")
    return payload


def judge_pr(runner, pr: PullRequest, review: ReviewResult, judge: str) -> Judgment:
    if review.error is not None or review.parse_error:
        raise ValueError("cannot judge a failed review result")
    if not review.comments:
        return Judgment(pr.pr_id, review.reviewer, review.mode, review.run_idx, judge, [], 0)

    system, user = build_judge_prompt(pr, review)
    output = runner.run(system, user, None, False)
    if output.error is not None:
        raise ValueError("judge runner failed")
    pairs = _parse_matches(output.text)
    valid_refs = {ref.ref_id for ref in pr.references if ref.valid is True}
    matches = []
    seen = set()
    invalid = 0
    for ref_id, ai_index in pairs:
        if (not isinstance(ref_id, str) or ref_id not in valid_refs
                or isinstance(ai_index, bool) or not isinstance(ai_index, int)
                or ai_index < 0 or ai_index >= len(review.comments)):
            invalid += 1
            continue
        pair = (ref_id, ai_index)
        if pair not in seen:
            seen.add(pair)
            matches.append(Match(*pair))
    return Judgment(pr.pr_id, review.reviewer, review.mode, review.run_idx, judge, matches, invalid)


def validation_subset(pr_ids: list[str], fraction: float, seed: int = 0) -> list[str]:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if not all(isinstance(pr_id, str) and pr_id for pr_id in pr_ids):
        raise ValueError("PR IDs must be nonempty strings")
    if len(set(pr_ids)) != len(pr_ids):
        raise ValueError("PR IDs must be unique")
    count = int(len(pr_ids) * fraction)
    if fraction > 0 and pr_ids and count == 0:
        count = 1
    return sorted(random.Random(seed).sample(sorted(pr_ids), count))
