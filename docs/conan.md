# Conan: публикация и содержимое пакета

Все операции выполняются через `ConanAPI.command.run()` в процессе Python.
Нативный `DepsGraph`, `ConanFile`, рецепты и lockfiles остаются внутри адаптера.
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
from pathlib import Path
from hwcatalog.api.catalog import Catalog
from hwcatalog.backends.conan import ConanBackend

with Catalog(ConanBackend(Path("/absolute/path/to/conan-home"))) as catalog:
    record = catalog.publish_file("package.json")
    print(record.source, record.files)
    content = catalog.materialize(record.ref).path
    print(content / "source")
    print(content / "artifacts" / "timing/cpu.lib")
```

`publish_file()` возвращает сохранённый `Record`. Каталог автоматически создаёт
промежуточный рецепт и lockfile, экспортирует рецепт, создаёт пакет и загружает
его на указанный remote. При `remote=None` используется только локальный Conan cache.
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

При создании пакета исходники копируются в `source/`, без `.git`; обычные файлы —
в `artifacts/`. Сервер получает готовый пакет, поэтому `materialize()` не требует
доступа к исходному Git или файлам издателя. Для захвата SCM и создания пакета
с Git source нужен установленный Git. Conan по своим правилам может запретить
загрузку рецепта с локальным SCM URL, если commit ещё не доступен в remote.
См. [SCM capture в Conan](https://docs.conan.io/2/examples/tools/scm/git/capture_scm/git_capture_scm.html).

`LocatedResource` сохраняет прежний смысл: внешний локальный или смонтированный
NFS-путь. Такой ресурс не загружается в Conan автоматически. Дополнительные
внешние ресурсы можно описывать в пользовательском `payload`.

## Граф и remote

```python
backend = ConanBackend(Path("/absolute/path/to/conan-home"), remote="catalog", timeout=60)
```

Remote и аутентификация настраиваются обычными средствами Conan в этом home.
Адаптер читает и публикует в один remote; локальные конфликтующие revisions не
подменяют удалённую идентичность. Настройки процесса `CONAN_HOME` и cwd после
операций сохраняются. Timeout ограничивает отдельный HTTP-запрос, а не полное
время публикации или Git-команды.

Root lockfile хранит точные recipe revisions всех зависимостей. Собственный
recipe revision корня в него не входит. Граф загружается с этим lockfile через
Conan API; зависимости и топологический порядок берутся из нативных объектов.
Типы рёбер, settings/options, build requirements, overrides и диапазоны версий
в публичной модели не поддерживаются. Поиск RTL → BE проверяет граф каждого
кандидата из `SearchQuery`.

Перед записью весь batch готовится в копии Conan home. Пакеты переносятся через
Conan cache save/restore. Публикация не транзакционная: `PartialPublicationError`
содержит `published` и `uncertain`. Автоматического rollback или retry нет.
Копирование home и архивов может быть дорогим для больших пакетов.

Старые канонические рецепты первой версии остаются читаемыми. Для источников
и файлов используется вторая версия внутреннего формата. Внешний CI использует
тот же `catalog.publish_file()`, без написания рецепта Conan. Ручные рецепты
не являются поддерживаемым входным форматом.

`MemoryBackend` поддерживает тот же JSON и хранит файлы во временном каталоге
до `close()`. Для локального Git source он фиксирует HEAD и локальный путь через
`GitResource`; проверка чистоты и выбор remote URL относятся к Conan-адаптеру.
Git-операции Memory требуют extra `git`. `materialize()` для записи без файлов
и source вызывает `MaterializationError`.
