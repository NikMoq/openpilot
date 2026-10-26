#pragma once

// Qt 5 text-only training overlay. Source PNG files and event/hitarea logic are
// never modified. Layout/font/native integration still require deployment QA.
#include <QColor>
#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QFont>
#include <QFontInfo>
#include <QFontMetrics>
#include <QGuiApplication>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QPainter>
#include <QRect>
#include <QRegularExpression>
#include <QSet>
#include <QSize>
#include <QString>
#include <QVector>
#include <cmath>

namespace comma3TrainingOverlayDetail {
constexpr int kWidth = 2160;
constexpr int kHeight = 1080;
constexpr int kSteps = 19;

struct Cover { QRect rect; QColor color; };
struct Block { int index; QRect cover; QColor background; QRect rect; QFont font; QColor color; int flags; QString text; };
struct Preserve { QRect rect; QSet<int> allowed; };
struct Cache {
  QString sourcePath, layoutPath;
  QByteArray sourceHash, layoutHash;
  int step = -1;
  QImage original, rendered;  // Exactly one page; never an all-page image cache.
};

inline bool integer(const QJsonValue &value, int minimum, int maximum, int *out) {
  if (!value.isDouble()) return false;
  const double number = value.toDouble();
  if (!std::isfinite(number) || std::floor(number) != number || number < minimum || number > maximum) return false;
  *out = static_cast<int>(number);
  return true;
}

inline bool dimensions(const QJsonValue &value) {
  if (!value.isArray()) return false;
  const auto array = value.toArray();
  int width = 0, height = 0;
  return array.size() == 2 && integer(array[0], kWidth, kWidth, &width) && integer(array[1], kHeight, kHeight, &height);
}

inline bool rectangle(const QJsonValue &value, QRect *out) {
  if (!value.isArray()) return false;
  const auto array = value.toArray();
  int x = 0, y = 0, width = 0, height = 0;
  if (array.size() != 4 || !integer(array[0], 0, kWidth - 1, &x) || !integer(array[1], 0, kHeight - 1, &y) ||
      !integer(array[2], 1, kWidth, &width) || !integer(array[3], 1, kHeight, &height) ||
      width > kWidth - x || height > kHeight - y) return false;
  *out = QRect(x, y, width, height);
  return true;
}

inline bool color(const QJsonValue &value, QColor *out) {
  static const QRegularExpression pattern(QStringLiteral("^#[0-9a-fA-F]{6}$"));
  if (!value.isString() || !pattern.match(value.toString()).hasMatch()) return false;
  *out = QColor(value.toString());
  return out->isValid() && out->alpha() == 255;
}

inline bool opaque(const QJsonValue &value) {
  return value.isDouble() && std::isfinite(value.toDouble()) && value.toDouble() == 1.0;
}

inline bool boundedRead(const QString &path, qint64 limit, QByteArray *bytes) {
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly) || file.size() < 1 || file.size() > limit) return false;
  *bytes = file.read(limit + 1);
  return file.error() == QFileDevice::NoError && !bytes->isEmpty() && bytes->size() <= limit;
}

inline QByteArray hash(const QByteArray &bytes) {
  return QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex();
}

inline bool sha256(const QString &value) {
  static const QRegularExpression pattern(QStringLiteral("^[0-9a-fA-F]{64}$"));
  return pattern.match(value).hasMatch();
}

inline bool font(const QJsonObject &object, QFont *out, QString *error) {
  int pixels = 0, weight = 0;
  const QString family = object.value(QStringLiteral("font_family")).toString();
  if (family.isEmpty() || family.size() > 80 || !integer(object.value(QStringLiteral("font_px")), 1, 200, &pixels) ||
      !integer(object.value(QStringLiteral("font_weight")), 400, 700, &weight)) {
    *error = QStringLiteral("invalid font specification"); return false;
  }
  *out = QFont(family);
  out->setPixelSize(pixels);
  // Qt 5 weights are 0..99; CSS/Qt 6 values must never be passed through.
  switch (weight) {
    case 400: out->setWeight(QFont::Normal); break;
    case 500: out->setWeight(QFont::Medium); break;
    case 600: out->setWeight(QFont::DemiBold); break;
    case 700: out->setWeight(QFont::Bold); break;
    default: *error = QStringLiteral("unsupported CSS font weight"); return false;
  }
  const auto underline = object.value(QStringLiteral("underline"));
  if (!underline.isUndefined() && !underline.isBool()) { *error = QStringLiteral("invalid underline flag"); return false; }
  out->setUnderline(underline.toBool(false));
  if (QFontInfo(*out).family().compare(family, Qt::CaseInsensitive) != 0) {
    *error = QStringLiteral("requested font family is unavailable"); return false;
  }
  return true;
}

