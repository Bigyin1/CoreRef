"""Resolve once, run a tool, then publish its results using the same working record."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import Conflict
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref
from coreref.testing import git_commit
from coreref.tools.manifest import ManifestFile
from tests.test_models import UserPayload

pytestmark = pytest.mark.conan_remote


def test_tool_results_are_published_without_resolving_the_graph_again(
    catalog: Catalog, checkout: Path, git_origin: Path, tmp_path: Path
) -> None:
    assert git_origin.is_dir()
    leaf = catalog.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0")))
    manifest = ManifestFile.create(Ref("soc.top.rtl.default/1.0"), checkout / "manifest.yml")
    manifest.add(leaf.ref)
    manifest.save()
    git_commit(checkout, push=True)
    assert manifest.record.source == checkout
    original = manifest.path.read_bytes()
    payload = UserPayload(None)
    manifest.record.payload = payload
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        with patch.object(payload, "to_json", side_effect=AssertionError("Tool has not run")):
            graph = manifest.resolve(catalog)
        assert graph.record is manifest.record
        assert graph.info(manifest.record.ref) is manifest.record
        assert graph.ordered_records(manifest.record.ref)[-1] is manifest.record
        report = tmp_path / "report.txt"
        report.write_text("tool output")
        manifest.record.files["report.txt"] = report
        payload.data = {"result": "completed"}
        published = catalog.publish(graph)
        assert sum(call.args[:2] == ("graph", "info") for call in commands.call_args_list) == 1
        assert sum(call.args[0] == "export-pkg" for call in commands.call_args_list) == 1
    assert payload.calls == 1
    assert published.payload == {"result": "completed"}
    assert published.files["report.txt"].read_text() == "tool output"
    assert published.has_source
    assert manifest.path.read_bytes() == original
    assert manifest.record.ref.revision is None
    installed = catalog.get_with_sources(published.ref)
    assert installed.source is not None
    assert (installed.source / "manifest.yml").read_bytes() == original


@pytest.mark.parametrize("prepared", [False, True])
def test_additional_dependency_uses_explicit_resolution_and_keeps_previous_choices(
    catalog: Catalog, tmp_path: Path, prepared: bool
) -> None:
    rtl = catalog.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0")))
    macro = catalog.publish(LocalRecord(Ref("soc.cpu.be.lib-lef/1.0")), dependencies=(rtl.ref,))
    manifest = ManifestFile.create(Ref("soc.top.be.result/1.0"), tmp_path / "manifest.yml")
    manifest.record.source = None  # Artifact-only result, without SCM.
    manifest.add(rtl.ref.without_revision())
    manifest.save()
    graph = manifest.resolve(catalog)
    graph.create_lockfile()
    before = manifest.lockfile.read_bytes()
    catalog.publish(LocalRecord(rtl.ref.without_revision(), {"generation": 2}))
    manifest.record.payload = {"selected_macro": str(macro.ref)}
    requirements = (*graph.requirements, macro.ref)
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        if prepared:
            augmented = Graph(catalog, manifest.record, dependencies=requirements)
            augmented.create_lockfile(manifest.lockfile)
            published = catalog.publish(augmented)
        else:
            published = catalog.publish(manifest.record, dependencies=requirements)
        assert sum(call.args[:2] == ("graph", "info") for call in commands.call_args_list) == 1
        assert sum(call.args[0] == "export-pkg" for call in commands.call_args_list) == 1
    assert graph.requirements == (rtl.ref,)
    assert manifest.dependencies == (rtl.ref.without_revision(),)
    assert set(Graph(catalog, published.ref).requirements) == {rtl.ref, macro.ref}
    assert (manifest.lockfile.read_bytes() != before) == prepared
    assert ManifestFile(manifest.path).record.payload is None


def test_conflicting_extra_dependency_does_not_replace_the_existing_graph(
    catalog: Catalog, tmp_path: Path
) -> None:
    rtl = catalog.publish(LocalRecord(Ref("soc.cpu.rtl.default/1.0")))
    root = LocalRecord(Ref("soc.top.be.result/1.0"))
    graph = Graph(catalog, root, dependencies=(rtl.ref,))
    lock = graph.create_lockfile(tmp_path / "project.lock")
    original = lock.read_bytes()
    other_rtl = catalog.publish(LocalRecord(rtl.ref.without_revision(), {"generation": 2}))
    macro = catalog.publish(
        LocalRecord(Ref("soc.cpu.be.lib-lef/1.0")), dependencies=(other_rtl.ref,)
    )
    with (
        patch.object(catalog._client, "export_package", side_effect=AssertionError("Export")),
        pytest.raises(Conflict),
    ):
        catalog.publish(root, dependencies=(*graph.requirements, macro.ref))
    assert graph.record is root and graph.requirements == (rtl.ref,)
    assert graph.info(rtl.ref).ref == rtl.ref
    assert lock.read_bytes() == original
