#pragma once

#include <QPainter>
#include <QPainterPath>
#include <cmath>
#include "selfdrive/ui/qt/util.h"

struct CameraControlOverlay {
  bool enabled = false;
  bool active = false;
  float speedCapMps = 0.0f;
  QString stateText;
  QString reasonText;
  bool manualTakeover = false;
};

// Fixed readable geometry, including split-map views. Keep the entire block
// below the speed header when it cannot fit to the left of the current speed.
inline QRect cameraOverlayRect(const QRect &surface, int height = 470) {
  const int speed_half_width = (QFontMetrics(InterFont(176, QFont::Bold)).horizontalAdvance("888") + 1) / 2;
  const int top = (surface.width() - 1) / 2 - speed_half_width >= 40 + 680 + 24 ? 40 : 330;
  return QRect(surface.left() + 40, surface.top() + top, 680, height);
}

inline void cameraOutlinedText(QPainter &p, const QRect &rect, const QString &text,
                               int size, QColor color = Qt::white,
                               int align = Qt::AlignLeft | Qt::AlignVCenter) {
  const QFont font = InterFont(size, QFont::DemiBold);
  const QFontMetricsF fm(font);
  qreal x = rect.left();
  if (align & Qt::AlignRight) x = rect.right() + 1 - fm.horizontalAdvance(text);
  else if (align & Qt::AlignHCenter) x += (rect.width() - fm.horizontalAdvance(text)) / 2;
  const qreal y = rect.top() + (rect.height() - fm.height()) / 2 + fm.ascent();
  QPainterPath path;
  path.addText(QPointF(x, y), font, text);
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setPen(QPen(QColor(15, 20, 25, 230), 5, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
  p.setBrush(color);
  p.drawPath(path);
  p.fillPath(path, color);
  p.restore();
}

inline bool cameraTargetVisible(const CameraControlOverlay &control) {
  const QString state = control.stateText.trimmed().toLower();
  const QString reason = control.reasonText.trimmed().toLower();
  return control.enabled && control.active && !control.manualTakeover &&
         (state == "approach" || state == "hold") &&
         reason != "gps_unreliable" && reason != "lmd_unreliable" && reason != "path_ambiguous" &&
         std::isfinite(control.speedCapMps) && control.speedCapMps > 0;
}

// Only the caller's fresh warning selection/threshold opens this section.
// An active control cap never opens it early. No distance extrapolation/timer.
// The upper 185 px are filled by the real HUD's cruise and road-limit state.
inline void drawCameraOverlay(QPainter &p, const QRect &surface, bool valid,
                              float distance_m, float limit, bool metric,
                              const CameraControlOverlay &control = {}, bool position_lost = false) {
  const bool valid_distance = valid && std::isfinite(distance_m) && distance_m >= 0;
  if (!valid_distance && !position_lost) return;
  const QRect rect = cameraOverlayRect(surface);
  const QColor color = !valid_distance ? QColor(255, 190, 60) : distance_m <= 100 ? QColor(255, 80, 80)
                       : distance_m <= 300 ? QColor(255, 190, 60) : QColor(Qt::white);
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.translate(rect.topLeft());
  p.setBrush(Qt::NoBrush);
  p.setPen(QPen(QColor(15, 20, 25, 230), 7));
  p.drawRoundedRect(QRect(0, 0, 680, 470), 24, 24);
  p.setPen(QPen(color, 3));
  p.drawRoundedRect(QRect(0, 0, 680, 470), 24, 24);
  p.drawLine(20, 185, 660, 185);
  cameraOutlinedText(p, {24, 202, 632, 52}, QObject::tr("CAMERA"), 46, color);
  const QString unit = metric ? QObject::tr("km/h") : QObject::tr("mph");
  if (position_lost) {
    cameraOutlinedText(p, {24, 266, 632, 100}, QObject::tr("POSITION LOST"), 40, color);
  } else {
    const QString distance = metric
      ? (distance_m < 1000 ? QString::number(qRound(distance_m)) + QObject::tr(" m")
                          : QString::number(distance_m / 1000, 'f', 1) + QObject::tr(" km"))
      : (distance_m < 1609 ? QString::number(qRound(distance_m * 3.28084f)) + QObject::tr(" ft")
                          : QString::number(distance_m / 1609.344f, 'f', 1) + QObject::tr(" mi"));
    const QString raw_limit = std::isfinite(limit) && limit > 0 ? QString::number(qRound(limit)) : "—";
    cameraOutlinedText(p, {24, 266, 180, 100}, raw_limit, 82, color);
    cameraOutlinedText(p, {190, 285, 135, 66}, unit, 32, color);
    cameraOutlinedText(p, {330, 266, 326, 100}, distance, 72, color, Qt::AlignRight | Qt::AlignVCenter);
  }
  const QString target = !position_lost && cameraTargetVisible(control)
    ? QString::number(qRound(control.speedCapMps * (metric ? 3.6f : 2.23694f))) : "—";
  cameraOutlinedText(p, {24, 365, 632, 60}, QObject::tr("ACC TARGET %1 %2").arg(target, unit), 44, color);
  p.restore();
}
