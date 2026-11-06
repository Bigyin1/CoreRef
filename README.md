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
BE-представление, и его части: по умолчанию `subcomponent="*"`.
`subcomponent="alu"` сужает поиск до конкретной части, а явный `subcomponent=None`
выбирает только целые компоненты. Фильтрация выполняется до загрузки метаданных.
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

В API разделены три роли:

- `LocalRecord` — изменяемый вход проекта: ref без собственной revision, payload,
  рабочий Git-каталог source и локальные файлы files для публикации.
- `Record` — метаданные реестра: ref, payload, file_names и has_source. Путей files/source нет.
- `InstalledRecord` — полученный пакет: те же метаданные, все упакованные files
  с путями в Conan cache и, по запросу, Git source.

Record и InstalledRecord — обычные классы со свойствами только для чтения.
Конструктор Record отделяет и замораживает payload, InstalledRecord принимает
готовые метаданные и пути. PostInit и object.__setattr__ не используются.
Внешние ресурсы, включая NFS-пути, описывает пользовательский payload;
их формат и проверку определяет flow. SCM URL/commit остаются в Conan.

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
metadata = published.ref.info()
print(metadata.file_names, metadata.has_source)
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

`Ref.info()`, поиск, прямые зависимости и записи разрешённого графа возвращают
Record без скачивания бинарных файлов или Git-исходников. Имена доступных файлов
известны через file_names; has_source показывает, объявлены ли SCM-исходники.

`Ref.get()` возвращает InstalledRecord со всеми файлами пакета; `get_with_sources()`
дополнительно получает Git-исходники штатным source() Conan. Если has_source=True
и source=None, исходники ещё не получены. sources_ready=True означает, что они
получены либо пакет их не объявляет. Publish также возвращает InstalledRecord:
созданные файлы уже находятся в бинарном пакете в cache издателя.

Для готового графа Graph.get(refs) и Graph.get_with_sources(refs) возвращают
InstalledRecord выбранных опубликованных узлов одной установкой. Graph.records сохраняет
Record с метаданными; результат установки возвращается отдельно. Публичного
materialize нет; рабочее дерево LocalRecord корня уже доступно.

Полный граф опубликованного пакета получают напрямую через `Ref.graph()`:

```python
snapshot = published.ref.graph()
root = snapshot.record.ref  # Точный Ref самого опубликованного пакета.
ordered = snapshot.ordered_refs(root)  # Зависимости первыми, корень последним.
installed = snapshot.get_with_sources(ordered)  # Включая сам корневой пакет.
for item in installed:
    print(item.ref, item.files, item.source)
snapshot.create_lockfile("/work/stored.lock")
```

Этот граф содержит только опубликованные Record с точными revisions, без искусственного
локального корня. Он получает метаданные одним `graph info`; файлы и Git-исходники
не устанавливаются до `get`/`get_with_sources`. Выбранные пакеты, включая корень,
получаются одной установкой и кэшируются в пределах снимка. Незакреплённый Ref
выбирает последнюю revision, а `ref.graph(lockfile=path)` передаёт выбор Conan:
lockfile, созданный для опубликованного графа, закрепляет и его корень.
Lockfile локального проекта фиксирует его зависимости; собственного опубликованного
корня в таком lockfile нет. Для опубликованного графа создают отдельный lockfile.

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

Рабочие Git-копии выбранных зависимостей можно зарегистрировать в `Workspace`:

```python
from hwcatalog.api.workspace import Workspace

workspace = Workspace(catalog, "/work/top/workspace.yaml")
editable = workspace.add(cpu_ref)
print(editable.source)  # /work/soc.cpu.rtl.default-1.0

local_top = LocalRecord(
    Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog), source=Path("/work/top")
)
project = Graph(catalog, local_top, dependencies=(cpu_ref,), workspace=workspace)
records = project.get_with_sources(project.requirements)
# Выбранные editable исходники читаются из working tree;
# остальные зависимости получаются одной обычной Conan-установкой.
workspace.remove(cpu_ref)  # Git-копия и локальные изменения сохраняются.
```

У Workspace две операции: add(ref) и remove(ref). Свойство members возвращает независимый
снимок состава в виде {Ref: Path}; path указывает на YAML-манифест. Для нескольких пакетов вызывают
add для каждого Ref. Повторный add возвращает уже зарегистрированную рабочую копию,
сохраняя изменения. directory="/work/other" у add задаёт другой родительский каталог
для нового checkout; по умолчанию копия создаётся рядом с текущим проектом.
Существующие посторонние каталоги не перезаписываются. В первом варианте поддержаны
пакеты с SCM-исходниками без отдельных упакованных files.

