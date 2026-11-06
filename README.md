![sunnypilot — интерфейс на устройстве](https://user-images.githubusercontent.com/47793918/233812617-beab2e71-57b9-479e-8bff-c3931347ca40.png)

**Русифицированная сборка для comma 3**

Переведено всё, что видит пользователь: меню, диалоги, предупреждения, экраны при запуске, обучающие страницы и справочные тексты.

[Последний релиз](https://github.com/NikMoq/openpilot/releases/latest) · [История изменений](CHANGELOG.md) · [Сообщить о проблеме](https://github.com/NikMoq/openpilot/issues)

Установка на чистое устройство
Подключите comma 3 к питанию и к Wi-Fi, чтобы был интернет.

На экране выбора ПО выберите Custom Software (Advanced) — это установка пользовательского ПО.

В поле адреса введите:

text
https://installer.comma.ai/NikMoq/stable
Сокращённый вариант тоже работает:

text
NikMoq/stable
Подтвердите адрес и дождитесь окончания загрузки, дальше просто следуйте тому, что показывает устройство. Когда оно попросит обучение и калибровку — пройдите их.
Ветки
Ветка	Для чего	Что вводить в установщик
stable	Основная сборка с полной русификацией	NikMoq/stable
Регистр в названии ветки важен — Stable и stable это не одно и то же.

На чём основано и что стоит учитывать
Проект вырос из [sunnypilot](https://github.com/sunnypilot/sunnypilot) и [openpilot от comma.ai](https://github.com/commaai/openpilot). [Документация sunnypilot](https://docs.sunnypilot.ai/) описывает upstream, то есть оригинальный проект, а не этот форк — есть ли у меня конкретная функция, лучше смотреть по коду и настройкам выбранной ветки, а не по докам.

Данные и обратная связь
Настройки загрузки данных и подключения к comma connect/sunnylink зависят от ветки и конфигурации. Прежде чем пользоваться, загляните в настройки приватности на устройстве и в условия соответствующих сервисов — это ваша зона ответственности.

Если хотите что-то поправить в основной ветке — присылайте pull request в stable. Если нашли ошибку — пишите в [Issues](https://github.com/NikMoq/openpilot/issues).

Licensing
sunnypilot is released under the [MIT License](LICENSE). This repository includes original work as well as significant portions of code derived from openpilot by comma.ai, which is also released under the MIT license with additional disclaimers.

The original openpilot license notice, including comma.ai’s indemnification and alpha software disclaimer, is reproduced below as required:

openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.

For full license terms, please see the [LICENSE](LICENSE) file.

