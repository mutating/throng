import pytest
from cantok import AbstractToken, SimpleToken

from throng import AbstractManager


@pytest.mark.mypy_testing
def mypy_scope_accepts_token(manager: AbstractManager, token: AbstractToken) -> None:
    with manager.scope() as isolate:
        isolate.run('command')
    with manager.scope(SimpleToken()) as isolate:
        isolate.run('command')
    with manager.scope(token) as isolate:
        isolate.run('command')
    with manager.scope(token=token) as isolate:
        isolate.run('command')
    with manager.scope as isolate:
        isolate.run('command')


@pytest.mark.mypy_testing
def mypy_scope_rejects_incompatible_arguments(manager: AbstractManager) -> None:
    manager.scope(None)  # E: [arg-type]
    manager.scope(token=None)  # E: [arg-type]
    manager.scope('invalid')  # E: [arg-type]
    manager.scope(token='invalid')  # E: [arg-type]
    manager.scope(unknown=True)  # E: [call-arg]
    manager.scope(SimpleToken(), SimpleToken())  # E: [call-arg]
