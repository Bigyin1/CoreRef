"""Read-only operation descriptions shared by dry-run plans and execution logs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .errors import InvalidPackage
from .query import SearchQuery
from .records import LocalRecord, Ref

if TYPE_CHECKING:
    from .graph import Graph

_LOG = logging.getLogger("coreref.operations")


@dataclass(frozen=True)
class OperationStep:
    """One semantic SDK action; conditional actions may be skipped during execution."""

    action: str
    target: str
    detail: str = ""
    conditional: bool = False

    def __str__(self) -> str:
        condition = " (if needed)" if self.conditional else ""
        detail = f": {self.detail}" if self.detail else ""
        return f"{self.action} {self.target}{condition}{detail}"

    def log(
        self,
        *,
        status: Literal["planned", "started", "completed"] = "started",
        level: int = logging.INFO,
    ) -> None:
        """Emit structured fields without configuring application handlers or levels."""
        _LOG.log(
            level,
            "%s %s",
            status,
            self,
            extra={
                "coreref_action": self.action,
                "coreref_target": self.target,
                "coreref_status": status,
                "coreref_conditional": self.conditional,
            },
        )


@dataclass(frozen=True)
class OperationPlan:
    """Pure intent, not an executor: no Conan/cache access, file writes or payload serialization.

    Transitive refs, cache misses, remote availability and new recipe revisions
    remain unknown. Conditional steps describe work Conan may need to perform.
    Construct plans before Catalog/Graph for a strictly side-effect-free dry-run.
    """

    steps: tuple[OperationStep, ...]

    def __iter__(self) -> Iterator[OperationStep]:
        return iter(self.steps)

    def log(self) -> None:
        for step in self.steps:
            step.log(status="planned")

    @classmethod
    def resolve(
        cls,
        record: LocalRecord | Ref,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> OperationPlan:
        ref = record.ref if isinstance(record, LocalRecord) else record
        if isinstance(record, LocalRecord) and ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        if isinstance(record, Ref) and dependencies:
            raise InvalidPackage("Published graph requirements come from its recipe")
        requirements = "requirements from the stored recipe"
        if isinstance(record, LocalRecord):
            requirements = ", ".join(map(str, dependencies)) or "no direct requirements"
        selection = (
            f"lockfile {lockfile}" if lockfile is not None else "configured registry and profiles"
        )
        return cls((OperationStep("resolve", str(ref), f"{requirements}; {selection}"),))

    @classmethod
    def install(
        cls,
        record: LocalRecord | Ref,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
        sources: bool = True,
    ) -> OperationPlan:
        plan = cls.resolve(record, dependencies=dependencies, lockfile=lockfile)
        if isinstance(record, LocalRecord) and not dependencies:
            return plan
        contents = "files and declared Git sources" if sources else "files"
        return cls(
            (
                *plan.steps,
                OperationStep(
                    "install",
                    "resolved packages",
                    f"{contents}; reuse cache and editable directories",
                    conditional=True,
                ),
            )
        )

    @classmethod
    def publish(
        cls,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> OperationPlan:
        from .graph import Graph

        prepared = isinstance(record, Graph)
        if prepared and (dependencies or lockfile is not None):
            raise InvalidPackage("A resolved graph already defines its dependencies and lockfile")
        root = record.record if isinstance(record, Graph) else record
        if not isinstance(root, LocalRecord) or root.ref.revision is not None:
            raise InvalidPackage("Publication requires a local root without a revision")
        steps: tuple[OperationStep, ...] = ()
        if not prepared and dependencies:
            steps = cls.resolve(root, dependencies=dependencies, lockfile=lockfile).steps
        detail = f"{len(root.files)} files; capture SCM={root.source is not None}"
        return cls(
            (
                *steps,
                OperationStep("package", str(root.ref), detail),
                OperationStep("upload", str(root.ref), "configured registry"),
            )
        )

    @classmethod
    def search(
        cls,
        query: SearchQuery,
        *,
        all_revisions: bool = False,
        metadata: bool = False,
    ) -> OperationPlan:
        steps: tuple[OperationStep, ...] = (
            OperationStep("search", query._conan_pattern(all_revisions=all_revisions)),
        )
        if metadata:
            steps += (OperationStep("metadata", "matching packages", conditional=True),)
        return cls(steps)

    @classmethod
    def lock(
        cls,
        record: LocalRecord | Ref,
        path: Path,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> OperationPlan:
        plan = cls.resolve(record, dependencies=dependencies, lockfile=lockfile)
        return cls((*plan.steps, OperationStep("lock", str(path), "save Conan lockfile")))


__all__ = ["OperationPlan", "OperationStep"]
