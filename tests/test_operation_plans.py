"""Dry-run descriptions are independent of Conan, filesystem state and payload encoding."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from coreref.api.errors import InvalidPackage
from coreref.api.operations import OperationPlan, OperationStep
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref

pytestmark = pytest.mark.usefixtures("no_io")


@pytest.fixture
def no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "coreref._internal.client.ConanAPI",
        "coreref._internal.client.ConanClient.run",
        "coreref._internal.package.TemporaryDirectory",
        "requests.sessions.Session.request",
        "pathlib.Path.write_text",
        "pathlib.Path.mkdir",
    ):
        monkeypatch.setattr(target, Mock(side_effect=AssertionError(f"Unexpected I/O: {target}")))


class UnbuiltPayload:
    def to_json(self) -> str:
        raise AssertionError("Dry-run must not evaluate the flow payload")


def test_publish_plan_accepts_unbuilt_files_and_source_without_serializing_payload() -> None:
    record = LocalRecord(
        Ref("soc.cpu.rtl.default/1.0"),
        UnbuiltPayload(),
        source=Path("not-created-yet"),
        files={"report": Path("future-report")},
    )
    plan = OperationPlan.publish(record, dependencies=(Ref("soc.std.rtl.default/1.0"),))
    assert [step.action for step in plan] == ["resolve", "package", "upload"]
    assert "1 files; capture SCM=True" in str(plan.steps[1])
    assert "soc.std.rtl.default/1.0" in plan.steps[0].detail
    record.ref = Ref("soc.changed.rtl.default/2.0")
    record.files = {}
    assert plan.steps[1].target == "soc.cpu.rtl.default/1.0"
    assert "1 files" in plan.steps[1].detail


def test_leaf_publication_does_not_plan_an_extra_resolution() -> None:
    plan = OperationPlan.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0")))
    assert [step.action for step in plan] == ["package", "upload"]


@pytest.mark.parametrize("sources", [False, True])
def test_install_keeps_unknown_transitive_selection_and_conditional_cache_work(
    sources: bool,
) -> None:
    root = LocalRecord(Ref("soc.top.rtl.default/1.0"))
    plan = OperationPlan.install(
        root,
        dependencies=(Ref("soc.cpu.rtl.default/1.0#abc"),),
        lockfile=Path("project.lock"),
        sources=sources,
    )
    assert [step.action for step in plan] == ["resolve", "install"]
    assert "project.lock" in plan.steps[0].detail
    assert plan.steps[1].target == "resolved packages"
    assert plan.steps[1].conditional
    assert ("Git sources" in plan.steps[1].detail) == sources


def test_empty_local_project_needs_no_install_but_stored_root_does() -> None:
    ref = Ref("soc.cpu.rtl.default/1.0")
    local = OperationPlan.install(LocalRecord(ref))
    stored = OperationPlan.install(ref)
    assert [step.action for step in local] == ["resolve"]
    assert "no direct requirements" in local.steps[0].detail
    assert [step.action for step in stored] == ["resolve", "install"]
    assert "stored recipe" in stored.steps[0].detail


@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize("history", [False, True])
def test_search_plan_preserves_query_and_metadata_mode(metadata: bool, history: bool) -> None:
    plan = OperationPlan.search(
        SearchQuery(namespace="soc", component="cpu", view="be"),
        all_revisions=history,
        metadata=metadata,
    )
    assert plan.steps[0].target == f"soc.cpu*.be.*/*#{'*' if history else 'latest'}"
    assert [step.action for step in plan] == (["search", "metadata"] if metadata else ["search"])


def test_lock_plan_does_not_read_or_write_project_lockfiles() -> None:
    root = LocalRecord(Ref("soc.top.rtl.default/1.0"))
    plan = OperationPlan.lock(root, Path("new.lock"), lockfile=Path("old.lock"))
    assert [step.action for step in plan] == ["resolve", "lock"]
    assert "old.lock" in plan.steps[0].detail
    assert plan.steps[1].target == "new.lock"


@pytest.mark.parametrize("operation", [OperationPlan.resolve, OperationPlan.publish])
def test_local_root_cannot_be_revision_pinned(
    operation: Callable[[LocalRecord], OperationPlan],
) -> None:
    with pytest.raises(InvalidPackage, match=r"local.*revision"):
        operation(LocalRecord(Ref("soc.top.rtl.default/1.0#abc")))


def test_stored_graph_does_not_accept_overridden_requirements() -> None:
    with pytest.raises(InvalidPackage, match="recipe"):
        OperationPlan.resolve(
            Ref("soc.top.rtl.default/1.0"), dependencies=(Ref("soc.cpu.rtl.default/1.0"),)
        )


def test_step_formatting_and_custom_flow_composition() -> None:
    plan = OperationPlan(
        (
            OperationStep("resolve", "soc.top.rtl.default/1.0"),
            OperationStep("simulate", "top", "run the selected flow tool", conditional=True),
        )
    )
    assert str(plan.steps[0]) == "resolve soc.top.rtl.default/1.0"
    assert str(plan.steps[1]) == "simulate top (if needed): run the selected flow tool"
