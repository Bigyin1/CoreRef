"""Scoped operation counters for deterministic integration assertions, not timing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from conan.tools.scm import Git  # type: ignore[import-untyped]
from requests import PreparedRequest, Response, Session

from .._internal.client import ConanClient
from .._internal.recipe import StoredRecipe
from .._internal.types import NativePackage


@dataclass
class OperationTrace:
    """Snapshot counters: attempts for commands/reads/clones, successful archive GETs."""

    _commands: list[tuple[str, ...]] = field(default_factory=list, repr=False)
    _archives: list[str] = field(default_factory=list, repr=False)
    _clones: list[str] = field(default_factory=list, repr=False)
    _recipes: list[str] = field(default_factory=list, repr=False)

    def _download(self, request: PreparedRequest, response: Response) -> None:
        if request.method != "GET" or response.status_code != HTTPStatus.OK:
            return
        if request.url and request.url == response.url:
            self._archives.append(request.url)

    @property
    def commands(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._commands)

    @property
    def installs(self) -> int:
        return sum(command[0] == "install" for command in self._commands)

    @property
    def recipe_downloads(self) -> int:
        return sum(urlsplit(url).path.endswith("/conan_export.tgz") for url in self._archives)

    @property
    def package_downloads(self) -> int:
        return sum(urlsplit(url).path.endswith("/conan_package.tgz") for url in self._archives)

    @property
    def source_clones(self) -> int:
        return len(self._clones)

    @property
    def recipe_reads(self) -> int:
        return len(self._recipes)


@contextmanager
def trace_operations() -> Iterator[OperationTrace]:
    """Observe SDK commands, metadata reads, archive downloads and Conan Git clones.

    Instrumentation is process-wide until context exit, including other catalogs.
    Use sequentially, outside measured benchmarks; mocks are restored on failure.
    Raw command arguments and URLs are retained only in the returned in-memory trace.
    """
    trace = OperationTrace()
    originals = (ConanClient.run, StoredRecipe.__init__, Session.send, Git.clone)

    def run(client: ConanClient, *arguments: str, workspace: Path | None = None) -> object:
        trace._commands.append(arguments)
        return originals[0](client, *arguments, workspace=workspace)

    def read(
        recipe: StoredRecipe,
        directory: Path,
        *,
        reference: str,
        native: NativePackage | None = None,
    ) -> None:
        trace._recipes.append(reference)
        originals[1](recipe, directory, reference=reference, native=native)

    def send(session: Session, request: PreparedRequest, **kwargs: object) -> Response:
        response = originals[2](session, request, **kwargs)
        trace._download(request, response)
        return response

    def clone(  # noqa: PLR0917 - Match the wrapped Conan Git.clone signature.
        git: Git,
        url: str,
        target: str = "",
        args: list[str] | None = None,
        hide_url: bool = True,
    ) -> None:
        trace._clones.append(url)
        originals[3](git, url, target=target, args=args, hide_url=hide_url)

    with ExitStack() as resources:
        resources.enter_context(patch.object(ConanClient, "run", autospec=True, side_effect=run))
        resources.enter_context(
            patch.object(StoredRecipe, "__init__", autospec=True, side_effect=read)
        )
        resources.enter_context(patch.object(Session, "send", autospec=True, side_effect=send))
        resources.enter_context(patch.object(Git, "clone", autospec=True, side_effect=clone))
        yield trace
