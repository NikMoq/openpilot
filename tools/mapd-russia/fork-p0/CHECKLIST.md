# Чеклист готовности P0

## Предварительные требования

```bash
cd ~/mapd  # твой форк
go version  # >= 1.25.1
capnp --version  # >= 1.0
```

---

## ☐ 1. `make capnp` — успешная регенерация схем

### Команда
```bash
cd ~/mapd
make capnp
```

### Ожидаемый результат
```
capnp compile -I ../go-capnp/std -ogo cereal/car/car.capnp
capnp compile -I ../go-capnp/std -ogo cereal/custom/custom.capnp
capnp compile -I ../go-capnp/std -ogo cereal/legacy/legacy.capnp
capnp compile -I ../go-capnp/std -ogo cereal/log/log.capnp
capnp compile -I ../go-capnp/std -ogo cereal/offline/offline.capnp
```
Без ошибок, exit code 0.

### Что проверить
```bash
grep -q "Camera" cereal/offline/offline.capnp.go && echo "OK: Camera struct exists"
grep -q "CameraTile" cereal/offline/offline.capnp.go && echo "OK: CameraTile struct exists"
grep -q "CameraInfo" cereal/custom/custom.capnp.go && echo "OK: CameraInfo struct exists"
grep -q "NextCamera" cereal/custom/custom.capnp.go && echo "OK: NextCamera field exists"
```

### Если не проходит
- Убедись что `../go-capnp` клонирован: `ls ../go-capnp/std/go.capnp`
- Проверь версию capnpc-go: `which capnpc-go`
- Переустанови: `go install capnproto.org/go/capnp/v3/capnpc-go@v3.1.0-alpha.1`

---

## ☐ 2. `go build ./...` — сборка без ошибок

### Команда
```bash
cd ~/mapd
go build ./...
```

### Ожидаемый результат
Никакого вывода (Go молча собирает при успехе). Exit code 0.

### Что проверить
```bash
echo $?  # должно быть 0
```

### Если не проходит — типичные ошибки

**Ошибка:** `undefined: offline.Camera`
- **Причина:** `offline.capnp.go` не обновлён после `make capnp`
- **Фикс:** `make capnp` ещё раз, проверить что схема записалась

**Ошибка:** `undefined: os.ReadFile`
- **Причина:** `speedcam_parser.go` использует `os.ReadFile` но `os` не импортирован
- **Фикс:** добавить `"os"` в import

**Ошибка:** `cannot use &w (type *Way) as map key`
- **Причина:** `Way` содержит slice, Go не позволяет использовать как ключ
- **Фикс:** в `way_index.go` замени `seen := make(map[*Way]bool)` на `seen := make(map[uint64]bool)` и используй хеш от координат первого нода

**Ошибка:** `m.Line undefined`
- **Причина:** импорт `math` конфликтует с `pfeifer.dev/mapd/math`
- **Фикс:** убедись что используется алиас `m "pfeifer.dev/mapd/math"`

---

## ☐ 3. Тестовый тайл Москвы генерируется

### Подготовка тестовых данных
```bash
# Скачай минимальный набор (можно вручную или через wget)
mkdir -p /tmp/mapd-test
cd /tmp/mapd-test

# Скачай Rus.radar.txt (или используй тестовый фрагмент)
curl -L "https://speedcamonline.ru/map/Rus/Rus.radar.txt" -o Rus.radar.txt

# Скачай OSM для Москвы (обрезанный, ~50MB)
# Вариант A: полная Россия (медленно)
# wget https://download.geofabrik.de/russia-latest.osm.pbf

# Вариант B: тест с фейковыми ways (быстро)
# Создадим позже в пункте 4
```

### Тестовый фрагмент Rus.radar.txt
Создай файл `/tmp/mapd-test/test_radar.txt`:
```
1|> Камеры Гибдд|1251|CityPlan01:24|65|1001
4. МЛЖ|55.7558|37.6173
17. МЛЖ|55.7580|37.6200
20. Кам наблюд|55.7600|37.6250
23. СТЦ|55.7500|37.6100
30. СТЦ|55.7520|37.6120
```

### Команда генерации
```bash
cd ~/mapd
go run . generate \
  --cameras /tmp/mapd-test/test_radar.txt \
  --osm /tmp/mapd-test/test_moscow.osm.pbf \
  --out /tmp/mapd-test/tiles/
```

> Примечание: команда `generate` может отсутствовать в CLI. Если её нет — используй тест `TestGenerateTiles` (см. ниже).

### Альтернатива: юнит-тест
Создай `maps/generate_cameras_test.go`:
```go
package maps

import (
	"testing"
	"os"
)

func TestGenerateMoscowTile(t *testing.T) {
	data, _ := os.ReadFile("/tmp/mapd-test/test_radar.txt")
	cams, err := ParseSpeedcam(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(cams) == 0 {
		t.Fatal("no cameras parsed")
	}
	t.Logf("Parsed %d cameras", len(cams))
	
	// Create fake ways for map matching (Moscow ring)
	ways := []Way{ /* ... populate with test data ... */ }
	
	tiles, err := GenerateCameraTiles(cams, ways, 1746150000)
	if err != nil {
		t.Fatal(err)
	}
	if len(tiles) == 0 {
		t.Fatal("no tiles generated")
	}
	t.Logf("Generated %d tiles", len(tiles))
}
```

### Ожидаемый результат
```
Parsed 5 cameras
Generated 1 tiles
Tile 55_37: 5 cameras
```

