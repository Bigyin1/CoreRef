# Публичный API

## Catalog

`Catalog(home=None, *, remote=None, timeout=60)` работает с уже настроенным Conan.

| Метод | Контракт |
|---|---|
| search(query=SearchQuery(), *, all_revisions=False) | tuple[Record, ...] точных AND-совпадений, отсортированных по Ref |
| publish(record: LocalRecord | Graph, *, dependencies=(), lockfile=None) | Публикует один пакет и возвращает InstalledRecord |

Source и files принадлежат LocalRecord. Source — рабочий Git checkout, files —
карта относительных имён внутри пакета и абсолютных локальных Path. Внешние ресурсы
описываются в payload; их формат, проверка и использование принадлежат flow.
Зависимости должны существовать. Передача опубликованного Record в publish отклоняется.
Publish всегда публикует на сервер: при отсутствии remote возникает CatalogError
до подготовки входов, разрешения графа или упаковки. Чтение cache и локальный Graph
без remote поддерживаются.

## Ref и Record

`Ref(reference, *, catalog)` разбирает строковые координаты
namespace.component[_subcomponent].view.type/version[#revision]. Приватный конкретный Catalog не участвует в identity,
хеше или JSON. Ссылки результатов создаются сразу с нужным контекстом.

| Метод Ref | Контракт |
|---|---|
| info() | Record с метаданными без получения бинарных файлов и Git |
| get() | InstalledRecord с доступными файлами пакета; source=None |
| get_with_sources() | InstalledRecord с файлами и Git-исходниками, если они объявлены |
| dependencies(filters=None) | tuple[Record, ...] непосредственных зависимостей |
| to_dict() | Координаты, включая revision, если она задана |
| without_revision() | Логические координаты с тем же каталогом |

`Ref.from_fields(namespace, component, view, type, version, *, subcomponent=None, revision=None, catalog)`
создаёт ту же ссылку из отдельных полей через основной строковый конструктор.
Ref проверяет канонический формат сразу; неверные координаты вызывают InvalidPackage.
Непосредственные зависимости читаются из опубликованных requirements.
Представления выбираются через `Catalog.search(SearchQuery.for_component(ref, ...))`;
принадлежность конкретному исходному пакету определяет flow.

Record содержит только метаданные: ref, payload, file_names (tuple[str, ...])
и has_source (bool). Его возвращают info(), search(), dependencies()
и опубликованная часть Graph.records. Files и source отсутствуют: поиск не требует
скачивания содержимого. Has_source вычисляется из SCM-данных Conan, а не дублируется
в переносимом record.json.

InstalledRecord наследует Record и добавляет files (Mapping[str, Path]) и
source (Path | None). Его возвращают publish(), get() и get_with_sources().
Конструктор InstalledRecord(record, *, files=None, source=None) принимает готовые
метаданные: все объявленные file_names должны присутствовать в files; source
допускается только для пакета с has_source=True. Без файлов files — пустой словарь.

При has_source=True и source=None исходники ещё не получены. Sources_ready=True
означает, что source получен либо исходники не объявлены. Get_with_sources() получает
их штатными средствами Conan. URL/commit остаются внутри Conan.

Record и InstalledRecord — обычные классы с приватными полями и свойствами только
для чтения. Payload отделяется от входа в конструкторе Record; готовые неизменяемые
метаданные используются повторно при создании InstalledRecord. PostInit и ручного
object.__setattr__ нет. Равенство и хеш зависят от метаданных, а не от типа представления
и физических путей: metadata и установленная запись одного пакета равны.

To_dict() выдаёт метаданные; InstalledRecord.to_dict() дополнительно всегда содержит
словарь files и при наличии — source. Record.from_json(data, *, catalog) восстанавливает
Record или InstalledRecord по этим полям. Материализации на экземпляре и to_json нет.

LocalRecord содержит ref, payload, source, files и остаётся изменяемым входом flow.
Его source — рабочий Git checkout, files — исходные файлы пользователя. Собственная
revision не задана. LocalRecord.to_json() готовит переносимый record.json с format=7,
логическим ref, payload и именами файлов. LocalRecipe.copy_files() добавляет SHA-256
в file_digests: хеши попадают в рецепт и влияют на RREV, содержимое — только в бинарный
пакет. Полученные зависимости и SCM сохраняются в conandata.yml.

## SearchQuery

Семь опциональных полей задают точные AND-фильтры координат.

- `criteria()` возвращает отдельный словарь заданных условий.
- `matches_ref(ref)` проверяет координаты до скачивания рецепта.
- `matches(record)` проверяет LocalRecord или Record по их Ref.
- `select(records)` принимает коллекцию или итератор записей, фильтрует и возвращает
  tuple в порядке Ref. Поддерживает `graph.records`, сохраняя исходные объекты.
- `conan_pattern(*, all_revisions=False)` сразу формирует шаблон поиска: `#latest`
  по умолчанию, `#*` для истории или конкретную revision из фильтра.
  Без ограничения subcomponent поиск включает целый компонент и его части; точность
  component проверяется до загрузки рецептов. С subcomponent="alu" сегмент равен cpu_alu.
  Непредставимое точное условие даёт None;
  пользовательские строки не интерпретируются как wildcard или регулярные выражения.

SearchQuery не хранит каталог и не выполняет запросы к Conan. Catalog.search и
получение прямых зависимостей используют select для одинаковой фильтрации и порядка.

## Graph

`Graph(catalog, record: LocalRecord, *, dependencies=(), lockfile=None)` соответствует
одному описанию проекта и выполняет один graph info при создании.
Переданный lockfile должен существовать; для создания нового сначала разрешают
Graph без lockfile, затем вызывают create_lockfile(path).
Один процесс может иметь несколько таких Graph и lockfile.

| Метод | Контракт |
|---|---|
| record | Независимая копия LocalRecord корня |
| requirements | Прямые зависимости с revisions, выбранными Conan |
| records | LocalRecord корня и Record всех опубликованных зависимостей |
| edges | Все рёбра Dependency(source, target) |
| info(ref) | Метаданные узла снимка без I/O |
| dependencies(ref=None, *, filters=None) | Прямые Record-зависимости узла или корня без I/O |
| get(refs) | Получает выбранные InstalledRecord с файлами одной установкой без запроса SCM |
| get_with_sources(refs) | Получает выбранные InstalledRecord с файлами и SCM-исходниками одной установкой |
| create_lockfile(path=None) | Сохраняет уже разрешённый граф через Conan, запоминает путь и возвращает Path |

catalog, record, records, edges, requirements, networkx и lockfile доступны
только для чтения. Изменение структуры требует нового Graph. Выданные LocalRecord
из record, records и ordered_records() отделены от внутреннего корня: их можно
редактировать для новых операций. Опубликованные Record остаются неизменяемыми.

LocalRecord можно редактировать между операциями; новый Graph создаёт новый снимок. Publish и создание Graph получают
независимые снимки входов; опубликованный Record и Ref неизменяемы.
Create_lockfile обновляет настроенный путь только после успешного выполнения Conan.

Корень не требуется публиковать; его локальные данные имеют приоритет над реестром.
Conan проверяет циклы и конфликты requirements. Разрешение использует временный рецепт
и один graph info без export/upload. Формат deps.toml принадлежит flow, включая зависимости,
добавленные на основе поиска. Формат lockfile обрабатывает только Conan.

get(refs) и get_with_sources(refs) принимают опубликованные узлы этого Graph, включая опубликованный корень.
Локальный корень уже доступен. Дубликаты Ref убираются с сохранением порядка;
пустой список и повторные запросы полученных записей не запускают Conan.
Graph.records сохраняет Record с метаданными, полученные InstalledRecord возвращаются отдельно.
Если сначала получены только файлы, get_with_sources() добавляет исходники одной
следующей установкой. После этого get() возвращает уже готовую запись с source;
полученные данные не отбрасываются.

SDK повторно использует проверенные рецепты и делает один install для ещё не полученных
файлов/исходников. SCM включается только для выбранных пакетов, остальные зависимости
остаются частью штатного разрешения/установки Conan. Для установки Conan создаёт
непрозрачный временный lockfile из уже разрешённого графа: проектный lockfile не читается
повторно и его путь не передаётся в методы получения записей. Выбор revisions сохраняется.
Ошибка установки не добавляет частичные результаты в кэш Graph; запрос можно повторить.
Если выбранные записи содержат только payload, install не требуется.

## Обход и представление графа

- `networkx` — ленивое кэшируемое представление с замороженной структурой. Для
  редактирования: `graph.networkx.copy()`. Узлы и рёбра не содержат копий Record
  и Dependency в атрибутах; эти данные предоставляет сам Graph. Пользовательские
  аннотации следует добавлять на рабочую копию.
- `ordered_refs(root, *, stop_at=())` — достижимые узлы, зависимости перед потребителями.
  У stop_at не обходятся исходящие зависимости; сами выбранные узлы входят в результат.
  Порядок для одинакового корня и stop_at вычисляется один раз.
- `ordered_records(root, *, stop_at=())` — записи в том же порядке.

Неизвестный корень и недопустимые или скрытые точки остановки вызывают ValueError.
Циклы зависимостей отклоняет Conan при создании Graph.
Изменения исходного LocalRecord, реестра или lockfile не пересчитывают существующий Graph.
Для повторного разрешения создают новый Graph. create_lockfile сохраняет его снимок
и обновляет настроенный путь только после успешного сохранения.

## Ошибки

- CatalogError — сбой Conan, конфигурации или получения файлов.
- NotFound — отсутствующий пакет.
- Conflict — несовместимые revisions/версии в графе.
- InvalidPackage — неверные локальные входы, координаты, рецепт или граф.

Ref без revision выбирает последнюю средствами Conan; Ref с revision задаёт точную запись.
Полученные Record содержат точный Ref. Publish разрешает повторную публикацию имени/версии;
локальный корень не должен задавать revision. Собственная revision не входит в record.json,
но сохраняется в inspection-снимках Record.to_dict()/from_json().
Search возвращает последние revisions; all_revisions=True включает историю.
Явный SearchQuery.revision действует в обоих режимах.

Graph сначала передаёт исходные требования и lockfile Conan. Его опубликованные узлы
и рёбра содержат revisions. Незакреплённые ссылки в get/get_with_sources и обходах
сопоставляются с выбранными узлами снимка, без нового поиска последней revision.
Явная несовпадающая revision отклоняется. Случайный conan.lock из cwd явно отключён.
Автоматических retry, rollback и управления конкурентными издателями нет.


`Catalog.publish(graph)` публикует локальный снимок без дополнительного graph info.
Graph должен принадлежать тому же экземпляру Catalog; его зависимости и lockfile
нельзя переопределить аргументами publish. Обновлённый или удалённый после разрешения
проектный lockfile не меняет выбор: временный lock создаётся из нативного графа Conan.
Для изменения метаданных, состава пакета или зависимостей создаётся новый Graph.

`SearchQuery.for_component(ref, *, view=None, type=None, version=None, revision=None)`
сохраняет namespace/component/subcomponent. Версию и revision исходного Ref не наследует.
