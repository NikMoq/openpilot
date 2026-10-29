/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/sidebar.h"

#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/sunnypilot/qt/util.h"
#include "selfdrive/ui/sunnypilot/qt/sidebar_temperature.h"
#include "common/params.h"

#include <cmath>

SidebarSP::SidebarSP(QWidget *parent) : Sidebar(parent) {
  // Redirect uiUpdate signal to SidebarSP::updateState instead of Sidebar::updateState
  QObject::disconnect(uiState(), &UIState::uiUpdate, this, &Sidebar::updateState);
  QObject::connect(uiStateSP(), &UIStateSP::uiUpdate, this, &SidebarSP::updateState);
}

void SidebarSP::updateState(const UIStateSP &s) {
  if (!isVisible()) return;
  Sidebar::updateState(s);

  const uint64_t now = nanos_since_boot();
  if (temperature_mode_read_time == 0 || now - temperature_mode_read_time >= 1000000000ULL) {
    bool valid_mode = false;
    const int mode = QString::fromStdString(params.get("SidebarTemperatureMode")).toInt(&valid_mode);
    temperature_mode = valid_mode && mode >= 0 && mode <= 4 ? mode : 0;
    temperature_mode_read_time = now;
  }

  if (temperature_mode != 0) {
    static constexpr const char *device_state = "deviceState";
    const auto &sm = *s.sm;
    const uint64_t received = sm.rcv_time(device_state);
    const bool fresh = received != 0 && now >= received && now - received <= 5000000000ULL;
    float value = std::numeric_limits<float>::quiet_NaN();
    QString label;
    if (temperature_mode == 1) label = tr("CPU");
    if (temperature_mode == 2) label = tr("GPU");
    if (temperature_mode == 3) label = tr("RAM");
    if (temperature_mode == 4) label = tr("MAX");
    if (sm.valid(device_state) && sm.alive(device_state) && fresh) {
      const auto state = sm[device_state].getDeviceState();
      const auto reading = sidebar_temperature::select(temperature_mode, state);
      value = reading.value;
      if (temperature_mode == 4) {
        if (reading.source == sidebar_temperature::Source::CPU) label = tr("CPU");
        if (reading.source == sidebar_temperature::Source::GPU) label = tr("GPU");
        if (reading.source == sidebar_temperature::Source::MEMORY) label = tr("RAM");
      }
    }
    const QString reading = sidebar_temperature::usable(value)
      ? QString::number(std::lround(value)) + QString::fromUtf8("°C")
      : QString::fromUtf8("—");
    // Sidebar::updateState already selected the thermal-status colour.
    setProperty("tempStatus", QVariant::fromValue(ItemStatus{{label, reading}, temp_status.second}));
  }

  ItemStatus sunnylinkStatus;
  auto sl_dongle_id = getSunnylinkDongleId();
  auto last_sunnylink_ping_str = params.get("LastSunnylinkPingTime");
  auto last_sunnylink_ping = std::stoull(last_sunnylink_ping_str.empty() ? "0" : last_sunnylink_ping_str);
  auto elapsed_sunnylink_ping = nanos_since_boot() - last_sunnylink_ping;
  auto sunnylink_enabled = params.getBool("SunnylinkEnabled");

  QString status = tr("DISABLED");
  QColor color = disabled_color;

  if (sunnylink_enabled && last_sunnylink_ping == 0) {
    // If sunnylink is enabled, but we don't have a dongle id, and we haven't received a ping yet, we are registering
    status = sl_dongle_id.has_value() ? tr("OFFLINE") : tr("REGIST...");
    color = sl_dongle_id.has_value() ? warning_color : progress_color;
  } else if (sunnylink_enabled) {
    // If sunnylink is enabled, we are considered online if we have received a ping in the last 80 seconds, else error.
    status = elapsed_sunnylink_ping < 80000000000ULL ? tr("ONLINE") : tr("ERROR");
    color = elapsed_sunnylink_ping < 80000000000ULL ? good_color : danger_color;
  }
  sunnylinkStatus = ItemStatus{{tr("SUNNYLINK"), status}, color};
  setProperty("sunnylinkStatus", QVariant::fromValue(sunnylinkStatus));
}

void SidebarSP::drawSidebar(QPainter &p) {
  Sidebar::drawSidebar(p);
  // metrics
  drawMetric(p, temp_status.first, temp_status.second, 310);
  drawMetric(p, panda_status.first, panda_status.second, 440);
  drawMetric(p, connect_status.first, connect_status.second, 570);
  drawMetric(p, sunnylink_status.first, sunnylink_status.second, 700);
}
