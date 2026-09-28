import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, call

import pytest

from throng.extensions.temporary_directory.read import read_directory


@pytest.mark.parametrize(
    'content',
    [b'', b'plain text', 'Привет\n世界'.encode(), bytes(range(256))],
)
@pytest.mark.parametrize('name', ['file', 'nested/file', 'one/two/file'])
def test_archive_preserves_file_data_and_relative_paths(tmp_path, name, content):
    """Preserve bytes and relative paths so snapshots can move to another directory."""
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)

    state = read_directory(tmp_path, None)

    assert isinstance(state, bytes)
    with tarfile.open(fileobj=BytesIO(state)) as archive:
        assert archive.getnames() == [name]
        assert archive.getmember(name).isfile()
        assert archive.extractfile(name).read() == content
    assert source.read_bytes() == content


@pytest.mark.parametrize(
    'name',
    [
        'with spaces.txt',
        'каталог/файл',
        'emoji-🌍',
        'many.dots.txt',
        '.hidden',
        '.hidden/file',
        'x' * 120,
    ],
)
def test_archive_preserves_supported_names(tmp_path, name):
    """Keep valid unusual names without losing or renaming their files."""
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'data')

    with tarfile.open(fileobj=BytesIO(read_directory(tmp_path, None))) as archive:
        assert archive.getnames() == [name]
        assert archive.extractfile(name).read() == b'data'


@pytest.mark.parametrize('tree', ['empty', 'directories', 'mixed'])
def test_empty_directories_are_not_archived(tmp_path, tree):
    """Transfer files only, including a valid empty archive when there are none."""
    if tree != 'empty':
        (tmp_path / 'empty' / 'nested').mkdir(parents=True)
    if tree == 'mixed':
        (tmp_path / 'file').write_bytes(b'data')

    with tarfile.open(fileobj=BytesIO(read_directory(tmp_path, None))) as archive:
        assert archive.getnames() == (['file'] if tree == 'mixed' else [])
        assert all(member.isfile() for member in archive.getmembers())


@pytest.mark.parametrize('path_kind', ['absolute', 'relative', 'current'])
def test_path_form_does_not_change_snapshot(tmp_path, monkeypatch, path_kind):
    """Produce the same relative file names for equivalent source paths."""
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    (source / 'file').write_bytes(b'root')
    (source / 'nested' / 'file').write_bytes(b'nested')
    monkeypatch.chdir(source if path_kind == 'current' else tmp_path)
    path = {'absolute': source, 'relative': Path('source'), 'current': Path()}[
        path_kind
    ]

    with tarfile.open(fileobj=BytesIO(read_directory(path, None))) as archive:
        assert {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
        } == {
            'file': b'root',
            'nested/file': b'nested',
        }


@pytest.mark.parametrize(
    ('patterns', 'expected'),
    [
        (None, ('keep.txt', 'drop.tmp', 'cache/file', 'nested/keep.txt')),
        ((), ('keep.txt', 'drop.tmp', 'cache/file', 'nested/keep.txt')),
        (('missing',), ('keep.txt', 'drop.tmp', 'cache/file', 'nested/keep.txt')),
        (('drop.tmp',), ('keep.txt', 'cache/file', 'nested/keep.txt')),
        (('*.tmp',), ('keep.txt', 'cache/file', 'nested/keep.txt')),
        (('cache/',), ('keep.txt', 'drop.tmp', 'nested/keep.txt')),
        (('*.tmp', 'cache/'), ('keep.txt', 'nested/keep.txt')),
        (('*',), ()),
        (
            ('*.txt', '!keep.txt'),
            ('keep.txt', 'drop.tmp', 'cache/file', 'nested/keep.txt'),
        ),
        (('!keep.txt', '*.txt'), ('drop.tmp', 'cache/file')),
        (('*.tmp', '*.tmp'), ('keep.txt', 'cache/file', 'nested/keep.txt')),
    ],
)
def test_exclusions_select_files_without_changing_source(tmp_path, patterns, expected):
    """Apply supported exclusion rules while leaving the original tree untouched."""
    names = ('keep.txt', 'drop.tmp', 'cache/file', 'nested/keep.txt')
    for name in names:
        source = tmp_path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode())
    exclude = None if patterns is None else list(patterns)

    with tarfile.open(fileobj=BytesIO(read_directory(tmp_path, exclude))) as archive:
        assert set(archive.getnames()) == set(expected)
        assert all(
            archive.extractfile(name).read() == name.encode() for name in expected
        )
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob('*')
        if path.is_file()
    } == {name: name.encode() for name in names}


