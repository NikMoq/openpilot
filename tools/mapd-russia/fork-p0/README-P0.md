# Этап P0 — Capnp схема + генератор + runtime поиск

## Что входит

1. **Cap'n Proto схемы** — `Camera`, `CameraTile`, `CameraInfo` в `MapdOut`
2. **Парсер** `Rus.radar.txt` — cp1251→UTF-8, нормализация типов, дедупликация
3. **Spatial index ways** — grid-based для map matching (≤25м)
4. **Генератор тайлов** — adaptive splitting, bearing assignment, avtodoria grouping
5. **Runtime поиск** — 9-grid tile lookup, bearing filter, mobile TTL
6. **Патч state.go** — проброс `CameraInfo` в `MapdOut`

## Файлы для коммита в форк

```
cereal/offline/offline.capnp      → заменить
cereal/custom/custom.capnp        → заменить
maps/speedcam_parser.go           → новый
maps/way_index.go                 → новый
maps/morton.go                    → новый
maps/camera_runtime.go            → новый
maps/generate_cameras.go          → новый
state.go                          → применить patch
```

## Пошаговая инструкция

### 1. Подготовка зависимостей

В форке `NikMoq/mapd`:
```bash
go get golang.org/x/text  # убедиться что encoding/charmap доступен
```

### 2. Замена схем

```bash
cp tools/mapd-russia/fork-p0/cereal/offline/offline.capnp cereal/offline/
cp tools/mapd-russia/fork-p0/cereal/custom/custom.capnp cereal/custom/
```

### 3. Регенерация Go-кода из схем

```bash
make capnp
```

Это создаст:
- `cereal/offline/offline.capnp.go` — с `Camera`, `CameraTile`
- `cereal/custom/custom.capnp.go` — с `CameraInfo`, `nextCamera` в `MapdOut`

### 4. Копирование Go-файлов

```bash
cp tools/mapd-russia/fork-p0/maps/*.go maps/
```

### 5. Адаптация `generate_cameras.go`

Открой `maps/generate_cameras.go`, найди `createCameraTile` и замени `panic(...)` на реальные вызовы generated API.

После `make capnp` проверь какие методы доступны у `offline.CameraTile` и `offline.Camera`.
Обычно это:
```go
tile, _ := offline.NewCameraTile(seg)
tile.SetMinLat(minLat)
// ...
tile.SetHash(maps.MortonHash(minLat, minLon, tileSize))
capnpCams, _ := tile.NewCameras(int32(len(cams)))
for i, c := range cams {
    cam := capnpCams.At(i)
    cam.SetLatitude(c.Lat)
    cam.SetLongitude(c.Lon)
    cam.SetType(c.Type)
    cam.SetSpeedLimit(c.SpeedLimit)
    cam.SetBearing(float32(c.Bearing))
    cam.SetConfidence(c.Confidence)
    cam.SetGroupId(c.GroupID)
    cam.SetTimestamp(generation)
}
```

### 6. Применение патча `state.go`

```bash
patch -p1 < tools/mapd-russia/fork-p0/state_camera.patch
```

Или вручную добавь:
- Поле `CameraIdx *maps.CameraIndex` в `struct State`
- Метод `InitCameraIndex()`
- Заполнение `output.SetNextCamera(...)` в `Send()`

### 7. Интеграция загрузки CameraIndex

В `main.go` после загрузки нового тайла добавь вызов:
```go
state.Data, err = maps.FindWaysAroundPosition(pos)
if err == nil {
    state.InitCameraIndex() // <-- добавить это
}
```

### 8. Сборка

```bash
go build -o build/mapd ./...
```

### 9. Тестирование

```bash
# Парсер
 go test -run TestParseSpeedcam ./maps

# Генерация тайлов (требует OSM PBF + Rus.radar.txt)
 go run ./... generate --cameras Rus.radar.txt --osm russia-latest.osm.pbf --out ./test-tiles

# Runtime (требует загруженного тайла)
# Запустить mapd, проверить mapdOut.nextCamera через cereal
```

## Что проверить после сборки

| Проверка | Ожидаемый результат |
|----------|---------------------|
| `make capnp` проходит без ошибок | `.capnp.go` файлы обновлены |
| `go build` проходит | бинарник собирается |
| Парсер `Rus.radar.txt` | ~100k камер, 0 дубликатов в радиусе 5м |
| Генератор тайлов | ≤1000 камер на тайл, ≤64KB сериализованный |
| Runtime поиск | <5ms на ARM64 для тайла Москвы |
| Bearing filter | встречные камеры (delta>45°) не показываются |
| Mobile TTL | через 3ч confidence падает, через 24ч скрывается |

## Следующий шаг (P1)

После успешного P0:
1. Поднять сервер для хостинга `.tar.gz` тайлов
2. Сгенерировать манифест (`manifest.json`)
3. Настроить автообновление через UI sunnypilot
