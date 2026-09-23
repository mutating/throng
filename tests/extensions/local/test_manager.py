from throng import throng


def test_simple_print():
    result = throng('.')['local'].run('echo lol')

    assert result.success
    assert result.returncode == 0
    assert result.stderr == ''
    assert result.stdout == 'lol'
