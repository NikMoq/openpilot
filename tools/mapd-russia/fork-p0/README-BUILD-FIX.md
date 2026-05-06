# Исправление ошибок сборки P0

## Проблемы и решения

### 1. `undefined: offline.Camera`, `offline.CameraTile`

**Причина:** Cap'n Proto схемы изменены, но Go-код не перегенерирован.

**Решение:**
```bash
cd ~/mapd
make capnp
```

### 2. `cannot use &w (type *Way) as map key`

**Причина:** `Way` struct содержит slice (`nodes`), Go не позволяет использовать такие типы как ключи map.

**Решение:** В `way_index.go` заменено на `map[uint64]bool` с хешем от координат первого нода.

### 3. `m.Line undefined` / конфликт имён `math`

**Причина:** импорт `"math"` конфликтует с `"pfeifer.dev/mapd/math"`.

**Решение:** Использованы алиасы:
```go
import (
    stdmath "math"
    m "pfeifer.dev/mapd/math"
)
```

### 4. `os.Dir undefined`

**Причина:** в Go нет функции `os.Dir()`, есть `filepath.Dir()`.

**Решение:** В `generate_cameras.go` заменено на `filepath.Dir(path)`.

### 5. Неправильное создание `capnp.Message` в `camera_runtime.go`

**Причина:** `capnp.NewSingleSegmentArena(nil)` возвращает `Arena`, а не `*Segment`.

**Решение:** Использован правильный API:
```go
arena := capnp.MultiSegment([][]byte{})
msg, seg, err := capnp.NewMessage(arena)
info, err := custom.NewRootCameraInfo(seg)
```

### 6. `CameraInfo` lifetime / указатели на capnp структуры

**Причина:** capnp структуры привязаны к сообщению; указатели могут стать невалидными.

**Решение:** `FindCameraAhead` возвращает `(custom.CameraInfo, bool)` по значению. Временное сообщение удерживается alive через внутренние ссылки.

---

## Файлы для коммита (обновлённые)

```
cereal/offline/offline.capnp      → заменить (Camera, CameraTile)
cereal/custom/custom.capnp        → заменить (CameraInfo, nextCamera)
maps/speedcam_parser.go           → новый
maps/way_index.go                 → новый
maps/morton.go                    → новый
maps/camera_runtime.go            → новый
maps/generate_cameras.go          → новый
maps/offline_camera.go            → новый
state_camera.patch                → обновлён
```

---

## Пошаговая сборка

```bash
cd ~/mapd

# 1. Заменить схемы
cp tools/mapd-russia/fork-p0/cereal/offline/offline.capnp cereal/offline/
cp tools/mapd-russia/fork-p0/cereal/custom/custom.capnp cereal/custom/

# 2. Перегенерировать Go-код
make capnp

# 3. Проверить что Camera/CameraTile/CameraInfo появились
grep -q "type Camera capnp.Struct" cereal/offline/offline.capnp.go && echo "OK"
grep -q "type CameraTile capnp.Struct" cereal/offline/offline.capnp.go && echo "OK"
grep -q "type CameraInfo capnp.Struct" cereal/custom/custom.capnp.go && echo "OK"

# 4. Скопировать Go-файлы
cp tools/mapd-russia/fork-p0/maps/*.go maps/

# 5. Применить патч state.go
patch -p1 < tools/mapd-russia/fork-p0/state_camera.patch

# 6. Добавить InitCameraIndex в main.go
# После: state.Data, err = maps.FindWaysAroundPosition(pos)
# Добавить: state.InitCameraIndex()

# 7. Собрать
go build ./...

# 8. Если ошибки — смотреть раздел ниже
```

---

## Типичные ошибки компиляции и фиксы

### `undefined: offline.Camera_List`
После `make capnp` должен сгенерироваться `Camera_List`. Если нет — проверь что `Camera` объявлен до `CameraTile` в схеме.

### `camera.Type undefined (type offline.Camera has no field or method Type)`
Метод может называться `Type_()` из-за конфликта с ключевым словом `type` в Go. Проверь сгенерированный код:
```bash
grep "func (s Camera) Type" cereal/offline/offline.capnp.go
```
Если `Type_()` — замени в `generate_cameras.go` и `camera_runtime.go`.

### `tile.Cameras undefined`
Проверь сгенерированный код:
```bash
grep "func (s CameraTile) Cameras" cereal/offline/offline.capnp.go
```

### `custom.NewRootCameraInfo undefined`
Проверь что `CameraInfo` объявлен как root в custom.capnp (не требуется, но проверь сгенерированный код).
Если `NewRootCameraInfo` отсутствует, используй:
```go
info, err := custom.NewCameraInfo(seg)
```
вместо `NewRootCameraInfo`.

---

## Проверка после сборки

```bash
# Парсер
go test -run TestParseSpeedcam -v ./maps

# Генерация (требует тестовых данных)
go test -run TestGenerateMoscowTile -v ./maps

# Бенчмарк поиска
go test -bench=BenchmarkFindCameraAhead -benchtime=1s ./maps
```
