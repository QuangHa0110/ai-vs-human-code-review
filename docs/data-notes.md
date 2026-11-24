# Data and execution notes

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
