#let ink = rgb("#172739")
#let muted = rgb("#4b5c6f")
#let border = rgb("#cbd6e2")
#let blue = rgb("#eaf2fa")
#let green = rgb("#e9f4ef")
#let amber = rgb("#faf2e5")
#let violet = rgb("#f1edf7")

#set document(title: "Зачем CoreRef")
#set page(paper: "a4", margin: (x: 16mm, y: 17mm))
#set text(font: "Libertinus Serif", size: 11pt, lang: "ru", fill: ink)
#set par(leading: 0.65em, spacing: 0.9em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 26pt)
#show heading.where(level: 2): set text(size: 15pt)
#show link: set text(fill: rgb("#235d92"))

#let stage-gap = 13pt
#let columns(count) = (1fr,) + (stage-gap, 1fr) * (count - 1)
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

#let inputs(..cells) = {
  let items = cells.pos()
  grid(
    columns: columns(items.len()),
    align: center + bottom,
    ..items.intersperse([]),
  )
}

#let dependency(title, detail, ports) = (
  title: title,
  detail: detail,
  ports: ports,
)

#let dependencies(count, ..packages) = layout(area => {
  let step = (area.width + stage-gap) / count
  let first = (step - stage-gap) / 2
  let width = step - 6pt
  let wire = 0.7pt + muted

  block(width: 100%, height: 65pt, above: 0pt, below: 0pt)[
    #for package in packages.pos() [
      #let ports = package.ports.map(((index, offset)) => first + index * step + offset)
      #let left-x = ports.first()
      #let right-x = ports.last()
      #let middle = (left-x + right-x) / 2
      #if left-x != right-x [
        #place(top + left, dx: left-x, dy: 14pt,
          line(length: right-x - left-x, stroke: wire))
      ]
      #place(top + left, dx: middle, dy: 14pt,
        line(end: (0pt, 12pt), stroke: wire))
      #for x in ports [
        #place(top + left, dx: x, line(end: (0pt, 14pt), stroke: wire))
        #place(top + left, dx: x - 2.5pt,
          polygon((0pt, 4pt), (2.5pt, 0pt), (5pt, 4pt), fill: muted))
      ]
      #place(top + left, dx: middle - width / 2, dy: 26pt)[
        #block(width: width, height: 38pt, inset: 4pt, radius: 4pt, fill: violet)[
          #align(center + horizon)[
            #text(size: 9.1pt, weight: "bold", package.title) \
            #text(size: 8.5pt, fill: muted, package.detail)
          ]
        ]
      ]
    ]
  ]
})

= Зачем CoreRef

Как и сборка C++, аппаратный маршрут проходит через промежуточные представления.
В одном случае результатом становится исполняемый файл, в другом — топология
микросхемы и данные для её анализа и интеграции.

== Сборка C++ на примере Clang и LLVM

#block(breakable: false)[
  #inputs(
    [],
    feed[Опции CMake],
    feed[Флаги компилятора],
    [],
    [],
    feed[Скрипт линкера \ Флаги линковки],
  )
  #pipeline(
    card([Исходники C++], [`.cpp` и заголовки], fill: blue),
    card([Подготовка], [CMake \ кодогенерация \ (если нужна)], fill: amber),
    card([Фронтенд], [Clang \ AST], fill: blue),
    card([Оптимизация], [LLVM IR], fill: blue),
    card([Генерация кода], [объектные файлы `.o`], fill: blue),
    card([Компоновка], [исполняемый файл], fill: green),
  )
  #dependencies(
    6,
    dependency([Заголовки], [stdlib, Boost], ((0, 0pt),)),
    dependency([Бинарные библиотеки], [stdlib, Boost], ((5, 0pt),)),
  )
]

*Исходники, файлы CMake, проектные флаги компилятора и скрипт линкера
разрабатываются вместе в одном репозитории.* Внешние заголовки и библиотеки
подключаются отдельно.
#link("https://cmake.org/cmake/help/latest/command/add_custom_command.html#examples-generating-files")[CMake]
· #link("https://clang.llvm.org/docs/Toolchain.html")[Clang]
· #link("https://sourceware.org/binutils/docs/ld.html#Scripts")[Линкерные скрипты]
· #link("https://www.boost.org/doc/libs/latest/more/getting_started/unix-variants.html")[Boost]

== Аппаратный маршрут от RTL до GDS

#block(breakable: false)[
  #inputs(
    [],
    feed[Tcl для DFT],
    feed[SDC, Tcl синтеза],
    feed[SDC, Tcl для BE],
    feed[Модели RC],
  )
  #pipeline(
    card([Исходники RTL], [SystemVerilog, Verilog], fill: amber),
    card([Подготовка DFT], [RTL с тестовой логикой], fill: blue),
    card([Синтез], [Genus \ вентильный нетлист], fill: blue),
    card(
      [Физическая реализация],
      [Innovus \ #text(size: 8pt)[floorplan / placement / \ CTS / routing]],
      fill: blue,
    ),
    card([Экспорт и анализ], [GDS \ LIB (Liberty), LEF \ SPEF, отчёты], fill: green),
  )
  #dependencies(
    5,
    dependency([Soft IP], [RTL], ((0, 0pt),)),
    dependency([Иерархический DFT], [RTL блока с DFT], ((1, 0pt),)),
    dependency([Технологическая библиотека], [Liberty, LEF], ((2, 0pt), (3, -9pt))),
    dependency([Hard IP], [Liberty, LEF, GDS], ((3, 9pt), (4, 0pt))),
  )
]

*Проектные ограничения SDC и Tcl-скрипты — часть исходного кода наравне с RTL.*
Общие скрипты и настройки для DFT, синтеза и физической реализации может
поставлять сам Flow.

У Hard IP физическая реализация использует Liberty и LEF, а при экспорте
топологии подключается GDS блока. Порядок DFT зависит от маршрута.
#link("https://openroad.readthedocs.io/en/latest/main/openroad/dft/README.html")[DFT]
· #link("https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html")[RTL-to-GDS]
· #link("https://openroad.readthedocs.io/en/latest/main/openroad/README.html#abstract-lef-support")[LEF]

== Что связывает CoreRef

В аппаратном маршруте команды передают друг другу нетлисты и другие промежуточные
артефакты. Flow описывает стадии и их входы и выходы, а CoreRef фиксирует
зависимости проекта и даёт публиковать и получать отдельные результаты.

#pagebreak()

= Текущий маршрут одного проекта

*RTL, DFT и BE составляют единый маршрут одного проекта* — хардмакроблока
или верхнеуровневого модуля микросхемы.

#block(breakable: false)[
  #pipeline(
    card(
      [Git-репозиторий RTL],
      [RTL и SDC \ Подключение Soft IP \ Линт с хардмакросами],
      fill: amber,
    ),
    card([Git-репозиторий DFT], [RTL на входе \ Скрипты DFT], fill: rgb("#f8e9ed")),
    card([Физическая реализация (BE)], [Вне Git], fill: blue),
    card([Результаты BE], [NFS], fill: green),
  )
  #dependencies(
    4,
    dependency([Хардмакросы], [Зависимости с NFS], ((0, 0pt), (2, 0pt))),
  )
]
