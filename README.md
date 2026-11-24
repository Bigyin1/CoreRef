# CoreRef

Python SDK для аппаратных маршрутов. Flow задаёт функции стадий, их входы и связи.
CoreRef строит derivations из выбранного маршрута. Nix запускает Python-функции и
переиспользует готовые результаты из Nix store или binary cache.

Проект объявляет зависимости в `manifest.yml` и фиксирует их в `manifest.lock`.
Результаты стадии доступны по отдельности. Flow выбирает RTL для симуляции, Liberty
и LEF для физического lint или GDS для финальной сборки.

- [Публичный API](docs/interfaces.md)
- [Пример маршрута](docs/use-cases.md)
- [Разработка и запуск в CI](docs/development.md)
