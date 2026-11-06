# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34, выделенный из hwcatalog
(коммит `3dba7e2`). Дистрибутив — `hwcatalog-conan`, Python-пакет — `hwcatalog`.

Ссылка имеет формат **namespace.component.view.type/version**, например
`soc.cpu.rtl.default/1.0` и `soc.cpu.be.lib-lef/4.0`. Все пять полей обязательны.
Версии RTL и BE независимы; их связь подтверждается точной зависимостью BE от RTL.
Типов рёбер и поддержки старого формата нет.

В API разделены два состояния:

- `LocalRecord` — описание текущего проекта с готовыми локальными путями; его формирует flow.
- `Record` — представление пакета из реестра с путями файлов Conan cache и, по запросу, исходников.

Оба содержат `ref`, `payload`, `source`, `files`. Source — Path исходников либо None,
files — словарь имён упакованных файлов и их локальных Path. Внешние ресурсы, включая
NFS-пути, описывает пользовательский payload; их формат и проверку определяет flow.
Координаты Git хранит Conan во внутренних метаданных рецепта; публичного поля scm нет.
Классов GitResource, LocatedResource и обёрток материализованных путей нет.

```python
from pathlib import Path

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import LocalRecord, Ref

catalog = Catalog()
local = LocalRecord(
    Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
    payload={"filelist": "rtl.f", "timing": "/nfs/cpu/timing.lib"},
    source=Path("/work/cpu"),
    files={"report": Path("/work/cpu/out/report.txt")},
)
published = catalog.publish(local)
record = published.ref.get_with_sources()
print(record.files["report"], record.source, record.payload)
implementations = published.ref.find_dependents(
    candidates=SearchQuery(namespace="soc", component="cpu", view="be", type="lib-lef"),
    transitive=False,
)
project = Graph(catalog, local, dependencies=())
lockfile = project.create_lockfile(Path("/work/cpu/conan.lock"))
print(project.records, project.dependencies)
```

`Catalog` предоставляет только `search` и `publish`. Публикация принимает один LocalRecord
и уже опубликованные dependencies. Рецепт генерируется Jinja-шаблоном; один export-pkg
создаёт пакет, upload отправляет его на remote. Локальный source передаётся SCM-механизму
Conan: SDK не клонирует Git самостоятельно. Файлы files копируются в пакет;
содержимое payload сохраняется как JSON.

`Ref.get()` возвращает готовый Record, заполняя files путями скачанных файлов.
`get_with_sources()` дополнительно получает исходники из GitLab штатным source() Conan
и заполняет Record.source. При обычном get() source=None.
Публичного materialize у Ref, Record и LocalRecord нет. При обходе графа исходники
зависимостей запрашиваются отдельно; рабочее дерево локального корня уже доступно.

`Graph` — обычный класс для одного описания проекта, например deps.toml. Формат файла
и добавление найденных зависимостей определяет flow. Graph разрешается при создании и не требует
публикации корня; результат содержит LocalRecord корня и Record зависимостей.
Graph.create_lockfile() сохраняет уже разрешённый граф через Conan и запоминает путь. Несколько Graph
могут находиться в одной директории и иметь разные манифесты и lockfile.
Lockfile остаётся непрозрачным файлом проекта и явно передаётся при публикации.
Если lockfile передан конструктору, он должен существовать; новый файл создают
через project.create_lockfile(path) после разрешения проекта.
Graph.networkx и результаты ordered_refs(root, stop_at=...) кэшируются.
ordered_records(root, stop_at=...) позволяет flow получить зависимости в порядке
обработки и остановить обход на выбранных макроблоках, сохранив общие зависимости.

LocalRecord.to_json() готовит переносимые метаданные: ref, payload и имена files.
Рабочие source/files-пути туда не попадают. Record.from_json(data, *, catalog) восстанавливает
inspection-снимок с явными путями. Метаданные пакета читает внутренний StoredRecipe
с обязательной директорией рецепта. Record.to_dict() предназначен
для просмотра готовых данных, включая пути cache; отдельного Record.to_json() нет.

Публичные модули находятся прямо в hwcatalog.api: catalog, graph, records, query,
errors. Тип JSON и протокол JSONSerializable находятся в records.
Ref — неизменяемые координаты с обязательным приватным _catalog;
контекст исключён из равенства, хеша и сериализации. Ссылки результатов уже содержат
этот контекст. Ref и Record неизменяемы, LocalRecord можно редактировать. Graph — готовый снимок.
Publish и создание Graph создают независимые снимки входов; пользовательский payload
сериализуется один раз за операцию. Create_lockfile запоминает путь только после успеха.

Внутренние LocalPackage и StoredPackage выполняют операции над пакетами, LocalRecipe
и StoredRecipe готовят и проверяют рецепты. ResolvedConanGraph разбирает нативный граф
без загрузки пакетов; ConanClient выполняет команды. Загруженный рецепт повторно
используется в пределах одной операции.

Conan должен быть настроен заранее: профили, remotes и аутентификация.
Catalog() использует стандартный cache с учётом CONAN_HOME и .conanrc. Один remote
выбирается автоматически, несколько требуют remote="catalog". Без remotes используется
cache. Его копирования, контекстных менеджеров, close и потоковых блокировок нет.

Для разработки нужны Python 3.15 и uv:

```sh
uv sync --frozen
uv run python examples/conan_macro.py
make check unit integration build
```

Ruff 0.17.2 закреплён в uv.lock. Включены строгие проверки сложности, аргументов,
локальных переменных, вложенности, возвратов и неиспользуемых аргументов. Лимиты
и контракты описаны в [API](docs/interfaces.md), [моделях](docs/data-model.md),
[Conan](docs/conan.md), [сценариях](docs/use-cases.md), [решениях](docs/decisions.md)
и [разработке](docs/development.md). Тесты используют изолированные cache и свой локальный сервер.
