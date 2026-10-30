# Camera Speed Control — проверка кандидата cam

Статус: OFFLINE_CANDIDATE_VALIDATED, SIMULATED_ACCEPTANCE_AUTHORIZED.
Аппаратная установка и дорожная готовность не заявляются.
База: `5e049f491ac36a9e6efb9ee31e4871b0f1408a32` (ICBM).

## Источники и границы

Управление разрабатывается заново на исходной ICBM. После отдельного указания
пользователя отсутствующие C++/Qt исходники и необходимые сборочные зависимости
native UI получены только из `dev` коммита
`aabf70ac1f211c343cca0181f77777dd12c1ff6e` того же репозитория.
Поимённый список исходных Git blobs и SHA-256 —
`CAMERA_NATIVE_UI_SOURCE_IMPORT.json`. Это не перенос контроллеров dev и не
использование stash или объектов/приложений прежних сборок.

Компилятор, системные заголовки и Qt SDK являются сборочными зависимостями;
исходники приложения и генерируемые схемы берутся из текущего cam.
Готовые приложения/объекты из SDK-окружения не используются.

## Функциональная цепочка

Локальная база → независимая проверка топологии/направления и качества GPS →
камерное событие со стабильной идентичностью → Resolver → штатная прибавка →
камерное автопринятие SLA → прежний минимум четырёх целей → прежний ICBM/PQ.
Отдельный камерный потолок после минимума удалён из исполняемого пути.
OSM используется для топологии, а не для числового лимита в camera mode.

Собственный переключатель камерного управления не меняет обычный режим SLA
или предпочтение предупреждений. Отключение предупреждений не отключает
управление; camera OFF возвращает прежние обычные настройки.

| Критерий | Проверка / состояние |
| --- | --- |
| ACC100, камера60, штатная+15 →75 | Actual Resolver/SLA/planner + real Cap'n Proto wire |
| Проезд →100, без кэша камерного лимита | Producer с реальной синтетической OSM-топологией, два разных GPS fix, wire → controls |
| SCC-V70 / SCC-M65 сохраняют меньшую цель | Actual four-source arbitration; ICBM продолжает соответствующие команды |
| Две камеры с одинаковым лимитом | Разные ID, независимые события и подтверждение проезда |
| Встречная/параллельная/двухуровневая дорога, развилка | Отдельные тесты RoadGraph/CameraSpeedSource, неоднозначность не авторизует камеру |
| Недостоверный GPS/потеря данных | Нет новой камеры; действующее ограничение удерживается; offset и знак соответствуют применённой цели |
| Ручная коррекция и поздняя обратная связь | Нет произвольного таймаута; изменённая native-цель в направлении физического действия |
| Активные RES/SET | Fine +1/−1 обновляют водительскую базу; RES после торможения сохраняет базу сессии |
| Ручная цель вне virtual range / неизвестная native-база | Исходный максимум 145 сохранён; ручные 150 не вызывают погоню к старым 100, новая атрибутированная коррекция 140 восстанавливает поддерживаемую базу; baseline0 не превращает последующие75 в действие водителя |
| Собственная отправка не становится buttonEvent | Original CANParser/CANPacker/CarState: src2/130/194 игнорируются PT0; src0 создаёт событие |
| Camera ON при обычном SLA OFF и warnings OFF | Камерная цель работает отдельно; отключение возвращает обычный OFF |
| Штатный знак/прибавка/отсчёт | Полные host/ARM UI собраны; реальный Qt renderer/widget и визуальная QA пройдены |
| Сохранение порога после перезапуска | Настоящий новый Params binding: типы/default/backup и сохранение через четыре cleanup-перехода и reopen проверены |

## Воспроизведение Python-проверок

Из корня cam в PowerShell:

```powershell
./tools/tests/run_camera_checks.ps1 `
  -DockerConfig C:/Users/Public/pq35-docker-client `
  -DockerHost npipe:////./pipe/docker_engine
```

Скрипт создаёт образ только с тестовыми зависимостями, монтирует текущие
исходники только для чтения и корректно подставляет оригинальную цель Git
symlink `cereal/car.capnp` в Windows checkout. Генерируемые файлы, provider DB
и пользовательские данные не копируются из других веток или сборок.

Часть тестов явно подменяет границы Params storage, messaging и модели/SCC
для выполнения настоящего управляющего кода без native транспорта. Отдельные
проверки используют настоящие Cap'n Proto схемы, parser/packer и ICBM.
Не следует выдавать эти подмены за испытание автомобиля.

