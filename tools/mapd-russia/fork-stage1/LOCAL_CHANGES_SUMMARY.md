# Локальные изменения в sunnypilot (Этап 1)

## Изменённые файлы

### `sunnypilot/mapd/mapd_installer.py`

- `VERSION` изменён с `v1.12.0` на `v1.12.1-rus.1`
- `URL` изменён с `pfeiferj/openpilot-mapd` на `NikMoq/mapd`
- Артефакт теперь `mapd-linux-arm64` (соответствует имени в GitHub Releases workflow)

## Что нужно сделать пользователю

1. Закоммитить изменения из `tools/mapd-russia/fork-stage1/` в свой форк `NikMoq/mapd`:
   - `.github/workflows/release.yml`
   - `settings/download.go`

2. Создать тег в форке и дождаться релиза:
   ```bash
   git tag v1.12.1-rus.1
   git push origin v1.12.1-rus.1
   ```

3. Убедиться, что в релизе появился файл `mapd-linux-arm64`.

4. Закоммитить изменения в sunnypilot (этот репозиторий):
   ```bash
   git add sunnypilot/mapd/mapd_installer.py
   git add tools/mapd-russia/
   git commit -m "mapd: switch to NikMoq fork for Russian map support (stage 1)"
   ```

5. Прошить устройство и проверить, что mapd скачивается из нового источника.

## Проверка

После первой загрузки на устройстве проверь:
```bash
ssh comma@192.168.x.x
/data/openpilot/third_party/mapd_pfeiferj/mapd --version
```

Должна показаться версия из твоего форка.
