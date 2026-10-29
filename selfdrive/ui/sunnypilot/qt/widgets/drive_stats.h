/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#pragma once

#include <QJsonDocument>
#include <QLabel>

#include "selfdrive/ui/sunnypilot/qt/widgets/drive_stats_data.h"

class DriveStats : public QFrame {
  Q_OBJECT

public:
  explicit DriveStats(QWidget* parent = 0);

private:
  void showEvent(QShowEvent *event) override;
  void updateStats();
  void refreshLocal();
  inline QString getDistanceUnit() const { return metric_ ? tr("KM") : tr("Miles"); }

  bool metric_;
  DriveStatsData::Snapshot local_stats_;
  bool have_local_ = false;
  QLabel *source_label_;
  struct StatsLabels {
    QLabel *routes, *distance, *distance_unit, *hours;
  } all_, week_;

};
