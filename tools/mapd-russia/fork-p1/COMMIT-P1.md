# P1 Commit Instructions

## Что коммитить в `NikMoq/mapd`

Если P0 уже применён, P1 требует минимальных изменений:

### 1. CLI команда `generate` (новый файл)

```bash
cp tools/mapd-russia/fork-p1/cmd/generate.go cmd/
git add cmd/generate.go
```

### 2. Если нужен OSM PBF парсер

Если в форке ещё нет парсера OSM PBF, добавь:
- `maps/osm_parser.go` — обёртка над `github.com/paulmach/osm`

### 3. Коммит

```bash
git commit -m "P1: Add generate command for camera tiles

- CLI: mapd generate --osm --cameras --out
- Integrates with P0 camera pipeline
- Outputs standard tar.gz tiles for sunnypilot"
```

## Что коммитить в `sunnypilot` (этот репозиторий)

**Ничего!** Sunnypilot уже обновлён в P0 (`mapd_installer.py`).

Только добавь серверные скрипты в `tools/mapd-russia/server/`:

```bash
git add tools/mapd-russia/server/
git commit -m "P1: Server pipeline for mapd-russia tiles"
```

## Порядок деплоя

1. Собрать `mapd` бинарник с P0+P1
2. Запустить `generate_tiles.py` на сервере
3. Загрузить `tiles/` на CDN
4. Sunnypilot автоматически скачает новые тайлы
