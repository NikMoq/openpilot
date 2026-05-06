# Этап 1 — Настройка форка mapd для России

## Что входит в этот этап

1. **Смена URL сервера карт** — вместо `map-data.pfeifer.dev` используется свой сервер (placeholder).
2. **GitHub Actions CI/CD** — автоматическая сборка бинарников `mapd` под `linux/amd64` и `linux/arm64` при пуше тега `v*`.

## Файлы для коммита в форк

```
.github/workflows/release.yml   →  новый файл
settings/download.go            →  заменить URL на свой
```

## Пошаговая инструкция

### 1. Клонируй свой форк локально

```bash
git clone https://github.com/NikMoq/mapd.git
cd mapd
git checkout -b stage1-custom-server
```

### 2. Примени изменения

**A. Скопируй workflow:**
```bash
mkdir -p .github/workflows
cp tools/mapd-russia/fork-stage1/.github/workflows/release.yml .github/workflows/
```

**B. Замени `settings/download.go`:**
```bash
cp tools/mapd-russia/fork-stage1/settings/download.go settings/download.go
```

### 3. Настрой свой URL (опционально, пока можно оставить placeholder)

Открой `settings/download.go` и замени:
```go
const mapDataBaseURL = "https://mapd-russia.your-server.com"
```
на реальный адрес, где будут хоститься тайлы.

> Пока сервер не готов — оставь placeholder. Главное, что бинарник будет собираться из твоего форка.

### 4. Закоммить и запушь

```bash
git add .github/workflows/release.yml settings/download.go
git commit -m "stage1: custom map server URL + GitHub Actions release builds"
git push origin stage1-custom-server
```

### 5. Создай Pull Request (или сразу в main)

```bash
git checkout main
git merge stage1-custom-server
git push origin main
```

### 6. Проверь сборку

Создай тег — GitHub Actions автоматически соберёт релиз:

```bash
git tag v1.12.1-rus.1
git push origin v1.12.1-rus.1
```

Через 2–3 минуты в разделе **Releases** появятся два файла:
- `mapd-linux-amd64`
- `mapd-linux-arm64` ← этот нужен для Comma 3/3X

### 7. Обнови sunnypilot чтобы тянул бинарник из твоего форка

В `sunnypilot/mapd/mapd_installer.py` измени:

```python
# Было:
VERSION = "v1.12.0"
URL = f"https://github.com/pfeiferj/openpilot-mapd/releases/download/{VERSION}/mapd"

# Стало:
VERSION = "v1.12.1-rus.1"  # твой тег
URL = f"https://github.com/NikMoq/mapd/releases/download/{VERSION}/mapd-linux-arm64"
```

> **Важно:** имя файла в релизе — `mapd-linux-arm64`, а не просто `mapd`. Либо переименуй артефакт в workflow, либо поменяй URL в installer.

---

## Что дальше (Этап 2)

- Добавить `Camera` в `cereal/offline/offline.capnp`
- Модифицировать `maps/generate_offline.go` для парсинга `Rus.radar.txt`
- Написать серверный скрипт генерации тайлов
- Поднять CDN/сервер для хостинга `.tar.gz`
