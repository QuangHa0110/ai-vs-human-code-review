import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from c3.config import ReviewerSpec
from c3.runners import (
    ClaudeCliRunner, CodexCliRunner, DeepSeekApiRunner, OpenAIApiRunner,
    make_runner, CachedRunner, RunResult,
)
from c3.config import Settings


def test_claude_diff_uses_no_tools_and_parses_result():
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if '--version' in argv:
            return SimpleNamespace(returncode=0, stdout='2.1.330\n', stderr='')
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'type': 'result', 'subtype': 'success', 'is_error': False,
            'result': '[{"comment":"bug"}]', 'usage': {'input_tokens': 12},
        }), stderr='')

    result = ClaudeCliRunner(ReviewerSpec('claude', 'claude_cli', 'pinned'), execute).run(
        'Review code', 'diff text', None, False)
    assert result.error is None
    assert json.loads(result.text) == [{'comment': 'bug'}]
    argv, kwargs = calls[-1]
    assert argv[argv.index('--tools') + 1] == ''
    assert argv[argv.index('--model') + 1] == 'pinned'
    assert '--safe-mode' in argv and '--strict-mcp-config' in argv
    assert kwargs['input'] == 'diff text'
    assert result.meta['tool_version'] == '2.1.330'
    assert result.meta['usage'] == {'input_tokens': 12}


def test_codex_diff_fails_before_invoking_cli():
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported mode must not launch any process')
    result = CodexCliRunner(ReviewerSpec('gpt', 'codex_cli', 'pinned'), forbidden).run(
        'system', 'diff', None, False)
    assert 'unsupported' in result.error


def test_codex_agent_parses_final_message_from_jsonl(tmp_path):
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if '--version' in argv:
            return SimpleNamespace(returncode=0, stdout='codex-cli 0.166.0', stderr='')
        events = [
            {'type': 'thread.started', 'thread_id': '123'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '[]'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 4}},
        ]
        return SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, events)), stderr='')
    result = CodexCliRunner(ReviewerSpec('gpt', 'codex_cli', 'pinned'), execute).run(
        'system', 'diff', tmp_path, True)
    assert result.error is None
    assert result.text == '[]'
    argv, kwargs = calls[-1]
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert argv[argv.index('--model') + 1] == 'pinned'
    assert kwargs['cwd'] == tmp_path
    assert result.meta['usage'] == {'input_tokens': 4}