Финальный прогон скрипта: **897 passed, 8 skipped**, 95.34 секунды.
До и после него совпал SHA-256 снимка 2559 исходников с расширениями
`.py/.pyx/.cc/.cpp/.h/.capnp` (Git tracked + untracked, без ignored output):
`9f5636869ae8c2d82ea1e838a5c473260016897ffb979d089d9df4d077c145d2`.
То есть исходники не менялись во время финальной регрессии.
Сборочные helpers также прошли CLI-help и Ruff-проверки. Native compile inputs
дополнительно проверяются по build manifests перед упаковкой.

Пропуски относятся к недоступным внешним BKM/iGo фикстурам и двум opt-in native
runtime модулям. Последние отдельно реально выполнены в безопасном disposable
Linux: **91 passed** = 44 исходных Resolver/SLA + 19 runtime + 28 override.
Params, msgq IPC, transformations собраны заново из текущих исходников; там
нет подмен модулей, сокетов или Params. Тестовый compatibility plugin исправляет
только эпоху старой Unix-фикстуры и представление Git symlink в Windows.
Команды: `CAMERA_NATIVE_RUNTIME_QA.md`. Пересекающиеся проверки не суммируются.

Ruff F-проверки изменённого production Python и `git diff --check` пройдены.

## Native UI и runtime artifacts

Доказательства: `CAMERA_NATIVE_UI_BUILD_RESULTS.json`,
`CAMERA_NATIVE_UI_INTEGRATION.md`, `CAMERA_PARAMS_BINDING.md`.

- ARM UI: `.native-build/camera-native-arm/ui`, SHA-256
  `76223afc43f6895bae02bfc1ac13aac02fbc003619d7d129a786938781371edc`.
- ARM CPython 3.12 Params binding:
  `.native-build/camera-params-binding/arm64/candidate/common/params_pyx.so`, SHA-256
  `07bb1204a587a9c4be8f8715045408065b01a5f0fb10a3b6b595e8f4cab66464`.
- Host UI и actual native Qt widget fixture собраны; проверки и PNG в
  `.native-build/camera-native-qa/`. Это настоящая отрисовка штатного компонента
  на синтетическом фоне, а не фотография установленного кандидата.
- ARM SDK closure: 65 AArch64 ELF, отсутствующих зависимостей нет; используются
  нужные Adreno SONAME, а не desktop Mesa. Это проверка SDK ABI, не устройства.
- Исходные отслеживаемые `selfdrive/ui/ui` и `common/params_pyx.so` сохранены.
  Новые два runtime artifact нужны вместе: старый Params registry удалял бы
  неизвестные новые настройки при cleanup.

Готовый overlay собирается только из согласованных native manifests и текущих
исходников; сборщик отвергает изменённые после компиляции файлы, неправильную
архитектуру и неполный SDK dependency report:

```powershell
python tools/prepare_camera_candidate.py `
  --ui .native-build/camera-native-arm/ui `
  --ui-manifest .native-build/camera-native-arm/manifest.json `
  --ui-abi .native-build/camera-native-arm/abi.json `
  --params .native-build/camera-params-binding/arm64/candidate/common/params_pyx.so `
  --params-manifest .native-build/camera-params-binding/arm64/candidate.json `
  --output .native-build/camera-release-v1
```

Это локальная подготовка overlay и manifest, не installer. Не включаются Panda,
provider DB, SDK-библиотеки, GPS-записи или приватные материалы. Стабильная ветка
и устройство этим сборщиком не меняются.

## Данные для эксплуатации и ограничения доказательств

Инструкции подготовки локальной базы и регионального OSM XML с provenance
sidecar находятся в `CAMERA_OFFLINE_DATA_CONTRACT.md`. В репозитории нет
скачанной provider DB или регионального графа. Без подходящего локального
графа новая камера не авторизуется. Offline-срок годности данных проверяется.

Generic native feedback не является application ACK радара. Изменение
штатной уставки того же направления, совпавшее с физическим действием и
остаточным предыдущим запросом, требует проверки на записанных runtime
транзакциях. При неизменившейся обратной связи ручной pending не снимается
по выдуманному таймауту; водитель может явно отменить/отключить управление.

