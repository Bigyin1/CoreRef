"""Internal typing for native Conan 2.34 objects.

These protocols describe existing Conan objects; they do not wrap or replace them.
Conan itself currently ships without typing metadata.
"""

from collections.abc import Iterable, Mapping, Sequence
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


class NativeFolders(Protocol):
    @property
    def source_folder(self) -> str | None: ...


class NativePackage(Protocol):
    @property
    def conan_data(self) -> Mapping[str, object] | None: ...

    @property
    def ref(self) -> NativeReference: ...

    @property
    def recipe_folder(self) -> str | None: ...

    @property
    def package_folder(self) -> str | None: ...

    @property
    def folders(self) -> NativeFolders: ...

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
    def recipe(self) -> str: ...

    @property
    def ref(self) -> NativeReference | None: ...

    @property
    def conanfile(self) -> NativePackage: ...


class NativeGraph(Protocol):
    @property
    def root(self) -> NativeNode: ...

    @property
    def nodes(self) -> Sequence[NativeNode]: ...

    def report_graph_error(self) -> None: ...

    def serialize(self) -> dict[str, object]: ...
