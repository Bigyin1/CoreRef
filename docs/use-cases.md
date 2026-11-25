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
проекта в порядке, заданном в `files.rtl`. Симуляция передаёт файл списка
Verilator и использует defines выбранной конфигурации.

## Бэкенд до готовности DFT

В примере SoC маркер `dft/enabled` разрешает запуск DFT. Стадия включается при
наличии маркера и скрипта `dft/run.tcl`. Flow выбирает вход синтеза обычным условием Nix.

~~~nix
rtl = if dftReady then dft.artifact else rtl.forSynth.inline.out;
~~~

При пропуске DFT синтез получает обычный RTL с теми же правилами выбора
макроблоков. Ошибка выполнения Tcl завершает сборку DFT, её вывод доступен
через `nix log .#dft`.
