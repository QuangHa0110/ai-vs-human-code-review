"""Blinded manual annotation, calibration, and rater agreement helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import itertools
import json
import math
import random
from collections.abc import Mapping

NOISE_LABELS = ("valid", "noise", "insufficient")
NOISE_CAUSES = ("lack_context", "over_sensitive", "vague_unactionable",
                "lack_domain", "anti_best_practice", "other")


@dataclass(frozen=True)
class AnnotationItem:
    item_id: str
    pr_id: str
    path: str
    line: int | None
    text: str
    pr_url: str


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _items(prs):
    if isinstance(prs, Mapping):
        return list(prs.values())
    return list(prs)


def _stable_id(seed, namespace, payload):
    raw = json.dumps([namespace, payload], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{seed}\0{raw}".encode()).hexdigest()[:20]


def _pr_url(pr):
    repo = _get(pr, "repo")
    number = _get(pr, "number")
    return f"https://github.com/{repo}/pull/{number}" if repo and number else ""


def _record_map(records):
    """Normalize nested reviewer→PR maps and tuple keyed artifact maps."""
    result = {}
    for key, value in records.items():
        if isinstance(key, tuple):
            reviewer, pr_id = key[:2]
            mode = key[2] if len(key) > 2 else _get(value, "mode")
            run_idx = key[3] if len(key) > 3 else _get(value, "run_idx")
            result[(str(reviewer), str(pr_id), mode, run_idx)] = value
        elif isinstance(value, Mapping) and not any(k in value for k in ("pr_id", "comments", "matches")):
            for pr_id, record in value.items():
                mode = _get(record, "mode")
                run_idx = _get(record, "run_idx")
                result[(str(key), str(pr_id), mode, run_idx)] = record
    return result


def unmatched_items(prs, reviews, judgments, reviewers, per_reviewer=None, seed=0):
    """Return blinded unmatched AI comments and a separate private item key."""
    pr_map = {str(_get(pr, "pr_id")): pr for pr in _items(prs)}
    review_map, judgment_map = _record_map(reviews), _record_map(judgments)
    if not isinstance(reviewers, (list, tuple)) or len(set(reviewers)) != len(reviewers):
        raise ValueError("reviewers must be a unique list")
    if per_reviewer is not None and (isinstance(per_reviewer, bool) or not isinstance(per_reviewer, int) or per_reviewer < 1):
        raise ValueError("per_reviewer must be a positive integer or None")
    available = []
    for (reviewer, pr_id, mode, run_idx), review in sorted(review_map.items(), key=lambda pair: str(pair[0])):
        if reviewer not in reviewers or pr_id not in pr_map:
            continue
        pr = pr_map[pr_id]
        if _get(pr, "dataset") in {"D1", "D2"} and (mode != "diff" or run_idx != 1):
            continue
        if _get(pr, "dataset") == "D2" and per_reviewer is None:
            raise ValueError("D2 requires per_reviewer sampling")
        if not _valid_review_for_annotation(review, pr_id, reviewer):
            continue
        judgment = judgment_map.get((reviewer, pr_id, mode, run_idx))
        if judgment is None:
            judgment = judgment_map.get((reviewer, pr_id, None, None))
        if not _valid_judgment_for_annotation(judgment, pr_id):
            continue
        matched = {item["ai_index"] for item in judgment["matches"]}
        for index, comment in enumerate(review["comments"]):
            if index in matched:
                continue
            fingerprint = review.get("fingerprint")
            raw = {"reviewer": reviewer, "pr_id": pr_id, "mode": review["mode"],
                   "run_idx": review["run_idx"], "ai_index": index,
                   "review_fingerprint": fingerprint}
            available.append((raw, pr, comment))
    available.sort(key=lambda entry: json.dumps(entry[0], sort_keys=True))
    if per_reviewer is not None:
        rng = random.Random(seed)
        chosen = [entry for entry in available if _get(entry[1], "dataset") == "D1"]
        for reviewer in sorted(reviewers):
            group = [entry for entry in available
                     if entry[0]["reviewer"] == reviewer and _get(entry[1], "dataset") == "D2"]
            chosen.extend(rng.sample(group, min(per_reviewer, len(group))))
        available = sorted(chosen, key=lambda entry: json.dumps(entry[0], sort_keys=True))
    output, private = [], {}
    for key, pr, comment in available:
        item_id = _stable_id(seed, "review", key)
        output.append(AnnotationItem(item_id, key["pr_id"], comment["path"], comment["line"],
                                     comment["comment"], _pr_url(pr)))
        private[item_id] = {**key, "dataset": _get(pr, "dataset")}
    return output, private


def _valid_review_for_annotation(record, pr_id, reviewer):
    return (isinstance(record, Mapping) and record.get("pr_id") == pr_id
            and record.get("reviewer") == reviewer and record.get("error") is None
            and record.get("parse_error") is False and isinstance(record.get("fingerprint"), str)
            and isinstance(record.get("mode"), str) and isinstance(record.get("run_idx"), int)
            and isinstance(record.get("comments"), list))


def _valid_judgment_for_annotation(record, pr_id):
    if not isinstance(record, Mapping) or record.get("pr_id", pr_id) != pr_id or not isinstance(record.get("matches"), list):
        return False
    return all(isinstance(match, Mapping) and isinstance(match.get("ai_index"), int)
               and not isinstance(match.get("ai_index"), bool) for match in record["matches"])


def calibration_items(prs_d1, n, seed):
    """Sample equal numbers of valid and negative D1 references with a hidden key."""
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0 or n % 2:
        raise ValueError("n must be a positive even number for balanced calibration")
    candidates = {True: [], False: []}
    for pr in _items(prs_d1):
        if _get(pr, "dataset") != "D1":
            continue
        pr_id = _get(pr, "pr_id")
        for ref in _get(pr, "references", []):
            valid = _get(ref, "valid")
            if valid in (True, False):
                candidates[valid].append((pr, ref))
    each = n // 2
    if min(map(len, candidates.values())) < each:
        raise ValueError("insufficient positive or negative expert references for balanced calibration")
    rng = random.Random(seed)
    selected = []
    for valid in (True, False):
        group = sorted(candidates[valid], key=lambda x: (_get(x[0], "pr_id"), _get(x[1], "ref_id")))
        selected.extend((valid, *entry) for entry in rng.sample(group, each))
    rng.shuffle(selected)
    items, labels = [], {}
    for valid, pr, ref in selected:
        key = {"pr_id": _get(pr, "pr_id"), "ref_id": _get(ref, "ref_id")}
        item_id = _stable_id(seed, "calibration", key)
        items.append(AnnotationItem(item_id, key["pr_id"], _get(ref, "path"), _get(ref, "line"),
                                    _get(ref, "text"), _pr_url(pr)))
        labels[item_id] = "valid" if valid else "noise"
    return items, labels


def validation_items(prs, reviews, pr_ids):
    """Build blind human-reference validation items and private judge hit labels."""
    ids = set(pr_ids)
    review_map = reviews if isinstance(reviews, Mapping) else {}
    items, labels = [], {}
    for pr in sorted(_items(prs), key=lambda p: _get(p, "pr_id")):
        pr_id = _get(pr, "pr_id")
        if pr_id not in ids:
            continue
        hits = set()
        for record in review_map.values():
            if isinstance(record, Mapping) and record.get("pr_id", pr_id) == pr_id:
                hits.update(m.get("ref_id") for m in record.get("matches", []) if isinstance(m, Mapping))
        for ref in _get(pr, "references", []):
            if _get(ref, "valid") is not True:
                continue
            ref_id = _get(ref, "ref_id")
            key = f"{pr_id}|{ref_id}"
            item_id = _stable_id(0, "validation", {"pr_id": pr_id, "ref_id": ref_id})
            items.append(AnnotationItem(item_id, pr_id, _get(ref, "path"), _get(ref, "line"),
                                        _get(ref, "text"), _pr_url(pr)))
            labels[key] = ref_id in hits
    return items, labels


def assign_raters(itemids, raters, seed):
    """Assign each item to two distinct raters while balancing total load."""
    original_items, original_raters = list(itemids), list(raters)
    items, raters = sorted(set(original_items)), sorted(set(original_raters))
    if len(items) != len(original_items) or len(raters) != len(original_raters):
        raise ValueError("item IDs and raters must be unique")
    if len(raters) < 2:
        raise ValueError("at least two raters are required")
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError("item IDs must be nonempty strings")
    rng = random.Random(seed)
    schedule = []
    loads = {rater: 0 for rater in raters}
    for item in items:
        candidates = sorted(raters, key=lambda r: (loads[r], r))
        lowest = loads[candidates[0]]
        tied = [r for r in candidates if loads[r] == lowest]
        rng.shuffle(tied)
        chosen = tied[:2]
        if len(chosen) < 2:
            candidates = sorted(raters, key=lambda r: (loads[r], r))
            chosen = candidates[:2]
        for rater in chosen:
            loads[rater] += 1
        schedule.append((item, sorted(chosen)))
    return dict(schedule)


def cohen_kappa(left, right):
    if len(left) != len(right):
        raise ValueError("rater labels must have equal lengths")
    if not left:
        return math.nan
    categories = set(left) | set(right)
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    expected = sum((sum(x == category for x in left) / len(left))
                   * (sum(x == category for x in right) / len(right)) for category in categories)
    if observed == 1 and expected == 1:
        return 1.0
    if expected == 1:
        return math.nan
    return (observed - expected) / (1 - expected)


def merge_labels(rater_labels, private_key, adjudication=None):
    """Merge rater decisions, requiring an explicit label for every disagreement."""
    adjudication = adjudication or {}
    if set(private_key) != set().union(*(set(v) for v in rater_labels.values())) if rater_labels else bool(private_key):
        raise ValueError("rater labels contain missing or unknown item IDs")
    if not all(isinstance(labels, Mapping) for labels in rater_labels.values()):
        raise ValueError("rater labels must map rater names to item labels")
    rows = []
    for item_id in sorted(private_key):
        supplied = [labels[item_id] for labels in rater_labels.values() if item_id in labels]
        values = [value.get("label") if isinstance(value, Mapping) else value for value in supplied]
        if not values or any(value not in NOISE_LABELS for value in values):
            raise ValueError(f"invalid or missing label for {item_id}")
        causes = [value.get("cause") for value in supplied if isinstance(value, Mapping) and value.get("cause") is not None]
        if any(cause not in NOISE_CAUSES for cause in causes):
            raise ValueError(f"invalid noise cause for {item_id}")
        decision = values[0] if len(set(values)) == 1 else None
        manual = adjudication.get(item_id)
        if decision is None:
            if not isinstance(manual, Mapping) or manual.get("label") not in NOISE_LABELS:
                raise ValueError(f"disagreement requires adjudication for {item_id}")
            decision = manual["label"]
        elif manual is not None and (not isinstance(manual, Mapping) or manual.get("label", decision) != decision):
            raise ValueError(f"adjudication conflicts with rater agreement for {item_id}")
        row = {key: private_key[item_id][key] for key in
               ("dataset", "pr_id", "reviewer", "mode", "run_idx", "ai_index", "review_fingerprint")
               if key in private_key[item_id]}
        row["label"] = decision
        row["missed_by_human"] = manual.get("missed_by_human") if isinstance(manual, Mapping) else None
        if row["missed_by_human"] is not None and not isinstance(row["missed_by_human"], bool):
            raise ValueError(f"missed_by_human must be a boolean for {item_id}")
        issue_id = manual.get("issue_id") if isinstance(manual, Mapping) else None
        if issue_id is not None:
            if not isinstance(issue_id, str) or not issue_id.strip() or decision != "valid":
                raise ValueError(f"issue_id must be nonempty and attached to a valid comment for {item_id}")
            row["issue_id"] = issue_id.strip()
        cause = manual.get("cause") if isinstance(manual, Mapping) else None
        if cause is None and len(set(causes)) == 1:
            cause = causes[0]
        if cause is not None:
            if cause not in NOISE_CAUSES or decision != "noise":
                raise ValueError(f"cause must be a supported noise cause for {item_id}")
            row["cause"] = cause
        rows.append(row)
    if set(adjudication) - set(private_key):
        raise ValueError("adjudication contains unknown item IDs")
    return {"schema": "c3-adjudication-v1", "items": rows}
