# Интеграция mapd (NikMoq/mapd) с openpilot pqplatform

## Что изменено

### 1. Сборка mapd бинарника (оптимизировано)
- **Старый способ**: Docker эмуляция ARM64 - 6+ часов
- **Новый способ**: Cross-compile через `gcc-aarch64-linux-gnu` - **< 10 минут**

### 2. Генерация тайлов (оптимизировано)
- **Старый способ**: Одна job для всей России - 6+ часов
- **Новый способ**: Параллельные job'ы по регионам - **< 1 часа**

### 3. Источник данных
- **Старый**: `pfeiferj/openpilot-mapd`
- **Новый**: `NikMoq/mapd`

---

## Файлы для загрузки в NikMoq/mapd

### `.github/workflows/build-mapd-release.yml`
Сборка бинарника mapd (native + ARM64) и создание Release.
- Время: ~5-10 минут
- Создаёт релиз с файлами:
  - `mapd-linux-arm64.tar.gz` - для comma device
  - `mapd-native.tar.gz` - для генерации тайлов

### `.github/workflows/generate-tiles-optimized.yml`
Генерация OSM тайлов по регионам параллельно.
- Время: ~30-45 минут (вместо 6+ часов)
- Регионы (параллельно):
  - Moscow & Central
  - St. Petersburg & North-West
  - Kazan & Volga
  - Sochi & South
  - Novosibirsk & Siberia
  - Yekaterinburg & Ural
- Создаёт релиз с `mapd-russia-tiles.tar.gz`

---

## Настройка

### Шаг 1: Создать GitHub Token
1. GitHub Settings → Developer settings → Personal access tokens
2. Создать token с правами `repo` и `workflow`
3. Добавить в Secrets репозитория NikMoq/mapd:
   - Name: `GITHUB_TOKEN` (уже должен быть доступен автоматически)

### Шаг 2: Загрузить workflow файлы
```bash
cd /path/to/mapd/repo
cp /path/to/openpilot/mapd_workflows/build-mapd-release.yml .github/workflows/
cp /path/to/openpilot/mapd_workflows/generate-tiles-optimized.yml .github/workflows/
git add .github/workflows/
git commit -m "Add optimized workflows for mapd build and tile generation"
git push
```

### Шаг 3: Запустить сборку бинарника
1. GitHub → NikMoq/mapd → Actions → "Build mapd Release"
2. Run workflow
3. Дождаться создания Release (v1.0.0)

### Шаг 4: Запустить генерацию тайлов
1. GitHub → NikMoq/mapd → Actions → "Generate Mapd Russia Tiles (Optimized)"
2. Run workflow
3. Дождаться создания Release с тайлами

### Шаг 5: Обновить openpilot на устройстве
```bash
cd /data/openpilot
git pull origin pqplatform
# Перезагрузить устройство
sudo reboot
```

---

## Как это работает в openpilot

### Скачивание бинарника mapd
При старте openpilot проверяет `sunnypilot/mapd/mapd_installer.py`:
```python
VERSION = "v1.0.0"
URL = f"https://github.com/NikMoq/mapd/releases/download/{VERSION}/mapd-linux-arm64.tar.gz"
```

Если бинарник не установлен или версия устарела - скачивает и распаковывает.

### Скачивание тайлов (опционально)
Если нужно скачивать готовые тайлы вместо локальной генерации - добавить в `mapd_manager.py`:
```python
TILES_URL = "https://github.com/NikMoq/mapd/releases/download/vYYYY.MM.DD-rus/mapd-russia-tiles.tar.gz"
```

---

## Оптимизации

### 1. Кэширование
- Go modules кэшируются между запусками
- OSM PBF файл кэшируется (если не изменился)

### 2. Параллельность
- 6 регионов генерируются одновременно
- Каждый регион ~5-10 минут вместо 6 часов для всей России

### 3. Cross-compile
- ARM64 бинарник собирается через `gcc-aarch64-linux-gnu`
- Без Docker эмуляции (медленно)

---

## Тroubleshooting

### Ошибка: "No release found"
- Убедитесь что workflow `build-mapd-release.yml` создал Release
- Проверьте тег версии (должен совпадать с `VERSION` в `mapd_installer.py`)

### Ошибка: "Failed to download"
- Проверьте интернет-соединение на устройстве
- Проверьте URL в `mapd_installer.py`

### Тайлы устарели
- Запустить workflow `generate-tiles-optimized.yml` вручную
- Или дождаться воскресенья (автозапуск по cron)
