# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34. Отдельный проект создан
на основе hwcatalog, коммит `3dba7e2`; имя дистрибутива — hwcatalog-conan,
Python-импорты — `hwcatalog`.

API описывает записи и точные зависимости пакетов. Узел хранится как рецепт Conan
с JSON-содержимым и собственным `conan.lock`. Имя имеет формат
`namespace.name.kind/version`, например `soc1.cpu.lib-lef/4.0`.
Тип представления задаётся полем Ref.kind; типизации рёбер нет.

```python
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend

rtl = Record(Ref("cpu", "2.3", "rtl", "soc1"))
macro = Record(Ref("cpu", "4.0", "lib-lef", "soc1"), {"files": ["/artifacts/cpu.lib"]})
with Catalog(ConanBackend()) as catalog:
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

Публикация файлов и Git-исходников:

```python
with Catalog(ConanBackend()) as catalog:
    record = catalog.publish_file("package.json")
    package = catalog.materialize(record.ref, with_sources=True)
```

JSON описывает один пакет: `ref`, `payload`, `dependencies`, необязательный один
`source` и карту `files`. Git-координаты фиксируются SCM-механизмом Conan и хранятся
в `conandata.yml`. В Conan упаковываются обычные файлы; Git-код остаётся в GitLab.
Опция `with_sources=True` получает его штатным `source()` Conan и возвращает
отдельный `package.source_path`. `LocatedResource` остаётся внешним путём. Пример полного описания — [в документации](docs/conan.md).

Conan-рецепты и нативный граф создаются внутри адаптера. Все команды выполняются
через Conan Python API; нативные dependencies и топологический порядок используются
внутри библиотеки. MemoryBackend реализует тот же API для тестирования.

Поиск зависимых записей проверяет граф каждого кандидата, а подготовка публикации
копирует Conan home для проверки batch. Эти ограничения требуют оценки на реальных данных.

Документация: [API](docs/interfaces.md), [модели](docs/data-model.md),
[Conan и внешний CI](docs/conan.md), [сценарии](docs/use-cases.md),
[решения и ограничения](docs/decisions.md), [разработка](docs/development.md).
