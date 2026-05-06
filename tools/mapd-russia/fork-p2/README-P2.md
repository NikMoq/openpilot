# P2: CI/CD + Smoke Test + Production Ready

## Что входит

| Компонент | Файл | Назначение |
|-----------|------|-----------|
| Smoke test | `.github/workflows/smoke-test.yml` | Проверка генерации тайлов на каждый push/PR |
| Dockerfile | `Dockerfile` | Multi-stage сборка ARM64 бинарника |
| Validation | `validate-tiles.sh` | Проверка тайлов перед релизом |

## Установка в форк

```bash
cd ~/mapd

# 1. Workflow
cp tools/mapd-russia/fork-p2/.github/workflows/smoke-test.yml .github/workflows/

# 2. Dockerfile
cp tools/mapd-russia/fork-p2/Dockerfile .

# 3. Validation script
cp tools/mapd-russia/fork-p2/validate-tiles.sh .
chmod +x validate-tiles.sh

# 4. Коммит
git add -A
git commit -m "P2: CI/CD smoke test + ARM64 Docker build + tile validation"
git push origin p0-russian-cameras
```

## Что проверяет CI

### `validate-tiles` job
- Скачивает OSM ЦФО (~300MB вместо 2.5GB РФ)
- Скачивает Rus.radar.txt
- Собирает mapd
- Генерирует тайлы
- Проверяет:
  - Тайлы созданы
  - Размер < 512KB каждый
  - Manifest.json валиден

### `test-arm64-build` job
- Собирает Docker образ для `linux/arm64`
- Проверяет что бинарник ARM64

### `check-data-sources` job
- Проверяет доступность Geofabrik
- Проверяет доступность speedcamonline.ru
- Проверяет GitHub API rate limit

## Использование validation script

```bash
# После генерации тайлов
./validate-tiles.sh ./tiles

# Вывод:
# ✅ manifest.json exists
# ✅ manifest.json structure valid
# ✅ Tiles found: 150 / 150 (missing: 0)
# ✅ All tiles under 64KB
# ✅ All hashes valid
# ✅ VALIDATION PASSED
```

## Docker сборка

```bash
# Собрать образ
docker build -t mapd-russia:latest .

# Проверить
docker run --rm mapd-russia:latest version

# Извлечь бинарник для sunnypilot
docker create --name extract mapd-russia:latest
docker cp extract:/usr/local/bin/mapd ./mapd-linux-arm64
docker rm extract
```

## Release checklist

- [ ] Smoke test проходит (`validate-tiles` зелёный)
- [ ] ARM64 бинарник собирается
- [ ] Источники данных доступны
- [ ] Тайлы проходят `validate-tiles.sh`
- [ ] GitHub Release создан с `mapd-russia-tiles.tar.gz`
- [ ] Sunnypilot скачивает и использует тайлы
