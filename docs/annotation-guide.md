# Manual annotation handoff

Task13 creates blinded files for two independent raters, keeps the identity key private, and merges manually reviewed labels for the existing C3 report. These commands do not run a model.

## Export a sample

For D1, `sample` exports every eligible unmatched AI comment from `run1`/`diff`. D2 draws its per-reviewer sample from unmatched `run1`/`diff` comments only:

```sh
uv run c3 sample --dataset D1 --reviewers reviewer_a,reviewer_b --raters alice,bob
uv run c3 sample --dataset D2 --reviewers reviewer_a,reviewer_b --per-reviewer 40 --raters alice,bob
```

The defaults write blind handoffs to `results/annotation/` and private mappings to `results/annotation-private/`. D1 sampling and calibration can use the same private directory because they write separate named keys; each key filename is write-once. Use a fresh public output directory per handoff. Public and private directories must be separate and cannot contain one another. `rater-01.json`, `rater-02.json`, and any additional numbered files contain only assigned items; a rater with no assignments still has an empty file and assignment list that must be returned. `assignments/` contains each rater's item IDs. The filenames do not contain reviewer or model names. Give each rater only their numbered file. Do not send the private directory or its key to raters.

The item ID is a seeded opaque identifier. Each private key entry retains the dataset, reviewer, run, AI comment index, and review fingerprint so labels can be tied to the saved report artifacts after the handoff.

## Label each item

Raters add a `labels` object keyed by their assigned `item_id`. A minimal example is:

```json
{
  "schema": "c3-annotation-v1",
  "items": [{"item_id": "a1b2c3d4e5f607182930", "pr_id": "owner/repo#1",
              "path": "src/Thing.java", "line": 12, "text": "Possible finding", "pr_url": "https://github.com/owner/repo/pull/1"}],
  "labels": {"a1b2c3d4e5f607182930": {"label": "noise", "cause": "lack_context"}}
}
```

`label` must be `valid`, `noise`, or `insufficient`. For `noise`, choose one cause: `lack_context`, `over_sensitive`, `vague_unactionable`, `lack_domain`, `anti_best_practice`, or `other`. Use `insufficient` when the supplied context cannot support a decision; resolve or exclude those items before merging report labels. Keep `items` and assignment IDs unchanged. Return the completed file under the same `rater-XX.json` name.

## Calibration and agreement

Create a balanced calibration handoff from D1 positive and negative expert references with an even total:

```sh
uv run c3 calibration-set --n 20 --raters alice,bob --output-dir results/calibration
```

Its positive/negative labels remain in `results/annotation-private/calibration-key.json`. Raters see no expert label or reference ID. Cohen's kappa can be computed from their completed labels with `c3.annotation.cohen_kappa`; no calibration scores are written into the report automatically.

## Adjudicate and merge

Prepare a separate JSON file keyed by `item_id`. A human must supply a boolean `missed_by_human` for every report item. A disagreement also needs an adjudicated `label`. For valid D1 comments that introduce a novel issue, include a human-assigned `issue_id` shared only with comments describing that same issue. Example:

```json
{
  "items": {
    "a1b2c3d4e5f607182930": {
      "label": "noise", "missed_by_human": false, "cause": "lack_context"
    },
    "11223344556677889900": {
      "label": "valid", "missed_by_human": true, "issue_id": "issue-1"
    }
  }
}
```

Then merge the completed rater files and explicit human adjudication:

```sh
uv run c3 merge-labels --labels-dir results/annotation \
  --private-dir results/annotation-private --adjudication results/annotation/adjudication.json
uv run c3 analyze --labels-dir results/annotation
```

The merge checks each numbered rater file against the private assignment schedule and requires exactly two distinct ratings for every item. It refuses missing or extra rater files, changed assignment IDs, unresolved disagreement, missing manual `missed_by_human`, missing noise causes, and pre-existing output. It writes `adjudicated_labels.json` in the handoff directory using `c3-adjudication-v1`, which the existing report validates against saved review fingerprints. Never publish `results/annotation-private/`.

The report evaluates only AI comments left unmatched by a fingerprint-validated judgment. Task13 labels only `run1`/`diff`; D1 RQ5 is activated when all eligible unmatched `run1`/`diff` comments have labels, and matched comments require no manual label. Noise and missed-by-human shares use 95% PR-cluster bootstrap intervals when at least two contributing PRs are represented. Partial D2 results have `sampled_estimate` status and include labeled and eligible unmatched counts; with fewer than two contributing PRs, intervals are blank and `ci_status` explains why. Adjusted precision is unavailable for partial D2 samples because the sample does not support the aggregated mismatch estimate. RQ6 noise differences remain unavailable unless both modes have complete labels for each paired PR/run.