@pytest.mark.parametrize('exclude', [None, [], ['*.tmp']])
def test_crawler_receives_source_and_exclusions(tmp_path, monkeypatch, exclude):
    """Delegate selection to the crawler and archive each returned relative path."""
    paths = [tmp_path / 'first', tmp_path / 'second']
    for path in paths:
        path.write_bytes(path.name.encode())
    crawler = Mock(return_value=iter(paths))
    monkeypatch.setattr('throng.extensions.temporary_directory.read.Crawler', crawler)

    with tarfile.open(fileobj=BytesIO(read_directory(tmp_path, exclude))) as archive:
        assert set(archive.getnames()) == {'first', 'second'}
    crawler.assert_called_once_with(tmp_path, exclude=exclude)


@pytest.mark.parametrize('change', ['add', 'modify', 'delete'])
def test_snapshots_are_independent_of_later_changes(tmp_path, change):
    """Keep previous snapshots immutable while later reads reflect source changes."""
    source = tmp_path / 'file'
    source.write_bytes(b'old')
    first = read_directory(tmp_path, None)
    if change == 'add':
        (tmp_path / 'new').write_bytes(b'new')
    elif change == 'modify':
        source.write_bytes(b'new')
    else:
        source.unlink()
    second = read_directory(tmp_path, None)

    with tarfile.open(fileobj=BytesIO(first)) as archive:
        assert archive.getnames() == ['file']
        assert archive.extractfile('file').read() == b'old'
    with tarfile.open(fileobj=BytesIO(second)) as archive:
        expected = {
            'add': {'file': b'old', 'new': b'new'},
            'modify': {'file': b'new'},
            'delete': {},
        }[change]
        assert {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
        } == expected


@pytest.mark.parametrize(
    'stage',
    ['crawler', 'open', 'iterate', 'first_file', 'second_file'],
)
@pytest.mark.parametrize('error_type', [FileNotFoundError, PermissionError])
def test_read_errors_propagate_and_close_open_archives(
    tmp_path,
    monkeypatch,
    stage,
    error_type,
):
    """Reject incomplete snapshots and close any archive already opened on failure."""
    error = error_type('snapshot failed')
    crawler = Mock(return_value=iter([tmp_path / 'one', tmp_path / 'two']))
    archive_context = MagicMock()
    archive = archive_context.__enter__.return_value
    open_archive = Mock(return_value=archive_context)
    if stage == 'crawler':
        crawler.side_effect = error
    elif stage == 'open':
        open_archive.side_effect = error
    elif stage == 'iterate':
        iterator = MagicMock()
        iterator.__iter__.side_effect = error
        crawler.return_value = iterator
    else:
        archive.add.side_effect = [None] * (stage == 'second_file') + [error]
    monkeypatch.setattr('throng.extensions.temporary_directory.read.Crawler', crawler)
    monkeypatch.setattr(
        'throng.extensions.temporary_directory.read.tarfile.open',
        open_archive,
    )

    with pytest.raises(error_type) as caught:
        read_directory(tmp_path, None)

    assert caught.value is error
    if stage in ('iterate', 'first_file', 'second_file'):
        archive_context.__exit__.assert_called_once()
        assert archive_context.__exit__.call_args.args[1] is error
    else:
        archive_context.__exit__.assert_not_called()
    if stage == 'crawler':
        open_archive.assert_not_called()
    if stage == 'second_file':
        assert archive.add.call_args_list == [
            call(tmp_path / 'one', arcname=Path('one')),
            call(tmp_path / 'two', arcname=Path('two')),
        ]
