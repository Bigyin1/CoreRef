# Публичный API

Catalog владеет бэкендом и поддерживает контекстный менеджер. Модели находятся
в hwcatalog.api.models, адаптеры — hwcatalog.backends.conan и hwcatalog.backends.memory.

## Записи

| Операция | Результат |
|---|---|
| get(ref) | Record; NotFound при отсутствии |
| get_many(refs) | tuple[Record, ...], сохраняет порядок и дубликаты; отсутствие любого Ref — ошибка |
| search(query=SearchQuery()) | Все точные AND-совпадения, отсортированные по identity |
| publish(records, *, dependencies=()) | Публикует новые Record и исходящие Dependency |
| find_dependents(ref, *, candidates, transitive=True) | Кандидаты, зависящие от точного ref |
| close() | Освобождает ресурсы бэкенда |

Publish не обновляет существующие записи. Издатели одной идентичности координируются
приложением. Атомарность определяется catalog.capabilities.atomic_publish.

Find_dependents сначала фильтрует кандидатов по SearchQuery, затем проверяет их
зависимости. Промежуточные узлы могут быть любыми. При transitive=False учитывается
только прямая зависимость. Сам ref исключается, результаты уникальны и упорядочены
по identity. Пустой candidates разрешает проверку всего каталога.

## Граф

| Операция catalog.graph | Результат |
|---|---|
| dependencies(ref, filters=SearchQuery()) | Непосредственные зависимости как tuple[Record, ...] |
| consumers(ref, filters=SearchQuery()) | Непосредственные потребители как tuple[Record, ...] |
| dependency_closure(ref) | GraphResult с точным графом, включая корень |

Filters применяются к найденным записям. Отсутствующий корень вызывает NotFound.
Обход Conan использует встроенный lockfile своего корня; Memory возвращает точный
сохранённый граф. Разрешение версий hwcatalog из API удалено.

Ранее унаследованные RelationKind, Direction, LineageDirection, Relation и Neighbor
удалены. Теперь публикация принимает dependencies из Dependency(source, target),
а GraphResult содержит dependencies. Вместо MultiDiGraph экспортируется DiGraph.
Методы neighbors, implementations и lineage, параметр resolve_versions удалены.
Для поиска RTL → BE используется find_dependents с фильтрами имени и представления.

## Гарантии и ошибки

Catalog.capabilities содержит atomic_publish, consistency и lockfiles. Memory даёт
атомарную публикацию и snapshot; Conan — последовательную публикацию и live, lockfiles=True.

Ошибки: NotFound, IdentityConflict, InvalidPublication, DependencyCycle,
VersionConflict, InvalidPackage, BackendUnavailable, PartialPublicationError.
Последняя содержит published и uncertain для восстановления после неопределённого
результата записи. Автоматических повторов или rollback удалённой публикации нет.

Backend — Protocol с graph, capabilities, get_many, search, publish и close.
JSONSerializable — Protocol с методом to_json() -> str; наследование не требуется.

## Внешние ресурсы

GitResource.from_repository() фиксирует commit локального репозитория.
GitResource.materialize(cache_dir) подготавливает checkout точного commit;
для этого нужен extra git. LocatedResource.materialize() проверяет абсолютный путь.
Оба возвращают MaterializedResource с полем path. Доступ к файловым ресурсам
и координация совместного Git-кэша остаются у приложения.
