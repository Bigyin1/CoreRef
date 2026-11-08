# CoreRef

Экспериментальный Python SDK для каталога аппаратных компонентов на Conan.
Пакет хранит метаданные, зависимости и файлы; RTL-исходники получает из Git через SCM Conan.
SDK генерирует рецепты сам: приложение работает с `Ref`, `LocalRecord` и `Graph`.

Требуются Python 3.15 и настроенный Conan 2.34: профили, remote и доступ к Git.
Используется стандартный Conan cache. Для установки из этого репозитория:

```sh
uv sync --frozen
```

```python
from coreref.api.catalog import Catalog
from coreref.api.records import Ref

catalog = Catalog()
rtl = Ref("soc.cpu.rtl.default/1.0", catalog=catalog)
packages = rtl.graph().get_with_sources()
```

Готовый формат проекта — необязательный `deps.yaml`. С дополнением `coreref[cli]`
доступны команды `coreref deps` для настройки и получения зависимостей, а также workspace;
группы Typer можно встроить в CLI своего flow. Собственные форматы также поддерживаются.

- [Публичный API и связи классов](docs/interfaces.md).
- [Примеры: RTL, поиск LIB/LEF и несколько результатов проекта](docs/use-cases.md).
- [Разработка и проверки](docs/development.md).
