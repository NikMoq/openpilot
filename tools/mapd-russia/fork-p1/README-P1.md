# P1: Server Pipeline + Fork Integration

## Цель
Серверная генерация тайлов с камерами + интеграция в форк mapd. Sunnypilot НЕ меняется.

## Архитектура

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ speedcamonline  │     │   OSM Russia    │     │   mapd binary   │
│  Rus.radar.txt  │────→│  russia-latest  │────→│  (NikMoq fork)  │
│   (cp1251)      │     │   .osm.pbf      │     │                 │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                              ┌──────────────────────────┘
                              ▼
                    ┌─────────────────┐
                    │  tiles/offline/ │
                    │  manifest.json  │
                    │  version.txt    │
                    └────────┬────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ┌──────────────┐    ┌──────────────┐
            │  CDN/Server  │    │  Local test  │
            │  (production)│    │  serve.py    │
            └──────────────┘    └──────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   sunnypilot    │
                    │  (same as now)  │
                    └─────────────────┘
```

## Что входит в P1

### 1. Сервер (`tools/mapd-russia/server/`)

| Файл | Назначение |
|------|-----------|
| `generate_tiles.py` | Скачивает OSM + Rus.radar.txt, запускает `mapd generate`, создаёт manifest |
| `serve.py` | Локальный HTTP сервер для тестирования (Range requests) |

### 2. Форк mapd (добавить в `NikMoq/mapd`)

В `main.go` или `cmd/generate.go` добавить CLI команду:
```go
// mapd generate --osm russia.osm.pbf --cameras Rus.radar.txt --out ./tiles
```

Эта команда должна:
1. Парсить OSM PBF → `[]Way`
2. Парсить Rus.radar.txt → `[]RawCamera`
3. Вызывать `maps.GenerateCameraTiles(rawCams, ways, generation)`
4. Сохранять тайлы в `offline/{lat}/{lon}.tar.gz`

### 3. Интеграция в существующий pipeline

Если в форке уже есть `generate` команда — добавить туда `--cameras` флаг.
Если нет — создать новую команду.

## Использование

### Генерация тайлов

```bash
# Автоматическая загрузка источников
python tools/mapd-russia/server/generate_tiles.py --out ./tiles --auto-download --mapd-bin ./mapd

# С локальными файлами
python tools/mapd-russia/server/generate_tiles.py \
    --out ./tiles \
    --osm ~/russia-latest.osm.pbf \
    --cameras ~/Rus.radar.txt \
    --mapd-bin ~/mapd/mapd
```

### Локальный тестовый сервер

```bash
python tools/mapd-russia/server/serve.py --root ./tiles --port 8080

# Проверка
curl http://localhost:8080/manifest.json
curl http://localhost:8080/offline/55/37.tar.gz -o test.tar.gz
```

### Production деплой

```bash
# Загрузить на CDN / S3 / etc
rsync -avz --progress ./tiles/ user@server:/var/www/mapd-russia/

# Или через rclone
rclone sync ./tiles remote:mapd-russia-bucket
```

## Sunnypilot

Ничего не меняется! URL уже обновлён в `mapd_installer.py` (P0).
Если используешь локальный сервер для теста — временно поменяй URL в `settings/download.go` форка mapd.

## Чеклист P1

- [ ] `mapd generate` работает в форке
- [ ] Тайлы создаются в `offline/{lat}/{lon}.tar.gz`
- [ ] `manifest.json` содержит хеши и размеры
- [ ] `version.txt` содержит дату версии
- [ ] Локальный сервер отдаёт файлы с Range support
- [ ] Sunnypilot скачивает тайлы без ошибок
- [ ] Камеры появляются в UI sunnypilot
