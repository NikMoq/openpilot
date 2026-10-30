/*
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.h"

#include <algorithm>
#include <QDateTime>
#include <QTimer>
#include <QRegularExpression>
#include <QUrl>

#include "selfdrive/ui/sunnypilot/qt/util.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/camera_error_ru.h"

namespace {
constexpr const char *SPEED_CAMERA_DEFAULT_URL = "https://speedcamonline.ru/cg77/Rus/comment/";

// Display only: persisted updater protocol values stay unchanged.
QString cameraStatus(const QString &status) {
  if (status.isEmpty() || status == "not downloaded") return QObject::tr("not downloaded");
  if (status == "update requested") return QObject::tr("update requested");
  if (status == "downloading") return QObject::tr("downloading");
  if (status == "ready") return QObject::tr("ready");
  if (status == "error") return QObject::tr("error");
  static const QRegularExpression retry("^retrying in ([0-9]+)s \\(([0-9]+)/([0-9]+)\\)$");
  const auto match = retry.match(status);
  if (match.hasMatch()) return QObject::tr("retrying in %1s (%2/%3)").arg(match.captured(1), match.captured(2), match.captured(3));
  return status;  // Preserve unknown technical diagnostics.
}

QString cameraRoadStatus(const QString &status) {
  if (status.isEmpty() || status == "missing" || status.contains("missing")) return QObject::tr("Offline road geometry required");
  if (status.contains("stale")) return QObject::tr("Update offline road geometry");
  if (status.contains("gps") || status == "position_jump") return QObject::tr("Waiting for reliable GPS");
  if (status.contains("ambiguous")) return QObject::tr("Road association uncertain");
  if (status == "route_associated" || status == "ready" || status == "no_camera" || status == "route_passed") return QObject::tr("Ready");
  return QObject::tr("Offline road geometry unavailable");
}

void cameraButtonSymbols(OptionControlSP *control) {
  // Inter contains these glyphs; the full-width variants need a system fallback font.
  for (auto *button : control->findChildren<QPushButton *>()) {
    if (button->text() == "－") button->setText("−");
    if (button->text() == "＋") button->setText("+");
  }
}

QString cameraTimestamp(const std::string &value) {
  bool ok = false;
  const double seconds = QString::fromStdString(value).toDouble(&ok);
  if (!ok || !std::isfinite(seconds) || seconds <= 0 || seconds > 4102444800.0) return QObject::tr("Never");
  return QDateTime::fromSecsSinceEpoch(static_cast<qint64>(seconds)).toLocalTime().toString("yyyy-MM-dd HH:mm");
}
}

SpeedLimitSettings::SpeedLimitSettings(QWidget *parent) : QStackedWidget(parent) {
  if (params.get("SpeedCameraAlertDistanceMeters").empty()) params.put("SpeedCameraAlertDistanceMeters", "1000");
  bool reaction_ok = false;
  const int reaction = QString::fromStdString(params.get("SpeedCameraReactionDistance")).toInt(&reaction_ok);
  if (!reaction_ok || reaction < 50 || reaction > 1000 || reaction % 50 != 0) params.put("SpeedCameraReactionDistance", "300");
  if (params.get("SpeedCameraDatabaseUpdatePeriodHours").empty()) params.put("SpeedCameraDatabaseUpdatePeriodHours", "24");
  const std::string camera_url = params.get("SpeedCameraDatabaseUrl");
  if (camera_url.empty() || camera_url == "https://speedcamonline.ru/igo/Rus/" ||
      camera_url == "https://radarbase.info/export/cache/RU/main_extended.json") {
    params.put("SpeedCameraDatabaseUrl", SPEED_CAMERA_DEFAULT_URL);
  }

  subPanelFrame = new QFrame();
  QVBoxLayout *subPanelLayout = new QVBoxLayout(subPanelFrame);
  subPanelLayout->setContentsMargins(0, 0, 0, 0);
  subPanelLayout->setSpacing(0);

  // Back button
  PanelBackButton *back = new PanelBackButton(tr("Back"));
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  subPanelLayout->addWidget(back, 0, Qt::AlignLeft);

  subPanelLayout->addSpacing(20);

  ListWidgetSP *list = new ListWidgetSP(this, false);

  speedLimitPolicyScreen = new SpeedLimitPolicy(this);

  std::vector<QString> speed_limit_mode_texts{
    tr(SpeedLimitModeTexts[static_cast<int>(SpeedLimitMode::OFF)].toStdString().c_str()),
    tr(SpeedLimitModeTexts[static_cast<int>(SpeedLimitMode::INFORMATION)].toStdString().c_str()),
    tr(SpeedLimitModeTexts[static_cast<int>(SpeedLimitMode::WARNING)].toStdString().c_str()),
    tr(SpeedLimitModeTexts[static_cast<int>(SpeedLimitMode::ASSIST)].toStdString().c_str())
  };
  speed_limit_mode_settings = new ButtonParamControlSP(
    "SpeedLimitMode",
    tr("Speed Limit"),
    "",
    "",
    speed_limit_mode_texts,
    380);
  list->addItem(speed_limit_mode_settings);

  list->addItem(horizontal_line());
  list->addItem(vertical_space());

  speedLimitSource = new PushButtonSP(tr("Customize Source"));
  connect(speedLimitSource, &QPushButton::clicked, [&]() {
    speedLimitScroller->setLastScrollPosition();
    setCurrentWidget(speedLimitPolicyScreen);
    speedLimitPolicyScreen->refresh();
  });
  connect(speedLimitPolicyScreen, &SpeedLimitPolicy::backPress, [&]() {
    speedLimitScroller->restoreScrollPosition();
    setCurrentWidget(subPanelFrame);
    showEvent(new QShowEvent());
  });

  speedLimitSource->setFixedWidth(720);
  list->addItem(speedLimitSource);

  list->addItem(vertical_space(0));
  list->addItem(horizontal_line());

  QFrame *offsetFrame = new QFrame(this);
  QVBoxLayout *offsetLayout = new QVBoxLayout(offsetFrame);

  std::vector<QString> speed_limit_offset_texts{
    tr(SpeedLimitOffsetTypeTexts[static_cast<int>(SpeedLimitOffsetType::NONE)].toStdString().c_str()),
    tr(SpeedLimitOffsetTypeTexts[static_cast<int>(SpeedLimitOffsetType::FIXED)].toStdString().c_str()),
    tr(SpeedLimitOffsetTypeTexts[static_cast<int>(SpeedLimitOffsetType::PERCENT)].toStdString().c_str())
  };
  speed_limit_offset_settings = new ButtonParamControlSP(
    "SpeedLimitOffsetType",
    tr("Speed Limit Offset"),
    "",
    "",
    speed_limit_offset_texts,
    500);

  offsetLayout->addWidget(speed_limit_offset_settings);

  speed_limit_offset = new OptionControlSP(
    "SpeedLimitValueOffset",
    "",
    "",
    "",
    {-30, 30}
    );
  offsetLayout->addWidget(speed_limit_offset);

  list->addItem(offsetFrame);

  list->addItem(horizontal_line());
  list->addItem(vertical_space());

  speed_camera_source = new ButtonControlSP(tr("Database Source"), tr("EDIT"),
    tr("CityGuide 7.7: all camera types and all Russian regions. Updates use SpeedCamOnline, with a verified backup copy from this repository when the site is unreachable.\nDefault: %1. Custom HTTPS sources do not use the backup. Source and direction data are retained; automatic control depends on camera type support.")
      .arg(QString::fromUtf8(SPEED_CAMERA_DEFAULT_URL)));
  connect(speed_camera_source, &ButtonControlSP::clicked, this, [this]() {
    const QString current = QString::fromStdString(params.get("SpeedCameraDatabaseUrl"));
    const QString selection = InputDialog::getText(tr("Camera database URL"), this,
      tr("SpeedCamOnline CityGuide 7.7 default: %1. Enter another HTTPS source to customize.")
        .arg(QString::fromUtf8(SPEED_CAMERA_DEFAULT_URL)), false, 8, current);
    if (selection.isEmpty()) return;
    const QUrl url(selection.trimmed());
    if (url.scheme() != "https" || url.host().isEmpty() || !url.userInfo().isEmpty()) {
      ConfirmationDialog::alert(tr("Enter a valid HTTPS database URL without credentials."), this);
      return;
    }
    params.put("SpeedCameraDatabaseUrl", url.toString().toStdString());
    params.put("SpeedCameraDatabaseStatus", "update requested");
    refresh();
  });
  list->addItem(speed_camera_source);

  std::vector<QString> speed_camera_interval_texts{
    tr("Manual"), tr("6 h"), tr("24 h"), tr("168 h")
  };
  speed_camera_interval = new MultiButtonControlSP(
    tr("Automatic Updates"),
    tr("Choose how often the camera database is refreshed."), "",
    speed_camera_interval_texts, 300);
  connect(speed_camera_interval, &MultiButtonControlSP::buttonClicked, this, [this](int id) {
    static constexpr const char *periods[] = {"0", "6", "24", "168"};
    params.put("SpeedCameraDatabaseUpdatePeriodHours", periods[std::clamp(id, 0, 3)]);
  });
  list->addItem(speed_camera_interval);

  speed_camera_reaction_distance = new OptionControlSP(
    "SpeedCameraReactionDistance", tr("Camera Reaction Distance"),
    tr("Begin reacting at this distance before the camera. This is not a guarantee of reaching the target speed before it."), "",
    {50, 1000}, 50);
  cameraButtonSymbols(speed_camera_reaction_distance);
  connect(speed_camera_reaction_distance, &OptionControlSP::updateLabels, this, [this]() {
    speed_camera_reaction_distance->setLabel(tr("%1 m").arg(QString::fromStdString(params.get("SpeedCameraReactionDistance"))));
  });
  list->addItem(speed_camera_reaction_distance);

  speed_camera_status = new LabelControlSP(tr("Database Status"));
  speed_camera_count = new LabelControlSP(tr("Camera Count"));
  speed_camera_updated = new LabelControlSP(tr("Last Update"));
  speed_camera_version = new LabelControlSP(tr("Database Version / Hash"));
  speed_camera_error = new LabelControlSP(tr("Last Error"));
  speed_camera_graph_status = new LabelControlSP(tr("Road Matching Status"));
  list->addItem(speed_camera_status);
  list->addItem(speed_camera_count);
  list->addItem(speed_camera_updated);
  list->addItem(speed_camera_version);
  list->addItem(speed_camera_error);
  list->addItem(speed_camera_graph_status);

  speed_camera_update = new PushButtonSP(tr("Update Database"), 720);
  connect(speed_camera_update, &QPushButton::clicked, this, [this]() {
    params.put("SpeedCameraDatabaseStatus", "update requested");
    params.remove("SpeedCameraDatabaseError");
    refresh();
  });
  list->addItem(speed_camera_update);

  connect(speed_limit_mode_settings, &ButtonParamControlSP::buttonClicked, this, &SpeedLimitSettings::refresh);
  connect(speed_limit_offset, &OptionControlSP::updateLabels, this, &SpeedLimitSettings::refresh);
  connect(speed_limit_offset_settings, &ButtonParamControlSP::showDescriptionEvent, speed_limit_offset, &OptionControlSP::showDescription);
  connect(speed_limit_offset_settings, &ButtonParamControlSP::buttonClicked, this, &SpeedLimitSettings::refresh);

  speed_camera_refresh_timer = new QTimer(this);
  speed_camera_refresh_timer->setInterval(1000);
  connect(speed_camera_refresh_timer, &QTimer::timeout, this, &SpeedLimitSettings::refresh);
  speed_camera_refresh_timer->start();

  refresh();
  speedLimitScroller = new ScrollViewSP(list, this);
  subPanelLayout->addWidget(speedLimitScroller);
  addWidget(subPanelFrame);
  addWidget(speedLimitPolicyScreen);
  setCurrentWidget(subPanelFrame);
}

void SpeedLimitSettings::refresh() {
  bool is_release = params.getBool("IsReleaseSpBranch");
  bool is_metric_param = params.getBool("IsMetric");
  SpeedLimitMode speed_limit_mode_param = static_cast<SpeedLimitMode>(std::atoi(params.get("SpeedLimitMode").c_str()));
  SpeedLimitOffsetType offset_type_param = static_cast<SpeedLimitOffsetType>(std::atoi(params.get("SpeedLimitOffsetType").c_str()));
  QString offsetLabel = QString::fromStdString(params.get("SpeedLimitValueOffset"));

  bool sla_available;
  auto cp_bytes = params.get("CarParamsPersistent");
  auto cp_sp_bytes = params.get("CarParamsSPPersistent");
  if (!cp_bytes.empty() && !cp_sp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    AlignedBuffer aligned_buf_sp;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    capnp::FlatArrayMessageReader cmsg_sp(aligned_buf_sp.align(cp_sp_bytes.data(), cp_sp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
    cereal::CarParamsSP::Reader CP_SP = cmsg_sp.getRoot<cereal::CarParamsSP>();

    bool has_longitudinal_control = hasLongitudinalControl(CP);
    bool has_icbm = hasIntelligentCruiseButtonManagement(CP_SP);

    /*
     * Speed Limit Assist is available when:
     * - has_longitudinal_control or has_icbm, and
     * - is not a release branch or not a disallowed brand, and
     * - is not always disallowed
     */
    bool sla_disallow_in_release = CP.getBrand() == "tesla" && is_release;
    bool sla_always_disallow = CP.getBrand() == "rivian";
    sla_available = (has_longitudinal_control || has_icbm) && !sla_disallow_in_release && !sla_always_disallow;
    speed_camera_control_available = has_icbm && sla_available;

    if (!sla_available && speed_limit_mode_param == SpeedLimitMode::ASSIST) {
      params.put("SpeedLimitMode", std::to_string(static_cast<int>(SpeedLimitMode::WARNING)));
    }
    if (!speed_camera_control_available && params.getBool("SpeedCameraControlEnabled")) {
      params.putBool("SpeedCameraControlEnabled", false);
    }
  } else {
    sla_available = false;
    speed_camera_control_available = false;
  }

  speed_limit_mode_settings->setDescription(modeDescription(speed_limit_mode_param));
  speed_limit_offset->setDescription(offsetDescription(offset_type_param));

  if (offset_type_param == SpeedLimitOffsetType::PERCENT) {
    offsetLabel += "%";
  } else if (offset_type_param == SpeedLimitOffsetType::FIXED) {
    offsetLabel += QString(" %1").arg(is_metric_param ? tr("km/h") : tr("mph"));
  }

  if (offset_type_param == SpeedLimitOffsetType::NONE) {
    speed_limit_offset->setVisible(false);
  } else {
    speed_limit_offset->setVisible(true);
    speed_limit_offset->setLabel(offsetLabel);
    if (params.get("LanguageSetting") != "main_ru") speed_limit_offset->showDescription();
  }

  if (sla_available) {
    speed_limit_mode_settings->setEnableSelectedButtons(true, convertSpeedLimitModeValues(getSpeedLimitModeValues()));
  } else {
    speed_limit_mode_settings->setEnableSelectedButtons(true, convertSpeedLimitModeValues(
      {SpeedLimitMode::OFF, SpeedLimitMode::INFORMATION, SpeedLimitMode::WARNING}));
  }

  speed_limit_mode_settings->refresh();
  if (params.get("LanguageSetting") != "main_ru") speed_limit_mode_settings->showDescription();
  if (params.get("LanguageSetting") != "main_ru") speed_limit_offset->showDescription();

  const QString status = QString::fromStdString(params.get("SpeedCameraDatabaseStatus"));
  speed_camera_reaction_distance->setLabel(tr("%1 m").arg(QString::fromStdString(params.get("SpeedCameraReactionDistance"))));
  const QString graph_status = QString::fromStdString(params.get("SpeedCameraRoadGraphStatus"));
  speed_camera_graph_status->setText(cameraRoadStatus(graph_status));
  speed_camera_status->setText(cameraStatus(status));
  const QString count = QString::fromStdString(params.get("SpeedCameraDatabaseCount"));
  speed_camera_count->setText(count.isEmpty() ? "0" : count);
  speed_camera_updated->setText(cameraTimestamp(params.get("SpeedCameraDatabaseUpdatedAt")));
  speed_camera_version->setText(QString::fromStdString(params.get("SpeedCameraDatabaseVersion")));
  const QString source_url = QString::fromStdString(params.get("SpeedCameraDatabaseUrl"));
  speed_camera_source->setValue(source_url == SPEED_CAMERA_DEFAULT_URL ? tr("SpeedCamOnline") : QUrl(source_url).host());
  const QString error = QString::fromStdString(params.get("SpeedCameraDatabaseError"));
  speed_camera_error->setText(error.isEmpty() ? tr("None") : CameraErrorRu::present(error, QString::fromStdString(params.get("LanguageSetting"))));
  speed_camera_update->setEnabled(status != "downloading" && !status.startsWith("retrying"));

  speedLimitSource->setVisible(!params.getBool("SpeedCameraControlEnabled"));
  const int period = std::atoi(params.get("SpeedCameraDatabaseUpdatePeriodHours").c_str());
  const int interval_index = period == 6 ? 1 : (period == 24 ? 2 : (period == 168 ? 3 : 0));
  speed_camera_interval->setCheckedButton(interval_index);
}

void SpeedLimitSettings::showEvent(QShowEvent *event) {
  refresh();
}

void SpeedLimitSettings::hideEvent(QHideEvent *event) {
  setCurrentWidget(subPanelFrame);
}
