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
    Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog),
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
Локальный путь передаётся export() через временный source-path.txt рядом с рецептом.
Этот файл не экспортируется, поэтому путь checkout не попадает в рецепт и не влияет
на recipe revision. Пакеты используют обычное поведение Conan без явного package_type.
SDK не клонирует Git самостоятельно. Публикация принимает один LocalRecord; его
зависимости должны быть опубликованы заранее. Пакеты имеют имена
namespace.component[_subcomponent].view.type/version[#revision], например soc.cpu_alu.be.lib-lef/4.0.
Первый `_` отделяет часть; component не содержит `_`, пустой subcomponent недопустим.

LocalRecord.to_json() готовит record.json с format=7, ref/payload и списком имён файлов.
Пути исходного checkout и файлов издателя туда не попадают. Временный Jinja-рецепт
при наличии зависимостей сначала используется в одном graph info с проектным lockfile.
Точные прямые requirements из нативного графа записываются в публикуемый рецепт,
после чего выполняются один export-pkg и upload. Без зависимостей graph info не нужен. Отдельного файла публикации и ручного
conanfile.py не требуется. Files копируются в staging artifacts/; SHA-256 записываются
в record.json.file_digests. Рецепт экспортирует только метаданные, а package() копирует
байты из временной source_folder в бинарный пакет. Поэтому чтение рецептов не получает
артефакты; изменение файла всё ещё меняет RREV через его хеш.
Начальный conandata.yml также генерируется Jinja-шаблоном и содержит requires.
Во время export() Git-helper Conan добавляет scm, сохраняя зависимости.
Рецепт получает их через self.conan_data в requirements(); отдельной копии списка нет.

## Готовые пути

Ref.info() возвращает Record с file_names и has_source без установки файлов.
Ref.get() возвращает InstalledRecord с путями доступных файлов пакета.
Ref.get_with_sources() дополнительно передаёт scoped-конфигурацию
`tools.build:download_source=True` в Conan install. Встроенный source() вызывает
Git(self, "source").checkout_from_conandata_coordinates() и возвращает точный commit
из GitLab. Git-helper сам создаёт поддиректорию source; явного mkdir в рецепте нет.
InstalledRecord.source содержит Path checkout. URL/commit остаются во внутренних метаданных Conan.
Без запроса source путь равен None. Повторное получение использует cache Conan.

Внешние ресурсы, например NFS-пути, flow может описать строками в payload.
SDK сохраняет эти данные как JSON и не проверяет пути или доступность ресурсов.
Проверка существования LIB/LEF перед запуском инструмента — задача flow.
Обёрток ресурсов, URI-провайдеров, digest и собственного Git-кэша нет.

Оба вида записей предоставляют paths напрямую. Публичного materialize нет:
внутренний код получения данных создаёт InstalledRecord с готовыми путями. Копировать cache
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

Ref.revision задаёт recipe revision (RREV); без неё Conan выбирает последнюю.
Publish разрешает новые revisions одной версии; одинаковое содержимое даёт тот же RREV.
Собственная revision не записывается в record.json: она вычисляется по экспортированным
файлам, а при чтении берётся из загруженного рецепта. Package revisions (PREV)
не входят в публичный Ref. Settings/options, build requirements, overrides и
диапазоны версий не поддерживаются. Поиск представлений использует поля Ref,
проверку их происхождения выполняет flow. Автоматических retry и rollback нет.

Единственный включённый remote выбирается автоматически; несколько требуют явного
remote="catalog". Без remotes доступны чтение cache и локальный Graph; publish() требует настроенный remote. При настроенном сервере локальные
неопубликованные записи не подменяют удалённые данные. SDK не создаёт профили.
SCM URL/commit должны быть доступны получателям; Git/SSH настраиваются обычными средствами.
