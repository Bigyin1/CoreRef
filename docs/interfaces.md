# API CoreRef

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
