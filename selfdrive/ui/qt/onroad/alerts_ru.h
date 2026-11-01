#pragma once

#include <QString>

namespace CommaRu {
// Presentation only. The caller retains every alert field except text1/text2.
// Unknown text and every language other than the exact Qt main_ru setting pass through.
QString presentAlertText(const QString &text, const QString &languageSetting);
}
