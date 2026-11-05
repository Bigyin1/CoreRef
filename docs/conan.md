# Conan: публикация и содержимое пакета

Все операции выполняются через `ConanAPI.command.run()` в процессе Python.
Нативный `DepsGraph`, `ConanFile` и рецепты остаются внутри адаптера.
Проект владеет своим lockfile и передаёт его путь, не разбирая формат.
Пользователь работает с `Record`, `Ref`, `Dependency` и `GraphResult`.
Зафиксирована версия Conan 2.34.x: Command API пока экспериментален.

## Один JSON-файл — один пакет

```json
{
  "ref": {"namespace": "soc1", "name": "cpu", "kind": "lib-lef", "version": "4.0"},
  "payload": {"corner": "tt"},
  "dependencies": [
    {"namespace": "soc1", "name": "cpu", "kind": "rtl", "version": "2.3"}
  ],
  "source": {"path": "../cpu"},
  "files": {
    "timing/cpu.lib": "out/cpu.lib",
    "physical/cpu.lef": "out/cpu.lef"
  }
}
```

Обязательно только `ref`. Зависимости уже должны быть опубликованы. Все пути
отсчитываются от JSON-файла. Ключи `files` — имена внутри пакета, значения — пути
к обычным файлам на машине издателя. Имена должны быть относительными POSIX-путями
без `..`. JSON не принимает дополнительные поля или списки источников.

```python
from hwcatalog.api.catalog import Catalog
from hwcatalog.backends.conan import ConanBackend

with Catalog(ConanBackend()) as catalog:
    record = catalog.publish_file("package.json")
    print(record.source, record.files)
    package = catalog.materialize(record.ref, with_sources=True)
    print(package.source_path)  # SCM checkout in Conan's source cache
    print(package.path / "artifacts" / "timing/cpu.lib")
```

`publish_file()` возвращает сохранённый `Record`. Каталог автоматически создаёт
промежуточный рецепт, экспортирует его, создаёт пакет и загружает
его на указанный remote. Без явного remote выбирается единственный включённый
remote из конфигурации.
Если включённых remotes нет, используется локальный cache; при нескольких
нужно указать имя через `remote="catalog"`.
Содержимое возвращаемого пути принадлежит Conan cache; его следует читать, а для
изменений копировать в рабочий каталог.

## Один Git source на пакет

`source: {"path": "../cpu"}` указывает на локальный checkout. Внутренний
`export()` вызывает `Git.coordinates_to_conandata(repository=True)`: Conan сам
проверяет чистоту репозитория и фиксирует URL/commit в `conandata.yml` под ключом
`scm`. В исходный checkout каталог ничего не записывает. Внутренний `source()`
использует `Git.checkout_from_conandata_coordinates()`.

Можно также передать уже известные координаты:

```json
{
  "source": {
    "provider": "git",
    "repository_url": "https://git.example.org/soc/cpu.git",
    "requested_ref": "release-2.3",
    "resolved_commit": "0123456789abcdef0123456789abcdef01234567"
  }
}
```

Этот фрагмент добавляется к полному описанию пакета. Commit обязателен и точен;
`requested_ref` служит подписью, выбор версии выполняется по commit. Git source
не записывается в `payload` или в Conan `record.json`: его координаты хранятся
штатным SCM-механизмом. `Record.source` возвращает `GitResource` из этих координат.
Второй Git source модель не допускает.

Исходный код остаётся в GitLab (или другом Git remote). В Conan хранится его
SCM — URL и точный commit. Публикация выполняется через `export-pkg`: Git-исходники
в бинарный пакет не копируются и для заранее заданных координат не скачиваются.
Обычные файлы из `files` сохраняются в пакете под `artifacts/`.

`materialize(ref)` устанавливает артефакты и возвращает `MaterializedPackage.path`.
Чтобы дополнительно получить код, вызовите `materialize(ref, with_sources=True)`.
Адаптер передаёт scoped-конфигурацию `tools.build:download_source=True` команде
Conan install; Conan выполняет `source()` и checkout точного commit. Путь к нему
возвращается отдельно в `MaterializedPackage.source_path`. При чтении артефактов
без этого флага `source_path` равен `None`.

