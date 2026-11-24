"""Flags verified against Claude Code 2.1.330 and codex-cli 0.166.0 help.

Enforcement smoke tests with model inference remain pending. Codex does not
have a verified all-tools-off mode; its diff-only runner fails preflight.
"""

_CLAUDE_CLEAN = [
    '--safe-mode', '--restricted', '--setting-sources', '',
    '--strict-mcp-config', '--no-chrome', '--no-session-persistence',
    '--disable-slash-commands', '--permission-mode', 'dontAsk',
    '--permission-prompts', 'none',
]
CLAUDE_DIFF_ONLY = [*_CLAUDE_CLEAN, '--tools', '']
CLAUDE_AGENT_READONLY = [
    *_CLAUDE_CLEAN, '--tools', 'Read,Glob,Grep',
    '--allowedTools', 'Read,Glob,Grep',
]
CLAUDE_JSON_OUTPUT = ['--output-format', 'json']
CODEX_DIFF_ONLY: list[str] = []  # Unsupported, never used to invoke Codex.
CODEX_AGENT_READONLY = [
    '--ignore-user-config', '--ignore-rules', '--ephemeral',
    '--sandbox', 'read-only', '-c', 'approval_policy="never"',
    '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"',
    '--disable', 'hooks', '--disable', 'memories', '--disable', 'multi_agent',
    '--disable', 'apps', '--disable', 'plugins', '--disable', 'remote_plugin',
]
CODEX_JSON_OUTPUT = ['--json', '--color', 'never']
