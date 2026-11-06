# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34. За основу взят hwcatalog,
коммит `3dba7e2`. Дистрибутив называется `hwcatalog-conan`, Python-пакет — `hwcatalog`.

Ссылка имеет формат **`namespace.component.view.type/version`**:

```text
soc.cpu.rtl.default/1.0
soc.cpu.be.lib-lef/4.0
```

Namespace задаёт проект, component — компонент, view — представление, type — его
вариант. Версия хранится в Conan version. Все пять полей обязательны; старый формат
не поддерживается. Версии RTL и BE независимы. Связь подтверждается точной
зависимостью BE от RTL, типов рёбер нет.

`Catalog` предоставляет только `search` и `publish`. Операции одного пакета находятся
в `Ref`; разрешение зависимостей и lockfile одного локального описания — в `Graph`.
Публичные модули расположены прямо в `hwcatalog.api`: `catalog`, `graph`, `records`,
`query`, `resources`, `protocols`, `errors`, `types`. Поддиректории `models` нет.

```python
from pathlib import Path

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref

catalog = Catalog()
rtl = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
macro = catalog.publish(
    Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"), {"corner": "tt"}),
    dependencies=(rtl.ref,),
    files={"cpu.lib": Path("out/cpu.lib"), "cpu.lef": Path("out/cpu.lef")},
)
implementations = rtl.ref.find_dependents(
    candidates=SearchQuery(namespace="soc", component="cpu", view="be", type="lib-lef"),
    transitive=False,
)
package = macro.ref.materialize()

project = Graph(
    catalog,
    Record(Ref("soc", "top", "rtl", "default", "1.0")),
    dependencies=(rtl.ref,),
    lockfile=Path("workspace/top.lock"),
)
project.create_lockfile()
resolved = project.resolve()
print(resolved.records, resolved.dependencies)
```

Каждый `Graph` соответствует одному описанию проекта, например одному `deps.toml`.
Парсинг TOML, JSON или другого формата принадлежит flow. В SDK передаются Python-модели.
Граф может получать дополнительные зависимости, найденные flow, вместе с явно
заданными в файле. Локальный корень не требуется публиковать для разрешения графа.
Несколько Graph могут использовать один Conan cache и разные lockfile в одной директории.

`Ref(..., catalog=catalog)` позволяет вызвать `get()`, `dependencies()`,
`find_dependents(...)` и `materialize(...)`. Ссылки в результатах каталога и графа
сразу содержат контекст `RefCatalog`. Он исключён из равенства, хеша и JSON;
`ref.to_dict()` возвращает только координаты. Без контекста Ref можно хранить
и сравнивать, но нельзя выполнять операции Conan.

Один `publish` создаёт один пакет. Его зависимости должны быть опубликованы заранее.
SDK генерирует рецепт Jinja-шаблоном, вызывает один Conan `export-pkg`, затем `upload`.
Пользовательские `conanfile.py` и отдельный формат файла публикации не требуются.
Обычные файлы передаются через `files`, их имена внутри пакета возвращаются в `Record.files`.

Для RTL указывается локальный Git checkout:

```python
record = catalog.publish(
    Record(Ref("soc", "mmu", "rtl", "default", "1.0")),
    source_path=Path("/work/mmu"),
)
package = record.ref.materialize(with_sources=True)
print(package.source_path)
```

Conan сам фиксирует SCM URL и commit. Исходники остаются в GitLab и получаются
через `source()`; они не копируются в binary. Можно передать заранее закреплённый
`GitResource` в `Record.source` вместо `source_path`. Внешние NFS-пути остаются
в payload и доступны через `LocatedResource`.

Lockfile принадлежит проекту и передаётся Conan как непрозрачный файл.
`Graph.create_lockfile()` создаёт или обновляет его для прямых зависимостей графа
и запоминает путь для дальнейших `resolve()`. При публикации путь передаётся
явно через `publish(..., lockfile=...)`. `Ref.materialize()` lockfile не принимает:
опубликованные requirements уже фиксируют точные revisions.

Conan должен быть настроен заранее: профили, remotes и аутентификация.
`Catalog()` использует стандартный cache, включая `CONAN_HOME` и `.conanrc`.
Один включённый remote выбирается автоматически, несколько требуют `remote="catalog"`.
Без включённых remotes используется локальный cache. Cache не копируется.
Операции выполняются последовательно, соединения открывать и закрывать не требуется.

Для разработки нужны Python 3.15 и uv:

```sh
uv sync --frozen
uv run python examples/conan_macro.py
make check test build
```

Тесты используют настоящий Conan, собственные временные cache и локальный сервер.
Документация: [API](docs/interfaces.md), [модели](docs/data-model.md),
[Conan и внешний CI](docs/conan.md), [сценарии](docs/use-cases.md),
[решения](docs/decisions.md), [разработка](docs/development.md).
