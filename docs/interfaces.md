# Публичный API

## Catalog

`Catalog(home=None, *, remote=None, timeout=60)` работает с уже настроенным Conan.

| Метод | Контракт |
|---|---|
| search(query=SearchQuery()) | tuple[Record, ...] точных AND-совпадений, отсортированных по Ref |
| publish(record: LocalRecord, *, dependencies=(), lockfile=None) | Публикует один пакет и возвращает Record |

Source, files и nfs принадлежат LocalRecord. Source — рабочий Git checkout, files —
карта относительных имён внутри пакета и абсолютных локальных Path. Nfs — карта имён
внешних ресурсов и абсолютных Path; файлы nfs не загружаются в Conan.
Зависимости должны существовать. Передача опубликованного Record в publish отклоняется.

## Ref и Record

`Ref(namespace, component, view, type, version, *, catalog)` задаёт координаты
namespace.component.view.type/version. Контекст RefCatalog не участвует в identity,
хеше или JSON. Ссылки результатов создаются сразу с нужным контекстом.

| Метод Ref | Контракт |
|---|---|
| get(*, with_sources=False) | Готовый Record; files содержит пути cache, source заполняется при запросе исходников |
| dependencies(filters=None) | tuple[Record, ...] непосредственных зависимостей |
| find_dependents(*, candidates, transitive=True) | Кандидаты, зависящие от точного Ref |
| to_dict() | Только координаты |

Непосредственные зависимости и обратный поиск читают requirements; транзитивный поиск
использует нативный граф. Сам Ref исключается. Отсутствующий пакет вызывает NotFound.
Пустой SearchQuery разрешает просмотр всего каталога. Фильтры включают все пять полей.

Record — представление уже полученного пакета: ref, payload, source (Path | None),
files (Mapping[str, Path]), nfs (Mapping[str, Path]), scm (Mapping[str, str] | None).
SCM содержит URL/commit Conan; отдельного класса Git-ресурса нет.

Record.from_json(data, *, catalog, package_dir=None) читает метаданные пакета.
Для списка имён файлов нужен package_dir; локальные пути будут заполнены под artifacts/.
Также поддерживается словарь уже полученных путей из inspection-снимка.
Record.to_dict() возвращает данные для просмотра или CLI. Публичных to_json и materialize
у Record нет; получение исходников выполняется через Ref.get(with_sources=True).
Физические пути files/source не участвуют в равенстве опубликованных записей:
один пакет может находиться в разных cache. SCM, payload и nfs сравниваются по содержимому.

LocalRecord содержит ref, payload, source, files, nfs. Его создаёт flow из текущего
проекта. LocalRecord.to_json() — подготовка переносимых метаданных для публикации:
source и исходные пути files исключаются, сохраняются имена файлов. Nfs остаётся
картой абсолютных путей. У локальной записи нет получения или материализации данных.

## Graph

`Graph(catalog, record: LocalRecord, *, dependencies=(), lockfile=None)` соответствует
одному описанию проекта. Один процесс может иметь несколько таких Graph и lockfile.

| Метод | Контракт |
|---|---|
| resolve() | GraphResult с LocalRecord корня и Record опубликованных зависимостей |
| create_lockfile(path=None) | Создаёт/обновляет lockfile dependencies, запоминает путь и возвращает Path |

Корень не требуется публиковать; его локальные данные имеют приоритет над реестром.
Conan проверяет циклы и конфликты requirements. Разрешение использует временный рецепт
и один graph info без export/upload. Формат deps.toml принадлежит flow, включая зависимости,
добавленные на основе поиска. Формат lockfile обрабатывает только Conan.

## Ошибки

- CatalogError — сбой Conan, конфигурации или получения файлов.
- NotFound — отсутствующий пакет.
- Conflict — повторная публикация или несовместимые revisions/версии.
- InvalidPackage — неверные локальные входы, координаты, рецепт или граф.

Один Ref соответствует одной revision. Случайный conan.lock из cwd явно отключён.
Автоматических retry, rollback и управления конкурентными издателями нет.
