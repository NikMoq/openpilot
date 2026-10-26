#pragma once

#include <QPainter>
#include <cmath>
#include "selfdrive/ui/qt/onroad/buttons.h"
#include "selfdrive/ui/qt/util.h"

struct CameraControlOverlay {
  bool enabled = false;
  bool active = false;
  float speedCapMps = 0.0f;
  QString stateText;
  QString reasonText;
  bool manualTakeover = false;
};

inline QRect cameraOverlayRect(const QRect &surface, int height) {
  constexpr int width = 360;
  constexpr int gap = 24;
  // The speed uses Inter 176 at the centre; the wheel is a separate 192 px
  // widget with UI_BORDER_SIZE margins, painted above the HUD.
  const int speed_half_width = (QFontMetrics(InterFont(176, QFont::Bold)).horizontalAdvance("888") + 1) / 2;
  const int left = surface.center().x() + speed_half_width + gap;
  const int right = surface.right() + 1 - UI_BORDER_SIZE - btn_size - gap;
  if (right - left >= width) {
    return QRect(left + (right - left - width) / 2, surface.top() + 45, width, height);
  }
  // Split map/sidebar layouts can leave no horizontal slot. Keep the text at
  // its readable size below the speed/header instead of covering either one.
  return QRect(surface.right() + 1 - UI_BORDER_SIZE - width, surface.top() + 330, width, height);
}

// Shared by the real HUD and the isolated preview: identical pixels for the
// same camera event, without needing vehicle-control messages in the preview.
inline void drawCameraOverlay(QPainter &p, const QRect &surface, bool valid,
                              float distance_m, float limit, bool metric,
                              const CameraControlOverlay &control = {}) {
  const QString state = control.stateText.trimmed().toLower();
  const QString reason = control.reasonText.trimmed().toLower();
  const bool waiting_for_map = reason == "awaiting_map_update";
  const bool show_position_hold = control.enabled && control.active && !control.manualTakeover &&
                                  (reason == "gps_unreliable" || reason == "lmd_unreliable" ||
                                   reason == "path_ambiguous" || waiting_for_map);
  const bool show_target = control.enabled && control.active && !control.manualTakeover &&
                           (state == "approach" || state == "hold") &&
                           (!show_position_hold || waiting_for_map) &&
                           std::isfinite(control.speedCapMps) && control.speedCapMps > 0.0f;
  const bool valid_distance = valid && std::isfinite(distance_m) && distance_m >= 0.0f;
  if (!valid_distance && !show_target && !show_position_hold) return;

  const QString distance = valid_distance ? (metric
    ? (distance_m < 1000 ? QString::number(qRound(distance_m)) + QObject::tr(" m")
                        : QString::number(distance_m / 1000, 'f', 1) + QObject::tr(" km"))
    : (distance_m < 1609 ? QString::number(qRound(distance_m * 3.28084f)) + QObject::tr(" ft")
                        : QString::number(distance_m / 1609.344f, 'f', 1) + QObject::tr(" mi"))) : QString();
  const QColor color = distance_m <= 100 ? QColor(255, 80, 80)
                        : (distance_m <= 300 ? QColor(255, 190, 60) : QColor(Qt::white));
  const QRect rect = cameraOverlayRect(surface, waiting_for_map ? 180 : (show_target ? 165 : 155));
  p.save();
  p.setPen(QPen(show_position_hold ? QColor(255, 190, 60) : color, 3));
  p.setBrush(QColor(0, 0, 0, 190));
  p.drawRoundedRect(rect, 16, 16);
  p.setPen(show_position_hold ? QColor(255, 190, 60) : color);
  p.setFont(InterFont(32, QFont::DemiBold));
  p.drawText(rect.adjusted(16, 8, -16, 0), Qt::AlignTop | Qt::AlignLeft, QObject::tr("CAMERA"));
  if (show_position_hold) {
    p.setFont(InterFont(25, QFont::DemiBold));
    p.drawText(rect.adjusted(16, 52, -16, 0), Qt::AlignTop | Qt::AlignLeft,
               waiting_for_map ? QObject::tr("WAITING FOR MAP") : QObject::tr("POSITION LOST"));
    p.drawText(rect.adjusted(16, 91, -16, 0), Qt::AlignTop | Qt::AlignLeft,
               QObject::tr("HOLDING"));
    if (show_target) {
      p.setFont(InterFont(25, QFont::Normal));
      p.drawText(rect.adjusted(16, 130, -16, 0), Qt::AlignTop | Qt::AlignLeft,
                 QObject::tr("ACC TARGET %1 %2").arg(qRound(control.speedCapMps * (metric ? 3.6f : 2.23694f)))
                                        .arg(metric ? QObject::tr("km/h") : QObject::tr("mph")));
    }
  } else {
    p.setFont(InterFont(27, QFont::Normal));
    const QString camera_limit = valid_distance && std::isfinite(limit) && limit > 0
      ? QString::number(qRound(limit)) + (metric ? QObject::tr(" km/h") : QObject::tr(" mph")) : QString();
    if (!camera_limit.isEmpty()) {
      p.drawText(rect.adjusted(16, 52, -16, 0), Qt::AlignTop | Qt::AlignLeft, camera_limit);
    }
    if (valid_distance) {
      p.drawText(rect.adjusted(16, show_target ? 52 : 91, -16, 0),
                 Qt::AlignTop | (show_target ? Qt::AlignRight : Qt::AlignLeft), distance);
    }
    if (show_target) {
      // The active ACC target may belong to a different camera than the nearest warning above.
      p.drawLine(rect.left() + 16, rect.top() + 99, rect.right() - 16, rect.top() + 99);
      p.setFont(InterFont(25, QFont::Normal));
      p.drawText(rect.adjusted(16, 113, -16, 0), Qt::AlignTop | Qt::AlignLeft,
                 QObject::tr("ACC TARGET %1 %2").arg(qRound(control.speedCapMps * (metric ? 3.6f : 2.23694f)))
                                            .arg(metric ? QObject::tr("km/h") : QObject::tr("mph")));
    }
  }
  p.restore();
}
