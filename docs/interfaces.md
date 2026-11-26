# API CoreRef

## Для CLI на Python

`Project` работает с локальной директорией или flake URL. `targets()` перечисляет
цели текущей платформы, `build()` возвращает пути выбранных результатов в Nix store,
а `log()` читает лог цели. Эти методы учитывают локальные подстановки.
`dependencies()` показывает прямые входы проекта и закреплённые ревизии.

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

Флейк объявляет `packages`, `checks`, `apps` и `devShells` обычными средствами Nix.
CoreRef предоставляет `coreref.lib.mkStage` для запуска Python-функции в отдельной
деривации. Автор маршрута определяет платформы, связи между стадиями и правила
выбора представлений IP в своём флейке.

Экспериментальный flow экспортирует собственные `lib.mkProject config` и
`lib.componentFor system`. Параметризуемую зависимость можно объявить обычной
Nix-функцией. Например, проект SRAM экспортирует `lib.mkSram { depth = 5; width = 3; }`.
Потребитель получает через `lib.componentFor system` представления выбранной конфигурации.

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

Для вызова передайте выбранный набор Nix-пакетов и функцию создания
Python-окружения. Пакет CoreRef доступен как `coreref.packages.${system}.default`.
Если используете его в окружении из `pkgs`, настройте во флейке
`inputs.coreref.inputs.nixpkgs.follows = "nixpkgs"`, чтобы Python-пакеты
использовали один nixpkgs.

~~~nix
let
  python = extra: pkgs.python3.withPackages (p:
    [ coreref.packages.${system}.default flowStages ] ++ extra p);
  stage = coreref.lib.mkStage { inherit pkgs python; };
in
stage {
  name = "synth";
  transform = "example_flow.synth:run";
  inputs.rtl = rtl.artifacts.out;
  nativeBuildInputs = [ pkgs.yosys ];
  outputs.out = "netlist.v";
}
~~~

В примере `flowStages` содержит Python-модуль `example_flow.synth`, а `rtl`
является предыдущей стадией маршрута. В экспериментальном flow код стадий
упакован отдельно от CLI. Изменение CLI или его зависимостей сохраняет ключи
сборки стадий.

Функция Python получает `work: Path`, `log: Path` и
`configuration: Mapping[str, object]`. Runner раскладывает объявленные входы
в рабочем каталоге. Функция записывает результаты относительно `work`, а
диагностику — в `log`. Лог доступен через `nix log .#имя-стадии`.
