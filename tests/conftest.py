import pytest


@pytest.fixture(params=['local', 'directories'])
def builtin_plugin_name(request):
    return request.param
