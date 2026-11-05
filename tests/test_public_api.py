"""Public domain models import without Conan; the adapter is loaded explicitly."""

import subprocess
import sys
from textwrap import dedent


def test_public_models_import_independently_of_conan() -> None:
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
        from hwcatalog.backends.conan import ConanBackend

        home = TemporaryDirectory()
        from conan.api.conan_api import ConanAPI
        native = ConanAPI(cache_folder=home.name)
        native.remotes.disable("*")
        profiles = Path(home.name) / "profiles"
        profiles.mkdir(exist_ok=True)
        (profiles / "default").write_text("[settings]\\nos=Linux\\n")
        backend: protocols.Backend = ConanBackend(Path(home.name))
        manifest: dict[str, types.JSON] = {"views": ["rtl", "timing"]}
        record = records.Record(
            ref=records.Ref("rtl", "v1", "rtl-project", "public"),
            payload=manifest,
        )
        with catalog.Catalog(backend) as instance:
            instance.publish(record=record)
            stored = instance.get(record.ref)
            assert stored.ref == record.ref
            assert stored.payload["views"] == ("rtl", "timing")
            assert instance.search(query.SearchQuery()) == (stored,)
            assert instance.dependencies(record.ref) == ()
            assert instance.resolve_local(stored).records == (stored,)
            try:
                instance.get(records.Ref("absent", "v1", "rtl-project", "public"))
            except errors.NotFound:
                pass
            else:
                raise AssertionError("Domain errors must use the public error contract")

        with TemporaryDirectory() as directory:
            local = resources.LocatedResource("nfs", directory).materialize()
            assert local.path == Path(directory).resolve()

        home.cleanup()
        assert "dulwich" not in sys.modules, "Conan source retrieval uses its native Git helper"
    """)
    result = subprocess.run(  # noqa: S603 - fixed script with the current Python interpreter
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
