"""Typing for the native Conan 2.34 objects returned by ConanBackend.load_graph.

These protocols describe existing Conan objects; they do not wrap or replace them.
Conan itself currently ships without typing metadata.
"""

from collections.abc import Iterable, Sequence
from typing import Protocol


class NativeReference(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> object: ...

    @property
    def revision(self) -> str | None: ...

    def repr_notime(self) -> str: ...


class NativeConfiguration(Protocol):
    def dumps(self) -> str: ...


class NativeDependencies(Protocol):
    @property
    def host(self) -> NativeDependencies: ...

    @property
    def direct_host(self) -> NativeDependencies: ...

    @property
    def build(self) -> NativeDependencies: ...

    @property
    def topological_sort(self) -> NativeDependencies: ...

    def values(self) -> Iterable[NativePackage]: ...

    def __getitem__(self, name: str) -> NativePackage: ...


class NativePackage(Protocol):
    @property
    def ref(self) -> NativeReference: ...

    @property
    def recipe_folder(self) -> str | None: ...

    @property
    def dependencies(self) -> NativeDependencies: ...

    @property
    def context(self) -> str: ...

    @property
    def settings(self) -> NativeConfiguration: ...

    @property
    def options(self) -> NativeConfiguration: ...


class NativeNode(Protocol):
    @property
    def conanfile(self) -> NativePackage: ...


class NativeGraph(Protocol):
    @property
    def root(self) -> NativeNode: ...

    @property
    def nodes(self) -> Sequence[NativeNode]: ...

    def report_graph_error(self) -> None: ...

    def serialize(self) -> dict[str, object]: ...
