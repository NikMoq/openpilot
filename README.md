# NikMoq openpilot — русская версия для comma 3

Форк sunnypilot/openpilot с полной русификацией интерфейса. Основная ветка поставки — **stable**. Подтверждённая совместимость: **comma 3 (C3)**.
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

Если уже установлено другое ПО, сначала сохраните нужные данные. Для возврата к экрану установки используйте штатное удаление ПО в разделе **Settings → Software**. Перепрошивка AGNOS для обычного обновления stable не требуется; восстановление ОС — отдельная процедура через [flash.comma.ai](https://flash.comma.ai/).

## Обновление через интерфейс

1. Откройте **Настройки → ПО** и нажмите **Проверить наличие обновления**.
2. В выборе целевой ветки укажите **stable**.
3. Нажмите **Загрузить** и дождитесь завершения подготовки.
4. Прочитайте описание изменений, затем воспользуйтесь кнопкой перезагрузки и обновления.

## Ветки и адреса

| Ветка | Назначение | Ввод в установщик |
| --- | --- | --- |
| [stable](https://github.com/NikMoq/openpilot/tree/stable) | Основная поставка с полной русификацией | `NikMoq/stable` |

Регистр имён веток важен.

## Релизы и изменения

Основной релиз содержит полную русификацию stable.

## Основа проекта и ограничения

Основа: [sunnypilot](https://github.com/sunnypilot/sunnypilot) и [openpilot от comma.ai](https://github.com/commaai/openpilot). [Документация sunnypilot](https://docs.sunnypilot.ai/) описывает upstream; доступность конкретной функции в этом форке следует проверять по коду и настройкам выбранной ветки.

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
