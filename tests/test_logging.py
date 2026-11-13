"""Application-owned logging describes attempts without exposing command arguments."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conan.errors import ConanException  # type: ignore[import-untyped]

from coreref._internal.client import ConanClient
from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.operations import OperationPlan, OperationStep
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref


def test_plan_logs_are_structured_and_application_owns_handlers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    root_handlers = tuple(logging.getLogger().handlers)
    plan = OperationPlan((OperationStep("simulate", "top", conditional=True),))
    with caplog.at_level(logging.INFO, logger="coreref"):
        plan.log()
    entry = caplog.records[-1]
    assert entry.name == "coreref.operations"
    assert entry.getMessage() == "planned simulate top (if needed)"
    assert entry.__dict__["coreref_action"] == "simulate"
    assert entry.__dict__["coreref_target"] == "top"
    assert entry.__dict__["coreref_status"] == "planned"
    assert entry.__dict__["coreref_conditional"] is True
    assert tuple(logging.getLogger().handlers) == root_handlers


@pytest.mark.parametrize("failure", [False, True])
def test_debug_command_logs_omit_arguments_payloads_and_exception_text(
    failure: bool, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    client = ConanClient(tmp_path / "unused-home", "configured", 5)
    api = Mock()
    api.command.run.return_value = {}
    if failure:
        api.command.run.side_effect = ConanException("private-token private-payload")
    with patch.object(client, "_api", return_value=api), caplog.at_level(logging.DEBUG, "coreref"):
        if failure:
            with pytest.raises(CatalogError):
                client.run("install", "private-payload", "--password=private-token")
        else:
            client.run("install", "private-payload", "--password=private-token")
    messages = [entry.getMessage() for entry in caplog.records]
    assert messages == (
        ["started command install"]
        if failure
        else ["started command install", "completed command install"]
    )
    assert "private-token" not in caplog.text and "private-payload" not in caplog.text
    assert not (tmp_path / "unused-home").exists()


@pytest.mark.conan_remote
def test_execution_logs_match_plans_and_cached_reads_have_no_commands(
    catalog: Catalog, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    report = tmp_path / "report"
    report.write_text("artifact")
    child = LocalRecord(
        Ref("soc.cpu.rtl.default/1.0"), {"secret": "payload-content"}, files={"report": report}
    )
    caplog.set_level(logging.INFO, "coreref")
    published = catalog.publish(child)
    assert [entry.__dict__["coreref_action"] for entry in caplog.records] == [
        "package",
        "upload",
        "upload",
    ]
    assert caplog.records[-1].__dict__["coreref_status"] == "completed"
    assert caplog.records[-1].__dict__["coreref_target"] == str(published.ref)
    assert "payload-content" not in caplog.text
    caplog.clear()
    root = LocalRecord(Ref("soc.top.rtl.default/1.0"))
    graph = Graph(catalog, root, dependencies=(published.ref,))
    lock = graph.create_lockfile(tmp_path / "project.lock")
    ready = graph.get_with_sources()
    assert ready[0].files["report"].read_text() == "artifact"
    assert [entry.__dict__["coreref_action"] for entry in caplog.records] == [
        "resolve",
        "lock",
        "install",
    ]
    caplog.clear()
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected I/O")):
        assert graph.get_with_sources() == ready
        assert graph.info(published.ref).ref == published.ref
        assert [step.action for step in OperationPlan.publish(graph)] == ["package", "upload"]
        with pytest.raises(InvalidPackage, match="already defines"):
            OperationPlan.publish(graph, lockfile=lock)
    assert caplog.records == []
    catalog.search_refs(SearchQuery(component="cpu"))
    assert [entry.__dict__["coreref_action"] for entry in caplog.records] == ["search"]


@pytest.mark.conan_remote
def test_debug_metadata_and_commands_preserve_one_batched_source_install(
    catalog: Catalog, checkout: Path, git_origin: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert git_origin.is_dir()
    first = catalog.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0"), source=checkout))
    second = catalog.publish(LocalRecord(Ref("soc.std.rtl.default/1.0"), source=checkout))
    caplog.set_level(logging.DEBUG, "coreref")
    graph = Graph(
        catalog,
        LocalRecord(Ref("soc.top.rtl.default/1.0")),
        dependencies=(first.ref, second.ref),
    )
    installed = graph.get_with_sources()
    assert all(record.source is not None for record in installed)
    actions = [entry.__dict__["coreref_action"] for entry in caplog.records]
    assert actions.count("metadata") == 2
    assert actions.count("install") == 1
    commands = [
        entry.__dict__["coreref_target"]
        for entry in caplog.records
        if entry.__dict__["coreref_action"] == "command"
        and entry.__dict__["coreref_status"] == "completed"
    ]
    assert commands.count("install") == 1
    assert commands.count("graph info") == 1
    assert str(git_origin) not in caplog.text
    caplog.clear()
    assert graph.get_with_sources() == installed
    assert caplog.records == []


def test_failed_upload_has_no_success_event(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    client = ConanClient(tmp_path / "unused-home", "configured", 5)
    with (
        patch.object(client, "run", side_effect=CatalogError("Registry unavailable")),
        caplog.at_level(logging.INFO, "coreref"),
        pytest.raises(CatalogError, match="Registry unavailable"),
    ):
        client.upload("soc.cpu.rtl.default/1.0#abc")
    assert [entry.__dict__["coreref_status"] for entry in caplog.records] == ["started"]
