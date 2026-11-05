# Публичный API

Catalog — обычный объект для работы с уже настроенным Conan.
Он хранит настройки клиента; открывать и закрывать соединение не требуется.
Конструктор: `Catalog(home=None, *, remote=None, timeout=60)`.
Профили, remotes и авторизация настраиваются заранее средствами Conan.
Модели находятся в hwcatalog.api.models.

## Записи

| Операция | Результат |
|---|---|
| search(query=SearchQuery()) | Все точные AND-совпадения, отсортированные по Ref |
| publish(record, *, dependencies=(), lockfile=None) | Публикует один новый пакет и возвращает Record; dependencies — tuple[Ref, ...] |
| publish_file(path, *, lockfile=None) | Публикует пакет из JSON с необязательными source и files; возвращает Record |
| create_lockfile(refs, path) | Создаёт или обновляет lockfile проекта штатными средствами Conan; возвращает Path |
| resolve_local(record, *, dependencies=(), lockfile=None) | GraphResult для локального корня и опубликованных зависимостей; корень не публикуется |

Операции одного пакета — методы `Ref`, создаваемого с `catalog=catalog`:

| Операция | Результат |
|---|---|
| ref.get() | Record; NotFound при отсутствии |
| ref.materialize(*, with_sources=False) | MaterializedPackage(path, source_path); SCM checkout запрашивается флагом |
| ref.dependencies(filters=None) | Непосредственные зависимости как tuple[Record, ...] |
| ref.find_dependents(*, candidates, transitive=True) | Кандидаты, зависящие от точного ref |

`Ref.catalog` имеет тип `RefCatalog`: это структурный протокол с публичным `search`
и внутренним контекстом Conan для чтения графов и получения файлов. Сам `Catalog`
соответствует протоколу; дополнительный объект или поле `view` не нужны.
Ссылки в результатах `publish`, `publish_file`, `search`, `resolve_local` и методов
`Ref` сразу создаются с этим контекстом. Отдельного шага привязки нет.
Контекст не участвует в равенстве, хеше, строковом представлении и JSON.
`ref.to_dict()` возвращает только координаты; `Record.from_json(data, catalog=catalog)`
создаёт ссылку с указанным контекстом. Без каталога Ref можно хранить и сравнивать,
а операции чтения вызывают CatalogError.

Publish принимает один Record. Все зависимости должны уже существовать;
коллекции новых записей не принимаются. Существующие записи не обновляются.
Издатели одной идентичности координируются приложением.

Find_dependents сначала фильтрует кандидатов по SearchQuery, затем проверяет их
зависимости. Промежуточные узлы могут быть любыми. При transitive=False учитывается
только прямая зависимость. Сам ref исключается, результаты уникальны и упорядочены
по Ref. Пустой candidates разрешает проверку всего каталога.

## Разрешение графа

Filters в `dependencies` применяются к найденным записям. Отсутствующий опубликованный
корень в `dependencies` и `find_dependents` вызывает NotFound.
Обход Conan использует явно переданный lockfile проекта. Новые пакеты не содержат
собственного lockfile. Разрешение версий выполняет Conan.

`resolve_local` принимает Record локального проекта и tuple[Ref, ...] его прямых
зависимостей. Conan получает один общий consumer-граф из временного рецепта через
`graph info`, без `export`, `export-pkg` или `upload`. Данные локального корня
имеют приоритет над ранее опубликованным пакетом с таким Ref. Все зависимости
должны существовать. Циклы и несовместимые требования отклоняются Conan.

Это единственная точка разрешения графа. Flow использует локальное описание
независимо от публикации Ref корня. Когда нужен граф по сохранённому описанию,
входные данные можно получить теми же операциями чтения:

```python
ref = Ref("cpu", "1.0", "rtl", "soc", catalog=catalog)
record = ref.get()
dependencies = tuple(item.ref for item in ref.dependencies())
graph = catalog.resolve_local(record, dependencies=dependencies, lockfile=lockfile)
```


Ранее унаследованные RelationKind, Direction, LineageDirection, Relation и Neighbor
удалены. Публикация принимает ссылки на зависимости без повторения исходного Ref;
Dependency(source, target) используется в GraphResult. Вместо MultiDiGraph экспортируется DiGraph.
Методы neighbors, implementations и lineage, параметр resolve_versions удалены.
Для поиска RTL → BE используется find_dependents с фильтрами имени и представления.

Нативные графы Conan и его рецепты остаются внутри адаптера. Публичные методы
возвращают доменные модели. Для Conan используются его встроенные зависимости
и топологический порядок. Формат JSON-входа описан в [документации Conan](conan.md).

## Ошибки

Все ошибки наследуются от CatalogError:

- CatalogError — сбой операции Conan, получения файлов или конфигурации клиента.
- NotFound — запрошенный Ref отсутствует.
- Conflict — повторная публикация или несовместимые версии/revisions.
- InvalidPackage — некорректное описание пакета, рецепт или граф, включая циклы.

Причина сбоя сохраняется в сообщении и цепочке исключений. Автоматических повторов
или rollback удалённой публикации нет.

Catalog предоставляет search, resolve_local, publish, publish_file и create_lockfile.
Ref предоставляет get, dependencies, find_dependents и materialize. В классах остаются
настройка клиента и короткие вызовы внутренних функций.
Чтение записей, работа с графом, публикация и получение файлов реализованы
в `_internal/conan/records.py`, `graph.py`, `publication.py` и `materialization.py`.
Отдельных Backend и CatalogGraph нет; нативные графы и рецепты остаются внутренними.
JSONSerializable — Protocol с методом to_json() -> str; наследование не требуется.

## Внешние ресурсы

GitResource хранит SCM-координаты без клонирования или собственного кэша.
Исходники получает `ref.materialize(with_sources=True)` через штатный
`source()` сгенерированного Conan-рецепта. LocatedResource.materialize()
проверяет внешний путь и возвращает MaterializedResource(path).

Прямые dependencies и find_dependents(transitive=False) читают
requirements канонических рецептов без graph info. Транзитивный поиск использует
нативный граф Conan. Materialize выполняет один install по точной recipe revision
и requirements опубликованного пакета, без проектного lockfile и команд lock.
В каталоге один Ref соответствует одной revision; несколько revisions вызывают
Conflict. Случайный conan.lock из текущей директории не используется.
