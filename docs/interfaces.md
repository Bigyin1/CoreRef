# Публичный API

Модели находятся в `hwcatalog.api`. Все операции используют заранее
настроенный Conan. Контекстный менеджер и закрытие соединения не требуются.

## Catalog

`Catalog(home=None, *, remote=None, timeout=60)` хранит настройки клиента.

| Метод | Результат |
|---|---|
| search(query=SearchQuery()) | tuple[Record, ...] точных AND-совпадений, отсортированных по Ref |
| publish(record, *, dependencies=(), source_path=None, files=None, lockfile=None) | Один новый опубликованный Record |

Зависимости уже должны существовать. `source_path` — Path локального Git checkout,
`files` — dict[str, Path] из имён внутри пакета и исходных файлов. Paths относительно
cwd обрабатываются обычным pathlib; flow может заранее разрешить их относительно
своего TOML. Встроенного парсера манифеста публикации нет.

Вместо checkout можно задать закреплённый `Record.source`. Одновременно передавать
оба источника нельзя. Имена files — относительные POSIX-пути без `..`, пересечений
и абсолютных путей. Если Record.files уже заполнен, он должен соответствовать карте files.

## Graph

`Graph(catalog, record, *, dependencies=(), lockfile=None)` описывает один локальный
проект: корневую Record, его прямые зависимости и необязательный Path lockfile.
Каждый экземпляр может соответствовать отдельному deps.toml с любым именем.
Это обычный класс с поведением проекта, не dataclass результата разрешения.

| Метод | Результат |
|---|---|
| resolve() | GraphResult с корнем и транзитивными зависимостями |
| create_lockfile(path=None) | Path созданного или обновлённого lockfile |

`resolve()` создаёт временный consumer-рецепт и вызывает один `graph info` без
экспорта и публикации корня. Локальные данные корня имеют приоритет над сохранённым
пакетом с такими координатами. Циклы и несовместимые requirements проверяет Conan.

`create_lockfile` использует dependencies экземпляра, включая найденные flow
зависимости, а не отдельный массив Ref. Переданный путь запоминается в `graph.lockfile`
для следующих resolve; без аргумента используется уже заданный путь. При отсутствии
обоих путей возникает CatalogError. Корень не входит в lockfile как публикуемый пакет.
Формат lockfile обрабатывает только Conan. При обычном resolve/publish файл не изменяется.

Для разрешения по сохранённым данным:

```python
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import Ref

ref = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
record = ref.get()
dependencies = tuple(item.ref for item in ref.dependencies())
graph = Graph(catalog, record, dependencies=dependencies, lockfile=lockfile).resolve()
```

## Ref

`Ref(namespace, component, view, type, version, *, catalog=None)` — неизменяемые
координаты `namespace.component.view.type/version` с необязательным контекстом.

| Метод | Результат |
|---|---|
| get() | Record; NotFound при отсутствии |
| dependencies(filters=None) | tuple[Record, ...] непосредственных зависимостей |
| find_dependents(*, candidates, transitive=True) | tuple[Record, ...] кандидатов, зависящих от этого точного Ref |
| materialize(*, with_sources=False) | MaterializedPackage(path, source_path) |
| to_dict() | dict[str, str] только с координатами |

Ref.catalog имеет тип структурного протокола RefCatalog: публичный search и
внутренний контекст Conan. Сам Catalog соответствует протоколу; дополнительного
объекта view или шага привязки нет. Все возвращаемые ссылки, включая концы рёбер
GraphResult, создаются с этим контекстом. Он исключён из равенства, хеша и JSON.
`Record.from_json(data, catalog=catalog)` также создаёт готовую ссылку.
Без каталога можно хранить и сравнивать координаты; операции Conan вызывают CatalogError.

Find_dependents сначала фильтрует кандидатов, затем проверяет их зависимости.
При transitive=False учитываются прямые requirements без graph info; транзитивный
поиск использует нативные графы Conan. Сам Ref исключается. Пустой SearchQuery
разрешает просмотр всего каталога. Filters в dependencies применяются к найденным
записям. Отсутствующий Ref вызывает NotFound.

## Ресурсы и ошибки

GitResource хранит SCM-координаты без собственного Git-кэша. Исходники получает
`ref.materialize(with_sources=True)` через Conan source(). LocatedResource.materialize()
проверяет внешний путь и возвращает MaterializedResource(path).

Все ошибки наследуются от CatalogError:

- CatalogError — сбой Conan, конфигурации или получения файлов.
- NotFound — отсутствующий пакет.
- Conflict — повторная публикация или несовместимые revisions/версии.
- InvalidPackage — некорректные координаты, входы, рецепт или граф.

Один Ref соответствует одной revision; несколько revisions вызывают Conflict.
Materialize выполняет один install без проектного lockfile: requirements уже закреплены.
Случайный conan.lock из cwd явно отключён. Автоматических retry и rollback нет.
Backend, CatalogGraph, типизированных рёбер и старого формата Ref нет.
