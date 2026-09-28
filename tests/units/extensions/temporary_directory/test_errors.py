from throng.extensions.temporary_directory.errors import DirectoryDoesNotExistError


def test_destroyed_directory_has_specific_error_type():
    """Let callers distinguish a destroyed directory from other execution failures."""
    assert issubclass(DirectoryDoesNotExistError, Exception)
    assert DirectoryDoesNotExistError is not Exception
