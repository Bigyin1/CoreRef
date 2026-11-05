# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34. Отдельный проект создан
на основе hwcatalog, коммит `3dba7e2`; имя дистрибутива — hwcatalog-conan,
Python-импорты — `hwcatalog`.

API описывает записи и точные зависимости пакетов. Узел хранится как рецепт Conan
с JSON-содержимым и точными requirements. `conan.lock` принадлежит использующему
пакеты проекту: RTL-репозиторию или BE workspace. Имя пакета имеет формат
`namespace.name.kind/version`, например `soc1.cpu.lib-lef/4.0`.
Тип представления задаётся полем Ref.kind; типизации рёбер нет.

```python
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend

rtl = Record(Ref("cpu", "2.3", "rtl", "soc1"))
macro = Record(Ref("cpu", "4.0", "lib-lef", "soc1"), {"files": ["/artifacts/cpu.lib"]})
with Catalog(ConanBackend()) as catalog:
    rtl = catalog.publish(rtl)
    macro = catalog.publish(macro, dependencies=(rtl.ref,))
    inputs = catalog.graph.dependencies(macro.ref)  # tuple[Record, ...]
    implementations = catalog.find_dependents(
        rtl.ref,
        candidates=SearchQuery(namespace="soc1", name="cpu", kind="lib-lef"),
    )
    graph = catalog.graph.dependency_closure(macro.ref)
    print(graph.dependencies)
```

Один `publish(record, dependencies=(ref1, ref2, ...))` создаёт один пакет и возвращает
его сохранённый `Record`. Зависимости должны быть опубликованы заранее. Публикация
использует один `export-pkg`, который экспортирует рецепт, проверяет граф Conan и
упаковывает файлы, затем `upload` отправляет пакет на сервер.

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

Проектный lockfile создаётся штатной командой Conan и передаётся как непрозрачный файл:

```python
with Catalog(ConanBackend()) as catalog:
    lockfile = catalog.create_lockfile((rtl.ref,), "workspace/conan.lock")
    graph = catalog.graph.dependency_closure(macro.ref, lockfile=lockfile)
```

Каталог не разбирает JSON lockfile и не задаёт версию его формата. Новые пакеты
не экспортируют собственный lockfile. Memory не поддерживает Conan lockfiles.

При настроенном remote сервер Conan — источник опубликованных данных, а стандартный
локальный cache используется для получения рецептов, файлов и подготовки публикации.
Cache не копируется; готовые пакеты загружаются на сервер обычным Conan upload.
Поиск зависимых записей проверяет граф каждого кандидата; его стоимость требует
оценки на реальных данных.

Документация: [API](docs/interfaces.md), [модели](docs/data-model.md),
[Conan и внешний CI](docs/conan.md), [сценарии](docs/use-cases.md),
[решения и ограничения](docs/decisions.md), [разработка](docs/development.md).
