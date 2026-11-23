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

== Сборка C++ на примере Clang и LLVM <cpp-build>

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

== Аппаратный маршрут от RTL до GDS <rtl-to-gds>

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


#table(
  columns: (21mm, 49mm, 1fr),
  inset: (x: 8pt, y: 7pt),
  stroke: 0.6pt + border,
  fill: (_, row) => if row == 0 { blue } else { white },
  table.header([*Часть*], [*Где ведётся работа*], [*Материалы и результаты*]),
  [*RTL*],
  [Git-репозиторий],
  [RTL-код и ограничения SDC. Для каждой зависимости Soft IP фиксируют ID
   Git-коммита (аналог lockfile) и по нему получают исходники. Для линта
   используют хардмакросы с NFS.],
  [*DFT*],
  [Отдельный Git-репозиторий],
  [Для входного RTL фиксируют ID Git-коммита или тег (аналог lockfile).
   DFT применяет свои скрипты и передаёт подготовленные файлы в BE.],
  [*BE*],
  [Вне Git],
  [Команде устно сообщают, какие файлы DFT скачать и по какому коммиту или тегу.
   Для работы используют хардмакросы с NFS и туда же выгружают результаты.],
)

== Разрозненные исходники одного проекта

*Разделять RTL, DFT и BE одного проекта на самостоятельные сущности —
принципиально неверный подход.*

RTL, ограничения SDC и проектные Tcl-скрипты для DFT, синтеза и физической
реализации — *исходный код одного проекта*. Все эти файлы входят в
#link(<rtl-to-gds>)[один маршрут RTL-to-GDS]. Сейчас RTL и DFT лежат в разных
Git-репозиториях, а проектные файлы BE — в локальных каталогах разработчиков.

DFT и BE — стадии сборки этого проекта. RTL проекта поступает на вход DFT,
а подготовленные файлы DFT — на вход BE. Это передача данных внутри маршрута,
искусственно разделённого по границам команд. Внешние зависимости проекта,
такие как Soft IP и хардмакросы, подключаются к нужным стадиям отдельно.

В проектах на C++ и других языках исходный код и проектные файлы сборки обычно
хранят в одном репозитории. В примере со #link(<cpp-build>)[сборкой C++] там
вместе лежат исходники, файлы CMake, флаги компилятора и скрипт линкера.
Проектные файлы аппаратного маршрута следует хранить вместе по тому же принципу.

#block(breakable: false)[
  == Если бы сборку C++ разделили так же

  Представим, что сборку одной программы на C++ разделили между тремя
  Git-репозиториями.

  #pipeline(
    card(
      [Репозиторий исходников],
      [Код C++ \ Описание интерфейса],
      fill: amber,
    ),
    card(
      [Репозиторий подготовки],
      [Код генератора \ Генерирует `.cpp` и `.hpp`],
      fill: rgb("#f8e9ed"),
    ),
    card(
      [Репозиторий сборки],
      [Флаги и скрипт линкера \ Компиляция и компоновка],
      fill: blue,
    ),
    card([Результат], [Исполняемый файл], fill: green),
  )
  #dependencies(
    4,
    dependency([Заголовки], [stdlib, Boost], ((0, 0pt),)),
    dependency([Бинарные библиотеки], [stdlib, Boost для компоновки], ((2, 0pt),)),
  )

  *Одна правка проекта оказывается в двух репозиториях.* Если генератор меняет
  правило именования функций, нужно одновременно обновить их вызовы в исходниках.
  В репозиторий сборки передают и исходный, и сгенерированный C++. Это похоже на
  передачу подготовленного RTL из DFT в BE внутри аппаратного маршрута.

  *Такое разделение сборки одной программы — очевидно несостоятельный подход.*
  На практике проекты на C++ так не организуют.
]

= Как помогают пакетные менеджеры

Проект объявляет прямые зависимости и допустимые версии. Пакетный менеджер,
например uv для Python, npm для JavaScript или Conan для C и C++, подбирает
совместимый набор. *Lockfile фиксирует точные версии прямых и транзитивных
зависимостей*.

Опубликованные npm-пакеты хранятся в публичном или частном реестре в виде
архивов. При установке npm распаковывает их в `node_modules` проекта.

По lockfile разработчики и CI могут автоматически получить тот же набор
пакетов для заданной конфигурации. Версии системных инструментов и библиотек
за пределами этого графа фиксируют отдельно.

