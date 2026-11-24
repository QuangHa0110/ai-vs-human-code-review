# Data and execution notes

Downloaded sources (`data/raw/`) and generated PR exports (`data/prs/`) are
ignored by Git. Keep them untracked until identities and publication suitability
have been reviewed; automated mention redaction does not prove all names are removed.

## D1 — AACR-Bench v1 Java

Use the public v1 JSON arrays at revision
`07ba1b35102110404f6a931bf05dea33e6d89389`. The dataset card defines label
`1` as correct and `0` as incorrect. The paper describes independent expert
annotation of each candidate by two reviewers, followed by adjudication of
disagreements. The candidates combine LLM-augmented original review discussion
and separately generated AI comments. Treat negative rows as expert-rejected
candidates for the released benchmark. RQ5 can estimate human-origin precision
within that curated set. The estimate applies to the selected and augmented
references, so its denominator excludes original discussion removed before
annotation. [Dataset card](https://huggingface.co/datasets/Alibaba-Aone/aacr-bench#data-sample)
and [paper, Appendix B.1](https://arxiv.org/html/2601.19494v1#A2.SS1).

For `alibaba/spring-ai-alibaba#869`, the public PR diff had five file
headers. The pinned `source_commit...target_commit` diff shared four headers
and 110 signed added or deleted lines with it. The reverse comparison shared
zero headers and zero signed lines. The upstream converter also uses
`source_commit` as base and `target_commit` as review head. The CLI requests
that pinned pair and writes only Java file blocks. The release omits titles and
descriptions, so the imported fields remain blank.
[PR diff](https://github.com/alibaba/spring-ai-alibaba/pull/869.diff),
[forward comparison](https://github.com/alibaba/spring-ai-alibaba/compare/76c06b58a4d9e8ee3fe3c0269a046987ad0502e5...3555a95e7b888335d9f72e432eb4f9fb9640e8cd.diff),
[reverse comparison](https://github.com/alibaba/spring-ai-alibaba/compare/3555a95e7b888335d9f72e432eb4f9fb9640e8cd...76c06b58a4d9e8ee3fe3c0269a046987ad0502e5.diff),
[converter](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/evaluation/converters/aacr_bench.py#L185-L222).

The 30 Java PRs span five repositories. The negative split covers 18 of those
PRs. The evaluation filter keeps references to `.java` paths on the right
side with a non-null `to_line`. The filter removes 12 comments on other file
types and one positive human-origin comment on the left side.
[Positive JSON](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/dataset/v1/positive_samples.json),
[negative JSON](https://github.com/alibaba/aacr-bench/blob/07ba1b35102110404f6a931bf05dea33e6d89389/dataset/v1/negative_samples.json).

| References | Raw human | Raw AI | Raw total | Evaluation human | Evaluation AI | Evaluation total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Positive | 61 | 152 | 213 | 56 | 146 | 202 |
| Negative | 16 | 43 | 59 | 15 | 42 | 57 |

Place both JSON files under `data/raw/aacr-bench/v1/` and set
`C3_ANONYMIZATION_SALT` in the environment before importing.

```bash
python -m c3 load-aacr \
  --positive data/raw/aacr-bench/v1/positive_samples.json \
  --negative data/raw/aacr-bench/v1/negative_samples.json
```

The command writes PR records and `_meta.json` under `data/prs/D1/` and
requires an absent or empty destination. The manifest records input hashes,
raw counts, evaluation counts and filter reasons. The pure loader preserves
the original comment note and upstream category. The CLI pseudonymizes
GitHub-style mentions in exported notes with the same scheme as D2. The source
files provide no reviewer login field, so plain-text identities may require
manual checking before publication. Run `python -m pytest -m data` against
the local pinned files to validate the reference counts. Set
`C3_AACR_V1_DIR` to use another local directory.

## CLI

Inspected locally on 2026-11-24 without inference: `claude --version`,
`claude --help`, `codex --version`, and `codex exec --help`.
Installed versions: Claude Code **2.1.330**, codex-cli **0.166.0**.
Real CLI enforcement smoke tests are **pending**; mocked tests verify argument
construction and response handling only. No API keys or credentials were read.

Claude uses print mode, an explicit model and system prompt, and JSON output.
Diff mode supplies `--tools ""`. Agent mode exposes and allows only
`Read,Glob,Grep`, with the supplied snapshot as cwd. Shared flags use
`--safe-mode --restricted --setting-sources "" --strict-mcp-config --no-chrome
--no-session-persistence --disable-slash-commands --permission-mode dontAsk
--permission-prompts none`. Local help describes safe mode as disabling personal
customizations, including CLAUDE.md, skills, plugins, hooks and MCP servers;
managed policy and managed-plugin hooks may still apply. Restricted mode confines
file tools to working directories. This is not an independently verified OS
filesystem sandbox. `--bare` is deliberately avoided because help says it does
not use subscription OAuth/keychain authentication.

Codex agent mode uses `exec --ignore-user-config --ignore-rules --ephemeral
--sandbox read-only --json --color never`, explicit model, disabled hooks,
memories, multi-agent, apps and plugins, and configuration overrides disabling
web search, approval prompts, and automatic AGENTS ingestion. A clean snapshot
must be supplied; ignoring user config does not promise isolation from every
managed or project configuration source. Authentication still uses CODEX_HOME.
**Codex diff mode returns an unsupported preflight error without starting a
process:** neither read-only sandboxing nor a no-tools prompt establishes an
all-tools-off policy. Select `C3_GPT_BACKEND=openai_api` explicitly when diff-only
reviews are required; the runner never silently changes provider.

Claude JSON is a result envelope; the runner extracts `structured_output` or
the JSON text inside `result`. Codex JSON output is JSONL events; a successful
completed turn and final `agent_message` are required. Nonzero exit, malformed
output, error envelopes/events, or absent final payload return errors. CLI
metadata retains version, requested model, argv, UTC start, usage, exit status,
and captured streams. CLI version checks are cached per runner instance.
Timeout defaults are 300 seconds for Claude and 900 seconds for Codex, with a
15-second version preflight. Standard subprocess timeouts terminate/wait for the
direct CLI process; descendant process-group cleanup is not guaranteed.

API runners send only system/user messages, expose no tools, reject agent mode,
and require explicit model/key configuration. Default SDK clients use a
300-second timeout and no automatic retries. Provider error messages are not
retained because they may contain request/authentication information.

Cache reuse requires `CachedRunner.with_context(run_idx=..., attempt_idx=...,
context=..., model=..., tool_version=...)`. Unscoped calls bypass caching.
`context` must identify all supplied context, including the immutable snapshot
revision/content in agent mode; a directory path alone is insufficient.
The SHA-256 identity also includes experiment tag, backend/reviewer, runner/cache
version, timeout, prompts, mode and working path. Bump the runner version when
execution behavior changes. CLI/SDK version mismatches skip cache writes.
Independent repetitions and retries have distinct identities. Only successful
results are stored using atomic replacement; malformed cache records are
recomputed. Cached results preserve original execution metadata and add a hit
marker. Cache/output directories may contain source text and prompts.

Sources: [Claude CLI reference](https://code.claude.com/docs/en/cli-reference),
[Claude headless mode](https://code.claude.com/docs/en/headless),
[Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
