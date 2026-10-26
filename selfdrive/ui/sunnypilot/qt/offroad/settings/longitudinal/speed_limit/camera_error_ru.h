#pragma once

#include <QCoreApplication>
#include <QRegularExpression>
#include <QString>
#include <QVector>

// Display-only mapping. Never modify persisted updater errors or unknown diagnostics.
namespace CameraErrorRu {
inline QString present(const QString &text, const QString &language, int depth = 0) {
  if (language != QStringLiteral("main_ru") || text.isEmpty() || text.size() > 16384 || depth > 2) return text;
  static const char *const messages[] = {
    QT_TRANSLATE_NOOP("CameraError", "duplicate mirror manifest field"),
    QT_TRANSLATE_NOOP("CameraError", "iGo snapshot is truncated: missing line terminator"),
    QT_TRANSLATE_NOOP("CameraError", "invalid iGo CSV quoting"),
    QT_TRANSLATE_NOOP("CameraError", "invalid iGo speed-camera header"),
    QT_TRANSLATE_NOOP("CameraError", "invalid iGo speed-camera schema"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror asset_name"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror fetched_at"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror manifest JSON/encoding"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror record_count"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror sha256"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror size_bytes"),
    QT_TRANSLATE_NOOP("CameraError", "invalid mirror timestamp"),
    QT_TRANSLATE_NOOP("CameraError", "mirror asset SHA-256 does not match manifest"),
    QT_TRANSLATE_NOOP("CameraError", "mirror asset length does not match manifest"),
    QT_TRANSLATE_NOOP("CameraError", "mirror manifest exceeds size limit"),
    QT_TRANSLATE_NOOP("CameraError", "mirror manifest must be an object"),
    QT_TRANSLATE_NOOP("CameraError", "mirror parsed record count does not match manifest"),
    QT_TRANSLATE_NOOP("CameraError", "mirror source_url does not match SpeedCamOnline CG77"),
    QT_TRANSLATE_NOOP("CameraError", "mirror timestamp is stale or too far in the future"),
    QT_TRANSLATE_NOOP("CameraError", "non-finite JSON constant in mirror manifest"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera Content-Length is invalid"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera HTTP host resolved to no addresses"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera URL must use http or https"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera download wallclock timeout"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera endpoint returned Content-Range instead of a complete snapshot"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera endpoint returned HTML"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera path and version are required"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera redirect must remain HTTP or HTTPS"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera response length does not match Content-Length"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera size limit must be a positive integer"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera size limit must be positive"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera snapshot contains no valid cameras"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera snapshot exceeds size limit"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera snapshot is empty or HTML"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera snapshot is empty or header-only"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera source delivery total deadline exceeded"),
    QT_TRANSLATE_NOOP("CameraError", "speed-camera timeout must be positive"),
    QT_TRANSLATE_NOOP("CameraError", "unsupported mirror schema_version"),
  };
  for (const char *source : messages) {
    if (text == QString::fromUtf8(source)) return QCoreApplication::translate("CameraError", source);
  }
  struct Rule { QRegularExpression expression; const char *source; int captures; bool suffix; };
  static const QVector<Rule> rules = {
    {QRegularExpression(QStringLiteral("^speed-camera HTTP deadline/read timeout after ([0-9]+\\.[0-9]{3})s; wire_bytes=([0-9]+)\\z")), QT_TRANSLATE_NOOP("CameraError", "speed-camera HTTP deadline/read timeout after %1s; wire_bytes=%2"), 2, false},
    {QRegularExpression(QStringLiteral("^speed-camera download read timeout; body_bytes=([0-9]+); ([\\s\\S]*)\\z")), QT_TRANSLATE_NOOP("CameraError", "speed-camera download read timeout; body_bytes=%1; %2"), 2, true},
    {QRegularExpression(QStringLiteral("^speed-camera download connection timeout; body_bytes=([0-9]+); ([\\s\\S]*)\\z")), QT_TRANSLATE_NOOP("CameraError", "speed-camera download connection timeout; body_bytes=%1; %2"), 2, true},
    {QRegularExpression(QStringLiteral("^speed-camera endpoint requires complete HTTP 200; got ([0-9]+)\\z")), QT_TRANSLATE_NOOP("CameraError", "speed-camera endpoint requires complete HTTP 200; got %1"), 1, false},
    {QRegularExpression(QStringLiteral("^invalid iGo row ([0-9]+)\\z")), QT_TRANSLATE_NOOP("CameraError", "invalid iGo row %1"), 1, false},
  };
  for (const Rule &rule : rules) {
    const auto match = rule.expression.match(text);
    if (!match.hasMatch()) continue;
    const QString translated = QCoreApplication::translate("CameraError", rule.source);
    if (rule.captures == 1) return translated.arg(match.captured(1));
    const QString suffix = rule.suffix ? present(match.captured(2), language, depth + 1) : match.captured(2);
    return translated.arg(match.captured(1), suffix);
  }
  return text;
}
}  // namespace CameraErrorRu