@pytest.mark.parametrize('runner_type', [DeepSeekApiRunner, OpenAIApiRunner])
def test_api_payload_usage_error_and_agent_rejection(runner_type):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='[]'))],
            usage=SimpleNamespace(model_dump=lambda: {'prompt_tokens': 3}), model='resolved')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    runner = runner_type(ReviewerSpec('api', 'openai_api', 'pinned'), 'unused', client)
    with pytest.raises(ValueError, match='agent'):
        runner.run('system', 'user', None, True)
    assert not calls
    result = runner.run('system', 'user', None, False)
    assert result.error is None and result.text == '[]'
    assert result.meta['usage'] == {'prompt_tokens': 3}
    assert calls[0]['messages'] == [
        {'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'user'}]
    def fail(**kwargs):
        raise RuntimeError('secret must not leak')
    client.chat.completions.create = fail
    error = runner.run('system', 'user', None, False)
    assert 'api_error' in error.error
    assert 'secret' not in error.error


def test_make_runner_does_not_silently_fallback():
    settings = Settings()
    settings.reviewers['gpt'].model = 'pinned'
    assert isinstance(make_runner('gpt', settings), CodexCliRunner)
    settings.reviewers['gpt'].backend = 'openai_api'
    with pytest.raises(ValueError, match='API key'):
        make_runner('gpt', settings)


def test_cache_hit_and_identity_isolation(tmp_path):
    class Fake:
        calls = 0
        def run(self, *args):
            self.calls += 1
            return RunResult('[]', {'tool': 'fake'})
    inner = Fake()
    cache = CachedRunner(inner, tmp_path, 'experiment')
    context = dict(run_idx=0, attempt_idx=0, context='snapshot-abc', model='pinned', tool_version='v1')
    scoped = cache.with_context(**context)
    assert scoped.run('s', 'u', None, False).error is None
    assert scoped.run('s', 'u', None, False).meta['cache_hit'] is True
    assert inner.calls == 1
    for key, value in [('run_idx', 1), ('attempt_idx', 1), ('context', 'other'),
                       ('model', 'other'), ('tool_version', 'v2')]:
        cache.with_context(**{**context, key: value}).run('s', 'u', None, False)
    assert inner.calls == 6
    # Unscoped callers keep Runner compatibility without unsafe cache reuse.
    cache.run('s', 'u', None, False)
    cache.run('s', 'u', None, False)
    assert inner.calls == 8


def test_cache_errors_are_not_stored_and_corrupt_cache_is_recomputed(tmp_path):
    class Fake:
        calls = 0
        def run(self, *args):
            self.calls += 1
            return RunResult('[]', {}, 'transient' if self.calls == 1 else None)
    inner = Fake()
    scoped = CachedRunner(inner, tmp_path, 'tag').with_context(
        run_idx=0, attempt_idx=0, context='diff', model='pinned', tool_version='v1')
    assert scoped.run('s', 'u', None, False).error == 'transient'
    assert not list(tmp_path.glob('*.json'))
    assert scoped.run('s', 'u', None, False).error is None
    next(tmp_path.glob('*.json')).write_text('{broken', encoding='utf-8')
    assert scoped.run('s', 'u', None, False).error is None
    assert inner.calls == 3


@pytest.mark.parametrize('runner_type', [ClaudeCliRunner, CodexCliRunner])
@pytest.mark.parametrize('failure', ['exit', 'invalid_json', 'timeout', 'error_envelope', 'no_final'])
def test_cli_failures_return_errors(runner_type, failure, tmp_path):
    def execute(argv, **kwargs):
        if '--version' in argv:
            return SimpleNamespace(returncode=0, stdout='v1', stderr='')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        if failure == 'exit':
            return SimpleNamespace(returncode=1, stdout='', stderr='denied')
        if failure == 'invalid_json':
            output = 'not json'
        elif runner_type is ClaudeCliRunner:
            output = json.dumps({'is_error': True, 'result': '[]'} if failure == 'error_envelope'
                                else {'type': 'system', 'result': '[]'})
        else:
            output = json.dumps({'type': 'turn.failed'} if failure == 'error_envelope'
                                else {'type': 'turn.completed'})
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    runner = runner_type(ReviewerSpec('cli', 'claude_cli', 'pinned'), execute)
    result = runner.run('s', 'u', tmp_path, True)
    assert result.error is not None


def test_claude_agent_uses_read_tools_and_snapshot(tmp_path):
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr='', stdout='v1' if '--version' in argv
                               else '{"type":"result","result":"[]"}')
    runner = ClaudeCliRunner(ReviewerSpec('claude', 'claude_cli', 'pinned'), execute)
    assert runner.run('s', 'u', tmp_path, True).error is None
    assert runner.run('s', 'u', tmp_path, True).error is None
    assert len([argv for argv, _ in calls if '--version' in argv]) == 1
    argv, kwargs = calls[-1]
    assert argv[argv.index('--tools') + 1] == 'Read,Glob,Grep'
    assert kwargs['cwd'] == tmp_path


def test_claude_structured_output_is_unwrapped():
    def execute(argv, **kwargs):
        return SimpleNamespace(returncode=0, stderr='', stdout='v1' if '--version' in argv else
                               '{"type":"result","structured_output":{"comments":[]}}')
    result = ClaudeCliRunner(ReviewerSpec('claude', 'claude_cli', 'pinned'), execute).run(
        's', 'u', None, False)
    assert json.loads(result.text) == {'comments': []}


@pytest.mark.parametrize('payload', [None, 7, False, {}, []])
def test_claude_nonstring_result_returns_parse_error(payload):
    def execute(argv, **kwargs):
        output = 'v1' if '--version' in argv else json.dumps({'type': 'result', 'result': payload})
        return SimpleNamespace(returncode=0, stderr='', stdout=output)
    result = ClaudeCliRunner(ReviewerSpec('claude', 'claude_cli', 'pinned'), execute).run(
        's', 'u', None, False)
    assert result.error is not None and result.error.startswith('parse_error:')


@pytest.mark.parametrize('structured', [{}, []])
def test_claude_structured_payload_takes_precedence_over_null_result(structured):
    def execute(argv, **kwargs):
        output = 'v1' if '--version' in argv else json.dumps(
            {'type': 'result', 'result': None, 'structured_output': structured})
        return SimpleNamespace(returncode=0, stderr='', stdout=output)
    result = ClaudeCliRunner(ReviewerSpec('claude', 'claude_cli', 'pinned'), execute).run(
        's', 'u', None, False)
    assert result.error is None
    assert json.loads(result.text) == structured


def test_cache_records_identity_and_skips_mismatched_versions(tmp_path):
    class Fake:
        def run(self, *args):
            return RunResult('[]', {'tool_version': 'actual'})
    scoped = CachedRunner(Fake(), tmp_path, 'tag').with_context(
        run_idx=2, attempt_idx=1, context='snapshot', model='pinned', tool_version='expected')
    result = scoped.run('s', 'u', None, False)
    assert result.error is None
    assert result.meta['cache_context']['run_idx'] == 2
    assert result.meta['cache_skipped']
    assert not list(tmp_path.glob('*.json'))
