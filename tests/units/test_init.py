from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ('name', 'module'),
    [
        ('AbstractIsolate', 'throng.abstracts.abstract_isolate'),
        ('AbstractManager', 'throng.abstracts.abstract_manager'),
        ('RunResultProtocol', 'throng.abstracts.results'),
        ('throng', 'throng.slots'),
        ('local', 'throng.extensions.plugins'),
        ('temporary_directory', 'throng.extensions.plugins'),
    ],
)
def test_public_exports_match_implementation(name, module):
    """Provide the canonical implementation through each public package export."""
    assert getattr(import_module('throng'), name) is getattr(
        import_module(module),
        name,
    )
