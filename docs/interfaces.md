# Публичный API

Catalog владеет бэкендом и поддерживает контекстный менеджер. Модели находятся
в hwcatalog.api.models, адаптер — hwcatalog.backends.conan.

## Записи

| Операция | Результат |
|---|---|
| get(ref) | Record; NotFound при отсутствии |
| get_many(refs) | tuple[Record, ...], сохраняет порядок и дубликаты; отсутствие любого Ref — ошибка |
| search(query=SearchQuery()) | Все точные AND-совпадения, отсортированные по Ref |
| publish(record, *, dependencies=(), lockfile=None) | Публикует один новый пакет и возвращает Record; dependencies — tuple[Ref, ...] |
| publish_file(path, *, lockfile=None) | Публикует пакет из JSON с необязательными source и files; возвращает Record |
| create_lockfile(refs, path) | Создаёт или обновляет lockfile проекта штатными средствами Conan; возвращает Path |
| materialize(ref, *, with_sources=False, lockfile=None) | MaterializedPackage(path, source_path); SCM checkout запрашивается флагом |
| find_dependents(ref, *, candidates, transitive=True) | Кандидаты, зависящие от точного ref |
| close() | Освобождает ресурсы бэкенда |

Publish принимает один Record. Все зависимости должны уже существовать;
коллекции новых записей не принимаются. Существующие записи не обновляются.
Издатели одной идентичности координируются приложением.

Find_dependents сначала фильтрует кандидатов по SearchQuery, затем проверяет их
зависимости. Промежуточные узлы могут быть любыми. При transitive=False учитывается
только прямая зависимость. Сам ref исключается, результаты уникальны и упорядочены
по Ref. Пустой candidates разрешает проверку всего каталога.

## Граф

| Операция catalog.graph | Результат |
|---|---|
| dependencies(ref, filters=SearchQuery()) | Непосредственные зависимости как tuple[Record, ...] |
| consumers(ref, filters=SearchQuery()) | Непосредственные потребители как tuple[Record, ...] |
| dependency_closure(ref, *, lockfile=None) | GraphResult с точным графом, включая корень |
| resolve_local(record, *, dependencies=(), lockfile=None) | GraphResult для локального корня и опубликованных зависимостей; корень не публикуется |

Filters применяются к найденным записям. Отсутствующий опубликованный корень
в `dependencies`, `consumers` и `dependency_closure` вызывает NotFound.
Обход Conan использует явно переданный lockfile проекта. Новые пакеты не содержат
собственного lockfile. Разрешение версий выполняет Conan.

`resolve_local` принимает Record локального проекта и tuple[Ref, ...] его прямых
зависимостей. Conan получает один общий consumer-граф из временного рецепта через
`graph info`, без `export`, `export-pkg` или `upload`. Данные локального корня
имеют приоритет над ранее опубликованным пакетом с таким Ref. Все зависимости
должны существовать. Циклы и несовместимые требования отклоняются Conan.

Ранее унаследованные RelationKind, Direction, LineageDirection, Relation и Neighbor
удалены. Публикация принимает ссылки на зависимости без повторения исходного Ref;
Dependency(source, target) используется в GraphResult. Вместо MultiDiGraph экспортируется DiGraph.
Методы neighbors, implementations и lineage, параметр resolve_versions удалены.
Для поиска RTL → BE используется find_dependents с фильтрами имени и представления.

Нативные графы Conan и его рецепты остаются внутри адаптера. Публичные методы
возвращают доменные модели. Для Conan используются его встроенные зависимости
и топологический порядок. Формат JSON-входа описан в [документации Conan](conan.md).

## Ошибки

Ошибки: NotFound, IdentityConflict, InvalidPublication, DependencyCycle,
VersionConflict, InvalidPackage, BackendUnavailable.
Сбой upload сообщается как BackendUnavailable. Автоматических повторов или rollback
удалённой публикации нет.

Backend — Protocol с graph, get_many, search, publish, publish_file, create_lockfile,
materialize и close.
JSONSerializable — Protocol с методом to_json() -> str; наследование не требуется.

## Внешние ресурсы

GitResource хранит SCM-координаты без клонирования или собственного кэша.
Исходники получает `catalog.materialize(ref, with_sources=True)` через штатный
`source()` сгенерированного Conan-рецепта. LocatedResource.materialize()
проверяет внешний путь и возвращает MaterializedResource(path).

Прямые dependencies, consumers и find_dependents(transitive=False) читают
requirements канонических рецептов без graph info. Транзитивный поиск использует
нативный граф Conan. При materialize один install разрешает и устанавливает граф;
явный проектный lockfile дополняется корнем во временной копии командой lock add.
