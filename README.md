# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34. Отдельный проект создан
на основе hwcatalog, коммит `3dba7e2`; имя дистрибутива — hwcatalog-conan,
Python-импорты — `hwcatalog`.

API описывает записи и точные зависимости пакетов. Узел хранится как рецепт Conan
с JSON-содержимым и собственным `conan.lock`. Имя имеет формат
`namespace.name.kind/version`, например `soc1.cpu.lib-lef/4.0`.
Тип представления задаётся полем Ref.kind; типизации рёбер нет.

```python
from pathlib import Path
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend

rtl = Record(Ref("cpu", "2.3", "rtl", "soc1"))
macro = Record(Ref("cpu", "4.0", "lib-lef", "soc1"), {"files": ["/artifacts/cpu.lib"]})
with Catalog(ConanBackend(Path("/absolute/path/to/conan-home"))) as catalog:
    catalog.publish((rtl, macro), dependencies=(Dependency(macro.ref, rtl.ref),))
    inputs = catalog.graph.dependencies(macro.ref)  # tuple[Record, ...]
    implementations = catalog.find_dependents(
        rtl.ref,
        candidates=SearchQuery(namespace="soc1", name="cpu", kind="lib-lef"),
    )
    graph = catalog.graph.dependency_closure(macro.ref)
    print(graph.dependencies)
```

Устанавливается через Python 3.15 и uv. Dev-окружение включает Conan и тестовый сервер:

```sh
uv sync --frozen
uv run python examples/conan_macro.py
make check unit build
make integration  # собственный временный Conan server
```

MemoryBackend — простой эталон для тестирования того же API в памяти.
Конфликты зависимостей Conan обрабатываются штатными механизмами и lockfiles.
Файлы ресурсов остаются во внешних хранилищах; Python-хелперы Git/файловых ресурсов сохранены.

Поиск зависимых записей проверяет граф каждого кандидата, а подготовка публикации
копирует Conan home для проверки batch. Эти ограничения требуют оценки на реальных данных.

Документация: [API](docs/interfaces.md), [модели](docs/data-model.md),
[Conan и внешний CI](docs/conan.md), [сценарии](docs/use-cases.md),
[решения и ограничения](docs/decisions.md), [разработка](docs/development.md).
