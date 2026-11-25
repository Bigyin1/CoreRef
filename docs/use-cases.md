# Проект с двумя стадиями

Автор Flow задаёт поставку входов синтезу и физической реализации в карте.

~~~toml
[modules.backend.synth]
transform = "demo_flow.synth:run"
inputs."source.v" = "rtl"
tools = ["yosys"]
outputs.out = "netlist.v"
exports.netlist = "out"

[modules.backend.layout]
transform = "demo_flow.layout:run"
inputs."netlist.v" = { stage = "synth", output = "out" }
inputs."constraints.sdc" = "constraints"
tools = ["openroad"]
outputs = { out = "layout.gds", lef = "abstract.lef" }
exports = { gds = "out", lef = "lef" }
~~~

Проект передаёт Flow пути к RTL и SDC. `nix build .#netlist` запускает синтез.
`nix build .#lef` выполняет физическую реализацию при промахе кэша. При наличии
LEF в кэше Nix скачивает этот выход. Изменение SDC в приведённой карте меняет
рецепт `layout`. Рецепт `synth` зависит от Verilog и своего инструмента.

Другой проект может использовать выход напрямую. Если ему одновременно нужны
артефакты макроблока и его поведенческая модель, Flow передаёт эти представления
разным стадиям.

## Бэкенд до готовности DFT

В примере SoC маркер `dft/enabled` разрешает запуск DFT. Стадия включается при
наличии маркера и скрипта `dft/run.tcl`. Синтез выбирает вход в карте стадий.

~~~toml
[modules.backend.synth.inputs."design.v"]
stage = "dft"
output = "out"
fallback = { stage = "rtl-synthesis-input", output = "out" }
~~~

При пропуске DFT синтез получает обычный RTL с теми же правилами выбора
макроблоков. Ошибка выполнения Tcl завершает сборку DFT, её вывод доступен
через `nix log .#dft`.
