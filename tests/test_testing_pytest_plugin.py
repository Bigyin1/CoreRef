"""A dependent repository can opt into fixtures with one pytest_plugins entry."""

import pytest

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.conan_remote


def test_downstream_plugin_fixtures_marker_and_editable_project(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["coreref.testing.pytest_plugin"]')
    pytester.makepyfile("""
def test_flow(coreref_environment, coreref_catalog, coreref_project, coreref_server, monkeypatch):
    env = coreref_environment
    assert coreref_server is env.server
    cpu = env.package(env.ref("cpu"), sources={"cpu.sv": "RTL"})
    published = coreref_catalog.publish(cpu)
    coreref_project.add(published.ref.without_revision())
    coreref_project.save()
    assert coreref_project.resolve().get_with_sources()[0].source is not None
    workspace = env.workspace(coreref_catalog, directory=coreref_project.path.parent)
    workspace.add(cpu.source, record=cpu)
    monkeypatch.chdir(coreref_project.path.parent)
    assert coreref_project.resolve().get_with_sources()[0].is_editable

def test_unrelated():
    assert True
""")
    result = pytester.runpytest_inprocess("-q", "--strict-markers", "-m", "coreref")
    result.assert_outcomes(passed=1, deselected=1)
