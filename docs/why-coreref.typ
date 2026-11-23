#let ink = rgb("#172739")
#let muted = rgb("#4b5c6f")
#let border = rgb("#cbd6e2")
#let blue = rgb("#eaf2fa")
#let green = rgb("#e9f4ef")
#let amber = rgb("#faf2e5")

#set document(title: "Зачем CoreRef")
#set page(paper: "a4", margin: (x: 16mm, y: 17mm))
#set text(font: "Libertinus Serif", size: 11pt, lang: "ru", fill: ink)
#set par(leading: 0.65em, spacing: 0.9em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 26pt)
#show heading.where(level: 2): set text(size: 15pt)
#show link: set text(fill: rgb("#235d92"))

#let columns = (1fr, 13pt, 1fr, 13pt, 1fr, 13pt, 1fr, 13pt, 1fr)
#let arrow = text(size: 12pt, fill: muted)[→]

#let card(title, detail, fill: white) = block(
  width: 100%,
  height: 72pt,
  inset: 5pt,
  radius: 4pt,
  stroke: 0.7pt + border,
  fill: fill,
)[
  #align(center + horizon)[
    #text(size: 9.6pt, weight: "bold", title) \
    #text(size: 9pt, fill: muted, detail)
  ]
]

#let pipeline(a, b, c, d, e) = grid(
  columns: columns,
  align: center + horizon,
  a, arrow, b, arrow, c, arrow, d, arrow, e,
)

#let feed(body) = align(center)[
  #block(
    width: 100%,
    height: 33pt,
    fill: amber,
    inset: 4pt,
    radius: 4pt,
  )[#align(horizon, text(size: 8.7pt, body))]
  #text(size: 11pt, fill: muted)[↓]
]

= Зачем CoreRef

Аппаратный маршрут можно рассматривать как компиляцию проекта. Инструменты
поэтапно преобразуют исходное описание в итоговое представление. В C++ это
исполняемый файл, а в ASIC-разработке — физическая топология микросхемы и набор
данных для её проверки.

== Сборка C++ на примере Clang и LLVM

#pipeline(
  card([Исходники C++], [`.cpp` и заголовки], fill: blue),
  card([Фронтенд Clang], [AST], fill: blue),
  card([Оптимизация LLVM], [LLVM IR], fill: blue),
  card([Генерация кода], [объектные файлы `.o`], fill: blue),
  card([Компоновка], [исполняемый файл], fill: green),
)

Драйвер `clang++` запускает нужные части инструментальной цепочки, включая
компоновщик.
Фронтенд разбирает код и строит абстрактное синтаксическое дерево (AST).
Затем код переводится в LLVM IR, оптимизируется и преобразуется в объектные
файлы. Компоновщик связывает их с библиотеками в исполняемый файл.
#link("https://clang.llvm.org/docs/Toolchain.html")[Документация Clang]

== Аппаратный маршрут от RTL до GDS

#block(breakable: false)[
  #grid(
    columns: columns,
    align: center + bottom,
    [], [],
    feed[SDC, Tcl синтеза \ Liberty], [],
    feed[Tcl для DFT], [],
    feed[SDC, Tcl для BE \ LEF и Liberty], [],
    feed[Модели RC],
  )
  #pipeline(
    card([Исходники RTL], [SystemVerilog, Verilog], fill: blue),
    card([Синтез], [Genus \ вентильный нетлист], fill: blue),
    card([DFT], [нетлист со scan-логикой], fill: blue),
    card([Физическая реализация], [Innovus \ разведённый дизайн], fill: blue),
    card([Экспорт и анализ], [GDS, SPEF, отчёты], fill: green),
  )
]

RTL задаёт логику, SDC описывает тактовые сигналы и временные ограничения,
а Tcl-сценарии задают действия инструментов для конкретного проекта.
Поэтому сценарии DFT, синтеза и физической реализации стоит рассматривать
как часть исходного кода. Инструменты также используют данные технологии
и библиотек, например временные модели Liberty и абстрактные описания геометрии ячеек в LEF.

Синтез выдаёт вентильный нетлист. DFT добавляет логику для производственных
тестов, например scan-цепочки. Физическая реализация размещает ячейки и
трассирует соединения. Из разведённого дизайна получают GDS с топологией,
SPEF с паразитными сопротивлениями и ёмкостями соединений и отчёты проверок. На схеме
показан один из вариантов маршрута. DFT может быть встроен в синтез или
частично выполняться на этапе физической реализации.
#link("https://www.cadence.com/en_US/home/resources/datasheets/genus-synthesis-solution-ds.html")[Genus]
· #link("https://www.cadence.com/en_US/home/resources/datasheets/innovus-implementation-system-ds.html")[Innovus]
· #link("https://openroad.readthedocs.io/en/latest/main/openroad/dft/README.html")[DFT]
· #link("https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html")[OpenROAD]

== Что связывает CoreRef

Многие переходы в C++ скрывает драйвер компилятора. В аппаратной разработке
нетлист и разведённый дизайн часто используют как самостоятельные артефакты.
Их передают другим командам, проверяют и используют в следующих задачах.
Для повторного запуска важно знать, какие версии RTL, ограничений, скриптов,
библиотек и инструментов использовались при сборке.

Flow объявляет стадии и их входы и выходы. CoreRef связывает эти стадии,
фиксирует зависимости проекта и даёт публиковать и получать отдельные
артефакты. Например, Flow может объявить выпущенный нетлист входом
отдельного маршрута физической реализации.
