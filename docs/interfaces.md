# Публичный API

Catalog владеет бэкендом и поддерживает контекстный менеджер. Модели находятся
в hwcatalog.api.models, адаптер — hwcatalog.backends.conan.

## Записи

| Операция | Результат |
|---|---|
| get(ref) | Record; NotFound при отсутствии |
| search(query=SearchQuery()) | Все точные AND-совпадения, отсортированные по Ref |
| publish(record, *, dependencies=(), lockfile=None) | Публикует один новый пакет и возвращает Record; dependencies — tuple[Ref, ...] |
| publish_file(path, *, lockfile=None) | Публикует пакет из JSON с необязательными source и files; возвращает Record |
| create_lockfile(refs, path) | Создаёт или обновляет lockfile проекта штатными средствами Conan; возвращает Path |
| materialize(ref, *, with_sources=False) | MaterializedPackage(path, source_path); SCM checkout запрашивается флагом |
| dependencies(ref, filters=SearchQuery()) | Непосредственные зависимости как tuple[Record, ...] |
| resolve_local(record, *, dependencies=(), lockfile=None) | GraphResult для локального корня и опубликованных зависимостей; корень не публикуется |
| find_dependents(ref, *, candidates, transitive=True) | Кандидаты, зависящие от точного ref |
| close() | Освобождает ресурсы бэкенда |

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
record = catalog.get(ref)
dependencies = tuple(item.ref for item in catalog.dependencies(ref))
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

Backend — единый Protocol с get, search, dependencies, resolve_local,
find_dependents, publish, publish_file, create_lockfile, materialize и close.
Catalog предоставляет эти операции напрямую, отдельного CatalogGraph нет.
JSONSerializable — Protocol с методом to_json() -> str; наследование не требуется.

## Внешние ресурсы

GitResource хранит SCM-координаты без клонирования или собственного кэша.
Исходники получает `catalog.materialize(ref, with_sources=True)` через штатный
`source()` сгенерированного Conan-рецепта. LocatedResource.materialize()
проверяет внешний путь и возвращает MaterializedResource(path).

Прямые dependencies и find_dependents(transitive=False) читают
requirements канонических рецептов без graph info. Транзитивный поиск использует
нативный граф Conan. Materialize выполняет один install по точной recipe revision
и requirements опубликованного пакета, без проектного lockfile и команд lock.
В каталоге один Ref соответствует одной revision; несколько revisions вызывают
Conflict. Случайный conan.lock из текущей директории не используется.