Манифест принадлежит SDK и хранится в текущем проекте; имена файлов можно выбирать:

```yaml
packages:
  soc.cpu.rtl.default/1.0#0123456789abcdef0123456789abcdef: ../soc.cpu.rtl.default-1.0
```

Ключ — Ref с точной базовой revision, значение — путь к исходникам относительно
манифеста. Другие файлы манифестов могут описывать независимые workspaces в той же
директории. Ни SCM-координаты, ни Conan-файлы в этот YAML не включаются.
Git checkout выполняет штатный workspace SCM API Conan; исходники не проходят
через source cache. Данные Conan — индекс и editable-рецепты — создаются только
в .hwcatalog/workspaces/<имя-манифеста>. Они производные; повторный add восстанавливает
их без повторного клонирования. В .hwcatalog создаётся собственный .gitignore;
пользовательский .gitignore проекта не изменяется.

Graph(..., workspace=...) и ref.graph(workspace=...) захватывают выбор editable-пакетов
при создании графа. Graph.editables показывает эти записи; get/get_with_sources
возвращают их локальные source без установки в cache. Сам граф зависимостей и
метаданные берутся из опубликованной основы; изменения файлов working tree доступны
сразу. Изменение пользовательского TOML в такой зависимости само по себе не меняет
граф: формат TOML и подготовку LocalRecord по-прежнему определяет flow.
Удаление регистрации не меняет существующий Graph, для нового выбора создают новый граф.

Базовая revision остаётся прежней после редактирования исходников. Незакреплённые
прямые требования выбирают зарегистрированную основу, а конфликт с явной revision,
транзитивным требованием или lockfile завершается ошибкой. Lockfile фиксирует
опубликованную основу и зависимости; рабочие изменения Git он не фиксирует.
Глобальный список editable-пакетов Conan не меняется. Это возможность SDK;
новые команды RTL/BE CLI на этом этапе не добавлены.

LocalRecord.to_json() готовит record.json: format, ref без собственной revision, payload и имена files.
Маркер format=7 относится к нашему формату записи, а не к Conan или lockfile.
При подготовке файлов LocalRecipe добавляет file_digests — SHA-256 содержимого
каждого файла. В экспорт рецепта входят только метаданные: артефакты остаются
во временной staging-директории и через package() копируются в бинарный пакет.
Их имена и хеши влияют на RREV, поэтому изменение содержимого создаёт новую revision,
а локальный путь издателя на неё не влияет. Поиск не скачивает артефакты.
Зависимости хранятся только в conandata.yml.requires; requirements() читает их
через self.conan_data. SCM-координаты Conan добавляет в тот же conandata.yml.
Отдельных hwcatalog.json и has_source нет: наличие исходников определяется по scm.
conanfile.py и начальный conandata.yml генерируются Jinja-шаблонами.
SDK проверяет формат и координаты record.json, но не сравнивает Python-код рецепта
с текущим шаблоном: изменение шаблона не делает прежние revisions нечитаемыми.
Рецепты из настроенного Conan-сервера считаются доверенным кодом; исполняет их Conan.
Рабочие source/files-пути туда не попадают. Record.from_json(data, *, catalog) восстанавливает
inspection-снимок с явными путями. Метаданные пакета читает внутренний StoredRecipe
с обязательной директорией рецепта. Record.to_dict() предназначен
для просмотра метаданных; InstalledRecord.to_dict() добавляет пути cache.
Record.from_json() восстанавливает оба вида снимков; отдельного to_json() у них нет.

Публичные модули находятся прямо в hwcatalog.api: catalog, graph, records, query,
errors. Тип JSON и протокол JSONSerializable находятся в records.
Ref — неизменяемые координаты с обязательным приватным _catalog;
контекст исключён из равенства, хеша и сериализации. Ссылки результатов уже содержат
этот контекст. Ref и Record неизменяемы, LocalRecord можно редактировать. Graph — готовый снимок.
Publish и создание Graph создают независимые снимки входов; пользовательский payload
сериализуется один раз за операцию. Create_lockfile запоминает путь только после успеха.

Внутренние LocalPackage и StoredPackage выполняют операции над пакетами, LocalRecipe
и StoredRecipe готовят рецепты и читают их метаданные. PackageGraph хранит общий
разрешённый снимок, рецепты и результаты пакетного получения для обоих видов корня.
ResolvedConanGraph разбирает нативный граф
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
