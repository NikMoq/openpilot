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
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal_panel.h"
#include "selfdrive/ui/sunnypilot/qt/widgets/drive_stats.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/trips_panel.h"

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
    assert(hud.cameraStatusText().isEmpty());  // Legacy manual flags no longer create a camera override.
    camera.setReason("button_event");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText().isEmpty());
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
  static void verifyPlaceholder() {
    Params prefs;
    prefs.put("SpeedLimitOffsetType", "1");
    prefs.put("SpeedLimitValueOffset", "15");
    HudRendererSP hud;
    hud.is_metric = true;
    hud.speedLimitMode = SpeedLimitMode::OFF;
    hud.speedLimitLast = 20;
    hud.speedLimitLastValid = true;
    capnp::MallocMessageBuilder message;
    auto camera = message.initRoot<cereal::LongitudinalPlanSP::SpeedCameraControl>();
    camera.setEnabled(true);
    camera.setState("inactive");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.speedLimitSignVisible() && !hud.cameraSignVisible && !hud.cameraRemaining);
    assert(!hud.speedLimitLastValid && hud.speedLimitLast == 0);
    assert(hud.speedLimitOffsetText() == "15");
    prefs.put("LanguageSetting", "main_ru");
    assert(hud.cameraStatusText() == QStringLiteral("Камер нет"));
    camera.setReason("gps_unreliable");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText() == QStringLiteral("Слабый GPS"));
    camera.setReason("road_graph_downloading");
    hud.updateCameraPresentation(camera.asReader(), true, true, 3.6f);
    assert(hud.cameraStatusText() == QStringLiteral("Камер нет"));
    prefs.remove("LanguageSetting");
    prefs.put("SpeedLimitOffsetType", "2");
    assert(hud.speedLimitOffsetText() == "15%");
    hud.updateCameraPresentation(camera.asReader(), true, false, 3.6f);
    assert(hud.speedLimitSignVisible() && !hud.cameraSignVisible && !hud.cameraRemaining);
    assert(hud.speedLimitOffsetText() == "15%");
    hud.updateCameraPresentation(camera.asReader(), false, false, 3.6f);
    assert(hud.cameraAwaitRoadPlan && hud.speedLimitSignVisible());
    assert(!hud.speedLimitLastValid && hud.speedLimitLast == 0);
    prefs.put("SpeedLimitOffsetType", "0");
    assert(hud.speedLimitOffsetText().isEmpty());
    prefs.put("SpeedLimitOffsetType", "1");
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
    hud.speedLimitMode = SpeedLimitMode::OFF;  // Camera display must not depend on ordinary SLA mode.
    hud.reversing = hud.hideVEgoUI = hud.roadName = false;
    hud.smartCruiseControlVisionEnabled = hud.smartCruiseControlMapEnabled = false;
    hud.greenLightAlert = hud.leadDepartAlert = hud.standstillTimer = hud.isStandstill = false;
    hud.showTurnSignals = false;
    hud.devUiInfo = 0;
    hud.speedLimitAheadValid = false;
    hud.speedLimitAssistState = cereal::LongitudinalPlanSP::SpeedLimit::AssistState::INACTIVE;
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
    // Exercise the real outer HUD gate. Calling only drawSpeedLimitSigns missed
    // the regression where the sign painter existed but draw() never called it.
    hud.draw(painter, image.rect());
    painter.end();
    assert(image.save(path));
    const QColor ring = image.pixelColor(380, 240);
    if (!(ring.red() > 220 && ring.green() < 40 && ring.blue() < 40))
      qWarning() << path << ring;
    assert(ring.red() > 220 && ring.green() < 40 && ring.blue() < 40);
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
  CameraUiFixture::verifyPlaceholder();
  Params placeholder_params;
  placeholder_params.put("SpeedLimitOffsetType", "1");
  placeholder_params.put("SpeedLimitValueOffset", "15");
  placeholder_params.put("LanguageSetting", "main_ru");
  CameraUiFixture::capture(output + "/placeholder-main-hud.png", 0, 300, "inactive", false, true, 0);
  CameraUiFixture::capture(output + "/weak-gps-main-hud.png", 0, 300, "inactive", false, true, 0, "gps_unreliable");
  placeholder_params.remove("LanguageSetting");
  CameraUiFixture::capture(output + "/active-main-hud.png", 300, 300, "active", true, true, 60 / 3.6f);
  placeholder_params.put("SpeedLimitOffsetType", "2");
  CameraUiFixture::capture(output + "/placeholder-percent-main-hud.png", 0, 300, "inactive", false, false, 0);
  placeholder_params.put("SpeedLimitOffsetType", "1");
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
  // The two camera switches belong to the main Cruise screen, never the subpage.
  assert(panel.findChildren<ParamControlSP *>().isEmpty());
  LongitudinalPanel cruise;
  cruise.setStyleSheet(panel.styleSheet());
  cruise.resize(1800, 1000);
  cruise.show();
  cruise.refresh(true);
  settle();
  auto findToggle = [&](const QString &title) -> ParamControlSP * {
    for (auto *control : cruise.findChildren<ParamControlSP *>()) {
      for (auto *button : control->findChildren<QPushButton *>()) {
        if (button->text() == title) return control;
      }
    }
    return nullptr;
  };
  auto *camera_toggle = findToggle("Camera Speed Control");
  auto *warning_toggle = findToggle("Speed Camera Warnings");
  assert(camera_toggle && warning_toggle && camera_toggle->findChild<ToggleSP *>()->getEnabled() && warning_toggle->findChild<ToggleSP *>()->getEnabled());
  auto *toggle = camera_toggle->findChild<ToggleSP *>();
  assert(toggle);
  for (auto *scroll : cruise.findChildren<QScrollArea *>()) scroll->ensureWidgetVisible(camera_toggle, 0, 30);
  settle();
  QTest::mouseClick(toggle, Qt::LeftButton);
  assert(!params.getBool("SpeedCameraControlEnabled"));
  QTest::mouseClick(toggle, Qt::LeftButton);
  assert(params.getBool("SpeedCameraControlEnabled"));
  assert(params.get("SpeedLimitMode") == "0" && !params.getBool("SpeedCameraEnabled"));
  // Both title taps expand native help without changing their preference.
  for (auto *control : {camera_toggle, warning_toggle}) {
    QPushButton *title = nullptr;
    QLabel *help = nullptr;
    for (auto *button : control->findChildren<QPushButton *>()) {
      if (!button->text().isEmpty()) title = button;
    }
    for (auto *label : control->findChildren<QLabel *>()) {
      if (!label->text().isEmpty()) help = label;
    }
    assert(title && help && !help->isVisible());
    title->click(); settle(); assert(help->isVisible());
    title->click(); settle(); assert(!help->isVisible());
  }
  params.putBool("SpeedCameraEnabled", true);
  params.putBool("IntelligentCruiseButtonManagement", false);
  cruise.refresh(true);
  assert(!camera_toggle->findChild<ToggleSP *>()->getEnabled() && !warning_toggle->findChild<ToggleSP *>()->getEnabled());
  assert(!params.getBool("SpeedCameraControlEnabled") && !params.getBool("SpeedCameraEnabled"));
  params.putBool("IntelligentCruiseButtonManagement", true);
  cruise.refresh(true);
  assert(camera_toggle->findChild<ToggleSP *>()->getEnabled() && warning_toggle->findChild<ToggleSP *>()->getEnabled());
  assert(!params.getBool("SpeedCameraControlEnabled") && !params.getBool("SpeedCameraEnabled"));
  assert(cruise.grab().save(output + "/cruise-camera-switches.png"));
  cruise.hide();
  params.put("LanguageSetting", "main_ru");
  {
    LongitudinalPanel russian_cruise;
    russian_cruise.setStyleSheet(panel.styleSheet());
    russian_cruise.resize(1800, 1000);
    russian_cruise.show(); russian_cruise.refresh(true); settle();
    int help_checks = 0;
    for (auto *control : russian_cruise.findChildren<ParamControlSP *>()) {
      QPushButton *title = nullptr;
      for (auto *button : control->findChildren<QPushButton *>()) {
        if (button->text() == QStringLiteral("Предупреждения о камерах") ||
            button->text() == QStringLiteral("Управление скоростью по камерам")) title = button;
      }
      if (!title) continue;
      QLabel *help = nullptr;
      for (auto *label : control->findChildren<QLabel *>()) {
        if (!label->text().isEmpty()) help = label;
      }
      assert(help && !help->isVisible());
      title->click(); settle(); assert(help->isVisible());
      assert(!params.getBool("SpeedCameraEnabled") && !params.getBool("SpeedCameraControlEnabled"));
      title->click(); settle(); assert(!help->isVisible());
      ++help_checks;
    }
    assert(help_checks == 2);
    assert(russian_cruise.grab().save(output + "/cruise-camera-switches-ru.png"));
    russian_cruise.hide();
  }
  params.remove("LanguageSetting");
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
  // Both stock trip views share the original API-backed DriveStats widget.
  // Feed its actual Qt response slot, with no dongle/request object or network.
  params.remove("DongleId");
  const QString trip_response = R"({"all":{"routes":12,"distance":100,"minutes":240},"week":{"routes":3,"distance":20,"minutes":60}})";
  auto verify_stock_stats = [&](DriveStats *stats) {
    assert(stats);
    assert(QMetaObject::invokeMethod(stats, "parseResponse", Qt::DirectConnection,
                                    Q_ARG(QString, trip_response), Q_ARG(bool, true)));
    settle();  // Let the stock layout resize labels after the response.
    QStringList values;
    for (auto *label : stats->findChildren<QLabel *>()) {
      assert(label->property("type").toString() != "source");
      if (label->property("type").toString() == "number") {
        values << label->text();
        assert(label->width() >= label->fontMetrics().horizontalAdvance(label->text()));
      }
    }
    assert(values == QStringList({"12", "160", "4", "3", "32", "1"}));
  };
  DriveStats home_stats;
  home_stats.setStyleSheet(home_stats.styleSheet() + " QLabel { color: white; }");
  home_stats.setFont(QFont("Inter"));
  home_stats.resize(900, 900);
  home_stats.show(); settle();
  verify_stock_stats(&home_stats);
  assert(home_stats.grab().save(output + "/stock-home-trip-statistics.png"));
  home_stats.hide();
  QStackedWidget stock_trips_canvas;
  stock_trips_canvas.setStyleSheet(panel.styleSheet());
  stock_trips_canvas.setFont(QFont("Inter"));
  auto *stock_trips = new TripsPanel(&stock_trips_canvas);
  stock_trips_canvas.addWidget(stock_trips);
  stock_trips_canvas.resize(1800, 1000);
  stock_trips_canvas.show(); settle();
  verify_stock_stats(stock_trips->findChild<DriveStats *>());
  assert(stock_trips_canvas.grab().save(output + "/stock-trips-panel.png"));
  stock_trips_canvas.hide();

  QFile result(output + "/result.json");
  assert(result.open(QIODevice::WriteOnly));
  result.write(QJsonDocument(QJsonObject{{"actual_native_sign", true}, {"same_sign_offset_preserved", true},
                 {"native_reaction_buttons_50_1000_step50", true}, {"native_toggle_persistence", true},
                 {"passed_stale_no_osm_fallback", true}}).toJson());
  return 0;
}
