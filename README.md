# NikMoq openpilot — русская версия для comma 3

Форк sunnypilot/openpilot с полной русификацией интерфейса. Основная ветка поставки — **stable**. Подтверждённая совместимость: **comma 3 (C3)**. В текущей ветке задана AGNOS 13.1. Совместимость этого релиза с другими моделями устройств отдельно не подтверждена.

Русифицированы меню, диалоги, предупреждения, экраны запуска, учебные страницы и справочные тексты, включая подсказку планирования поворотов по поворотнику. В stable также включены исправления выбора веток и загрузки обновлений через интерфейс.

[Последний релиз](https://github.com/NikMoq/openpilot/releases/latest) · [История изменений](CHANGELOG.md) · [Сообщить о проблеме](https://github.com/NikMoq/openpilot/issues)

## Установка на чистое устройство

1. Подключите comma 3 к питанию и Wi-Fi с доступом в интернет.
2. На экране выбора программного обеспечения выберите **Custom Software (Advanced)** — установку пользовательского ПО.
3. В поле адреса введите:

   ```text
   https://installer.comma.ai/NikMoq/stable
   ```

   Поддерживается и сокращённая запись:

   ```text
   NikMoq/stable
   ```

4. Подтвердите адрес, дождитесь завершения загрузки и следуйте инструкциям на экране. Пройдите обучение и калибровку, когда устройство их запросит.

Это адрес установщика, а не ссылка на страницу GitHub и не скачивание архива из Releases. Установщик получает актуальное содержимое ветки stable; конкретный выпуск закрепляется отдельным тегом в Releases.

Формат URL и сокращённой записи сверены с [официальным установщиком comma](https://installer.comma.ai/) и [разделом Fork Installation в wiki commaai/openpilot](https://github.com/commaai/openpilot/wiki/Forks#fork-installation). Чистая установка этого выпуска по приведённому URL отдельно не проверялась на устройстве.

Если уже установлено другое ПО, сначала сохраните нужные данные. Для возврата к экрану установки используйте штатное удаление ПО в разделе **Settings → Software**. Перепрошивка AGNOS для обычного обновления stable не требуется; восстановление ОС — отдельная процедура через [flash.comma.ai](https://flash.comma.ai/).

## Обновление через интерфейс

1. Откройте **Настройки → ПО** и нажмите **Проверить наличие обновления**.
2. В выборе целевой ветки укажите **stable**.
3. Нажмите **Загрузить** и дождитесь завершения подготовки.
4. Прочитайте описание изменений, затем воспользуйтесь кнопкой перезагрузки и обновления.

Выбор ветки через UI получает ветки текущего origin. При переходе из другого форка используйте установочный адрес выше: выбор имени stable сам по себе не переключает GitHub-репозиторий.

Если загрузка завершилась ошибкой, приложите к issue текст ошибки, название текущей и целевой ветки, SHA коммита и модель устройства. Не публикуйте ключи SSH и другие данные входа.

## Ветки и адреса

| Ветка | Назначение | Ввод в установщик |
| --- | --- | --- |
| [stable](https://github.com/NikMoq/openpilot/tree/stable) | Основная поставка с полной русификацией | `NikMoq/stable` |
| [dev](https://github.com/NikMoq/openpilot/tree/dev) | Разработка; набор функций может отличаться | `NikMoq/dev` |
| [ICBM](https://github.com/NikMoq/openpilot/tree/ICBM) | Отдельная ветка разработки ICBM | Не основной канал установки |
| [ui9](https://github.com/NikMoq/openpilot/tree/ui9) | Отдельная ветка работы над UI | Не основной канал установки |

Регистр имён веток важен. Ветки pqplatform, rumappq, rus и res удалены; прежние ссылки на них не используются. Адреса release.sunnypilot.ai и staging.sunnypilot.ai устанавливают upstream sunnypilot, а не этот форк.

## Релизы и изменения

Основной релиз содержит полную русификацию stable. SpeedCamOnline Rus — служебный prerelease с базой камер и манифестом для разработческой ветки dev; он не является установочным образом русификации. Его адреса сохранены для работы резервного источника данных.

При каждой публикации изменений в stable обновляется [CHANGELOG.md](CHANGELOG.md). Последний выпуск расположен в начале файла; его описание читает интерфейс обновления. Старые записи upstream сохранены как история, а не описание текущего выпуска.

## Основа проекта и ограничения

Основа: [sunnypilot](https://github.com/sunnypilot/sunnypilot) и [openpilot от comma.ai](https://github.com/commaai/openpilot). [Документация sunnypilot](https://docs.sunnypilot.ai/) описывает upstream; доступность конкретной функции в этом форке следует проверять по коду и настройкам выбранной ветки.

Подтверждение работы русификации на C3 не является подтверждением всех автомобилей, изменений управления или готовности модификаций EPS/Panda. Совместимость автомобиля, проводки и конкретной конфигурации проверяется отдельно. Водитель сохраняет ответственность за управление.

## Данные и обратная связь

Настройки загрузки данных и подключения к comma connect/sunnylink зависят от выбранной ветки и конфигурации. Перед использованием ознакомьтесь с настройками приватности на устройстве и условиями соответствующих сервисов.

Изменения для основного канала предлагайте через pull request в stable. Для сообщения об ошибке используйте [Issues](https://github.com/NikMoq/openpilot/issues).

## Licensing

sunnypilot is released under the [MIT License](LICENSE). This repository includes original work as well as significant portions of code derived from [openpilot by comma.ai](https://github.com/commaai/openpilot), which is also released under the MIT license with additional disclaimers.

The original openpilot license notice, including comma.ai’s indemnification and alpha software disclaimer, is reproduced below as required:

> openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.
>
> Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.
>
> **THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
> YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
> NO WARRANTY EXPRESSED OR IMPLIED.**

For full license terms, please see the [`LICENSE`](LICENSE) file.