#block(breakable: false)[
  #pipeline(
    card([Требования проекта], [Библиотека A версии `1.x`], fill: amber),
    card(
      [Lockfile],
      [`A 1.4.2` \ `B 2.1.0` — зависимость A],
      fill: violet,
    ),
    card(
      [Окружение разработчика и CI],
      [Автоматически устанавливаются \ `A 1.4.2` и `B 2.1.0`],
      fill: green,
    ),
  )
]

Lockfile хранят рядом с исходниками проекта. При обновлении зависимости
изменения версий видны на ревью, в том числе если обновились транзитивные пакеты.

*Для всех стадий одного проекта нужен один общий lockfile.* В текущем
маршруте RTL и DFT фиксируют входные версии раздельно. У BE lockfile
отсутствует. Какие входные файлы скачать, команда узнаёт устно.

#pagebreak()

== FuseSoC

FuseSoC собирает аппаратный дизайн из переиспользуемых блоков, которые он
называет *core*. Это может быть FIFO, контроллер или верхний уровень SoC.
Блок описывают в YAML-файле `.core` по схеме CAPI2. Его идентификатор VLNV
включает поставщика, библиотеку, имя и версию. FuseSoC ищет `.core` в
подключённых библиотеках — локальных каталогах или копиях удалённых
репозиториев. Файлы блока лежат рядом с `.core` либо загружаются в кэш
из Git или архива, указанного полем `provider`.

#grid(
  columns: (1fr, 1fr),
  column-gutter: 12pt,
  [
    ```yaml
    CAPI=2:
    name: demo:ip:counter:1.0.0
    filesets:
      rtl:
        files: [rtl/counter.sv]
        file_type: systemVerilogSource
        depend: ["^demo:ip:cells:1.2.0"]
      tb:
        files: [tb/counter_tb.sv]
        file_type: systemVerilogSource
    targets:
      default: {filesets: [rtl]}
      sim:
        filesets: [rtl, tb]
        toplevel: counter_tb
        default_tool: verilator
    ```
  ],
  [
    В `filesets` группируют RTL, тестбенчи и ограничения. `depend` задаёт
    зависимости группы от других блоков, в том числе диапазоны версий.
    `targets` выбирает группы и настройки запуска. При подключении блока
    FuseSoC использует группы его цели `default`.

    Для `sim` FuseSoC разрешает зависимости и передаёт упорядоченный список
    файлов и настройки Verilator через Edalize. Lockfile может сохранить
    выбранные VLNV. Другие цели могут запускать линт, синтез или сборку
    битстрима. Для Vivado Edalize генерирует из шаблонов Tcl-скрипты
    для создания проекта, синтеза и выпуска битстрима.
  ],
)

== Bender

Пакет Bender — исходники и `Bender.yml` в Git-репозитории или локальном каталоге.
Копии Git-зависимостей по умолчанию лежат в `.bender` в корне проекта.

#grid(
  columns: (1fr, 1fr),
  column-gutter: 12pt,
  [
    ```yaml
    package:
      name: counter
    dependencies:
      cells:
        git: https://git.example/ip/cells.git
        version: "1.2"
    sources:
      - rtl/counter.sv
      - target: simulation
        files: [tb/counter_tb.sv]
    ```
  ],
  [
    В `sources` можно задать порядок файлов, пути включения и `defines`.
    Здесь `target` добавляет тестбенч для симуляции. `Bender.lock` фиксирует
    выбранные версии и точные коммиты Git-зависимостей.

    Bender формирует список файлов и генерирует скрипты из встроенных
    или пользовательских шаблонов, например Tcl для Questa или Genus.
    Инструмент запускает сам командный маршрут.
  ],
)

== Где возникают проблемы в общем маршруте

FuseSoC и Bender готовят входные файлы для EDA-инструментов. В полном
RTL-to-GDS маршруте нужны и результаты других проектов и стадий.

*Макроблоки.* Для симуляции нужна модель макроблока, для синтеза — Liberty,
для размещения — LEF, для финальной топологии — GDS. Маршрут должен получать
нужные файлы одного выпуска и конфигурации.

*Иерархический DFT.* Верхний уровень использует подготовленный RTL дочернего
блока с тестовой логикой. Нужно выбрать DFT-результат, соответствующий версии
блока и настройкам тестирования.

*Финальный результат BE.* Главное — зафиксировать выпущенные GDS, Liberty и LEF
в версионированном пакете. Промежуточные результаты можно сохранять по мере
необходимости, чтобы продолжать сборку с готового нетлиста или размещения.
Например, scan Tcl должен соответствовать нетлисту, для которого его подготовили.

Поверх FuseSoC или Bender эти правила публикации и выбора артефактов
пришлось бы реализовать и сопровождать в командном маршруте.
