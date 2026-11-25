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

Флейк Flow вызывает `coreref.lib.mkFlow` и передаёт `nixpkgs`, путь к исходникам
Python-пакета через `source` и карту `stageMap`. В корне `source` находятся
`pyproject.toml` для Hatchling и каталог `src`. Функция `configure` связывает
логические имена инструментов с пакетами Nix и задаёт общие `parameters`.
Через `pythonDependencies` можно добавить зависимости Python-пакета Flow.
Имя скрипта из `project.scripts`, переданное в `cli`, выставляет приложение
`nix run .#flow`. В каталоге проекта `nix develop` даёт Python, CoreRef и Flow
с зависимостями из закреплённого nixpkgs. Скрипты Flow доступны в `PATH`,
например `nix develop -c coreref-flow deps`.

Флейк аппаратного проекта вызывает `flow.lib.mkProject`. Он задаёт `profile`,
`name`, `source` для идентификации ревизии, `files`, `parameters` и функцию
`dependencies` от платформы. Файлы проекта переопределяют одноимённые пути
из `[files]` карты, заданные относительно репозитория Flow.

- `profiles` выбирают модули через `use`, переопределения через `variants` и цель
  `default`. Правило с `whenFiles` добавляет модули, варианты и представления
  при наличии всех перечисленных файлов. При их отсутствии применяется `otherwise`.
- `views` профиля дают имена файлам проекта и выходам стадий. Потребитель получает
  эти представления через `lib.componentFor system`. `groups` указывают имена
  файлов у потребителя и список подходящих представлений в порядке предпочтения.
  В пути `sources/{name}.v` CoreRef подставляет имя зависимости.
- Именованная `selection` группы ограничивает обход через `stopAt` и фильтрует
  компоненты по `kinds` или `excludeKinds`. Остановка на макроблоке обрезает его
  ветвь. Общая зависимость остаётся доступной через другую ветвь.
  `dependencyParameters` позволяет взять параметр у одной выбранной зависимости
  и задать значение для пустой выборки.

Параметризуемый компонент экспортирует Nix-функцию, которая передаёт конкретные
значения в `mkProject.parameters` и задаёт имя компонента для этой конфигурации.
Например, потребитель SRAM получает нужное представление через
`(sram.lib.mkSram { depth = 5; width = 3; }).lib.componentFor system`.
Сам проект SRAM может выставить типовые геометрии как именованные цели для CLI.

Низкоуровневый `coreref.lib.mkRoute` принимает Nixpkgs `pkgs`, имя проекта, путь `stageMap`,
список `use`, функцию `python` и данные для привязки входов. Функция `python`
получает список дополнительных Python-пакетов стадии и возвращает интерпретатор
с установленными CoreRef, Flow и этими пакетами.

Каждый модуль карты содержит именованные стадии. Стадия объявляет `transform`
в форме `package.module:function`, выходы `outputs` и используемые входы.
В `outputs` должен быть `out`. Ссылка `{ stage = "synth", output = "out" }`
соединяет вход с определённым выходом. Строка во входе выбирает файл из `files`.
Поле `fallback` в ссылке задаёт запасной вход. CoreRef выбирает его при оценке
графа, если основная стадия известна карте и отсутствует в выбранном маршруте.
`groups` добавляет именованные группы файлов, а `parameters` выбирает нужные
ключи из параметров проекта. `tools` выбирает пакеты Nix либо закреплённые
инструменты площадки из `siteTools`. `needs` задаёт порядок для проверки,
которая не отдаёт файл следующей стадии.

`variants` применяет именованные переопределения к выбранным стадиям. `exports`
добавляет имя цели для отдельного выхода. CoreRef возвращает `packages` для
flake, `stages` с derivations и `artifacts` с путями файлов, пригодными для
передачи другой стадии или проекту.

~~~python
from collections.abc import Mapping
from pathlib import Path


def synthesize(work: Path, log: Path, parameters: Mapping[str, object]) -> None:
    """Run synthesis on the declared files and leave netlist.v in work."""
    ...
~~~

Runner вызывает функцию в каталоге с объявленными файлами. Инструменты доступны
через PATH. Функция пишет выходы относительно `work`, диагностику в `log`.
Nix сохраняет каждый объявленный файл в отдельном выходе, а лог доступен через
`nix log .#имя-стадии`.
