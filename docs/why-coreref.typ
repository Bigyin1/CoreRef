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

#let columns(count) = (1fr,) + (13pt, 1fr) * (count - 1)
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

#let pipeline(..cards) = {
  let nodes = cards.pos()
  grid(
    columns: columns(nodes.len()),
    align: center + horizon,
    ..nodes.intersperse(arrow),
  )
}

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

Как и сборка C++, аппаратный маршрут проходит через промежуточные представления.
В одном случае результатом становится исполняемый файл, в другом — топология
микросхемы и данные для её анализа и интеграции.

== Сборка C++ на примере Clang и LLVM

#pipeline(
  card([Исходники C++], [`.cpp` и заголовки], fill: blue),
  card([Подготовка], [CMake \ кодогенерация \ (если нужна)], fill: amber),
  card([Фронтенд], [Clang \ AST], fill: blue),
  card([Оптимизация], [LLVM IR], fill: blue),
  card([Генерация кода], [объектные файлы `.o`], fill: blue),
  card([Компоновка], [исполняемый файл], fill: green),
)

CMake конфигурирует сборку. Её правила могут запускать генераторы, чьи
результаты компилируются вместе с остальными исходниками.
#link("https://cmake.org/cmake/help/latest/command/add_custom_command.html#examples-generating-files")[CMake]
· #link("https://clang.llvm.org/docs/Toolchain.html")[Clang]

== Аппаратный маршрут от RTL до GDS

#block(breakable: false)[
  #grid(
    columns: columns(5),
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
    card([Экспорт и анализ], [GDS \ LIB (Liberty), LEF \ SPEF, отчёты], fill: green),
  )
]

RTL, ограничения SDC и управляющие Tcl-скрипты для DFT, синтеза и физической
реализации вместе описывают исходный проект. На выходе наряду с GDS можно получить
временную модель блока (Liberty) и его физический абстракт (LEF). Порядок DFT
зависит от маршрута.
#link("https://openroad.readthedocs.io/en/latest/main/openroad/dft/README.html")[DFT]
· #link("https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html")[RTL-to-GDS]
· #link("https://openroad.readthedocs.io/en/latest/main/openroad/README.html#abstract-lef-support")[LEF]

== Что связывает CoreRef

В аппаратном маршруте команды передают друг другу нетлисты и другие промежуточные
артефакты. Flow описывает стадии и их входы и выходы, а CoreRef фиксирует
зависимости проекта и даёт публиковать и получать отдельные результаты.
