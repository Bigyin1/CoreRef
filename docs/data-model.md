# Структуры данных

## Ref и Record

Ref — неизменяемая точная ссылка с обязательными непустыми строками namespace,
name, kind и version. Они соответствуют SoC-проекту, компоненту, представлению
и версии представления. Идентичность включает все четыре поля; package_name
возвращает namespace.name.kind без версии. str(ref) возвращает полную ссылку.
Kind — пользовательская строка, встроенного Enum нет.

Conan кодирует ссылку как namespace.name.kind/version. Компоненты имени содержат
lowercase-буквы, цифры, `_`, `-`; точки служат разделителями. Версия и длина ссылки
проверяются на совместимость с Conan.

Record содержит ref, payload, необязательный один source (GitResource) и files
(имена упакованных артефактов). source/files управляются каталогом и отделены от payload. Payload — произвольное JSON-значение или объект
с методом to_json(), возвращающим JSON-документ. Сериализация принадлежит модели:
Record.to_json()/from_json(). При публикации содержимое становится независимым
неизменяемым снимком: объекты — frozendict, массивы — tuple. Python-классы пользователя
не сохраняются и не восстанавливаются автоматически. NaN/Infinity запрещены.

## Dependency

Dependency(source: Ref, target: Ref) означает, что source требует target.
Оба конца задаются точно. Дополнительных полей, типов и параллельных привязок нет.
Модель неизменяема; отдельного формата хранения или JSON-сериализации рёбер нет.

Один вызов publish создаёт один source и принимает его зависимости как tuple[Ref, ...].
Каждый target должен уже существовать. Dependency(source, target) возвращается при
чтении графа. Повторная публикация идентичности, дубликаты зависимостей, ссылки
на отсутствующие узлы и ссылки на себя запрещены. Conan проверяет разрешение графа
и конфликты версий. Опубликованные данные не изменяются.

## Поиск и графы

SearchQuery задаёт необязательные точные фильтры namespace/name/kind/version,
соединённые через AND. Пустой запрос находит все записи. Поиска по payload нет.

Dependencies и find_dependents возвращают непосредственно tuple[Record, ...],
отсортированный по Ref. Промежуточной структуры соседней привязки нет.

GraphResult содержит records и dependencies. Корень входит в records;
записи сортируются по полям Ref, зависимости — по source и target.

To_networkx() создаёт независимый nx.DiGraph с ключами Ref. Атрибут вершины record
содержит Record, атрибут ребра dependency — Dependency. Изменение экспортированного
графа не меняет каталог или исходный GraphResult.

## Ресурсы

GitResource описывает точный Git-коммит; LocatedResource — абсолютный путь
к внешнему файлу или каталогу. GitResource не выполняет Git-операций;
LocatedResource.materialize() возвращает MaterializedResource(path).
Git source пакета задаётся полем source; в Conan его координаты хранятся в conandata.yml.
LocatedResource может храниться в payload и не загружается в Conan автоматически.
Catalog.materialize(ref) возвращает MaterializedPackage: path указывает на артефакты,
а source_path — на отдельный SCM checkout, если передан with_sources=True.
Git-код не копируется в новые Conan binaries; source() получает его из GitLab.
