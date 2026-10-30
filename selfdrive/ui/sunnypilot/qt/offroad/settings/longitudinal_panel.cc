/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal_panel.h"

#include "selfdrive/ui/sunnypilot/qt/util.h"

LongitudinalPanel::LongitudinalPanel(QWidget *parent) : QWidget(parent) {
  russian_help = params.get("LanguageSetting") == "main_ru";
  if (params.get("SpeedCameraEnabled").empty()) params.putBool("SpeedCameraEnabled", true);
  if (params.get("SpeedCameraControlEnabled").empty()) params.putBool("SpeedCameraControlEnabled", false);
  setStyleSheet(R"(
    #back_btn {
      font-size: 50px;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #dddddd;
      background-color: #393939;
    }
    #back_btn:pressed {
      background-color:  #4a4a4a;
    }
  )");

  main_layout = new QStackedLayout(this);
  ListWidget *list = new ListWidget(this, false);

  cruisePanelScreen = new QWidget(this);
  QVBoxLayout *vlayout = new QVBoxLayout(cruisePanelScreen);
  vlayout->setContentsMargins(0, 0, 0, 0);

  cruisePanelScroller = new ScrollViewSP(list, this);
  vlayout->addWidget(cruisePanelScroller);

  intelligentCruiseButtonManagement = new ParamControlSP(
    "IntelligentCruiseButtonManagement",
    tr("Intelligent Cruise Button Management (ICBM)"),
    "",
    "",
    this
  );
  QObject::connect(intelligentCruiseButtonManagement, &ParamControlSP::toggleFlipped, this, [=](bool) {
    refresh(offroad);
  });
  list->addItem(intelligentCruiseButtonManagement);


  speedCameraEnabled = new ParamControlSP(
    "SpeedCameraEnabled",
    russian_help ? QStringLiteral("Предупреждения о камерах") : tr("Speed Camera Warnings"),
    russian_help ? QStringLiteral("Показывает предупреждения о камерах из локальной базы. Сам по себе этот переключатель не меняет скорость. Работает независимо от управления по камерам при включённом ICBM. Настройки базы находятся в разделе «Ограничение скорости».")
                 : tr("Show warnings from the local camera database without changing cruise speed. Independent of Camera Speed Control while ICBM is enabled. Database settings are under Speed Limit."), "", this);
  list->addItem(speedCameraEnabled);

  speedCameraControlEnabled = new ParamControlSP(
    "SpeedCameraControlEnabled",
    russian_help ? QStringLiteral("Управление скоростью по камерам") : tr("Camera Speed Control"),
    russian_help ? QStringLiteral("Через штатный ICBM снижает уставку ACC перед камерой с учётом штатной прибавки. Нужны локальная база камер, достоверный GPS и граф дорог. Дорожные ограничения OSM не используются в камерном режиме. Кнопки руля имеют приоритет; восстановление после камеры требует подтверждённого проезда. Дистанция реакции и прибавка настраиваются в разделе «Ограничение скорости».")
                 : tr("Use ICBM to reduce the ACC target before a camera, applying the standard offset once. Requires a local camera database, reliable GPS and road geometry. Road speed limits are excluded in camera mode. Steering-wheel buttons take priority; restoration requires proven passage. Reaction distance and offset are under Speed Limit."), "", this);
  list->addItem(speedCameraControlEnabled);

  dynamicExperimentalControl = new ParamControlSP(
    "DynamicExperimentalControl",
    tr("Dynamic Experimental Control (DEC)"),
    tr("Enable toggle to allow the model to determine when to use sunnypilot ACC or sunnypilot End to End Longitudinal."),
    "",
    this
  );
  list->addItem(dynamicExperimentalControl);

  SmartCruiseControlVision = new ParamControl(
    "SmartCruiseControlVision",
    russian_help ? QStringLiteral("Скорость в поворотах — камера") : tr("Smart Cruise Control - Vision"),
    russian_help ? QStringLiteral("SCC-V оценивает скорость прохождения предстоящих поворотов по траектории, предсказанной видеомоделью. Это не камеры контроля скорости и не дорожные знаки ограничения.")
                 : tr("Use vision path predictions to estimate the appropriate speed to drive through turns ahead."),
    "");
  list->addItem(SmartCruiseControlVision);

  SmartCruiseControlMap = new ParamControl(
    "SmartCruiseControlMap",
    russian_help ? QStringLiteral("Скорость в поворотах — карта") : tr("Smart Cruise Control - Map"),
    russian_help ? QStringLiteral("SCC-M оценивает скорость прохождения поворотов по геометрии дороги на карте. Это отдельная функция: она не берёт дорожные ограничения скорости OSM и не управляет камерами контроля скорости.")
                 : tr("Use map data to estimate the appropriate speed to drive through turns ahead."),
    "");
  list->addItem(SmartCruiseControlMap);

  customAccIncrement = new CustomAccIncrement("CustomAccIncrementsEnabled", tr("Custom ACC Speed Increments"), "", "", this);
  list->addItem(customAccIncrement);

  QObject::connect(uiState(), &UIState::offroadTransition, this, &LongitudinalPanel::refresh);

  speedLimitSettings = new PushButtonSP(tr("Speed Limit"), 750, this);
  connect(speedLimitSettings, &QPushButton::clicked, [&]() {
    cruisePanelScroller->setLastScrollPosition();
    main_layout->setCurrentWidget(speedLimitScreen);
  });
  list->addItem(speedLimitSettings);

  speedLimitScreen = new SpeedLimitSettings(this);
  connect(speedLimitScreen, &SpeedLimitSettings::backPress, [=]() {
    cruisePanelScroller->restoreScrollPosition();
    main_layout->setCurrentWidget(cruisePanelScreen);
  });

  main_layout->addWidget(cruisePanelScreen);
  main_layout->addWidget(speedLimitScreen);
  main_layout->setCurrentWidget(cruisePanelScreen);
  if (russian_help) {
    for (auto *control : findChildren<AbstractControlSP *>()) control->setDescriptionFontSize(34);
    connect(device(), &Device::displayPowerChanged, this, [this](bool on) {
      if (!on) collapseRussianHelp();
    });
  }
  refresh(offroad);
}

