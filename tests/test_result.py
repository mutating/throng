from sys import executable

from suby import SubprocessResult

from throng import RunResult, throngs


def test_run_result_success_stdout(tmp_path, monkeypatch):
    """Verify that a successful command captures stdout and maps to a successful RunResult."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run('printf hello', catch_output=True)

    assert result.stdout == 'hello'
    assert result.stderr in (None, '')
    assert result.returncode == 0
    assert result.success is True


def test_run_result_success_stderr(tmp_path, monkeypatch):
    """Verify that a successful command can capture stderr while still reporting success."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run(executable, '-c', 'import sys; sys.stderr.write("warn")', catch_output=True, split=False)

    assert result.stderr == 'warn'
    assert result.returncode == 0
    assert result.success is True


def test_run_result_nonzero_captured(tmp_path, monkeypatch):
    """Verify that a captured non-zero command preserves stderr, return code, and success=False."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run(executable, '-c', 'import sys; sys.stderr.write("bad"); sys.exit(7)', catch_output=True, catch_exceptions=True, split=False)

    assert result.stderr == 'bad'
    assert result.returncode == 7
    assert result.success is False


def test_run_result_no_output(tmp_path, monkeypatch):
    """Verify that a successful command with no output maps empty output and success=True."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run(executable, '-c', 'pass', catch_output=True, split=False)

    assert result.stdout in (None, '')
    assert result.stderr in (None, '')
    assert result.returncode == 0
    assert result.success is True


def test_run_result_default_forwards_and_maps_output(tmp_path, monkeypatch, capsys):
    """Verify that default run forwards stdout while still mapping suby's returned stream content."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run(executable, '-c', 'print("forwarded")', split=False)
    captured = capsys.readouterr()

    assert captured.out == 'forwarded\n'
    assert result.stdout == 'forwarded\n'
    assert result.stderr in (None, '')
    assert result.returncode == 0
    assert result.success is True


def test_run_result_startup_failure_returncode(tmp_path, monkeypatch):
    """Verify that a nonexistent command maps to suby's startup-failure result shape."""
    monkeypatch.chdir(tmp_path)

    isolate = throngs()['local'].get_isolate()

    result = isolate.run('definitely-not-a-real-command-for-throng', catch_output=True, catch_exceptions=True)

    assert result.id
    assert result.stdout == ''
    assert result.stderr == ''
    assert result.returncode == 1
    assert result.success is False


def test_run_result_no_killed_by_token_attribute():
    """Verify that RunResult deliberately does not expose suby's killed_by_token attribute."""
    result = RunResult(id='abc', stdout=None, stderr=None, returncode=0)

    assert not hasattr(result, 'killed_by_token')


def test_run_result_id_maps_suby_id(monkeypatch, tmp_path):
    """Verify that the subprocess result is fully mapped into RunResult, including the suby id."""
    monkeypatch.chdir(tmp_path)

    def fake_run(*_args, **_kwargs):
        return SubprocessResult(id='fixed-id', stdout='out', stderr='err', returncode=0)

    monkeypatch.setattr('throng.plugins.directory_isolate.run_suby', fake_run)

    result = throngs()['local'].get_isolate().run('anything', catch_output=True)

    assert result.id == 'fixed-id'
    assert result.stdout == 'out'
    assert result.stderr == 'err'
    assert result.returncode == 0
    assert result.success is True


def test_run_result_success_property_values():
    """Verify that RunResult.success is true only when returncode is exactly zero."""
    assert RunResult(id='ok', stdout=None, stderr=None, returncode=0).success is True
    assert RunResult(id='fail', stdout=None, stderr=None, returncode=1).success is False
    assert RunResult(id='none', stdout=None, stderr=None, returncode=None).success is False
