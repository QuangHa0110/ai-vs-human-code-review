# AI review compared with human code review

C3 is a research pipeline for measuring how AI code review compares with human review on Java pull requests. The design and research questions are described in [the study design](docs/specs/2026-11-21-c3-ai-code-review-design.md); dataset provenance and limits are in [data notes](docs/data-notes.md). The completed software work and remaining experiment steps are recorded in the [implementation report](docs/implementation-report.md).

## Setup

Requires Python 3.11 or newer. Create an environment and install the project:

```sh
uv sync --extra dev
cp .env.example .env
```

Edit `.env` for the local run. Keep all tokens and keys private; `.env` is ignored by Git. `GITHUB_TOKEN` is used for dataset collection/import. `DEEPSEEK_API_KEY` is only needed for DeepSeek API reviews. `OPENAI_API_KEY` is needed only if GPT is explicitly configured for the API backend. Configure `C3_DATA_DIR` and `C3_RESULTS_DIR` to select local data and report locations.

Pin model identifiers only after the pilot. The repository intentionally does not select a model version or claim a model has been chosen. `C3_GPT_BACKEND=codex_cli` is the default. Codex CLI does not currently support the pipeline's diff-only preflight, so GPT diff-only reviews need the explicit `C3_GPT_BACKEND=openai_api` fallback and an `OPENAI_API_KEY`. The OpenAI API fallback has no selected model until the pilot settles that choice. Do not run inference until the reviewer/backend/model and read-only constraints have been agreed.

Check the local command interface without invoking a model:

```sh
uv run c3 --help
uv run c3 analyze --help
```

## Pipeline

The usual sequence is:

1. Import/collect data: `load-aacr`, `load-swepr`, and `collect` for D1, D3, and D2.
2. Classify references with `classify`.
3. Run `review` for each selected reviewer, mode, and run.
4. Match saved reviews with `judge`.
5. Plan sample size with `power` from pilot estimates.
6. Complete the separately coordinated manual annotation and adjudication work.
7. Generate tables with `analyze`.

The end-to-end commands are documented in `uv run c3 <command> --help`. Review model selection, account ownership, and who runs each subscription-backed CLI with the research group before inference. The current experiment has no selected model version. Task13 annotation tooling is ready for collaborators, but no human labels have been collected.

## Reporting

Run:

```sh
uv run c3 analyze
```

Reports are written under `C3_RESULTS_DIR` (default `results/`). `rq1_rq2.csv` contains PR-bootstrap intervals for recall and mismatch rate plus explicit run-error, parse-error, and missing-artifact counts. The other tables cover reviewer comparisons, run variability, categories, pooled D1 coverage, paired diff/agent context, agreement, and D3 calibration. RQ6 reports paired mean changes in comment count and mismatch rate. With complete labels for both modes, noise change is the mean same-PR delta with a seeded PR-bootstrap interval; pairs with zero comments in either mode are excluded and counted in `noise_status`. A recall PNG is created when finite recall data exists. Generated reports are local outputs and are ignored by Git.

No hand judgments are currently present. Consequently, the report leaves `true_noise_share`, `missed_by_human_share`, and `adjusted_precision` blank with a label status; it does not infer labels from unmatched comments. Pooled RQ5 uses the valid D1 references as a baseline and marks that it has no adjudicated new-issue clusters. Human precision is reported only when D1 contains verified negative examples. D3 calibration stays unavailable until a benchmark confidence interval is supplied.

When adjudications are ready, `--labels-dir DIR` accepts `DIR/adjudicated_labels.json` with schema `c3-adjudication-v1`. Each `items` entry must contain `dataset`, `pr_id`, `reviewer`, `mode`, `run_idx`, `ai_index`, `review_fingerprint`, `label` (`valid` or `noise`), and boolean `missed_by_human`. Entries must cover every unmatched AI comment in the selected `run1`/`diff` group and match saved review fingerprints. Every valid D1 comment unmatched to expert references must have a nonempty `issue_id`; it groups the same novel issue across reviewers. An `issue_id` is rejected if the corresponding judgment matched that AI comment to an expert reference. RQ5 uses only complete, fingerprint-bound D1 `run1`/`diff` labels to add issue clusters; absent, partial, or missing-`issue_id` labels leave the pool `baseline_only`. Optional `d3_benchmark_ci` maps a reviewer to supplied `recall_low` and `recall_high` bounds; no bounds are inferred. Partial D2 labels produce marked sample estimates where supported; malformed or stale labels are unavailable.

## Collaborator annotation handoff

Task13 provides blinded `sample`, balanced D1 `calibration-set`, and `merge-labels` commands. The workflow exports one file per rater plus assignment lists, keeps reviewer/run/fingerprint keys under ignored `results/annotation-private/`, and requires explicit human adjudication before report labels are written. Follow the [annotation guide](docs/annotation-guide.md) for label definitions, handoff examples, calibration, and merge steps. Do not share or publish the private key directory.

## Repository conventions

Do not commit API keys, tokens, downloaded PR data, or local reports. Use a task branch and have another person review changes before merging. Dataset publication requires an anonymization review.
