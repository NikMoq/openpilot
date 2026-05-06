# P3: Деплой на Comma — Финальные шаги

## Что изменено в sunnypilot

| Файл | Изменение |
|------|-----------|
| `sunnypilot/mapd/mapd_installer.py` | URL → NikMoq/mapd v1.12.1-rus.1, функция `update_tiles_version()` |
| `sunnypilot/mapd/live_map_data/osm_map_data.py` | Метод `get_next_camera()` — читает камеры из Params |

## Что нужно сделать ПЕРЕД обновлением на Comma

### 1. Убедиться что форк `NikMoq/mapd` готов

```bash
cd ~/mapd  # твой клон форка

# Проверить что всё на месте
git log --oneline -5
# Должно быть: P0, P1, P2 коммиты

# Собрать релиз
go build -o mapd-linux-arm64 .

# Создать тег (если ещё не создан)
git tag v1.12.1-rus.1
git push origin v1.12.1-rus.1
```

### 2. GitHub Actions должен создать первый релиз тайлов

```bash
# В GitHub UI:
# Actions → "Generate Mapd Russia Tiles" → "Run workflow"

# Проверить что релиз создан:
curl -s https://api.github.com/repos/NikMoq/mapd/releases/latest | jq .tag_name
# Должно вернуть: "v2026.05.02-rus" (или текущую дату)
```

### 3. Обновить sunnypilot на Comma

#### Вариант A: Через SSH (для разработчиков)

```bash
# Подключиться к Comma
ssh comma@192.168.x.x

# Перейти в openpilot
cd /data/openpilot

# Забрать свежий код (если используешь git)
git fetch origin
git checkout dev  # или твоя ветка

# Пересобрать
scons -j$(nproc)

# Перезагрузить
sudo reboot
```

#### Вариант B: Через обновление прошивки (для пользователей)

1. Зайти в **Settings → Software**
2. Нажать **Check for Updates**
3. Дождаться скачивания и установки
4. Устройство перезагрузится автоматически

### 4. Первый запуск после обновления

После перезагрузки sunnypilot:
1. **Скачает mapd** с GitHub Releases (NikMoq/mapd)
2. **Скачает тайлы** при первом запросе карт (или при ручном обновлении в Settings → OSM)
3. **Камеры появятся** когда машина въедет в зону покрытия тайлов

### 5. Проверка работы

```bash
# Через SSH на Comma
ssh comma@192.168.x.x

# Проверить версию mapd
cat /data/params/d/MapdVersion
# Должно быть: v1.12.1-rus.1

# Проверить версию тайлов
cat /data/params/d/MapdRussiaTilesVersion
# Должно быть: v2026.05.02-rus (или текущая)

# Проверить что тайлы скачались
ls /data/mapd/offline/
# Должны быть папки с тайлами

# Посмотреть логи mapd
journalctl -u mapd --no-pager -n 50
# Или
/data/openpilot/sunnypilot/mapd/mapd_installer.py
```

### 6. Проверка камер в UI

Когда машина движется и впереди есть камера:
- В **OsmMapData** будет доступен `get_next_camera()`
- UI sunnypilot может использовать эти данные для отображения

Проверить программно:
```python
# На Comma через Python
from openpilot.common.params import Params
p = Params()
import json
cam = json.loads(p.get("NextCamera") or "{}")
print(cam)
# {'type': 'stationary', 'distance': 450.5, 'speedLimit': 60.0, ...}
```

## Fallback — если что-то пошло не так

### mapd не скачивается
```bash
# Проверить интернет
ping github.com

# Проверить URL
wget https://github.com/NikMoq/mapd/releases/download/v1.12.1-rus.1/mapd-linux-arm64 -O /tmp/test
# Должен скачаться бинарник

# Ручная установка
wget https://github.com/NikMoq/mapd/releases/download/v1.12.1-rus.1/mapd-linux-arm64 -O /data/mapd/mapd
chmod +x /data/mapd/mapd
```

### Тайлы не скачиваются
```bash
# Проверить релиз
wget https://github.com/NikMoq/mapd/releases/latest/download/mapd-russia-tiles.tar.gz -O /tmp/tiles.tar.gz

# Ручная установка
mkdir -p /data/mapd/offline
tar -xzf /tmp/tiles.tar.gz -C /data/mapd/
```

### Вернуться на оригинальный mapd
```bash
# В sunnypilot/mapd/mapd_installer.py вернуть:
VERSION = "v1.12.0"
URL = f"https://github.com/pfeiferj/openpilot-mapd/releases/download/{VERSION}/mapd"

# Пересобрать и перезагрузить
```

## Чеклист деплоя

- [ ] Форк `NikMoq/mapd` собран и протегирован `v1.12.1-rus.1`
- [ ] GitHub Release с тайлами создан
- [ ] Sunnypilot код обновлён (этот PR/коммит)
- [ ] Обновление загружено на Comma
- [ ] Mapd скачался (`MapdVersion` = `v1.12.1-rus.1`)
- [ ] Тайлы скачались (`MapdRussiaTilesVersion` не пустой)
- [ ] При движении `NextCamera` содержит данные о камере
- [ ] Если камер нет — система работает как раньше (без ошибок)
