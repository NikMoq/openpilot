# P0 Commit-Ready Package

Всё необходимое для коммита в форк `NikMoq/mapd`.

## Структура

```
commit-ready/
├── cereal/
│   ├── offline/offline.capnp    # +Camera, +CameraTile, +generation/version
│   └── custom/custom.capnp      # +CameraInfo, +nextCamera@24 in MapdOut
├── maps/
│   ├── speedcam_parser.go       # Парсер Rus.radar.txt (cp1251→UTF-8)
│   ├── way_index.go             # Grid spatial index для map matching
│   ├── morton.go                # Morton hash для тайлов
│   ├── camera_runtime.go        # FindCameraAhead с bearing-filter
│   ├── generate_cameras.go      # Генерация тайлов + adaptive split
│   └── offline_camera.go        # Расширение Offline для camera tiles
├── install-p0.sh                # Автоматический скрипт установки
└── README.md                    # Этот файл
```

## Быстрая установка

```bash
cd ~/mapd  # твой форк
bash tools/mapd-russia/fork-p0/commit-ready/install-p0.sh
```

## Ручная установка

```bash
cd ~/mapd

# 1. Схемы
cp tools/mapd-russia/fork-p0/commit-ready/cereal/*/*.capnp cereal/

# 2. Перегенерация
make capnp

# 3. Go-файлы
cp tools/mapd-russia/fork-p0/commit-ready/maps/*.go maps/

# 4. Зависимость
go get golang.org/x/text/encoding/charmap

# 5. Сборка
go build ./...
```

## После сборки

```bash
# Применить патч state.go
patch -p1 < tools/mapd-russia/fork-p0/state_camera.patch

# В main.go добавить после загрузки тайла:
# state.InitCameraIndex()

# Создать тег
git add -A
git commit -m "P0: Russian speed camera support"
git tag v1.12.1-rus.1
git push origin v1.12.1-rus.1
```
