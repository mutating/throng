import pytest


@pytest.fixture(params=['local', 'temporary_directory'])
def builtin_plugin_name(request):
    return request.param
