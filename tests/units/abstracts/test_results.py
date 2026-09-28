import pytest

from throng.abstracts.results import SimpleRunResult


@pytest.mark.parametrize('success', [False, True])
def test_result_without_execution_details(success):
    """Expose all result fields even when no execution details are available."""
    result = SimpleRunResult(success)

    assert result.success is success
    assert result.returncode is None
    assert result.stdout is None
    assert result.stderr is None


@pytest.mark.parametrize(
    ('success', 'returncode', 'stdout', 'stderr'),
    [
        (True, 0, 'output', ''),
        (False, 2, '', 'error'),
        (False, -9, 'partial', 'interrupted'),
        (False, None, None, None),
        (True, 0, '', ''),
        (False, 1, 'Привет\n世界\n', 'ошибка\n'),
    ],
)
def test_result_preserves_execution_details(success, returncode, stdout, stderr):
    """Preserve the supplied status and output without interpreting them."""
    result = SimpleRunResult(success, returncode, stdout, stderr)

    assert (result.success, result.returncode, result.stdout, result.stderr) == (
        success,
        returncode,
        stdout,
        stderr,
    )
