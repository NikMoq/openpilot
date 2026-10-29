// cam native QA: real HudRendererSP sign and SpeedLimitSettings, isolated Params.
#include <QApplication>
#include <QDir>
#include <QScrollArea>
#include <QScrollBar>
#include <QPushButton>
#include <QLabel>
#include <QTimer>
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QFontDatabase>
#include <QtTest/QTest>
#include <QFile>
#include <capnp/message.h>
#include <capnp/serialize.h>
#include <cassert>
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/sunnypilot/qt/onroad/hud.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.h"

class CameraUiFixture {
public:
  static void verifyTransitions() {
    HudRendererSP hud;
    hud.is_metric = true;
    hud.speedLimitLast = 20;  // Simulate a cached OSM limit before camera mode.
    hud.speedLimitLastValid = true;
    capnp::MallocMessageBuilder message;
    auto camera = message.initRoot<cereal::LongitudinalPlanSP::SpeedCameraControl>();
    camera.setEnabled(true);
    camera.setEventId("camera-60");
    camera.setRawLimit(60 / 3.6f);
    camera.setAppliedOffset(15 / 3.6f);
    camera.setReactionDistance(300);
    camera.setState("active");
    camera.setCountdownValid(true);
    camera.setDistance(300);
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraRemaining == 300 && std::abs(hud.speedLimitLast - 60) < .01);
    assert(std::abs(hud.speedLimitFinalLast - 75) < .01);
    camera.setDistance(200);
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraRemaining == 200);
    camera.setState("driverOverride"); camera.setManualTakeover(true);
    camera.setReason("awaiting_driver_feedback");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText() == "Waiting for ACC response");
    camera.setReason("button_event");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText() == "Driver override");
    assert(hud.cameraSignVisible && hud.speedCameraControlManualTakeover);
    assert(std::abs(hud.speedLimitOffset - 15) < .01);
    camera.setState("active"); camera.setManualTakeover(false); camera.setReason("camera_limit");
    camera.setRawLimit(20 / 3.6f);
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(std::abs(hud.speedLimitFinalLast - 35) < .01 && hud.cameraStatusText().isEmpty());
    camera.setAppliedOffset(0);
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText() == "Below ACC minimum");
    camera.setState("passed");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(!hud.cameraSignVisible && !hud.cameraRemaining && !hud.speedLimitLastValid && hud.speedLimitLast == 0);
    camera.setState("active"); camera.setReactionDistance(49); camera.setDistance(300);
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraRemaining == 300);  // Invalid persisted setting defaults to 300.
    hud.updateCameraPresentation(camera.asReader(), true, false, 3.6f);
    assert(!hud.cameraSignVisible && !hud.cameraRemaining);
    hud.updateCameraPresentation(camera.asReader(), false, true, 3.6f);
    assert(hud.cameraAwaitRoadPlan && !hud.speedLimitLastValid && hud.speedLimitLast == 0);
    camera.setEnabled(false);
    hud.updateCameraPresentation(camera.asReader(), false, false, 3.6f);
    assert(hud.cameraAwaitRoadPlan);  // A stale road-mode frame cannot restore OSM.
    hud.speedLimitLast = 20; hud.speedLimitLastValid = true;
    hud.updateCameraPresentation(camera.asReader(), false, true, 3.6f);
    assert(!hud.cameraAwaitRoadPlan && hud.speedLimitLast == 20);
  }
  static void capture(const QString &path, double distance, int threshold, const char *state,
                      bool countdown, bool fresh, float raw = 20 / 3.6f, const char *reason = "", float offset = 15 / 3.6f) {
    HudRendererSP hud;
    hud.is_metric = true;
    hud.speed = 74;
    hud.set_speed = 80;
    hud.is_cruise_set = hud.is_cruise_available = true;
    hud.speedLimitAssistActive = false;
    hud.longOverride = hud.carControlEnabled = false;
    hud.pcmCruiseSpeed = true;
    hud.speedLimitMode = SpeedLimitMode::ASSIST;
    capnp::MallocMessageBuilder message;
    auto camera = message.initRoot<cereal::LongitudinalPlanSP::SpeedCameraControl>();
    camera.setEnabled(true);
    camera.setEventId("same-camera-20");
    camera.setRawLimit(raw);
    camera.setAppliedOffset(offset);
    camera.setDistance(distance);
    camera.setReactionDistance(threshold);
    camera.setCountdownValid(countdown);
    camera.setState(state);
    camera.setReason(reason);
    camera.setManualTakeover(std::string(state) == "driverOverride");
    hud.updateCameraPresentation(camera.asReader(), true, fresh, 3.6f);
    const bool passed = std::string(state) == "passed";
    assert(hud.cameraSignVisible == (fresh && !passed && raw > 0));
    assert(bool(hud.cameraRemaining) == (fresh && !passed && countdown && distance <= threshold));
    QImage image(1920, 1080, QImage::Format_ARGB32);
    image.fill(QColor(19, 32, 43));
    QPainter painter(&image);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(QPen(QColor(190, 190, 190), 6));
    painter.drawLine(340, 1080, 870, 430);
    painter.drawLine(1580, 1080, 1050, 430);
    hud.drawSetSpeedSP(painter, image.rect());
    hud.drawCurrentSpeedSP(painter, image.rect());
    QRect sign(280, 45, 200, 204);
    if (hud.cameraSignVisible) hud.drawSpeedLimitSigns(painter, sign);
    hud.drawCameraStatus(painter, sign);
    painter.end();
    assert(image.save(path));
  }
};

