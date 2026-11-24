"""Explicit CLI/API backends and identity-scoped successful-result caching."""

import json
import hashlib
import os
import subprocess
import tempfile
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Protocol

from c3.cli_flags import (
    CLAUDE_DIFF_ONLY, CLAUDE_AGENT_READONLY, CLAUDE_JSON_OUTPUT,
    CODEX_AGENT_READONLY, CODEX_JSON_OUTPUT,
)
from c3.config import ReviewerSpec, Settings
from c3.jsonutil import extract_json


@dataclass
class RunResult:
    text: str
    meta: dict
    error: str | None = None


class Runner(Protocol):
    def run(self, system: str, user: str, workdir: Path | None,
            allow_read_tools: bool) -> RunResult: ...


class ClaudeCliRunner:
    def __init__(self, spec: ReviewerSpec, exec_fn=subprocess.run, *, timeout: float = 300):
        self.spec, self.exec_fn, self.timeout = spec, exec_fn, timeout
        self.tool_version = None

    def run(self, system, user, workdir, allow_read_tools):
        flags = CLAUDE_AGENT_READONLY if allow_read_tools else CLAUDE_DIFF_ONLY
        argv = ['claude', '-p', '--model', self.spec.model, '--system-prompt', system,
                *flags, *CLAUDE_JSON_OUTPUT]
        meta = dict(tool='claude', tool_version=self.tool_version, model=self.spec.model,
                    argv=argv, started_at=datetime.now(timezone.utc).isoformat(), usage=None)
        try:
            if self.tool_version is None:
                version = self.exec_fn(['claude', '--version'], capture_output=True,
                                       text=True, timeout=15)
                if version.returncode:
                    return RunResult('', meta, 'preflight: CLI version check failed')
                self.tool_version = version.stdout.strip()
            meta['tool_version'] = self.tool_version
            result = self.exec_fn(argv, input=user, cwd=workdir, capture_output=True,
                                  text=True, timeout=self.timeout)
            meta.update(exit_code=result.returncode, stderr=result.stderr, stdout=result.stdout)
            if result.returncode:
                return RunResult('', meta, f'cli_failure: exit {result.returncode}')
            envelope = json.loads(result.stdout)
            if not isinstance(envelope, dict):
                raise ValueError('Expected CLI result object')
            if envelope.get('type') != 'result':
                raise ValueError('Missing final result envelope')
            meta['usage'] = envelope.get('usage')
            if envelope.get('is_error') or envelope.get('subtype', 'success') != 'success':
                return RunResult('', meta, 'cli_failure: unsuccessful result envelope')
            payload = envelope.get('structured_output')
            text = json.dumps(payload, ensure_ascii=False) if payload is not None else envelope['result']
            if not isinstance(text, str):
                raise ValueError('Final result payload must be text')
            extract_json(text)
            return RunResult(text, meta)
        except subprocess.TimeoutExpired:
            return RunResult('', meta, 'timeout: CLI exceeded configured deadline')
        except OSError as exc:
            return RunResult('', meta, f'preflight: {type(exc).__name__}')
        except (ValueError, KeyError, TypeError) as exc:
            return RunResult('', meta, f'parse_error: {exc}')


class CodexCliRunner:
    def __init__(self, spec: ReviewerSpec, exec_fn=subprocess.run, *, timeout: float = 900):
        self.spec, self.exec_fn, self.timeout = spec, exec_fn, timeout
        self.tool_version = None

    def run(self, system, user, workdir, allow_read_tools):
        argv = ['codex', '--no-daemon', 'exec', '--model', self.spec.model,
                *CODEX_AGENT_READONLY, *CODEX_JSON_OUTPUT, '-']
        meta = dict(tool='codex', tool_version=self.tool_version, model=self.spec.model,
                    argv=argv, started_at=datetime.now(timezone.utc).isoformat(), usage=None)
        if not allow_read_tools:
            return RunResult('', meta, 'unsupported: Codex no-tools mode is not verified; '
                             'explicitly configure openai_api for diff-only reviews')
        if workdir is None:
            return RunResult('', meta, 'preflight: agent mode requires a snapshot workdir')
        try:
            if self.tool_version is None:
                version = self.exec_fn(['codex', '--version'], capture_output=True,
                                       text=True, timeout=15)
                if version.returncode:
                    return RunResult('', meta, 'preflight: CLI version check failed')
                self.tool_version = version.stdout.strip()
            meta['tool_version'] = self.tool_version
            result = self.exec_fn(argv, input=system + '\n\n' + user, cwd=workdir,
                                  capture_output=True, text=True, timeout=self.timeout)
            meta.update(exit_code=result.returncode, stderr=result.stderr, stdout=result.stdout)
            if result.returncode:
                return RunResult('', meta, f'cli_failure: exit {result.returncode}')
            final, completed = None, False
            for line in result.stdout.splitlines():
                event = json.loads(line)
                if event.get('type') in {'error', 'turn.failed'}:
                    return RunResult('', meta, 'cli_failure: error event')
                if event.get('type') == 'item.completed':
                    item = event.get('item', {})
                    if item.get('type') == 'agent_message':
                        final = item.get('text')
                if event.get('type') == 'turn.completed':
                    completed = True
                    meta['usage'] = event.get('usage')
            if not completed or not isinstance(final, str):
                raise ValueError('Missing completed turn or final agent message')
            extract_json(final)
            return RunResult(final, meta)
        except subprocess.TimeoutExpired:
            return RunResult('', meta, 'timeout: CLI exceeded configured deadline')
        except OSError as exc:
            return RunResult('', meta, f'preflight: {type(exc).__name__}')
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return RunResult('', meta, f'parse_error: {exc}')