---

## ☐ 4. Размер тайла < 64KB

### Команда
```bash
cd ~/mapd
# После генерации тайлов
ls -la /tmp/mapd-test/tiles/

# Или проверь размер внутри Go:
go test -run TestTileSize -v ./maps
```

### Тестовая функция
```go
func TestTileSize(t *testing.T) {
	// ... generate tile ...
	for i, tile := range tiles {
		// Serialize tile to bytes
		// (exact API depends on capnp generated code)
		size := len(tileBytes)
		t.Logf("Tile %d size: %d bytes", i, size)
		if size > 64*1024 {
			t.Errorf("Tile %d exceeds 64KB: %d bytes", i, size)
		}
	}
}
```

### Ожидаемый результат
```
Tile 0 size: 2048 bytes
PASS
```

### Если > 64KB
- Уменьши `maxCameras` в `subdivideTile` (сейчас 1000, попробуй 500)
- Уменьши минимальный тайл (сейчас 0.125°, попробуй 0.0625°)
- Увеличь confidence threshold для фильтрации (сейчас 0.6)

---

## ☐ 5. Камеры в тайле имеют bearing ≠ 0

### Тестовая функция
```go
func TestCameraBearing(t *testing.T) {
	// ... generate tile ...
	for i, tile := range tiles {
		cams, _ := tile.Cameras()
		for j := 0; j < cams.Len(); j++ {
			cam := cams.At(j)
			b := cam.Bearing()
			t.Logf("Tile %d Cam %d: bearing=%.1f", i, j, b)
			if b == 0 {
				t.Errorf("Camera has zero bearing (unmatched): tile=%d cam=%d", i, j)
			}
		}
	}
}
```

### Ожидаемый результат
```
Tile 0 Cam 0: bearing=45.3
Tile 0 Cam 1: bearing=225.3  // two-way road, reversed
PASS
```

### Если bearing == 0
- Камера не приматчилась к way (map matching failed)
- Проверь что `wayIndex.FindNearestWays` возвращает результаты
- Увеличь maxDist в `GenerateCameraTiles` (сейчас 25м)
- Проверь что тестовые ways пересекаются с координатами камер

---

## ☐ 6. `FindCameraAhead` возвращает результат < 5ms

### Бенчмарк
Создай `maps/camera_runtime_test.go`:
```go
package maps

import (
	"testing"
	"time"
)

func BenchmarkFindCameraAhead(b *testing.B) {
	// Setup: load Moscow tile
	idx := buildTestIndex() // helper that loads real or synthetic tile
	
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		// Simulate driving on MKAD
		lat := 55.7558 + float64(i%100)*0.0001
		lon := 37.6173 + float64(i%100)*0.0001
		heading := 45.0
		speed := 20.0 // m/s ≈ 72 km/h
		
		start := time.Now()
		_ = idx.FindCameraAhead(lat, lon, heading, speed)
		elapsed := time.Since(start)
		
		if elapsed > 5*time.Millisecond {
			b.Fatalf("Search took %v, expected < 5ms", elapsed)
		}
	}
}
```

### Команда
```bash
cd ~/mapd
go test -bench=BenchmarkFindCameraAhead -benchtime=1s ./maps
```

### Ожидаемый результат
```
BenchmarkFindCameraAhead-8    1000000    1200 ns/op
PASS
```

### Если > 5ms
- Проверь что `tileMap` (hash→index) используется, а не линейный поиск
- Убедись что `collectCandidates` загружает только 9 тайлов, а не все
- Проверь что `CameraTile` не содержит >1000 камер (adaptive split)
- Включи CPU profiler: `go test -bench=. -cpuprofile=cpu.prof ./maps`

---

## Автоматический скрипт проверки

Создай файл `check-p0.sh` в корне форка:

```bash
#!/bin/bash
set -e

echo "=== P0 Readiness Check ==="

echo "[1/6] make capnp..."
make capnp > /dev/null 2>&1
echo "✓ capnp OK"

echo "[2/6] go build..."
go build ./...
echo "✓ build OK"

echo "[3/6] Parse test data..."
go test -run TestParseSpeedcam -v ./maps
echo "✓ parser OK"

echo "[4/6] Generate tile..."
go test -run TestGenerateMoscowTile -v ./maps
echo "✓ generator OK"

echo "[5/6] Check tile size..."
go test -run TestTileSize -v ./maps
echo "✓ size OK"

echo "[6/6] Check bearing..."
go test -run TestCameraBearing -v ./maps
echo "✓ bearing OK"

echo "[7/6] Benchmark..."
go test -bench=BenchmarkFindCameraAhead -benchtime=1s ./maps
echo "✓ performance OK"

echo ""
echo "=== ALL CHECKS PASSED ==="
```

Запуск:
```bash
chmod +x check-p0.sh
./check-p0.sh
```

---

## Критерий готовности

Все 6 пунктов должны быть отмечены (✓). Только после этого переходить к P1.

| Пункт | Критерий |
|-------|----------|
| capnp | 5 файлов `.capnp.go` без ошибок, `Camera`/`CameraTile`/`CameraInfo` присутствуют |
| build | `go build ./...` exit code 0 |
| генерация | ≥1 тайл создан, камеры внутри >0 |
| размер | сериализованный тайл ≤ 65536 байт |
| bearing | 100% камер имеют bearing > 0 |
| performance | `FindCameraAhead` ≤ 5ms в 95% случаев |
