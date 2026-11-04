# Conan-бэкенд

Проверенная версия — Conan 2.34.0. Граф загружается в текущем Python-процессе
через ConanAPI.command.run(); результат содержит настоящий объект DepsGraph.
JSON-преобразования графа нет. Адаптер использует нативные dependencies.direct_host
и dependencies.host.topological_sort. Поиск индекса, export/upload и операции кэша
пока используют CLI со структурированным JSON там, где он нужен.

Интеграция с объектами Conan закреплена на версии 2.34.x: Python API и структура
графа зависят от версии Conan. Граница этой зависимости находится в одном модуле.
[Conan API](https://docs.conan.io/2/reference/extensions/python_api/ConanAPI.html).

## Координаты и возможности

Ref с namespace=soc1, name=cpu, kind=lib-lef, version=4.0 соответствует
`soc1.cpu.lib-lef/4.0`. Namespace/name/kind состоят из lowercase-букв, цифр, `_`, `-`.
Точки служат разделителями. Версия и длина ссылки ограничены Conan.
Неподходящие координаты отклоняются без переименования.
Функции `to_conan_reference`, `from_conan_reference` находятся в `hwcatalog.backends.conan`.

Оба бэкенда предоставляют один API Record/Dependency без типов связей и направлений.
Dependencies и consumers возвращают tuple[Record, ...]; dependency_closure(ref)
возвращает GraphResult с полем dependencies. Для представлений используется
find_dependents с фильтрами кандидатов по координатам.

## Хранилище

```python
backend = ConanBackend(
    home=Path("/absolute/path/to/conan-home"),
    remote="catalog",  # без remote используется только заданный локальный кэш
    executable="conan",
    timeout=60,
)
```

Remote и авторизацию заранее настраивает пользователь или CI штатными командами Conan.
Конструктор не меняет remotes и учётные данные. Он создаёт собственный пустой профиль
hwcatalog-profile; default-профиль не применяется. Home задаётся явно.
В режиме remote индекс читается только с выбранного сервера. Кэш используется для
загрузки рецептов; локальные посторонние записи не становятся частью удалённого каталога.
Fallback на кэш при недоступности remote отсутствует. Close идемпотентен.

## Нативный граф из обычного Python-скрипта

```python
from pathlib import Path
from hwcatalog.api.models.records import Ref
from hwcatalog.backends.conan import ConanBackend

backend = ConanBackend(Path("/absolute/path/to/conan-home"))
try:
    graph = backend.load_graph(Ref("cpu", "4.0", "lib-lef", "soc1"))
    dependencies = graph.root.conanfile.dependencies
    for package in dependencies.direct_host.values():
        print("direct:", package.ref)
    for package in dependencies.host.topological_sort.values():
        print("dependency first:", package.ref)
finally:
    backend.close()
```

Это исходный Conan DepsGraph с root, nodes и serialize(), а не восстановленный
из JSON объект. Доступны его ConanFile и нативные read-only представления зависимостей.
Topological_sort выдаёт зависимости раньше потребителей и не включает сам корень.
Типы NativeGraph/NativePackage/NativeDependencies — только Protocol для аннотаций:
во время выполнения они не оборачивают объекты Conan.

Load_graph проверяет формат записей и использует встроенный lockfile корня.
Catalog.graph.dependency_closure(ref) по-прежнему возвращает переносимый GraphResult;
порядок records там задан identity. Для нативного топологического порядка используется
показанный интерфейс Conan. Собственный обход существует только в MemoryBackend.

Timeout ограничивает CLI-команды и отдельные HTTP-запросы Conan API. Полная загрузка
большого графа в процессе может длиться дольше одного timeout.

## Формат рецепта и внешний CI

Поддерживаются канонические рецепты первой версии, созданные публичным write_recipe.
Внешний CI может пользоваться им независимо от Catalog.publish:

```python
from pathlib import Path
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import write_recipe

write_recipe(
    Path("recipe"),
    Record(Ref("cpu", "4.0", "lib-lef", "soc1"), {"files": ["/artifacts/cpu.lib"]}),
    dependencies=(Ref("cpu", "2.3", "rtl", "soc1"),),
)
```

Helper создаёт conanfile.py, record.json и hwcatalog.json с версией формата и requires.
CI должен создать lockfile до export:

```sh
# CONAN_HOME указывает на кэш CI с настроенным remote.
printf '[settings]\n[options]\n[conf]\n' > empty-profile
conan lock create recipe --lockfile="" --lockfile-out=recipe/conan.lock \
    --profile:all=empty-profile --remote=catalog
conan export recipe --no-remote
conan upload soc1.cpu.lib-lef/4.0 --only-recipe --remote=catalog --confirm
```

Для локальных зависимостей используется --no-remote вместо --remote=catalog.
Весь рецепт, включая lockfile, готовится до export. Settings/options, диапазоны,
overrides, tool/python requirements и произвольные изменения conanfile.py не входят
в формат v1. Бинарные пакеты не нужны; RTL/LIB/LEF остаются внешними ресурсами.

Catalog.publish проверяет batch и существующие внешние зависимости, копирует home
во временный кэш и готовит рецепты в порядке зависимостей. Все графы проверяются
до публикации; затем выполняются последовательные export/upload.

Одна логическая версия публикуется один раз. Любое изменение требует нового version.
Повторная публикация и несколько recipe revisions одного Ref вызывают IdentityConflict.
Соглашение обязательно и для внешнего CI; конкурентные издатели координируются приложением.

## Lockfiles и поиск

Lockfile фиксирует версии и recipe revisions зависимостей. Revision самого публикуемого
корня в него не включается, чтобы избежать самоссылки в хеше рецепта.
Обход явно передаёт встроенный lockfile корня в conan graph info. Вложенные lockfiles
не объединяются; отдельный запрос вложенного узла использует его собственный lockfile.

Отсутствующий, неполный или несогласованный lockfile вызывает ошибку. Partial-режим
и автоматическое пересоздание при чтении не применяются. Прямые рёбра читаются из
нативного direct_host; транзитивные зависимости не превращаются в непосредственные.
Lockfile не устраняет несовместимые требования: такие графы вызывают VersionConflict.
Разрешение зависимостей принадлежит Conan; в публичном API каталога отдельного резолвера нет.

Find_dependents сначала преобразует незаданные координаты кандидатов в wildcard Conan list,
проверяет точные координаты и затем загружает граф каждого кандидата с его lockfile.
Transitive=False учитывает только прямую зависимость. Промежуточные узлы могут быть любыми.
Корень исключается; результаты уникальны и отсортированы по identity. Пустой SearchQuery
проверяет весь каталог. Непредставимые фильтры не имеют совпадений.

## Ошибки и ограничения эксперимента

NotFound означает отсутствие Ref; InvalidPackage — повреждённый или неподдерживаемый
формат; BackendUnavailable — ошибку API, CLI, сети или сервера. Ошибки кандидатов не скрываются.
PartialPublicationError содержит published — подтверждённые Ref и uncertain — Ref с
неопределённым результатом записи. Автоматического rollback/retry нет.

Нет индекса обратных связей и межоперационного кэша результатов. Стоимость поиска растёт
с количеством кандидатов, subprocess и сетевых запросов; публикация дополнительно копирует
Conan home. Это эксперимент для оценки сценариев на реальных данных, без гарантий
приемлемой производительности большого каталога.