Источники должны быть доступны из GitLab при первом получении в новом cache;
готовый Conan binary исходного кода не содержит. Уже полученный checkout использует
source cache Conan. Права Git/SSH и GitLab настраиваются обычными средствами Git.
При захвате SCM из локального пути нужны чистый checkout и установленный Git.
Conan может запретить upload с локальным SCM URL, если commit не доступен в remote.
См. [принудительное получение source в Conan](https://docs.conan.io/2/reference/conanfile/methods/source.html#forced-retrieval-of-sources)
и [SCM capture](https://docs.conan.io/2/examples/tools/scm/git/capture_scm/git_capture_scm.html).

`LocatedResource` сохраняет прежний смысл: внешний локальный или смонтированный
NFS-путь. Такой ресурс не загружается в Conan автоматически. Дополнительные
внешние ресурсы можно описывать в пользовательском `payload`.

## Граф и remote

```python
backend = ConanBackend(remote="catalog", timeout=60)
```

Без аргумента `home` используется стандартный cache Conan (обычно `~/.conan2`).
Путь выбирает `ConanAPI().home_folder`, с учётом `CONAN_HOME` и `.conanrc`.
Явный `home` также должен быть настроен заранее. SDK не создаёт профили
и не запускает `profile detect`. Применяются стандартные профили Conan, включая
`CONAN_DEFAULT_PROFILE` / `CONAN_DEFAULT_BUILD_PROFILE` и `core:default_profile` /
`core:default_build_profile` из `global.conf`.
Remote и аутентификация настраиваются обычными средствами Conan в выбранном home.
При нескольких включённых remotes адаптер требует явный выбор имени.
Адаптер читает и публикует в один remote; локальные конфликтующие revisions не
подменяют удалённую идентичность. Настройки процесса `CONAN_HOME` и cwd после
операций сохраняются. Timeout ограничивает отдельный HTTP-запрос, а не полное
время публикации или Git-команды.

Проектный `conan.lock` хранится в RTL-репозитории или BE workspace. Создание и
обновление выполняет `catalog.create_lockfile(refs, path)` через штатные команды
Conan. Формат, версия схемы и содержимое файла принадлежат Conan; каталог не
парсит его JSON. Зависимости и топологический порядок берутся из нативного графа.

```python
lockfile = catalog.create_lockfile((rtl_ref,), "/work/project/conan.lock")
record = catalog.publish_file("package.json", lockfile=lockfile)
dependencies = tuple(item.ref for item in catalog.graph.dependencies(record.ref))
graph = catalog.graph.resolve_local(record, dependencies=dependencies, lockfile=lockfile)
package = catalog.materialize(record.ref, with_sources=True, lockfile=lockfile)
```

Существующий файл используется как вход при явном обновлении. При обычном чтении,
публикации или materialize он не изменяется. Без `lockfile` Conan получает явно
пустой аргумент, чтобы не подобрать случайный файл из cwd.
Типы рёбер, settings/options, build requirements, overrides и диапазоны версий
в публичной модели не поддерживаются. Поиск RTL → BE проверяет граф каждого
кандидата из `SearchQuery`.

При настроенном remote сервер является источником опубликованных данных: get,
search и проверка наличия Ref обращаются к нему. Локальные пакеты, ещё не загруженные
на этот сервер, не считаются опубликованными. Недоступность сервера даёт ошибку,
без переключения на содержимое cache.

Один publish создаёт один пакет. Зависимости передаются как tuple[Ref, ...] и
должны быть опубликованы заранее. Проверка наличия выполняется по точному Ref;
полный каталог перед публикацией не загружается.
После создания временного рецепта вызывается один `export-pkg`: Conan сам
экспортирует рецепт, вычисляет граф, проверяет совместимость требований и вызывает
package(). Отдельные предварительные `graph info` и `export` не выполняются.
SCM фиксируется единственным export() внутри export-pkg. Полная ссылка с revision
берётся из возвращённого нативного графа, а пакет отправляется через upload.
Home не копируется, cache save/restore не используется. После ошибки в cache
могут остаться рецепт или пакет. Сбой upload сообщается как BackendUnavailable;
автоматического rollback или retry нет.

Рецепт генерируется Jinja-шаблоном
`src/hwcatalog/_internal/backends/templates/conanfile.py.j2`.
Шаблон загружается из установленного пакета через PackageLoader; переменные
проверяются StrictUndefined, Python-литералы выводятся через repr.
JSON-метаданные и SCM-координаты сериализуются моделями.

Канонические рецепты форматов 1–4 остаются читаемыми через legacy-шаблон.
Новые рецепты используют пятый формат: requirements фиксируют recipe revisions
прямых зависимостей, а lockfile не экспортируется. `exports_sources` не используется.
Обычные файлы артефактов входят в `exports` и копируются из `recipe_folder` методом `package()`;
`source()` получает Git отдельно. Исторические пакеты не переписываются. Внешний CI использует
тот же `catalog.publish_file()`, без написания рецепта Conan. Ручные рецепты
не являются поддерживаемым входным форматом.

GitResource — описание SCM без собственной реализации Git-кэша. Сгенерированный
source() получает исходники штатным conan.tools.scm.Git. Для записи без файлов
и source materialize() вызывает MaterializationError.
