# hwcatalog-conan

Экспериментальный каталог hardware-проектов на Conan 2.34, выделенный из hwcatalog
(коммит `3dba7e2`). Дистрибутив — `hwcatalog-conan`, Python-пакет — `hwcatalog`.

Ссылка имеет формат **namespace.component[_subcomponent].view.type/version[#revision]**, например
`soc.cpu.rtl.default/1.0` и `soc.cpu_alu.be.lib-lef/4.0`. Пять основных полей обязательны;
`subcomponent` — необязательная часть компонента, задаваемая именованным аргументом.
Без неё имя сохраняет вид `soc.cpu.be.lib-lef/4.0`. Первый `_` отделяет subcomponent;
сам component использует буквы, цифры и `-`.
Версии RTL и BE независимы; их связь подтверждается точной зависимостью BE от RTL.
Типов рёбер и поддержки старого формата нет.

Поиск `SearchQuery(namespace="soc", component="cpu", view="be")` возвращает и целое
BE-представление, и его части. `subcomponent="alu"` сужает поиск до конкретной части.
Связь с исходным RTL по-прежнему задаётся обычной зависимостью пакета.

`Ref(..., revision=None, catalog=catalog)` обозначает последнюю recipe revision (RREV),
а `revision="…"` закрепляет конкретную. `without_revision()` возвращает логические
координаты с тем же каталогом. Полученные Record и опубликованные узлы Graph всегда
содержат точную revision; локальный корень её не задаёт. Она участвует в равенстве и хеше.
Повторный publish одной версии разрешён: Conan вычисляет revision по содержимому,
одинаковые входы дают ту же revision. Старые revisions остаются доступны.

`catalog.search(query)` возвращает последнюю revision каждого имени/версии.
`catalog.search(query, all_revisions=True)` возвращает историю;
`SearchQuery(revision="…")` задаёт точный фильтр в обоих режимах.
Без lockfile незакреплённые зависимости проверяются на настроенном remote;
lockfile сохраняет прежний выбор. Существующий Graph не меняет revisions после публикаций.

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
и уже опубликованные dependencies. При наличии зависимостей один graph info выбирает
их revisions с учётом lockfile; в рецепт записываются точные ссылки. Рецепт генерируется Jinja-шаблоном; один export-pkg
создаёт пакет, upload отправляет его на remote. Локальный source передаётся SCM-механизму
Conan: SDK не клонирует Git самостоятельно. Файлы files копируются в пакет;
содержимое payload сохраняется как JSON.

`Ref.get()` возвращает готовый Record, заполняя files путями скачанных файлов.
`get_with_sources()` дополнительно получает исходники из GitLab штатным source() Conan
и заполняет Record.source. При обычном get() source=None.
Публичного materialize у Ref, Record и LocalRecord нет. Для зависимостей готового графа
Graph.get(refs) получает файлы выбранных пакетов одной установкой без запроса SCM,
а Graph.get_with_sources(refs) дополнительно получает их исходники;
рабочее дерево локального корня уже доступно.

`Graph` — обычный класс для одного описания проекта, например deps.toml. Формат файла
и добавление найденных зависимостей определяет flow. Graph разрешается при создании и не требует
публикации корня; результат содержит LocalRecord корня и Record зависимостей.
Graph.create_lockfile() сохраняет уже разрешённый граф через Conan и запоминает путь. Несколько Graph
могут находиться в одной директории и иметь разные манифесты и lockfile.
Lockfile остаётся непрозрачным файлом проекта и явно передаётся при публикации.
Если lockfile передан конструктору, он должен существовать; новый файл создают
через project.create_lockfile(path) после разрешения проекта.
Структура Graph доступна только для чтения. Для изменения входов создают новый Graph;
путь lockfile обновляет только успешный create_lockfile(). record, records и
ordered_records() возвращают независимую копию изменяемого LocalRecord корня.
Редактирование этой копии не меняет снимок или его кэши. Опубликованные Record неизменяемы.
Graph.networkx содержит только топологию Ref, без объектов Record и Dependency
в атрибутах; записи и связи доступны через Graph. Сам NetworkX и результаты
ordered_refs(root, stop_at=...) кэшируются; рабочую копию получают через networkx.copy().
ordered_records(root, stop_at=...) позволяет flow получить зависимости в порядке
обработки и остановить обход на выбранных макроблоках, сохранив общие зависимости.
Для пакетов, оставшихся после отбора, вызывают Graph.get_with_sources(refs): проверенные
рецепты используются повторно, повторный запрос в пределах Graph не обращается к Conan.

LocalRecord.to_json() готовит record.json: format, ref без собственной revision, payload и имена files.
Маркер format=6 относится к нашему формату записи, а не к Conan или lockfile.
Зависимости хранятся только в conandata.yml.requires; requirements() читает их
через self.conan_data. SCM-координаты Conan добавляет в тот же conandata.yml.
Отдельных hwcatalog.json и has_source нет: наличие исходников определяется по scm.
conanfile.py и начальный conandata.yml генерируются Jinja-шаблонами.
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
выбирается автоматически, несколько требуют remote="catalog". Без remotes доступны
чтение cache и разрешение локального проекта; publish()
завершается CatalogError до сериализации входов и упаковки. Его копирования, контекстных менеджеров, close и потоковых блокировок нет.

Для разработки нужны Python 3.15 и uv:

```sh
uv sync --frozen
uv run python -m examples.conan_macro
make check unit integration build
```

Ruff 0.17.2 закреплён в uv.lock. Включены строгие проверки сложности, аргументов,
локальных переменных, вложенности, возвратов и неиспользуемых аргументов. Лимиты
и контракты описаны в [API](docs/interfaces.md), [моделях](docs/data-model.md),
[Conan](docs/conan.md), [сценариях](docs/use-cases.md), [решениях](docs/decisions.md)
и [разработке](docs/development.md). Тесты используют изолированные cache и свой локальный сервер.