inline bool consentBlock(const QJsonObject &object, const Block &block) {
  const QString choice = object.value(QStringLiteral("choice")).toString();
  if (choice.isEmpty()) return true;
  if (choice == QStringLiteral("decline_data_sharing")) {
    return object.value(QStringLiteral("record_front_value")).isBool() && !object.value(QStringLiteral("record_front_value")).toBool() &&
           block.text == QString::fromUtf8("Нет") && QRect(112, 804, 531, 164).contains(block.rect.center()) &&
           QRect(112, 804, 531, 164).contains(block.cover.center());
  }
  if (choice == QStringLiteral("allow_data_sharing")) {
    return object.value(QStringLiteral("record_front_value")).isBool() && object.value(QStringLiteral("record_front_value")).toBool() &&
           block.text == QString::fromUtf8("Да") && QRect(707, 804, 531, 164).contains(block.rect.center()) &&
           QRect(707, 804, 531, 164).contains(block.cover.center());
  }
  return false;
}
}  // namespace comma3TrainingOverlayDetail

// Paints native image pixels BEFORE TrainingGuide's existing scaling. Any
// validation failure returns the unmodified input and an optional diagnostic.
// No Params writes, event dispatch, hitarea changes, PNG writes or HTML parsing.
inline QImage drawRussianTrainingOverlay(const QImage &original, const QString &sourcePath,
                                         const QString &layoutPath, int step, const QString &languageSetting,
                                         QString *diagnostic = nullptr) {
  using namespace comma3TrainingOverlayDetail;
  if (diagnostic) diagnostic->clear();
  if (languageSetting != QStringLiteral("main_ru")) return original;
  auto fail = [&](const QString &reason) -> QImage {
    if (diagnostic) *diagnostic = QStringLiteral("step %1: %2").arg(step).arg(reason);
    return original;
  };
  try {
    if (!qobject_cast<QGuiApplication *>(QCoreApplication::instance()) || step < 0 || step >= kSteps || original.isNull() ||
        original.size() != QSize(kWidth, kHeight) || original.devicePixelRatio() != 1.0) {
      return fail(QStringLiteral("unsupported application/image geometry"));
    }
    QByteArray sourceBytes, layoutBytes;
    if (!boundedRead(sourcePath, 32 * 1024 * 1024, &sourceBytes) || !boundedRead(layoutPath, 2 * 1024 * 1024, &layoutBytes)) {
      return fail(QStringLiteral("source or layout cannot be read within size limits"));
    }
    const QByteArray sourceHash = hash(sourceBytes), layoutHash = hash(layoutBytes);
    const QString sourceIdentity = QFileInfo(sourcePath).absoluteFilePath(), layoutIdentity = QFileInfo(layoutPath).absoluteFilePath();
    static thread_local Cache cache;
    if (cache.step == step && cache.sourcePath == sourceIdentity && cache.layoutPath == layoutIdentity &&
        cache.sourceHash == sourceHash && cache.layoutHash == layoutHash && cache.original == original && !cache.rendered.isNull()) {
      return cache.rendered;
    }
    QJsonParseError parseError;
    const auto document = QJsonDocument::fromJson(layoutBytes, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) return fail(QStringLiteral("invalid layout JSON"));
    const auto root = document.object();
    int schemaVersion = 0;
    if (!integer(root.value(QStringLiteral("schema_version")), 1, 1, &schemaVersion) ||
        !dimensions(root.value(QStringLiteral("native_dimensions_px"))) ||
        root.value(QStringLiteral("coordinate_space")).toString() != QStringLiteral("native_image_pixels") ||
        root.value(QStringLiteral("apply_before_existing_resize")) != QJsonValue(true) ||
        root.value(QStringLiteral("source_pngs_are_immutable")) != QJsonValue(true) || !root.value(QStringLiteral("steps")).isArray()) {
      return fail(QStringLiteral("unsupported layout contract"));
    }
    const auto pages = root.value(QStringLiteral("steps")).toArray();
    if (pages.size() != kSteps) return fail(QStringLiteral("layout must contain all 19 steps"));
    for (int index = 0; index < pages.size(); ++index) {
      int pageIndex = -1;
      if (!pages[index].isObject() || !integer(pages[index].toObject().value(QStringLiteral("step")), index, index, &pageIndex)) {
        return fail(QStringLiteral("layout step order is invalid"));
      }
    }
    const auto page = pages[step].toObject();
    const QString asset = QStringLiteral("step%1.png").arg(step);
    const QString expectedHash = page.value(QStringLiteral("source_sha256")).toString();
    if (page.value(QStringLiteral("asset")).toString() != asset || QFileInfo(sourcePath).fileName() != asset ||
        !dimensions(page.value(QStringLiteral("dimensions_px"))) || !sha256(expectedHash) ||
        sourceHash != expectedHash.toLatin1().toLower()) return fail(QStringLiteral("source PNG identity differs from layout"));
    const QImage verified = QImage::fromData(sourceBytes, "PNG");
    if (verified.isNull() || verified.size() != original.size() || verified != original) {
      return fail(QStringLiteral("input image differs from verified PNG bytes"));
    }
    if (!page.value(QStringLiteral("background_covers")).isArray() || !page.value(QStringLiteral("blocks")).isArray() ||
        !page.value(QStringLiteral("preserve_regions")).isArray()) return fail(QStringLiteral("missing page paint arrays"));
    QVector<Cover> covers;
    QVector<Block> blocks;
    QVector<Preserve> protectedRegions;
    QSet<int> indices;
    for (const auto &value : page.value(QStringLiteral("background_covers")).toArray()) {
      if (!value.isObject()) return fail(QStringLiteral("invalid background cover"));
      const auto cover = value.toObject(); Cover item;
      if (!rectangle(cover.value(QStringLiteral("rect_xywh")), &item.rect) || !color(cover.value(QStringLiteral("fill_color")), &item.color) ||
          !opaque(cover.value(QStringLiteral("opacity")))) return fail(QStringLiteral("invalid background cover geometry/color"));
      covers.append(item);
    }
    const auto entries = page.value(QStringLiteral("blocks")).toArray();
    if (entries.isEmpty() || entries.size() > 128) return fail(QStringLiteral("invalid text block count"));
    QSet<QString> consentChoices;
    for (const auto &value : entries) {
      if (!value.isObject()) return fail(QStringLiteral("invalid text block"));
      const auto object = value.toObject();
      int index = -1;
      if (!integer(object.value(QStringLiteral("text_index")), 0, 127, &index) || indices.contains(index) || !object.value(QStringLiteral("draw")).isBool()) {
        return fail(QStringLiteral("invalid or duplicate block index/draw flag"));
      }
      indices.insert(index);
      if (!object.value(QStringLiteral("draw")).toBool()) continue;  // No cover and no text.
      Block block; block.index = index;
      QString fontError;
      if (!rectangle(object.value(QStringLiteral("source_text_cover_rect_xywh")), &block.cover) ||
          !rectangle(object.value(QStringLiteral("text_rect_xywh")), &block.rect) ||
          !color(object.value(QStringLiteral("cover_fill_color")), &block.background) || !color(object.value(QStringLiteral("text_color")), &block.color) ||
          !opaque(object.value(QStringLiteral("cover_opacity"))) || !font(object, &block.font, &fontError)) {
        return fail(QStringLiteral("block %1: invalid geometry/color/font %2").arg(index).arg(fontError));
      }
      block.text = object.value(QStringLiteral("text_ru")).toString();
      if (block.text.trimmed().isEmpty() || block.text.size() > 8192 || block.text.contains(QChar(0))) return fail(QStringLiteral("empty or invalid Russian text"));
      const QString alignment = object.value(QStringLiteral("alignment")).toString();
      if (alignment == QStringLiteral("left_top")) block.flags = Qt::AlignLeft | Qt::AlignTop;
      else if (alignment == QStringLiteral("center")) block.flags = Qt::AlignCenter;
      else return fail(QStringLiteral("unsupported text alignment"));
      if (!object.value(QStringLiteral("line_wrap")).isBool()) return fail(QStringLiteral("invalid wrap flag"));
      if (object.value(QStringLiteral("line_wrap")).toBool()) block.flags |= Qt::TextWordWrap;
      const QFontMetrics metrics(block.font);
      const QRect bounds = metrics.boundingRect(block.rect, block.flags, block.text);
      if (bounds.width() > block.rect.width() || bounds.height() > block.rect.height() ||
          !block.rect.contains(bounds)) return fail(QStringLiteral("block %1: text does not fit; no clipping/eliding substitute").arg(index));
      for (const uint character : block.text.toUcs4()) {
        if (!QChar::isSpace(character) && !metrics.inFontUcs4(character)) return fail(QStringLiteral("block %1: font lacks a glyph").arg(index));
      }
      if (step == 9 && !consentBlock(object, block)) return fail(QStringLiteral("step 9 consent label/position metadata differs"));
      if (step == 9 && !object.value(QStringLiteral("choice")).toString().isEmpty()) {
        const QString choice = object.value(QStringLiteral("choice")).toString();
        if (consentChoices.contains(choice)) return fail(QStringLiteral("duplicate step 9 consent choice"));
        consentChoices.insert(choice);
      }
      blocks.append(block);
    }
    if (blocks.isEmpty() || (step == 9 && consentChoices.size() != 2)) return fail(QStringLiteral("missing enabled text or consent labels"));
    for (const auto &value : page.value(QStringLiteral("preserve_regions")).toArray()) {
      if (!value.isObject()) return fail(QStringLiteral("invalid preserve region"));
      const auto object = value.toObject(); Preserve region;
      if (!rectangle(object.value(QStringLiteral("rect_xywh")), &region.rect)) return fail(QStringLiteral("invalid preserve rectangle"));
      const auto allowed = object.value(QStringLiteral("allowed_text_block_indices"));
      if (!allowed.isUndefined() && !allowed.isArray()) return fail(QStringLiteral("invalid preserve allowlist"));
      for (const auto &entry : allowed.toArray()) {
        int index = -1;
        if (!integer(entry, 0, 127, &index) || !indices.contains(index)) return fail(QStringLiteral("invalid preserved-region block reference"));
        region.allowed.insert(index);
      }
      protectedRegions.append(region);
    }
    for (const auto &region : protectedRegions) {
      for (const auto &cover : covers) if (cover.rect.intersects(region.rect)) return fail(QStringLiteral("background cover intersects a preserved region"));
      for (const auto &block : blocks) if (!region.allowed.contains(block.index) &&
          (block.cover.intersects(region.rect) || block.rect.intersects(region.rect))) return fail(QStringLiteral("text block intersects a preserved region"));
    }
    QImage rendered = original.convertToFormat(QImage::Format_ARGB32);
    QPainter painter(&rendered);
    if (!painter.isActive()) return fail(QStringLiteral("unable to start image painter"));
    painter.setOpacity(1.0);
    for (const auto &cover : covers) painter.fillRect(cover.rect, cover.color);
    for (const auto &block : blocks) painter.fillRect(block.cover, block.background);
    for (const auto &block : blocks) {
      painter.save();
      painter.setClipRect(block.rect, Qt::ReplaceClip);
      painter.setFont(block.font); painter.setPen(block.color);
      painter.drawText(block.rect, block.flags, block.text);
      painter.restore();
    }
    painter.end();
    cache.sourcePath = sourceIdentity; cache.layoutPath = layoutIdentity;
    cache.sourceHash = sourceHash; cache.layoutHash = layoutHash; cache.step = step;
    cache.original = original; cache.rendered = rendered;
    return rendered;
  } catch (...) {
    return fail(QStringLiteral("overlay exception; original image retained"));
  }
}
