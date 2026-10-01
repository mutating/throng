from contextlib import nullcontext
from shutil import rmtree
from unittest.mock import Mock

import pytest

from throng.extensions.temporary_directory.manager import TemporaryDirectoryManager


@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
def test_read_delegates_source_settings(tmp_path, monkeypatch, exclude):
    """Read the configured source directory with the manager's exclusions."""
    expected_exclude = None if exclude is None else exclude.copy()
    manager = TemporaryDirectoryManager(tmp_path, exclude)
    read = Mock(return_value=b'\x00\xffstate')
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.read_directory',
        read,
    )

    assert manager.read() is read.return_value
    read.assert_called_once_with(tmp_path, expected_exclude)


@pytest.mark.parametrize('state', [b'', b'state', b'\x00\xff'])
@pytest.mark.parametrize('exclude', [None, [], ['cache/', '*.tmp', '!keep.tmp']])
@pytest.mark.parametrize('prepare', [None, [], ['first', 'second']])
def test_get_delegates_snapshot_without_reading_source(
    tmp_path,
    monkeypatch,
    state,
    exclude,
    prepare,
):
    """Restore the supplied opaque snapshot without rereading the source directory."""
    expected_exclude = None if exclude is None else exclude.copy()
    expected_prepare = None if prepare is None else prepare.copy()
    manager = TemporaryDirectoryManager(tmp_path, exclude, prepare)
    constructor, read, read_source = Mock(), Mock(), Mock()
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.TemporaryDirectoryIsolate',
        constructor,
    )
    monkeypatch.setattr(manager, 'read', read)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.manager.read_directory',
        read_source,
    )

    assert manager.get(state) is constructor.return_value
    constructor.assert_called_once_with(state, expected_exclude, expected_prepare)
    read.assert_not_called()
    read_source.assert_not_called()


@pytest.mark.parametrize('operation', ['read', 'get'])
def test_delegate_error_is_preserved(tmp_path, monkeypatch, operation):
    """Keep the original cause when snapshot reading or isolate creation fails."""
    error = OSError('delegate failed')
    dependency = Mock(side_effect=error)
    name = 'read_directory' if operation == 'read' else 'TemporaryDirectoryIsolate'
    monkeypatch.setattr(
        f'throng.extensions.temporary_directory.manager.{name}',
        dependency,
    )
    manager = TemporaryDirectoryManager(tmp_path)

    with pytest.raises(OSError, match='delegate failed') as caught:
        getattr(manager, operation)(*(() if operation == 'read' else (b'state',)))

    assert caught.value is error
    assert dependency.call_count == 1


@pytest.mark.parametrize('populated', [False, True])
def test_same_snapshot_produces_independent_isolates(tmp_path, populated):
    """Keep directories, modifications and cleanup independent for each isolate."""
    if populated:
        (tmp_path / 'file').write_bytes(b'original')
    manager = TemporaryDirectoryManager(tmp_path)
    state = manager.read()
    first = manager.get(state)
    try:
        second = manager.get(state)
        try:
            assert first is not second
            assert first.path != second.path
            (first.path / 'file').write_bytes(b'changed')
            if populated:
                assert (second.path / 'file').read_bytes() == b'original'
            else:
                assert not (second.path / 'file').exists()
            first.kill()
            assert not first.lock.locked()
            assert second.path.is_dir()
            assert tmp_path.is_dir()
        finally:
            second.directory.cleanup()
            if second.lock.locked():
                second.lock.release()
    finally:
        first.directory.cleanup()
        if first.lock.locked():
            first.lock.release()


@pytest.mark.parametrize('change', ['modify_file', 'delete_file', 'delete_directory'])
def test_snapshot_survives_source_changes(tmp_path, change):
    """Restore saved contents even after the source file or whole directory disappears."""
    directory = tmp_path / 'source'
    directory.mkdir()
    source = directory / 'file'
    source.write_bytes(b'saved')
    manager = TemporaryDirectoryManager(directory)
    state = manager.read()
    if change == 'modify_file':
        source.write_bytes(b'new')
    elif change == 'delete_file':
        source.unlink()
    else:
        rmtree(directory)
    isolate = manager.get(state)
    try:
        assert (isolate.path / 'file').read_bytes() == b'saved'
    finally:
        isolate.directory.cleanup()
        if isolate.lock.locked():
            isolate.lock.release()


@pytest.mark.parametrize('fail_body', [False, True])
def test_scope_discards_copy_and_keeps_source(tmp_path, fail_body):
    """Remove the temporary copy on every exit without changing source files."""
    (tmp_path / 'file').write_bytes(b'original')
    manager = TemporaryDirectoryManager(tmp_path)
    expectation = (
        pytest.raises(ValueError, match='body failed') if fail_body else nullcontext()
    )

    with expectation, manager.scope as isolate:
        assert isolate.path != tmp_path
        assert (isolate.path / 'file').read_bytes() == b'original'
        (isolate.path / 'file').write_bytes(b'changed')
        (isolate.path / 'new').write_bytes(b'new')
        if fail_body:
            raise ValueError('body failed')

    assert not isolate.path.exists()
    assert (tmp_path / 'file').read_bytes() == b'original'
    assert not (tmp_path / 'new').exists()
