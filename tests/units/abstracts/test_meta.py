from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from throng.abstracts.meta import (
    BenchmarkResult,
    Benchmarks,
    IsolateMeta,
    IsolateResourcesDescription,
    Resources,
)


@pytest.mark.parametrize('operation', ['assign', 'delete'])
@pytest.mark.parametrize('target_case', ['isolate_meta', 'resources', 'resources_internet', 'benchmarks', 'benchmarks_equivalent', 'benchmark_result', 'benchmark_result_equivalent', 'resources_description'])
def test_metadata_models_reject_field_mutation(target_case, operation):
    """Keep all metadata models immutable, including values reached through a snapshot.

    Each case uses a fresh populated tree and a valid replacement value, so a
    FrozenInstanceError proves immutability rather than an invalid field type.
    Inspect the original field afterwards to catch mutation before an error.
    """
    result = BenchmarkResult(12.5, datetime(2026, 10, 19, tzinfo=timezone.utc), '0.0.2', equivalent=1.5)
    meta = IsolateMeta('isolate-a', Resources(Benchmarks(result, None, 2.0), True, ('peer-a',), True))
    description = IsolateResourcesDescription(True, ('peer-a',), True)
    target, field, replacement = {
        'isolate_meta': (meta, 'id', 'other-isolate'),
        'resources': (meta.resources, 'shared', False),
        'resources_internet': (meta.resources, 'internet', False),
        'benchmarks': (meta.resources.benchmarks, 'single_core', None),
        'benchmarks_equivalent': (meta.resources.benchmarks, 'equivalent', 3.0),
        'benchmark_result': (meta.resources.benchmarks.single_core, 'number', 99.5),
        'benchmark_result_equivalent': (meta.resources.benchmarks.single_core, 'equivalent', 3.0),
        'resources_description': (description, 'shared_with', ('other-peer',)),
    }[target_case]
    original = getattr(target, field)
    mutate = setattr if operation == 'assign' else delattr
    arguments = (target, field, replacement) if operation == 'assign' else (target, field)

    with pytest.raises(FrozenInstanceError):
        mutate(*arguments)

    assert getattr(target, field) == original


@pytest.mark.parametrize(
    ('number', 'when', 'benchmarker_version', 'provider_kwargs', 'expected'),
    [
        (42, datetime(2026, 10, 19, 12, tzinfo=timezone.utc), '0.0.2', {}, ('benchset', None)),
        (
            42.5,
            datetime(2026, 10, 20, 15, 30, 1, 123456, tzinfo=timezone(timedelta(hours=3))),
            'custom-3', {'benchmarker': 'other-suite', 'equivalent': 1.5}, ('other-suite', 1.5),
        ),
    ],
)
def test_benchmark_result_preserves_measurement(number, when, benchmarker_version, provider_kwargs, expected):
    """Preserve each measurement field and distinguish the default from an explicit provider.

    Compare the timezone separately: equal datetimes can represent the same
    instant with different offsets, which would hide unwanted normalization.
    Expected fields come from input constants rather than a second constructor.
    """
    expected_provider, expected_equivalent = expected
    result = BenchmarkResult(number, when, benchmarker_version, **provider_kwargs)

    assert result.number == number
    assert result.when == when
    assert result.when.tzinfo == when.tzinfo
    assert result.benchmarker_version == benchmarker_version
    assert result.benchmarker == expected_provider
    assert result.equivalent == expected_equivalent
