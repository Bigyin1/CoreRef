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

`checkout("counter", "/work/counter")` клонирует закреплённую Git-ревизию
и сразу подключает checkout. `editables()` показывает активные подстановки,
а `remove_editable("counter")` убирает одну из них. Для вложенного входа
укажите путь вроде `flow/coreref`. Входы в `flake.nix` и `flake.lock`
остаются исходными. Подстановки записываются локально в
`.coreref/workspace.json` и передаются Nix как `--override-input`.

## Для автора маршрута

`coreref.lib.mkRoute` принимает Nixpkgs `pkgs`, имя проекта, путь `stageMap`,
список `use`, функцию `python` и данные для привязки входов. Функция `python`
получает список дополнительных Python-пакетов стадии и возвращает интерпретатор
с установленными CoreRef, Flow и этими пакетами.

Каждый модуль карты содержит именованные стадии. Стадия объявляет `transform`
в форме `package.module:function`, выходы `outputs` и используемые входы.
В `outputs` должен быть `out`. Ссылка `{ stage = "synth", output = "out" }`
соединяет вход с определённым выходом. Строка во входе выбирает файл из `files`.
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
