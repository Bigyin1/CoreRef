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

= Текущий маршрут

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

Опубликованные npm-пакеты хранятся в реестре в виде архивов. По умолчанию
npm обращается к `registry.npmjs.org`. Команда может настроить частный реестр.

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

= FuseSoC

Единица переиспользования в FuseSoC называется *core*. Это может быть
RTL-библиотека, макроблок, генератор IP или целый SoC. Файл `.core` описывает
компонент, а его имя в формате VLNV задаёт поставщика, библиотеку, имя и версию.
Например, `demo:ip:counter:1.0.0` обозначает версию 1.0.0 счётчика из библиотеки
`ip` поставщика `demo`.

== Что находится в файле `.core`

#grid(
  columns: (1fr, 1fr),
  column-gutter: 12pt,
  [
    #set text(size: 9pt)
    ```yaml
    CAPI=2:
    name: demo:ip:counter:1.0.0
    filesets:
      rtl:
        files: [rtl/counter.sv]
        file_type: systemVerilogSource
        depend: ["^demo:ip:increment:1.0.0"]
      tb:
        files: [tb/counter_tb.sv]
        file_type: systemVerilogSource
    targets:
      default: {filesets: [rtl]}
      sim:
        filesets: [rtl, tb]
        toplevel: counter_tb
        flow: sim
        flow_options: {tool: verilator}
    ```
  ],
  [
    *`filesets`* группирует исходники, тестбенчи, ограничения и другие файлы.
    У файлов можно указывать тип и пути включения. Зависимости `depend`
    относятся к конкретной группе.

    *`targets`* выбирает группы и параметры для конкретного запуска.
    `default` объявляет то, что получит потребитель счётчика. `sim` добавляет
    тестбенч и выбирает Verilator.

    Core также может зарегистрировать генератор. Например, SoC может
    дважды вызвать генератор SRAM с разными глубиной и шириной,
    чтобы получить две модели памяти.
  ],
)

== Где хранятся описание и файлы

В Git-репозитории исходники могут лежать рядом с `.core`. Для отдельно
распространяемого компонента в описание добавляют `provider`, например
адрес Git-репозитория с тегом или коммитом либо URL архива. Так релизный `counter_macro.core`
может перечислять интерфейс, Liberty, LEF и GDS, а сами файлы лежат
в архиве на сервере артефактов.

#table(
  columns: (1fr, 2.45fr),
  inset: 7pt,
  stroke: 0.6pt + border,
  fill: (_, row) => if row == 0 { blue } else { white },
  table.header([*Источник описаний*], [*Как FuseSoC его использует*]),
  [Библиотека компонентов],
  [Директория с `.core`, в том числе локальная копия Git-репозитория.
  Её подключают в `fusesoc.conf`. Команда `library update` синхронизирует
  удалённую библиотеку.],
  [FuseSoC Package Directory],
  [Веб-каталог публикуемых `.core`. Из него можно получить одно описание
  или ZIP с набором описаний и подключить его как библиотеку.],
)

Для выпуска макроблока CI загружает архив с артефактами и публикует
`.core` с его адресом. Такой блок `provider` можно добавить в описание
макроблока в Git-библиотеке или опубликовать вместе с описанием в каталоге.

```yaml
provider:
  name: url
  url: https://ip.example.com/counter-1.0.0.tar.gz
  filetype: tar
```

FuseSoC загружает файлы через `provider` при первом обращении к компоненту
и переиспользует локальный кэш. Обновление готового макроблока выпускают
под новой версией с отдельным архивом.

#pagebreak()

== Как разрешаются зависимости

#pipeline(
  card([SoC], [Цель `sim` выбирает \ группы файлов], fill: amber),
  card([Счётчик], [Зависимость SoC \ `counter 1.0.0`], fill: blue),
  card([Инкремент], [Зависимость счётчика \ например, `increment 1.2.0`], fill: green),
)

FuseSoC читает `.core` из локальных копий подключённых библиотек и
подбирает совместимые версии для прямых и транзитивных зависимостей.
Новые описания становятся доступны после синхронизации библиотеки.
В примере `^1.0.0` допускает версию 1.0.0 и более новые версии младше 2.0.0.
У зависимых компонентов используются группы из цели `default`, поэтому
в симуляцию SoC попадёт RTL счётчика, а его собственный тестбенч останется
в цели `sim` счётчика. Файлы зависимости идут перед файлами группы,
которая её объявила.

Lockfile FuseSoC фиксирует выбранные идентификаторы VLNV.

== EDAM и Edalize

*EDAM (EDA Metadata)* — структурированное описание входов конкретного запуска.
В `.core` проект объявляет группы файлов, зависимости и цели. FuseSoC
выбирает цель, разрешает зависимости и формирует EDAM с фактическими путями
и настройками. Это описание он сохраняет в `.eda.yml`.

Из этих данных бэкенд Edalize формирует настройки инструмента.
Из одного формата можно получить список файлов для Verilator
или проект Vivado.

#grid(
  columns: (1fr, 1fr),
  column-gutter: 12pt,
  [
    #set text(size: 9pt)
    ```yaml
    name: demo_soc
    toplevel: soc_tb
    files:
      # Остальные исходники опущены
      - name: src/include/config.svh
        file_type: systemVerilogSource
        is_include_file: true
      - name: src/soc_tb.sv
        file_type: systemVerilogSource
    parameters:
      TRACE:
        datatype: bool
        paramtype: vlogdefine
        default: true
    flow_options: {tool: verilator, mode: binary}
    ```
  ],
  [
    *Файлы.* `files` перечисляет RTL, заголовки, ограничения и данные
    инициализации памяти. `is_include_file` добавляет каталог
    заголовка в пути поиска, а `soc_tb.sv` идёт на компиляцию.

    *Параметры.* `vlogdefine` задаёт макрос `TRACE` при компиляции.
    EDAM также поддерживает параметры Verilog, VHDL generics и plusargs.

    *Запуск.* `flow_options` задаёт настройки маршрута и инструмента.
    EDAM также может хранить связи выбранных компонентов и команды,
    которые нужно выполнить до или после сборки и запуска.
  ],
)

#pipeline(
  card([FuseSoC], [Разрешает зависимости \ и собирает файлы], fill: amber),
  card([EDAM], [Описывает \ входы запуска], fill: violet),
  card([Edalize], [Готовит конфигурацию \ и команды], fill: blue),
  card([EDA-инструмент], [Симуляция, синтез \ или другой запуск], fill: green),
)

=== Маршруты в Edalize

*Flow API* связывает инструменты в граф. При подготовке каждый адаптер
читает входной EDAM, описывает свои команды и добавляет в выходной EDAM
сведения о файлах, которые эти команды создадут. Следующий адаптер может
сослаться на будущий файл ещё до запуска сборки.

Например, в маршруте IceStorm Yosys выдаёт JSON-нетлист, nextpnr использует
его для размещения и трассировки, а icepack собирает битстрим. Edalize
генерирует правила с зависимостями между файлами. В этом маршруте их
исполняет Make.

В `.core` проект выбирает готовый маршрут через `flow` и настраивает его
через `flow_options`. Связи между инструментами задаёт Python-класс маршрута.

В CoreRef flow может сформировать EDAM из входов выбранной стадии
и передать его в Edalize напрямую как словарь Python.