void LongitudinalPanel::showEvent(QShowEvent *event) {
  main_layout->setCurrentWidget(cruisePanelScreen);
  refresh(offroad);
}

void LongitudinalPanel::collapseRussianHelp() {
  if (!russian_help) return;
  for (auto *control : findChildren<AbstractControlSP *>()) control->hideDescription();
}

void LongitudinalPanel::hideEvent(QHideEvent *event) {
  collapseRussianHelp();
  main_layout->setCurrentWidget(cruisePanelScreen);
}

void LongitudinalPanel::refresh(bool _offroad) {
  offroad = _offroad;
  const QString icbm_description = tr("When enabled, sunnypilot will attempt to manage the built-in cruise control buttons by emulating button presses for limited longitudinal control.");

  auto cp_bytes = params.get("CarParamsPersistent");
  auto cp_sp_bytes = params.get("CarParamsSPPersistent");
  if (!cp_bytes.empty() && !cp_sp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    AlignedBuffer aligned_buf_sp;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    capnp::FlatArrayMessageReader cmsg_sp(aligned_buf_sp.align(cp_sp_bytes.data(), cp_sp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
    cereal::CarParamsSP::Reader CP_SP = cmsg_sp.getRoot<cereal::CarParamsSP>();

    has_longitudinal_control = hasLongitudinalControl(CP);
    is_pcm_cruise = CP.getPcmCruise();
    has_icbm = hasIntelligentCruiseButtonManagement(CP_SP);

    if (CP_SP.getIntelligentCruiseButtonManagementAvailable() && !has_longitudinal_control) {
      intelligentCruiseButtonManagement->setEnabled(offroad);
      intelligentCruiseButtonManagement->setDescription(icbm_description);
    } else {
      params.remove("IntelligentCruiseButtonManagement");
      intelligentCruiseButtonManagement->setEnabled(false);

      const QString icbm_unavaialble = tr("Intelligent Cruise Button Management is currently unavailable on this platform.");

      QString long_desc = icbm_unavaialble;
      if (has_longitudinal_control) {
        if (CP.getAlphaLongitudinalAvailable()) {
          long_desc = icbm_unavaialble + " " + tr("Disable the sunnypilot Longitudinal Control (alpha) toggle to allow Intelligent Cruise Button Management.");
        } else {
          long_desc = icbm_unavaialble + " " + tr("sunnypilot Longitudinal Control is the default longitudinal control for this platform.");
        }
      }

      intelligentCruiseButtonManagement->setDescription("<b>" + long_desc + "</b><br><br>" + icbm_description);
      if (!russian_help) intelligentCruiseButtonManagement->showDescription();
    }

    if (has_longitudinal_control || has_icbm) {
      // enable Custom ACC Increments when long is available and is not PCM cruise
      customAccIncrement->setEnabled(((has_longitudinal_control && !is_pcm_cruise) || has_icbm) && offroad);
      dynamicExperimentalControl->setEnabled(has_longitudinal_control);
      SmartCruiseControlVision->setEnabled(true);
      SmartCruiseControlMap->setEnabled(true);
    } else {
      params.remove("CustomAccIncrementsEnabled");
      params.remove("DynamicExperimentalControl");
      params.remove("SmartCruiseControlVision");
      params.remove("SmartCruiseControlMap");
      customAccIncrement->setEnabled(false);
      dynamicExperimentalControl->setEnabled(false);
      SmartCruiseControlVision->setEnabled(false);
      SmartCruiseControlMap->setEnabled(false);
    }

    intelligentCruiseButtonManagement->refresh();
    customAccIncrement->refresh();
    dynamicExperimentalControl->refresh();
    SmartCruiseControlVision->refresh();
    SmartCruiseControlMap->refresh();
  } else {
    has_longitudinal_control = false;
    is_pcm_cruise = false;
    has_icbm = false;
    intelligentCruiseButtonManagement->setDescription("<b>" + tr("Start the vehicle to check vehicle compatibility.") + "</br><b><b>" + icbm_description);
  }

  // Camera warnings and control are separate preferences under the ICBM master.
  // Only a confirmed compatible vehicle may clear them when the master is off.
  const bool camera_available = has_icbm;
  if (!cp_bytes.empty() && !cp_sp_bytes.empty() && !camera_available) {
    params.putBool("SpeedCameraEnabled", false);
    params.putBool("SpeedCameraControlEnabled", false);
  }
  speedCameraEnabled->setEnabled(camera_available);
  speedCameraControlEnabled->setEnabled(camera_available);
  speedCameraEnabled->refresh();
  speedCameraControlEnabled->refresh();

  QString accEnabledDescription = tr("Enable custom Short & Long press increments for cruise speed increase/decrease.");
  QString accNoLongDescription = tr("This feature can only be used with sunnypilot longitudinal control enabled.");
  QString accPcmCruiseDisabledDescription = tr("This feature is not supported on this platform due to vehicle limitations.");
  QString onroadOnlyDescription = tr("Start the vehicle to check vehicle compatibility.");

  if (offroad) {
    customAccIncrement->setDescription(russian_help ? accEnabledDescription + "<br><br>" + onroadOnlyDescription : onroadOnlyDescription);
    if (!russian_help) customAccIncrement->showDescription();
  } else {
    if (has_longitudinal_control || has_icbm) {
      if (has_longitudinal_control && is_pcm_cruise) {
        customAccIncrement->setDescription(russian_help ? accEnabledDescription + "<br><br>" + accPcmCruiseDisabledDescription : accPcmCruiseDisabledDescription);
        if (!russian_help) customAccIncrement->showDescription();
      } else {
        customAccIncrement->setDescription(accEnabledDescription);
      }
    } else {
      customAccIncrement->toggleFlipped(false);
      customAccIncrement->setDescription(russian_help ? accEnabledDescription + "<br><br>" + accNoLongDescription : accNoLongDescription);
      if (!russian_help) customAccIncrement->showDescription();
    }
  }

  offroad = _offroad;
}