Реальные входные записи также проверены: три сырых rlog-сегмента, каждый с
порогами 300 и 1000 м, текущая база и настоящий полный candidate-inclusive OSM
export. Неполное покрытие сначала устранено; после этого matcher всё равно
отклонял кандидатов из-за неоднозначной привязки/неединственного коридора.
Активной камеры или достоверного проезда в этих доступных данных нет.
Это отрицательный replay, а не положительная приёмка камерного снижения и
восстановления. Полные агрегаты, хеши и оговорка о rebased replay-clock:
`CAMERA_RECORDED_ROUTE_QA.md`, `CAMERA_RECORDED_ROUTE_EVIDENCE.json`.
Пользователь после этого прямо указал, что положительного лога нет и для
приёмки следует использовать смоделированные тесты. Положительные проверки
через настоящий IPC/runtime и Qt выполнены именно на таких входах; отсутствие
recorded-проезда больше не блокирует принятую пользователем офлайн-приёмку.
Аппаратная проверка остаётся отдельным этапом; дорожная готовность не заявляется.

При рассчитанной цели ниже штатного минимума ACC существующие ограничения
сохраняются. Пример 20 + 15 → 35 проверен; при 20 + 5 → 25 ACC не опускается
ниже своего минимума, а native UI показывает ограничение возможности ACC.

Новые исходники UI, успешная сборка и simulation не доказывают установку
кандидата, аппаратное принятие команд, стендовую или дорожную готовность.
Panda/Volkswagen/CAN/safety не изменяются этой работой.


## Published automatic graph delivery correction

The earlier offline/manual graph prerequisite is now handled automatically by
`RoadGraphUpdater` in the `OsmMapData` path. The first load of a new area requires
network and a fresh accurate GPS position; cached verified regions work offline
and after restart. See `CAMERA_OFFLINE_DATA_CONTRACT.md` for exact limits and
`CAMERA_GRAPH_DELIVERY_QA.json` for new integration/native/performance evidence.

Only the background graph worker is demoted from inherited realtime scheduling
to SCHED_OTHER and nice10 before any acquisition or parsing. The mapd/control
thread scheduling is not changed. Refusal to demote a still-realtime worker
prevents heavy work and schedules a later retry. Downloads and cache are bounded;
XML is not parsed in the regular source tick. These architectural limits and
host timings are not measurements of actual comma load.

HUD empty labels are now only «Камер нет» and «Слабый GPS». Accurate stationary
GPS can prepare geometry without being mislabeled as weak solely for speed0.
A selected camera still supplies its raw limit at the center, the applied native
offset above-right, and the reaction-distance countdown above-left. This update
does not alter Resolver/SLA/ICBM or driver-speed retention and passage restoration.


## Current: installed offline OSM maps, no runtime map downloads

The preceding automatic XML-region implementation is superseded and its runtime
modules are removed. The camera producer now reads the existing packed mapd
files through `camera_osm_bridge.py` and a locally declared wire schema. Its
format was verified against getters in the shipped ARM binary and a real public
stock tile. No new regional graph, map API call or mandatory runtime network is
used. Camera limits still come only from the local camera database; OSM supplies
road association and path distance, never a posted camera target.

See `CAMERA_OFFLINE_DATA_CONTRACT.md` and
`CAMERA_OFFLINE_OSM_BRIDGE_QA.json` for current requirements, provenance and QA.
Pure metadata tests do not substitute for the production installed-map path.
The full IPC runtime rebuilt actual Params/msgq/transformations and passed91
checks including raw20+15, driver priority, passage restore and next-camera cycle.
The legacy graph keys remain registered for binary compatibility but no longer
select an external XML input. No Panda/pandad/control policy binary was changed.


## Current camera lifecycle and frozen pre-camera target

A reproducible source defect could retain a camera forever if GPS/map uncertainty
hid its zero crossing: all recovered fixes were already behind, so the required
positive-to-negative transition could never happen. With installed-road evidence,
retained confirmed approach plus two fresh moving behind-camera fixes can now
prove passage. No missing-data timeout releases the speed ceiling.

The user explicitly changed the earlier manual-button contract. In-zone ACC
buttons no longer create persistent manual authority or overwrite the saved
pre-camera driver target. Proven passage completes the camera immediately,
including with a held button. Physical pedals/buttons and disengaged ACC still
block actual ICBM commands. A brake never triggers automatic ACC engagement.

ICBM additionally validates finite positive stock set-speed feedback before
rounding or transmitting: zero/NaN/Inf are not acceleration requests. This is
input validation, not a change of the original virtual maximum145 or stock
minimum30km/h. Native150 feedback can still be reduced to a valid target85.
Panda firmware and CAN encoding are unchanged. Current evidence:
CAMERA_EVENT_LIFECYCLE_QA.json.
