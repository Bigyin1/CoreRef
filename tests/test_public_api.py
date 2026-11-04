"""The public API and in-memory catalog work without importing the Conan adapter."""

import subprocess
import sys
from textwrap import dedent


def test_public_api_and_memory_backend_import_independently_of_conan() -> None:
    code = dedent("""
        import sys
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from hwcatalog import api, backends

        assert "pydantic" not in sys.modules
        assert "networkx" not in sys.modules
        assert "conan" not in sys.modules

        from hwcatalog.api import errors

        assert "hwcatalog.api.models" not in sys.modules
        assert "pydantic" not in sys.modules
        assert "conan" not in sys.modules

        from hwcatalog.api import models

        assert "pydantic" not in sys.modules
        assert "networkx" not in sys.modules

        from hwcatalog.api.models import resources

        assert "hwcatalog.api.models.records" not in sys.modules
        assert "networkx" not in sys.modules
        assert "conan" not in sys.modules

        from hwcatalog.api.models import records

        assert "networkx" not in sys.modules
        assert "pydantic" not in sys.modules

        from hwcatalog.api import catalog, protocols, types
        from hwcatalog.api.models import query
        from hwcatalog.backends.memory import MemoryBackend

        backend: protocols.Backend = MemoryBackend()
        manifest: dict[str, types.JSON] = {"views": ["rtl", "timing"]}
        record = records.Record(
            ref=records.Ref("rtl", "v1", "rtl-project", "public"),
            payload=manifest,
        )
        with catalog.Catalog(backend) as instance:
            instance.publish(records=(record,))
            stored = instance.get(record.ref)
            assert stored.ref == record.ref
            assert stored.payload["views"] == ("rtl", "timing")
            assert instance.search(query.SearchQuery()) == (stored,)
            assert instance.graph.dependencies(record.ref) == ()
            assert instance.graph.dependency_closure(record.ref).records == (stored,)
            try:
                instance.get(records.Ref("absent", "v1", "rtl-project", "public"))
            except errors.NotFound:
                pass
            else:
                raise AssertionError("Domain errors must use the public error contract")

        with TemporaryDirectory() as directory:
            local = resources.LocatedResource("nfs", directory).materialize()
            assert local.path == Path(directory).resolve()

        assert "conan" not in sys.modules, "Public API and memory must not import Conan"
        assert "hwcatalog._internal.backends.conan" not in sys.modules
        assert "conan" not in sys.modules
        assert "dulwich" not in sys.modules, "Git support must be loaded only when called"
    """)
    result = subprocess.run(  # noqa: S603 - fixed script with the current Python interpreter
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
