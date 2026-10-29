/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/widgets/drive_stats.h"

#include <QGridLayout>
#include <QVBoxLayout>
#include <QTimer>
#include <QVariant>
#include <QLocale>

#include "common/params.h"

static QLabel* newLabel(const QString& text, const QString &type) {
  QLabel* label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget* parent) : QFrame(parent) {
  metric_ = Params().getBool("IsMetric");

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(50, 50, 50, 60);

  auto add_stats_layouts = [=](const QString &title, StatsLabels& labels) {
    QGridLayout* grid_layout = new QGridLayout;
    grid_layout->setVerticalSpacing(10);
    grid_layout->setContentsMargins(0, 10, 0, 10);

    int row = 0;
    grid_layout->addWidget(newLabel(title, "title"), row++, 0, 1, 3);
    grid_layout->addItem(new QSpacerItem(0, 30), row++, 0, 1, 1);

    grid_layout->addWidget(labels.routes = newLabel("0", "number"), row, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance = newLabel("0", "number"), row, 1, Qt::AlignLeft);
    grid_layout->addWidget(labels.hours = newLabel("0", "number"), row, 2, Qt::AlignLeft);

    grid_layout->addWidget(newLabel((tr("Drives")), "unit"), row + 1, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance_unit = newLabel(getDistanceUnit(), "unit"), row + 1, 1, Qt::AlignLeft);
    grid_layout->addWidget(newLabel(tr("Hours"), "unit"), row + 1, 2, Qt::AlignLeft);

    main_layout->addLayout(grid_layout);
  };

  add_stats_layouts(tr("ALL TIME"), all_);
  main_layout->addStretch();
  add_stats_layouts(tr("PAST WEEK"), week_);

  source_label_ = newLabel("", "source");
  source_label_->setWordWrap(true);
  source_label_->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
  main_layout->addSpacing(15);
  main_layout->addWidget(source_label_);

  refreshLocal();
  auto *local_timer = new QTimer(this);
  connect(local_timer, &QTimer::timeout, this, [this]() { if (isVisible()) refreshLocal(); });
  local_timer->start(10000);

  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="title"] { font-size: 51px; font-weight: 500; }
    QLabel[type="number"] { font-size: 78px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 51px; font-weight: 300; color: #A0A0A0; }
    QLabel[type="source"] { font-size: 34px; font-weight: 300; color: #A0A0A0; }
  )");
}

void DriveStats::updateStats() {
  const QLocale locale = Params().get("LanguageSetting") == "main_ru" ? QLocale(QLocale::Russian, QLocale::Russia) : QLocale();
  auto decimal = [&](double value, int places) {
    const double scale = places == 1 ? 10.0 : 1.0;
    return locale.toString(qRound64(value * scale) / scale, 'f', places);
  };
  auto number = [](QLabel *label, const QString &value) {
    label->setText(value);
    label->setStyleSheet(QString("font-size: %1px; font-weight: 500;").arg(qMin(78, 360 / qMax(1, value.size()))));
  };
  const bool have_data = have_local_;
  const auto &stats = local_stats_;
  auto update = [&](const DriveStatsData::Totals &totals, bool incomplete_routes, StatsLabels& labels) {
    const double distance = totals.distance_m / (metric_ ? 1000.0 : 1609.344);
    const QString routes = incomplete_routes ? (totals.routes > 0 ? QString::fromUtf8("≥") + locale.toString(totals.routes) : QString::fromUtf8("—")) : locale.toString(totals.routes);
    number(labels.routes, have_data ? routes : QString::fromUtf8("—"));
    number(labels.distance, have_data ? decimal(distance, distance < 100 ? 1 : 0) : QString::fromUtf8("—"));
    labels.distance_unit->setText(getDistanceUnit());
    number(labels.hours, have_data ? decimal(totals.duration_s / 3600.0, 1) : QString::fromUtf8("—"));
  };
  update(stats.all, stats.all_routes_incomplete, all_);
  update(stats.week, stats.week_routes_incomplete, week_);
  source_label_->setText(have_local_ ? tr("Recorded on this device") : tr("No trip data yet"));
}

void DriveStats::refreshLocal() {
  DriveStatsData::Snapshot snapshot;
  // Keep the last verified local snapshot across a transient read failure.
  if (DriveStatsData::loadLocal(snapshot)) {
    local_stats_ = snapshot;
    have_local_ = true;
  }
  updateStats();
}

void DriveStats::showEvent(QShowEvent* event) {
  bool metric = Params().getBool("IsMetric");
  if (metric_ != metric) {
    metric_ = metric;
  }
  refreshLocal();
}
