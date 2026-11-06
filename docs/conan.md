# Conan: публикация и содержимое пакета

Все команды выполняются через Conan Python Command API в процессе Python.
Версия закреплена на Conan 2.34.x. DepsGraph, ConanFile и рецепты остаются внутри SDK.
Пользовательские входы — Record, Ref, прямые dependencies и пути к файлам.

## Прямая публикация

```python
from pathlib import Path
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import Record, Ref

catalog = Catalog()
rtl = catalog.publish(
    Record(Ref("soc", "cpu", "rtl", "default", "1.0"), {"filelist": "rtl.f"}),
    source_path=Path("/work/cpu"),
)
macro = catalog.publish(
    Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"), {"corner": "tt"}),
    dependencies=(rtl.ref,),
    files={"timing/cpu.lib": Path("out/cpu.lib"), "physical/cpu.lef": Path("out/cpu.lef")},
)
package = macro.ref.materialize()
print(package.path / "artifacts/timing/cpu.lib")
```

Ключи files — имена внутри пакета, значения — обычные Path на машине издателя.
Относительные пути считаются от cwd; flow может разрешить их относительно своего
манифеста заранее. Встроенного файла публикации SDK не требует и не разбирает.
Каждый publish создаёт один пакет; dependencies должны уже существовать.

SDK генерирует временный рецепт через Jinja PackageLoader и StrictUndefined.
Python-литералы экранируются repr. Один export-pkg экспортирует рецепт, разрешает
Conan-граф и упаковывает файлы; затем upload отправляет пакет на remote.
Предварительных graph info/export при публикации нет. Имя пакета имеет формат
namespace.component.view.type/version; revision берётся из нативного результата.
Канонический рецепт проверяется перед чтением. Старые форматы не поддерживаются.

## SCM и артефакты

source_path задаёт единственный локальный Git checkout. Внутренний export() вызывает
Git.coordinates_to_conandata(repository=True): Conan проверяет чистоту checkout
и сохраняет URL/commit в conandata.yml. В исходный репозиторий SDK ничего не записывает.
Можно передать заранее известный GitResource в Record.source вместо source_path;
тогда при публикации Git не вызывается. Commit должен быть точным.

SCM не входит в payload и record.json. Record.source восстанавливается из метаданных
Conan. Код остаётся в GitLab и не копируется в binary. Обычные файлы проходят через
exports и package() и сохраняются под artifacts/. exports_sources не используется.

`ref.materialize()` возвращает артефакты. `ref.materialize(with_sources=True)`
дополнительно передаёт tools.build:download_source=True в Conan install.
Conan выполняет source() с Git.checkout_from_conandata_coordinates(); путь к исходникам
возвращается отдельно в MaterializedPackage.source_path. Без флага source_path=None.
При первом получении Git remote должен быть доступен; дальнейшие обращения используют
source cache Conan. Его содержимое принадлежит Conan и предназначено для чтения.

Git/SSH, GitLab и права на commit настраиваются обычными средствами. Conan может
запретить upload с локальным SCM URL. Тестовый сервер разрешает их только в своих
изолированных cache. См. [SCM capture](https://docs.conan.io/2/examples/tools/scm/git/capture_scm/git_capture_scm.html)
и [получение исходников](https://docs.conan.io/2/reference/conanfile/methods/source.html#forced-retrieval-of-sources).

LocatedResource сохраняет внешний локальный или смонтированный NFS-путь.
Такие файлы не загружаются в Conan автоматически и могут описываться в payload.

## Проектные графы и lockfile

```python
from hwcatalog.api.graph import Graph

project = Graph(
    catalog,
    Record(Ref("soc", "top", "rtl", "default", "1.0")),
    dependencies=(rtl.ref,),
    lockfile=Path("/work/top/conan.lock"),
)
lockfile = project.create_lockfile()
resolved = project.resolve()
record = catalog.publish(project.record, dependencies=project.dependencies, lockfile=lockfile)
```

Один Graph соответствует одному описанию проекта. Парсинг deps.toml принадлежит flow,
который может дополнить зависимости результатами поиска до создания Graph.
create_lockfile фиксирует прямые dependencies и их транзитивный граф; корень не
должен быть опубликован. Существующий lockfile используется при явном обновлении.
resolve и publish не изменяют его. Несколько Graph могут иметь разные имена lockfile
и находиться в одной директории. RTL хранит файл в Git, BE — в своём workspace.

SDK не парсит JSON lockfile и не задаёт версию схемы. Материализация опубликованного
Ref не принимает проектный lockfile: точные revisions уже закреплены в requirements.
Для одной идентичности допускается только одна revision, иначе Conflict.
Посторонний conan.lock из cwd явно отключается.

## Настройки и ограничения

Catalog() использует стандартный Conan home, обычно ~/.conan2, включая CONAN_HOME
и .conanrc. Явный home должен быть настроен заранее. SDK не создаёт профили и не
запускает profile detect. Используются настройки global.conf и стандартные переменные
CONAN_DEFAULT_PROFILE / CONAN_DEFAULT_BUILD_PROFILE.

При одном включённом remote он выбирается автоматически; при нескольких необходимо
remote="catalog". Без включённых remotes используется cache. Сервер определяет наличие
опубликованных записей; недоступность сервера не переключает чтение на локальные
неопубликованные данные. Cache не копируется. Timeout ограничивает HTTP-запрос.

Settings/options, build requirements, overrides и диапазоны версий не поддерживаются.
Прямой обратный поиск читает requirements; транзитивный разрешает граф каждого кандидата.
Сбой может оставить рецепт или пакет в cache. Автоматических rollback и retry нет.
Внешний CI использует тот же Catalog.publish с Python-моделями, без ручного Conan-рецепта.
