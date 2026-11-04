# hwcatalog-conan

Отдельный экспериментальный проект на основе hwcatalog, коммит `3dba7e2`.
Имя дистрибутива — `hwcatalog-conan`, импорты сохранены: `hwcatalog`.

Узлы хранятся как Conan-рецепты с Record и собственным `conan.lock`.
Формат имени — `namespace.name.kind/version`, например `soc1.cpu.lib-lef/4.0`.
Поиск представлений выбирает кандидатов по имени и проверяет их зависимость
от точной версии RTL; промежуточные узлы могут быть любыми.

```python
from pathlib import Path
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Ref
from hwcatalog.backends.conan import ConanBackend

with Catalog(ConanBackend(Path("/absolute/path/to/conan-home"))) as catalog:
    rtl = Ref("cpu", "2.3", "rtl", "soc1")
    implementations = catalog.find_dependents(
        rtl,
        candidates=SearchQuery(namespace="soc1", name="cpu", kind="lib-lef"),
    )
```

Требуются Python 3.15, uv и Conan 2.34. Окружение разработки включает Conan:

```sh
uv sync --frozen
uv run python examples/conan_macro.py
make check unit build
make integration        # изолированный Memgraph через Docker
make conan-integration  # временный локальный Conan server
```

Пример публикует RTL, промежуточный узел и LIB/LEF в одноразовый локальный кэш.
API сохранён с добавлением find_dependents и capabilities. Изменение поведения:
`dependency_closure()` возвращает точный граф по умолчанию; резолвер Memory/Memgraph
включается через `resolve_versions=True`. Conan использует lockfile корня.

Обратный поиск проверяет граф каждого кандидата. Подготовка публикации копирует
Conan home во временный кэш для проверки всего batch. Эти ограничения существенны
для оценки пригодности Conan на больших данных. Поддерживается только канонический
формат рецепта без settings/options; файлы ресурсов остаются внешними.

Подробности: [Conan, lockfiles, внешний CI и ограничения](docs/conan.md).

## Унаследованный полный API

Python-библиотека для каталога версионированных hardware-проектов, запусков flow
и связей между ними. Каталог хранит граф и пользовательские данные; файлы ресурсов
остаются во внешних хранилищах.

Основные возможности:

- Единая модель записей с точными координатами: namespace, имя, версия и вид объекта.
- Произвольный JSON или пользовательский сериализуемый объект в содержимом записи.
- Публикация неизменяемых записей и связей, чтение и поиск по общим полям.
- Прямой и обратный поиск зависимостей, поиск реализаций и происхождения результатов flow.
- Разрешение версий по явно объявленной обратной совместимости со стабильным
  результатом для фиксированного корня.
- Экспорт графа в NetworkX с сохранением параллельных связей.
- Хранение в Memgraph, памяти или Conan с ограниченным набором возможностей.
- Материализация Git-ресурсов в кэш и ссылки на файловые ресурсы по абсолютному пути.

Проект находится на стадии proof of concept. Требуется Python 3.15.

Документация:

- [Варианты использования](docs/use-cases.md)
- [Структуры данных](docs/data-model.md)
- [Интерфейсы и операции](docs/interfaces.md)
- [Архитектурные решения и ограничения](docs/decisions.md)
- [Разработка и локальное окружение](docs/development.md)
