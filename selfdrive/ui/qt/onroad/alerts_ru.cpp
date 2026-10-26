#include "alerts_ru.h"

#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QVector>

namespace {
const char catalogJson[] =
#include "alerts_ru_data.inc"
;

struct Rule {
  QRegularExpression expression;
  QString replacement;
};

struct Catalog {
  QHash<QString, QString> texts;
  QVector<Rule> rules;
};

const Catalog &catalog() {
  static const Catalog value = [] {
    Catalog result;
    const QJsonDocument document = QJsonDocument::fromJson(QByteArray(catalogJson, sizeof(catalogJson) - 1));
    if (!document.isObject()) return result;  // Invalid data falls back to original text.
    const QJsonObject root = document.object();
    const QJsonObject texts = root.value(QStringLiteral("static")).toObject();
    for (auto item = texts.constBegin(); item != texts.constEnd(); ++item) {
      if (!item.value().toString().isEmpty()) result.texts.insert(item.key(), item.value().toString());
    }
    for (const QJsonValue &item : root.value(QStringLiteral("rules")).toArray()) {
      const QJsonObject row = item.toObject();
      QRegularExpression expression(row.value(QStringLiteral("pattern")).toString());
      if (!expression.isValid()) continue;
      expression.optimize();
      result.rules.push_back({expression, row.value(QStringLiteral("replacement")).toString()});
    }
    return result;
  }();
  return value;
}
}

QString CommaRu::presentAlertText(const QString &text, const QString &languageSetting) {
  if (languageSetting != QStringLiteral("main_ru") || text.isEmpty()) return text;
  const Catalog &data = catalog();
  const auto found = data.texts.constFind(text);
  if (found != data.texts.constEnd()) return found.value();
  for (const Rule &rule : data.rules) {
    const QRegularExpressionMatch match = rule.expression.match(text);
    // Keep the full-message constraint even if a future catalog rule loses anchors.
    if (match.hasMatch() && match.capturedStart() == 0 && match.capturedLength() == text.size()) {
      QString output = text;
      output.replace(rule.expression, rule.replacement);
      return output;
    }
  }
  return text;
}

#ifdef COMMA_ALERTS_SELF_TEST
#include <cstdio>
int main() {
  const auto present = [](const char *source) {
    return CommaRu::presentAlertText(QString::fromUtf8(source), QStringLiteral("main_ru"));
  };
  if (present("BRAKE!") != QString::fromUtf8("ТОРМОЗИТЕ!")) return 1;
  if (present("Drive above 32 km/h to engage") != QString::fromUtf8("Для включения двигайтесь быстрее 32 км/ч")) return 2;
  if (present("Speed Error: -1.5 m/s") != QString::fromUtf8("Ошибка скорости: -1.5 м/с")) return 3;
  if (present("modeld, camerad") != QStringLiteral("modeld, camerad")) return 4;
  if (CommaRu::presentAlertText(QStringLiteral("BRAKE!"), QStringLiteral("main_en")) != QStringLiteral("BRAKE!")) return 5;
  if (present("1 second remaining. Press again to save early.") !=
      QString::fromUtf8("До конца записи: 1 с. Нажмите ещё раз для досрочного сохранения.")) return 6;
  std::puts("QT_ALERT_PRESENTATION_TEST_PASS");
  return 0;
}
#endif
