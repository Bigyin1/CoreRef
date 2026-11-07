# hwcatalog-conan

Экспериментальный Python SDK для каталога аппаратных компонентов на Conan.
Пакет хранит метаданные, зависимости и файлы; RTL-исходники получает из Git через SCM Conan.
SDK генерирует рецепты сам: приложение работает с `Ref`, `LocalRecord` и `Graph`.

Требуются Python 3.15 и настроенный Conan 2.34: профили, remote и доступ к Git.
Используется стандартный Conan cache. Для установки из этого репозитория:

```sh
uv sync --frozen
```

```python
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import Ref

catalog = Catalog()
rtl = Ref("soc.cpu.rtl.default/1.0", catalog=catalog)
packages = rtl.graph().get_with_sources()
```

- [Публичный API и связи классов](docs/interfaces.md).
- [Два примера: RTL-проект и поиск LIB/LEF](docs/use-cases.md).
- [Разработка и проверки](docs/development.md).
