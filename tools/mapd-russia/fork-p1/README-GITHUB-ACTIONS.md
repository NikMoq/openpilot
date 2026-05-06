# P1: GitHub Actions — Автогенерация тайлов в Releases

## Что это

Полностью автоматический pipeline:
1. Каждое воскресенье в 3:00 UTC GitHub Actions запускается
2. Скачивает свежий OSM Russia + Rus.radar.txt
3. Собирает mapd и генерирует тайлы
4. Создаёт GitHub Release с архивом тайлов
5. Sunnypilot (через форк mapd) скачивает обновления из релиза

**Нужен только форк на GitHub. Никакого собственного сервера.**

## Файлы для форка `NikMoq/mapd`

| Файл | Куда копировать |
|------|----------------|
| `.github/workflows/generate-tiles.yml` → | `.github/workflows/generate-tiles.yml` |
| `settings/download_release.go` → | `settings/download_release.go` (дополнение к download.go) |
| `main_integration.go` → | Интегрировать в `main.go` |

## Установка в форк

```bash
cd ~/mapd  # твой клон форка

# 1. Workflow
cp tools/mapd-russia/fork-p1/.github/workflows/generate-tiles.yml .github/workflows/

# 2. Загрузчик из релизов
cp tools/mapd-russia/fork-p1/settings/download_release.go settings/

# 3. Интеграция в main.go (вручную)
# Добавь вызов checkAndUpdateTiles() в основной цикл

# 4. Коммит
git add .github/workflows/generate-tiles.yml settings/download_release.go
git commit -m "P1: GitHub Actions auto-generate tiles + release downloader"
git push origin p0-russian-cameras
```

## Настройка GitHub Actions

1. Перейди в Settings → Actions → General
2. Убедись что **Workflow permissions** = "Read and write permissions"
3. Это нужно для создания релизов

## Первый запуск

```bash
# Вручную из GitHub UI:
# Actions → "Generate Mapd Russia Tiles" → "Run workflow"
```

## Как sunnypilot получает обновления

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  GitHub Release │────→│  mapd (на устройстве)│────→│   sunnypilot    │
│  v2026.05.02-rus│     │  checkAndUpdateTiles │     │   (UI/камеры)   │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
```

1. Mapd раз в сутки проверяет `releases/latest` через GitHub API
2. Если версия новее локальной — скачивает `mapd-russia-tiles.tar.gz`
3. Распаковывает в `/data/mapd/offline/`
4. Sunnypilot использует тайлы через существующий интерфейс

## URL для скачивания (прямые ссылки)

```
# Latest release asset (прямая ссылка)
https://github.com/NikMoq/mapd/releases/latest/download/mapd-russia-tiles.tar.gz

# Конкретная версия
https://github.com/NikMoq/mapd/releases/download/v2026.05.02-rus/mapd-russia-tiles.tar.gz

# Manifest
https://github.com/NikMoq/mapd/releases/download/v2026.05.02-rus/manifest.json
```

## Размер

| Компонент | Оценка |
|-----------|--------|
| OSM Russia PBF (вход) | ~2.5 GB |
| Сгенерированные тайлы | ~200–500 MB |
| `mapd-russia-tiles.tar.gz` | ~50–150 MB (сжатый) |
| GitHub Release storage | Бесплатно для публичных репозиториев |

## Ограничения GitHub

- **Storage**: 500 MB для публичных репозиториев (soft limit, обычно не строгий)
- **Bandwidth**: 2 GB/месяц для LFS, но Releases assets — безлимитно для публичных репо
- **CI minutes**: 2000 минут/месяц для Free плана (генерация ~2-3 часа/неделю = ~12 часов/месяц)

Если тайлы станут слишком большими:
1. Уменьшить coverage (только европейская часть РФ)
2. Увеличить размер тайла (4°×4° вместо 2°×2°)
3. Хранить только последние 2-3 релиза, удалять старые

## Проверка работы

```bash
# На устройстве (через SSH)
ls /data/mapd/offline/          # должны быть тайлы
cat /data/mapd/offline/manifest.json  # версия и список

# Проверить загрузку вручную
wget https://github.com/NikMoq/mapd/releases/latest/download/mapd-russia-tiles.tar.gz
```
