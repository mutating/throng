from datetime import datetime

import pytest

from throng import AbstractIsolate
from throng.abstracts.meta import (
    BenchmarkResult,
    Benchmarks,
    IsolateMeta,
    IsolateResourcesDescription,
    Resources,
)
from throng.extensions.local.isolate import LocalIsolate


@pytest.mark.mypy_testing
def mypy_metadata_accepts_supported_values_and_access(isolate: AbstractIsolate, when: datetime) -> None:
    """Accept supported values and retain precise public types through the abstract isolate.

    Separate tuple lengths protect the variadic peer type. Reveal notes check
    the actual inferred types, since assignments alone would also accept Any.
    These function bodies are checked by mypy rather than executed by pytest.
    """
    single = BenchmarkResult(7, when, '0.0.2')
    multi = BenchmarkResult(7.5, when, 'custom-3', benchmarker='other-suite')
    Benchmarks(single, None)
    Benchmarks(None, multi)
    benchmarks = Benchmarks(single, multi)
    IsolateResourcesDescription(False, None)
    IsolateResourcesDescription(True, ())
    IsolateResourcesDescription(True, ('peer',))
    IsolateResourcesDescription(True, ('peer-a', 'peer-b'))
    Resources(benchmarks, False, None)
    Resources(benchmarks, True, ())
    Resources(benchmarks, True, ('peer',))
    resources = Resources(benchmarks, True, ('peer-a', 'peer-b'))
    IsolateMeta('isolate', resources)

    reveal_type(isolate.meta)  # R: throng.abstracts.meta.IsolateMeta
    reveal_type(isolate.meta.id)  # R: builtins.str
    reveal_type(isolate.meta.resources)  # R: throng.abstracts.meta.Resources
    reveal_type(isolate.meta.resources.benchmarks)  # R: throng.abstracts.meta.Benchmarks
    reveal_type(isolate.meta.resources.shared)  # R: builtins.bool
    reveal_type(isolate.meta.resources.shared_with)  # R: Union[builtins.tuple[builtins.str, ...], None]
    reveal_type(isolate.get_id())  # R: builtins.str
    reveal_type(isolate.get_benchmarks())  # R: throng.abstracts.meta.Benchmarks
    reveal_type(isolate.get_resources_description())  # R: throng.abstracts.meta.IsolateResourcesDescription
    result = isolate.meta.resources.benchmarks.single_core
    reveal_type(result)  # R: Union[throng.abstracts.meta.BenchmarkResult, None]
    reveal_type(isolate.meta.resources.benchmarks.multi_core)  # R: Union[throng.abstracts.meta.BenchmarkResult, None]
    if result is not None:
        reveal_type(result.number)  # R: Union[builtins.int, builtins.float]
        reveal_type(result.when)  # R: datetime.datetime
        reveal_type(result.benchmarker_version)  # R: builtins.str
        reveal_type(result.benchmarker)  # R: builtins.str


@pytest.mark.mypy_testing
def mypy_metadata_rejects_unknown_fields(isolate: AbstractIsolate, result: BenchmarkResult, when: datetime) -> None:
    """Catch misspelled fields at each metadata level and misspelled constructor keywords."""
    _ = isolate.meta.resource  # E: [attr-defined]
    _ = isolate.meta.resources.benchmarks.single_cor  # E: [attr-defined]
    _ = result.benchmarker_verison  # E: [attr-defined]
    BenchmarkResult(1, when, '0.0.2', benchmaker='other-suite')  # E: [call-arg]


@pytest.mark.mypy_testing
def mypy_metadata_rejects_incompatible_constructor_arguments(when: datetime, benchmarks: Benchmarks, resources: Resources) -> None:
    """Reject invalid field types without assuming dataclasses validate values at runtime.

    Both peer annotations and both optional benchmark slots are independent
    declarations, so each has its own deliberately invalid constructor call.
    """
    BenchmarkResult('invalid', when, '0.0.2')  # E: [arg-type]
    BenchmarkResult(1, 'invalid', '0.0.2')  # E: [arg-type]
    BenchmarkResult(1, when, 2)  # E: [arg-type]
    BenchmarkResult(1, when, '0.0.2', benchmarker=2)  # E: [arg-type]
    Benchmarks('invalid', None)  # E: [arg-type]
    Benchmarks(None, 'invalid')  # E: [arg-type]
    IsolateResourcesDescription(1, None)  # E: [arg-type]
    IsolateResourcesDescription(True, 'peer')  # E: [arg-type]
    IsolateResourcesDescription(True, ['peer'])  # E: [arg-type]
    IsolateResourcesDescription(True, (1,))  # E: [arg-type]
    Resources(resources, True, None)  # E: [arg-type]
    Resources(benchmarks, 1, None)  # E: [arg-type]
    Resources(benchmarks, True, 'peer')  # E: [arg-type]
    Resources(benchmarks, True, ['peer'])  # E: [arg-type]
    Resources(benchmarks, True, (1,))  # E: [arg-type]
    IsolateMeta(1, resources)  # E: [arg-type]
    IsolateMeta('isolate', benchmarks)  # E: [arg-type]


@pytest.mark.mypy_testing
def mypy_metadata_rejects_frozen_field_assignment(meta: IsolateMeta, description: IsolateResourcesDescription) -> None:
    """Reject writes to all five frozen models and to the nested peer tuple.

    Narrow optional values before attempting writes, so the expected errors
    concern immutability rather than unsafe access to None.
    """
    if meta.resources.benchmarks.single_core is not None:
        meta.resources.benchmarks.single_core.number = 2  # E: [misc]
    meta.id = 'other'  # E: [misc]
    meta.resources.shared = False  # E: [misc]
    meta.resources.benchmarks.single_core = None  # E: [misc]
    description.shared_with = ('other',)  # E: [misc]
    if meta.resources.shared_with is not None:
        meta.resources.shared_with[0] = 'other'  # E: [index]


@pytest.mark.mypy_testing
def mypy_metadata_requires_optional_result_narrowing(benchmarks: Benchmarks) -> None:
    """Require clients to handle unavailable single-core and multi-core results separately."""
    _ = benchmarks.single_core.number  # E: [union-attr]
    _ = benchmarks.multi_core.number  # E: [union-attr]


@pytest.mark.mypy_testing
def mypy_metadata_provider_overrides_require_declared_return_types() -> None:
    """Check metadata provider signatures for plugin authors, not only metadata consumers.

    Local subclasses are source examples for mypy, not runtime test helpers.
    Invalid subclasses need not be instantiated to check an override. The valid
    example inherits concrete execution methods so no unrelated stubs are needed.
    """
    class InvalidId(AbstractIsolate):
        def get_id(self) -> int:  # E: [override]
            return 1

    class InvalidBenchmarks(AbstractIsolate):
        def get_benchmarks(self) -> None:  # E: [override]
            return None

    class InvalidDescription(AbstractIsolate):
        def get_resources_description(self) -> BenchmarkResult:  # E: [override]
            return BenchmarkResult(1, datetime(2026, 10, 19), '0.0.2')

    class ValidMetadata(LocalIsolate):
        def get_id(self) -> str:
            return 'typed-isolate'

        def get_benchmarks(self) -> Benchmarks:
            return Benchmarks(None, None)

        def get_resources_description(self) -> IsolateResourcesDescription:
            return IsolateResourcesDescription(False, None)
