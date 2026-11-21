# CoreRef

Экспериментальный Python SDK для каталога аппаратных компонентов на Conan.
Пакет хранит метаданные, зависимости и файлы; RTL-исходники получает из Git через SCM Conan.
SDK генерирует рецепты сам: приложение работает с `Ref`, `LocalRecord` и `Graph`.

Требуются Python 3.10+ и настроенный Conan 2.34: профили, remote и доступ к Git.
Используется стандартный Conan cache. Для установки из этого репозитория:

```sh
uv sync --frozen
```

```python
from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import Ref

catalog = Catalog()
cpu = Ref("soc.cpu/1.0")
packages = Graph(catalog, cpu).get_with_sources()
```

Готовый формат проекта — необязательный `manifest.yml`. С дополнением `coreref[cli]`
доступны группы Typer `deps` и `workspace` для включения в CLI своего flow. Собственные форматы также поддерживаются.

- [Публичный API и связи классов](docs/interfaces.md).
- [Примеры работы с RTL, LIB/LEF и результатами маршрута](docs/use-cases.md).
- [Разработка и проверки](docs/development.md).
