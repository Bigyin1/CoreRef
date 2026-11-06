# Conan: публикация и получение

SDK использует Conan 2.34.x Python Command API в текущем процессе. ConanFile,
DepsGraph и рецепты остаются внутри библиотеки. Профили, remotes и авторизация
должны быть настроены заранее; Catalog() использует обычный Conan home.

## Локальные входы

```python
from pathlib import Path
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import LocalRecord, Ref

catalog = Catalog()
local = LocalRecord(
    Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
    payload={"filelist": "rtl.f", "lib": "/nfs/cpu/cpu.lib"},
    source=Path("/work/cpu"),
    files={"report": Path("/work/cpu/out/report.txt")},
)
published = catalog.publish(local, dependencies=())
record = published.ref.get_with_sources()
print(record.source, record.files, record.payload)
```

LocalRecord.source — текущий Git checkout. При export() Conan сам проверяет его
чистоту и фиксирует координаты через Git.coordinates_to_conandata(repository=True).
SDK не клонирует Git самостоятельно. Публикация принимает один LocalRecord; его
зависимости должны быть опубликованы заранее. Пакеты имеют имена
namespace.component.view.type/version, например soc.cpu.be.lib-lef/4.0.

LocalRecord.to_json() готовит переносимые ref/payload и список имён файлов.
Пути исходного checkout и файлов издателя туда не попадают. Временный Jinja-рецепт
передаётся одному export-pkg, затем upload. Отдельного файла публикации и ручного
conanfile.py не требуется. Files копируются под artifacts/.

## Готовые пути

Ref.get() заполняет Record.files путями доступных файлов пакета.
Ref.get_with_sources() дополнительно передаёт scoped-конфигурацию
`tools.build:download_source=True` в Conan install. Встроенный source() вызывает
Git.checkout_from_conandata_coordinates() и возвращает точный commit из GitLab.
Record.source содержит Path checkout. URL/commit остаются во внутренних метаданных Conan.
Без запроса source путь равен None. Повторное получение использует cache Conan.

Внешние ресурсы, например NFS-пути, flow может описать строками в payload.
SDK сохраняет эти данные как JSON и не проверяет пути или доступность ресурсов.
Проверка существования LIB/LEF перед запуском инструмента — задача flow.
Обёрток ресурсов, URI-провайдеров, digest и собственного Git-кэша нет.

Оба вида записей предоставляют paths напрямую. Публичного materialize нет:
внутренний код получения данных заполняет поля до возврата Record. Копировать cache
для этого не нужно. Для изменений данные копируются в рабочую директорию потребителя.

## Проектные графы

```python
from hwcatalog.api.graph import Graph

project = Graph(catalog, local, dependencies=())
lockfile = project.create_lockfile(Path("/work/cpu/conan.lock"))
ordered = project.ordered_records(local.ref)
```

Graph соответствует одному описанию проекта. Парсинг deps.toml и добавление зависимостей
по результатам поиска выполняет flow. Локальный корень не публикуется для разрешения:
Graph содержит LocalRecord корня и Record зависимостей. Source локального
корня уже доступен; выбранные зависимости flow получает через Graph.get_with_sources(refs)
одним install с закреплёнными revisions и выборочным tools.build:download_source.
Graph.get(refs) таким же способом получает файлы без запроса Git-исходников.

Lockfile принадлежит проекту и передаётся явно при публикации и разрешении. SDK не
парсит его JSON и не задаёт версию схемы. Создание/обновление выполняет Conan LockfileAPI из уже разрешённого графа;
обычные чтение и публикация файл не изменяют. Несколько Graph могут иметь разные
lockfile в одной директории. Посторонний conan.lock из cwd отключён.

## Ограничения

Один Ref соответствует одной revision. Повторная публикация запрещена, несколько
revisions вызывают Conflict. Settings/options, build requirements, overrides и
диапазоны версий не поддерживаются. Прямой обратный поиск читает requirements,
транзитивный использует нативный граф кандидата. Автоматических retry и rollback нет.

Единственный включённый remote выбирается автоматически; несколько требуют явного
remote="catalog". Без remotes используется cache. При настроенном сервере локальные
неопубликованные записи не подменяют удалённые данные. SDK не создаёт профили.
SCM URL/commit должны быть доступны получателям; Git/SSH настраиваются обычными средствами.
