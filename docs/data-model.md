# Структуры данных

## Координаты и записи

Ref содержит пять обязательных строк в порядке `namespace, component, view, type, version`.
Namespace задаёт проект, component — компонент, view — представление (rtl, be, dft,
simulation), type — вариант внутри представления. Для текущих flow используются
rtl/default и be/lib-lef. Это соглашения flow; SDK не ограничивает словарь view/type.

Conan-имя — `namespace.component.view.type`, Conan version — отдельная версия.
Например, `soc.mmu.rtl.default/1.0`. Части имени содержат lowercase-буквы, цифры, `_`
и `-`; точки служат разделителями. Пустые значения и неоднозначное кодирование
отклоняются. Старые name/kind и трёхчастное имя не поддерживаются.
Все пять полей участвуют в равенстве, хеше и сортировке; каталог в identity не входит.

Ref остаётся frozen dataclass для стабильных ключей графа и словарей. Record,
Dependency, GraphResult и дескрипторы ресурсов также являются неизменяемыми значениями.
Catalog и Graph — обычные классы: они отвечают за операции и контекст проекта.

Record содержит ref, произвольный payload, необязательный GitResource source и
files — tuple имён упакованных файлов. Payload может быть JSON-значением либо
объектом с to_json() -> str (протокол JSONSerializable). После чтения или публикации
JSON становится независимым неизменяемым снимком: объекты — frozendict, массивы — tuple.
Python-классы не сохраняются, NaN/Infinity запрещены.

Record.to_json()/from_json() сериализуют запись; ref.to_dict() — только координаты.
Git source в Conan хранится отдельно от payload и record.json, в conandata.yml.
Один пакет имеет не более одного SCM-источника. Карта исходных файлов передаётся
в publish(files=...); в Record.files сохраняются только имена внутри пакета.

## Зависимости и поиск

Dependency(source, target) означает, что source требует target. Это точное ребро
без типа и дополнительных свойств. Один publish создаёт один source с уже
опубликованными зависимостями. Совместимость требований проверяет Conan.

SearchQuery содержит необязательные точные фильтры namespace/component/view/type/version,
соединённые через AND. Пустой запрос находит все записи; поиска по payload нет.
View и type независимы: один компонент может иметь несколько типов одного view
с одинаковой версией и отдельными Conan-пакетами.

## Проектный граф

Graph хранит описание одного проекта и явно выбранные dependencies и lockfile.
Flow сам читает deps.toml или другой формат и добавляет найденные зависимости.
Graph.resolve() возвращает GraphResult(records, dependencies), включая локальный корень.
Записи сортируются по Ref, рёбра — по source и target.

GraphResult.to_networkx() создаёт независимый DiGraph с ключами Ref. Атрибут вершины
record содержит Record, атрибут ребра dependency — Dependency. Изменение этого
графа не меняет исходный результат или опубликованные пакеты.

## Ресурсы

GitResource описывает точный Git-коммит; source() Conan получает его из GitLab.
Ref.materialize() возвращает MaterializedPackage.path с артефактами, а при
with_sources=True — отдельный source_path. Git-код не копируется в binary.
LocatedResource описывает внешний локальный или смонтированный путь и может храниться
в payload; его файлы не загружаются в Conan автоматически.
