from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, Union


@dataclass(frozen=True)
class BenchmarkResult:
    number: Union[int, float]
    when: datetime
    benchmarker_version: str
    benchmarker: str = 'benchset'


@dataclass(frozen=True)
class Benchmarks:
    single_core: Optional[BenchmarkResult]
    multi_core: Optional[BenchmarkResult]


@dataclass(frozen=True)
class IsolateResourcesDescription:
    """
    Describe sharing with other isolates using their get_id() values.

    shared_with excludes this isolate's own ID. None means the neighbors are
    unknown; an empty tuple means there are no neighbors.
    """

    shared: bool
    shared_with: Optional[Tuple[str, ...]]


@dataclass(frozen=True)
class Resources:
    benchmarks: Benchmarks
    shared: bool
    shared_with: Optional[Tuple[str, ...]]


@dataclass(frozen=True)
class IsolateMeta:
    id: str
    resources: Resources
