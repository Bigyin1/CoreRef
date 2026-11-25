# API CoreRef

## Для CLI на Python

`Project` работает с локальной директорией или flake URL. `build()` возвращает
пути выбранных результатов в Nix store, `dependencies()` показывает прямые
входы проекта и закреплённые ревизии.

~~~python
from pathlib import Path

from coreref import Project

project = Project(Path.cwd())
for dependency in project.dependencies():
    print(dependency.name, dependency.revision)

project.add_editable("counter", "/work/counter")
(netlist,) = project.build("netlist", options=("--no-link",))
print(netlist / "netlist.v")
~~~

`checkout("counter")` клонирует закреплённую Git-ревизию в
`.coreref/checkouts/counter` и сразу подключает checkout. Можно также передать
свой путь. `editables()` показывает активные подстановки,
а `remove_editable("counter")` убирает одну из них. Для вложенного входа
укажите путь вроде `flow/coreref`. Входы в `flake.nix` и `flake.lock`
остаются исходными. Подстановки записываются локально в
`.coreref/workspace.json` и передаются Nix как `--override-input`.

`lock()` добавляет недостающие записи в `flake.lock`. `lock("counter")` обновляет выбранный вход.
`run("flow", "deps")` запускает CLI Flow из флейка проекта.

## Для автора маршрута

Флейк Flow вызывает `coreref.lib.mkFlow` с `nixpkgs`, `source` и функцией
`project`. В корне `source` находятся `pyproject.toml` для Hatchling и каталог
`src`. `pythonDependencies` добавляет зависимости Python-пакета Flow. Имя
скрипта из `project.scripts`, переданное в `cli`, выставляет приложение
`nix run .#flow`. В каталоге проекта `nix develop` даёт Python, CoreRef и
Flow с зависимостями из закреплённого nixpkgs.

Функция `project = { pkgs, system, mkStage }: config: ...` вызывается из
`flow.lib.mkProject config` для каждой платформы. Она возвращает `packages` и
`component`. Flow определяет форму `config` и выбирает готовый маршрут.
Например, экспериментальный Flow принимает `profile`, `name`, `source`,
настройки стадий `rtl`, `dft`, `backend` и `memory`, параметры проекта
и функцию `dependencies` от платформы.

`lib.componentFor system` даёт доступ к компоненту с полями `name`, `id`,
`kind`, `views`, `parameters` и `dependencies`. Библиотечная функция
`selectComponents { roots, stopAt, filter }` возвращает достижимые компоненты
в порядке зависимостей. Предикат `stopAt` обрезает ветвь после выбранного узла,
а `filter` отбирает компоненты из результатов обхода. Общая зависимость
включается один раз, если она достижима хотя бы по одному пути. Две ревизии
одного компонента или разные наборы его зависимостей в области обхода
считаются конфликтом. Фильтр применяется к уже упорядоченному списку. Для сравнения представлений
передайте `viewKey = component: component.views.rtl` или другую функцию выбора.
При повторном имени сравниваются ID и выбранные входы, включая порядок файлов.
Несовместимые представления вызывают ошибку с обоими путями к компоненту.

Для параметризуемых зависимостей Flow или сам проект могут экспортировать
обычную Nix-функцию. Например, нужное представление SRAM доступно через
`(sram.lib.mkSram { depth = 5; width = 3; }).lib.componentFor system`.

### Стадии

`mkStage` принимает имя `name`, функцию `transform` в форме
`package.module:function`, именованные `inputs`, `outputs` и
`configuration` с параметрами Python-функции. Входом может быть путь к файлу
или каталогу, выбранный результат Nix либо упорядоченный список путей. Список
на входе `files` раскладывается в `work / "files"` по подкаталогам
`000000`, `000001` и далее с сохранением имён файлов. В `outputs` укажите
основной выход `out` и при необходимости дополнительные выходы.

`nativeBuildInputs` добавляет инструменты из nixpkgs, `pythonPackages`
выбирает дополнительные пакеты из набора Python, `siteTools` принимает
точные версии установленных на площадке инструментов. `needs` принимает
список derivations, которые нужно собрать перед стадией, например проверку
синтеза перед floorplan.

- `stage.package` можно выставить как цель flake или взять из неё конкретный
  выход через `pkgs.lib.getOutput`.
- `stage.artifacts.<output>` — путь к файлу или каталогу в выбранном выходе.
  Передайте этот путь в `inputs` потребителя. Nix собирает каждую стадию
  со своим набором инструментов и переиспользует готовые выходы.
  Вызовы `stage.inline.<output>` из прежних Flow заменяются на
  `stage.artifacts.<output>`.

При прямом вызове используйте `coreref.lib.mkStage { inherit pkgs python; } spec`.
`python` принимает функцию выбора пакетов и возвращает интерпретатор
с CoreRef, модулем преобразования и выбранными пакетами.

Функция Python получает `work: Path`, `log: Path` и
`configuration: Mapping[str, object]`. Runner раскладывает объявленные входы
в рабочем каталоге. Функция записывает результаты относительно `work`, а
диагностику — в `log`. Лог доступен через `nix log .#имя-стадии`.