static void settle() {
  QEventLoop loop;
  QTimer::singleShot(100, &loop, &QEventLoop::quit);
  loop.exec();
}

int main(int argc, char **argv) {
  // The container fixture never uses installed device/desktop Params.
  qputenv("PARAMS_ROOT", "/tmp/camera-native-ui-qa");
  qputenv("OPENPILOT_PREFIX", "cam-qa");
  const QString output = QDir(argc > 1 ? argv[1] : ".native-build/camera-qa").absolutePath();
  const QString source_root = QDir::currentPath();
  QDir().mkpath(output);
  initApp(argc, argv);
  QApplication app(argc, argv);
  QDir::setCurrent(source_root + "/selfdrive/ui");
  const QDir fonts(source_root + "/selfdrive/assets/fonts");
  for (const auto &font : fonts.entryList({"*.ttf", "*.otf"}, QDir::Files)) {
    QFontDatabase::addApplicationFont(fonts.absoluteFilePath(font));
  }
  CameraUiFixture::verifyTransitions();
  CameraUiFixture::capture(output + "/threshold-1000.png", 1000, 1000, "active", true, true);
  CameraUiFixture::capture(output + "/threshold-300.png", 300, 300, "active", true, true);
  CameraUiFixture::capture(output + "/countdown-149.png", 149, 300, "active", true, true);
  CameraUiFixture::capture(output + "/camera-60-offset-15.png", 200, 300, "active", true, true, 60 / 3.6f);
  CameraUiFixture::capture(output + "/below-acc-minimum.png", 200, 300, "active", true, true, 20 / 3.6f, "camera_limit:below_stock_acc_minimum", 0);
  CameraUiFixture::capture(output + "/threshold-50.png", 50, 50, "active", true, true);
  CameraUiFixture::capture(output + "/approaching.png", 350, 300, "approaching", false, true);
  CameraUiFixture::capture(output + "/manual-override.png", 149, 300, "driverOverride", true, true);
  CameraUiFixture::capture(output + "/awaiting-acc.png", 149, 300, "driverOverride", true, true, 20 / 3.6f, "awaiting_driver_feedback");
  CameraUiFixture::capture(output + "/gps-hold.png", 149, 300, "active", false, true, 20 / 3.6f, "unreliable_hold");
  CameraUiFixture::capture(output + "/passed-no-osm.png", 0, 300, "passed", false, true);
  CameraUiFixture::capture(output + "/stale-hidden.png", 149, 300, "active", false, false);

  Params params;
  params.putBool("IsMetric", true);
  params.put("SpeedCameraReactionDistance", "300");
  params.put("SpeedLimitMode", "0");  // Ordinary road mode stays OFF during camera mode.
  params.put("SpeedLimitOffsetType", "1");
  params.put("SpeedLimitValueOffset", "15");
  params.putBool("IntelligentCruiseButtonManagement", true);
  capnp::MallocMessageBuilder car_message;
  car_message.initRoot<cereal::CarParams>().setBrand("volkswagen");
  auto car_bytes = capnp::messageToFlatArray(car_message);
  params.put("CarParamsPersistent", reinterpret_cast<const char *>(car_bytes.begin()), car_bytes.size() * sizeof(capnp::word));
  capnp::MallocMessageBuilder sp_message;
  sp_message.initRoot<cereal::CarParamsSP>().setIntelligentCruiseButtonManagementAvailable(true);
  auto sp_bytes = capnp::messageToFlatArray(sp_message);
  params.put("CarParamsSPPersistent", reinterpret_cast<const char *>(sp_bytes.begin()), sp_bytes.size() * sizeof(capnp::word));
  params.putBool("SpeedCameraControlEnabled", true);
  params.putBool("SpeedCameraEnabled", false);  // Independent warning preference.
  params.put("SpeedCameraRoadGraphStatus", "ready");

  for (const auto &invalid : {"", "garbage", "300junk", "49", "51", "1001"}) {
    if (std::string(invalid).empty()) params.remove("SpeedCameraReactionDistance");
    else params.put("SpeedCameraReactionDistance", invalid);
    SpeedLimitSettings defaults;
    assert(params.get("SpeedCameraReactionDistance") == "300");
    assert(params.get("SpeedLimitMode") == "0" && !params.getBool("SpeedCameraEnabled"));
  }
  for (const auto &valid : {"50", "1000"}) {
    params.put("SpeedCameraReactionDistance", valid);
    SpeedLimitSettings defaults;
    assert(params.get("SpeedCameraReactionDistance") == valid);
  }
  params.put("SpeedCameraReactionDistance", "300");

  SpeedLimitSettings panel;
  // Apply the exact native SettingsWindowSP ancestor stylesheet, rather than
  // inventing preview styling or relying on the desktop's light Qt palette.
  QFile native_style(source_root + "/selfdrive/ui/sunnypilot/qt/offroad/settings/settings.cc");
  assert(native_style.open(QIODevice::ReadOnly));
  const QString settings_source = QString::fromUtf8(native_style.readAll());
  const QString marker = "  setStyleSheet(R\"(";
  const int style_start = settings_source.indexOf(marker) + marker.size();
  const int style_end = settings_source.indexOf(")\");", style_start);
  assert(style_start >= marker.size() && style_end > style_start);
  panel.setStyleSheet(settings_source.mid(style_start, style_end - style_start));
  QFile cruise_style(source_root + "/selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal_panel.cc");
  assert(cruise_style.open(QIODevice::ReadOnly));
  const QString cruise_source = QString::fromUtf8(cruise_style.readAll());
  const int cruise_start = cruise_source.indexOf(marker) + marker.size();
  const int cruise_end = cruise_source.indexOf(")\");", cruise_start);
  assert(cruise_start >= marker.size() && cruise_end > cruise_start);
  panel.setStyleSheet(panel.styleSheet() + cruise_source.mid(cruise_start, cruise_end - cruise_start));
  // A stand-alone QStackedWidget lacks LongitudinalPanel in its parent chain.
  // Apply that exact ancestor rule directly to its Back controls for QA.
  const QString cruise_rules = cruise_source.mid(cruise_start, cruise_end - cruise_start);
  for (auto *back : panel.findChildren<PanelBackButton *>()) {
    assert(back->objectName() == "back_btn");
    back->setStyleSheet(cruise_rules);
  }
  panel.setFont(QFont("Inter"));
  panel.resize(1800, 1000);
  panel.show();
  settle();
  ParamControlSP *camera_toggle = nullptr;
  for (auto *control : panel.findChildren<ParamControlSP *>()) {
    for (auto *button : control->findChildren<QPushButton *>()) {
      if (button->text() == "Camera Speed Control") camera_toggle = control;
    }
  }
  assert(camera_toggle && camera_toggle->isEnabled());
  auto *toggle = camera_toggle->findChild<ToggleSP *>();
  assert(toggle);
  for (auto *scroll : panel.findChildren<QScrollArea *>()) scroll->ensureWidgetVisible(camera_toggle, 0, 30);
  settle();
  QTest::mouseClick(toggle, Qt::LeftButton);
  assert(!params.getBool("SpeedCameraControlEnabled"));
  QTest::mouseClick(toggle, Qt::LeftButton);
  assert(params.getBool("SpeedCameraControlEnabled"));
  assert(params.get("SpeedLimitMode") == "0" && !params.getBool("SpeedCameraEnabled"));
  for (auto *scroll : panel.findChildren<QScrollArea *>()) scroll->verticalScrollBar()->setValue(0);
  settle();
  assert(panel.grab().save(output + "/settings-native-offset.png"));
  OptionControlSP *reaction = nullptr;
  for (auto *control : panel.findChildren<OptionControlSP *>()) {
    for (auto *button : control->findChildren<QPushButton *>()) {
      if (button->text() == "Camera Reaction Distance") reaction = control;
    }
  }
  assert(reaction);
  QPushButton *plus = nullptr, *minus = nullptr;
  for (auto *button : reaction->findChildren<QPushButton *>()) {
    if (button->text() == "+") plus = button;
    if (button->text() == "−") minus = button;
  }
  assert(plus && minus);
  auto capture = [&](int expected) {
    settle();
    assert(params.get("SpeedCameraReactionDistance") == std::to_string(expected));
    for (auto *scroll : panel.findChildren<QScrollArea *>()) scroll->ensureWidgetVisible(reaction, 0, 30);
    settle();
    assert(panel.grab().save(output + QString("/settings-%1.png").arg(expected)));
  };
  capture(300);
  for (int value = 350; value <= 1000; value += 50) { plus->click(); capture(value); }
  plus->click(); capture(1000);
  for (int value = 950; value >= 50; value -= 50) { minus->click(); capture(value); }
  minus->click(); capture(50);
  assert(params.get("SpeedLimitValueOffset") == "15");
  QFile result(output + "/result.json");
  assert(result.open(QIODevice::WriteOnly));
  result.write(QJsonDocument(QJsonObject{{"actual_native_sign", true}, {"same_sign_offset_preserved", true},
                 {"native_reaction_buttons_50_1000_step50", true}, {"native_toggle_persistence", true},
                 {"passed_stale_no_osm_fallback", true}}).toJson());
  return 0;
}