class OpenAIApiRunner:
    base_url = None
    tool = 'openai_api'

    def __init__(self, spec: ReviewerSpec, api_key: str | None, client=None):
        self.spec = spec
        if client is None:
            if not api_key:
                raise ValueError(f'{self.tool} requires an API key')
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=300, max_retries=0)
        self.client = client
        self.tool_version = package_version('openai')

    def run(self, system, user, workdir, allow_read_tools):
        if allow_read_tools:
            raise ValueError('API runner does not support agent mode')
        meta = dict(tool=self.tool, tool_version=self.tool_version, model=self.spec.model,
                    argv=[], started_at=datetime.now(timezone.utc).isoformat(), usage=None)
        try:
            response = self.client.chat.completions.create(
                model=self.spec.model, messages=[{'role': 'system', 'content': system},
                                                 {'role': 'user', 'content': user}])
            text = response.choices[0].message.content
            meta['usage'] = response.usage.model_dump() if response.usage is not None else None
            meta['resolved_model'] = response.model
            extract_json(text)
            return RunResult(text, meta)
        except (ValueError, TypeError, IndexError, AttributeError) as exc:
            return RunResult('', meta, f'parse_error: {type(exc).__name__}')
        except Exception as exc:
            # Provider exception messages may contain request or authentication data.
            return RunResult('', meta, f'api_error: {type(exc).__name__}')


class DeepSeekApiRunner(OpenAIApiRunner):
    base_url = 'https://api.deepseek.com'
    tool = 'deepseek_api'


def make_runner(name: str, settings: Settings) -> Runner:
    spec = settings.reviewers[name]
    if not spec.model.strip():
        raise ValueError(f'Explicit model required for reviewer {name}')
    if spec.backend == 'claude_cli':
        return ClaudeCliRunner(spec)
    if spec.backend == 'codex_cli':
        return CodexCliRunner(spec)
    if spec.backend == 'deepseek_api':
        return DeepSeekApiRunner(spec, settings.deepseek_api_key)
    if spec.backend == 'openai_api':
        return OpenAIApiRunner(spec, settings.openai_api_key)
    raise ValueError(f'Unknown backend: {spec.backend}')


class CachedRunner:
    """Cache only explicitly identified repetitions; unscoped calls bypass it.

    ``context`` must identify all supplied context, including the immutable
    repository revision/snapshot in agent mode. ``attempt_idx`` identifies one
    retry; changing it never reuses another attempt's output.
    """

    def __init__(self, inner: Runner, cache_dir: Path, tag: str):
        self.inner, self.cache_dir, self.tag = inner, Path(cache_dir), tag
        self.cache_context = None

    def with_context(self, *, run_idx: int, attempt_idx: int, context: str,
                     model: str, tool_version: str):
        if (isinstance(run_idx, bool) or not isinstance(run_idx, int) or run_idx < 0
                or isinstance(attempt_idx, bool) or not isinstance(attempt_idx, int) or attempt_idx < 0):
            raise ValueError('run_idx and attempt_idx must be nonnegative integers')
        if not all(isinstance(v, str) and v.strip() for v in (context, model, tool_version)):
            raise ValueError('context, model and tool_version are required')
        spec = getattr(self.inner, 'spec', None)
        if spec is not None and spec.model != model:
            raise ValueError('Cache model must match runner model')
        scoped = CachedRunner(self.inner, self.cache_dir, self.tag)
        scoped.cache_context = dict(run_idx=run_idx, attempt_idx=attempt_idx,
                                    context=context, model=model, tool_version=tool_version)
        return scoped

    def run(self, system, user, workdir, allow_read_tools):
        if self.cache_context is None:
            return self.inner.run(system, user, workdir, allow_read_tools)
        spec = getattr(self.inner, 'spec', None)
        identity = dict(
            cache_version=1, runner_version='c3-runners-v1', tag=self.tag,
            runner=type(self.inner).__name__, timeout=getattr(self.inner, 'timeout', None),
            reviewer=asdict(spec) if spec is not None else None,
            **self.cache_context, system=system, user=user, allow_read_tools=allow_read_tools,
            workdir=str(Path(workdir).resolve()) if workdir is not None else None,
        )
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = self.cache_dir / f'{key}.json'
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if (cached['identity'] == identity and cached['error'] is None
                    and isinstance(cached['text'], str) and isinstance(cached['meta'], dict)):
                return RunResult(cached['text'], {**cached['meta'], 'cache_hit': True}, None)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result = self.inner.run(system, user, workdir, allow_read_tools)
        result.meta = {**result.meta, 'cache_context': dict(self.cache_context), 'cache_hit': False}
        if result.error is None:
            actual_version = result.meta.get('tool_version')
            if actual_version is not None and actual_version != self.cache_context['tool_version']:
                result.meta['cache_skipped'] = 'tool_version differs from cache context'
                return result
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            record = dict(identity=identity, **asdict(result))
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                 dir=self.cache_dir, delete=False) as handle:
                    temporary = handle.name
                    json.dump(record, handle, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        return result
