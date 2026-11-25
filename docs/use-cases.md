# Проект с двумя стадиями

В функции проекта Flow синтез и физическая реализация связаны прямой
ссылкой на выход предыдущей стадии.

~~~nix
let
  synth = mkStage {
    name = "demo-synth";
    transform = "demo_flow.synth:run";
    inputs.rtl = files.rtl;
    nativeBuildInputs = [ pkgs.yosys ];
    outputs.out = "netlist.v";
  };
  layout = mkStage {
    name = "demo-layout";
    transform = "demo_flow.layout:run";
    inputs = {
      netlist = synth.artifacts.out;
      constraints = files.constraints;
    };
    nativeBuildInputs = [ pkgs.openroad ];
    outputs = { out = "layout.gds"; lef = "abstract.lef"; };
  };
in {
  netlist = synth.package;
  gds = layout.package;
  lef = pkgs.lib.getOutput "lef" layout.package;
}
~~~

Проект передаёт Flow пути к RTL и SDC. `nix build .#netlist` запускает синтез.
`nix build .#lef` выполняет физическую реализацию при промахе кэша. При наличии
LEF в кэше Nix скачивает этот выход. Изменение SDC меняет
рецепт `layout`. Рецепт `synth` зависит от Verilog и своего инструмента.

Другой проект может использовать выход напрямую. Если ему одновременно нужны
артефакты макроблока и его поведенческая модель, Flow передаёт эти представления
разным стадиям.

## Выбор RTL по параметрам

Тестовый проект `increment` предоставляет `mkRtl`. Опция `step` выбирает файл
с реализацией шага и define для тестбенча.

~~~nix
variant = increment.lib.mkRtl { step = 2; };
rtl = variant.packages.${system}.rtl;
simulation = variant.packages.${system}.simulate;
~~~

Вход `rtl` стадии симуляции подключён через `rtl.inline.out`. При сборке
RTL-стадия сохраняет исходники и формирует `sources.f` с путями относительно
своего выходного каталога. В списке сначала идут зависимости, затем файлы
проекта в порядке, заданном в `rtl.sources`. Симуляция передаёт файл списка
Verilator. Маршрут явно передаёт список `defines` стадии RTL или DFT,
которая сохраняет его рядом с исходниками. Симуляция и синтез используют
опции выбранного RTL. `verificationDefines` добавляет параметры тестбенча.

В SoC функции `mkSram { depth = 4; width = 4; }` и
`mkSram { depth = 8; width = 8; }` дают разные компоненты. Симуляция получает
их сгенерированный Verilog. Физический маршрут получает отдельные
Liberty, LEF и GDS для каждой геометрии. Верхний синтез читает обычный RTL
макроблока с флагом Yosys `-lib`. Если поставщик уже предоставляет Verilog-файл
с портами, проект может передать его в `rtl.blackbox`.

Ревизию зависимости выбирает `flake.lock`. Пока SoC использует старый lock,
новый коммит в репозитории макроблока не меняет сборку. После обновления lock
Nix вычисляет стадии по новым входам. Правка RTL макроблока меняет ключ
верхнего синтеза при использовании этого RTL. Правка одной документации
при тех же входах позволяет переиспользовать ранее собранный netlist.

## Бэкенд до готовности DFT

В SoC `dft.enabled = true` разрешает запуск DFT. Проект задаёт
скрипт в `dft.script`. Flow выбирает вход синтеза обычным условием Nix.

~~~nix
dft = {
  enabled = true;
  script = ./dft/run.tcl;
};
backend = {
  constraints = ./be/constraints.sdc;
  scripts.place = ./be/place.tcl;
};
~~~

~~~nix
rtl = if dftReady then dftRoute.artifact else rtlRoute.forSynth.inline.out;
~~~

При `dft.enabled = false` или отсутствии скрипта синтез получает обычный RTL с теми же правилами выбора
макроблоков. Ошибка выполнения Tcl завершает сборку DFT, её вывод доступен
через `nix log .#dft`.
