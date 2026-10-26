// Exercises the actual native SpeedLimitSettings widget in an isolated Params root.
#include <QApplication>
#include <QDir>
#include <QScrollArea>
#include <QScrollBar>
#include <QTimer>
#include <QTranslator>
#include <QPushButton>
#include <QLabel>
#include <QCommandLineParser>
#include <QDateTime>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFontDatabase>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <cstdio>
#include <algorithm>
#include <QSysInfo>
#include <capnp/dynamic.h>

#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.h"
#include "selfdrive/ui/sunnypilot/qt/onroad/camera_warning.h"
#include "selfdrive/ui/sunnypilot/qt/onroad/hud.h"
#include "selfdrive/ui/sunnypilot/qt/onroad/buttons.h"

static bool verifyCameraSetting(SpeedLimitSettings &panel, const QString &output, const QString &title,
                                const char *key, const QString &format, int minimum, int maximum, int step,
                                const QString &prefix, int sample) {
  auto findControl = [&](SpeedLimitSettings &settings) -> OptionControlSP * {
    for (auto *control : settings.findChildren<OptionControlSP *>()) {
      for (auto *button : control->findChildren<QPushButton *>()) {
        if (button->text() == title) return control;
      }
    }
    return nullptr;
  };
  auto settle = []() {
    QEventLoop loop;
    QTimer::singleShot(150, &loop, &QEventLoop::quit);
    loop.exec();
  };
  auto visibleValue = [&](OptionControlSP *control, int value) {
    const QString expected = format.arg(value);
    for (auto *label : control->findChildren<QLabel *>()) {
      if (label->isVisible() && label->text() == expected &&
          label->fontMetrics().horizontalAdvance(expected) <= label->contentsRect().width()) return true;
    }
    return false;
  };
  QJsonArray steps;
  bool passed = true;
  auto capture = [&](SpeedLimitSettings &settings, OptionControlSP *control, const QString &name, int expected) {
    // Check the binding before processing a timer, then allow layout and paint before evidence capture.
    const bool immediate = visibleValue(control, expected);
    settle();
    for (auto *scroll : settings.findChildren<QScrollArea *>()) {
      if (scroll->isVisible()) scroll->ensureWidgetVisible(control, 0, 30);
    }
    settle();
    const QString persisted = QString::fromStdString(Params().get(key));
    const bool shown = visibleValue(control, expected);
    const QString screenshot = output + "/" + prefix + "-" + name + ".png";
    const bool saved = settings.grab().save(screenshot);
    QStringList labels;
    for (auto *label : control->findChildren<QLabel *>()) {
      if (label->isVisible()) labels.append(label->text());
    }
    const bool ok = immediate && shown && persisted == QString::number(expected) && saved;
    passed &= ok;
    steps.append(QJsonObject{{"step", name}, {"expected_value", expected}, {"persisted", persisted},
                             {"immediate_label", immediate}, {"visible_value_fits", shown},
                             {"labels", QJsonArray::fromStringList(labels)}, {"screenshot", screenshot}, {"passed", ok}});
    std::printf("%s %s: expected=%d persisted=%s immediate-label=%d visible-fit=%d\n",
                qPrintable(title), qPrintable(name), expected, qPrintable(persisted), immediate, shown);
  };
  auto *control = findControl(panel);
  QPushButton *plus = nullptr, *minus = nullptr;
  if (control) {
    for (auto *button : control->findChildren<QPushButton *>()) {
      if (button->text() == "+") plus = button;
      if (button->text() == "−") minus = button;
    }
  }
  bool valid = false;
  const int initial = QString::fromStdString(Params().get(key)).toInt(&valid);
  if (!control || !plus || !minus || !valid || initial < minimum || initial > maximum || (initial - minimum) % step) {
    std::fprintf(stderr, "%s control/buttons/initial Params missing or invalid\n", qPrintable(title));
    return false;
  }
  capture(panel, control, "initial", initial);
  const bool increase_first = initial < maximum;
  const int changed = increase_first ? std::min(initial + step, maximum) : initial - step;
  (increase_first ? plus : minus)->click();
  capture(panel, control, increase_first ? "plus" : "minus", changed);
  (increase_first ? minus : plus)->click();
  capture(panel, control, "restored", initial);
  auto reopen = [&](int expected, const QString &name) {
    SpeedLimitSettings reopened;
    reopened.resize(panel.size());
    reopened.show();
    auto *reopened_control = findControl(reopened);
    if (reopened_control) capture(reopened, reopened_control, name, expected);
    else passed = false;
  };
  auto moveTo = [&](int target) {
    int current = QString::fromStdString(Params().get(key)).toInt();
    for (int clicks = 0; current != target && clicks <= (maximum - minimum) / step; ++clicks) {
      const int next = std::clamp(current + (current < target ? step : -step), minimum, maximum);
      (current < target ? plus : minus)->click();
      current = QString::fromStdString(Params().get(key)).toInt();
      if (current != next || !visibleValue(control, next)) {
        passed = false;
        return;
      }
    }
    passed &= current == target;
  };
  moveTo(minimum);
  minus->click();
  const bool minimum_disabled = !minus->isEnabled();
  passed &= minimum_disabled;
  capture(panel, control, QString("minimum-%1").arg(minimum), minimum);
  plus->click();
  capture(panel, control, QString("plus-%1").arg(minimum + step), minimum + step);
  reopen(minimum + step, QString("reopened-%1").arg(minimum + step));
  plus->click();
  capture(panel, control, QString("plus-%1").arg(minimum + 2 * step), minimum + 2 * step);
  moveTo(sample);
  capture(panel, control, QString("selected-%1").arg(sample), sample);
  reopen(sample, QString("reopened-selected-%1").arg(sample));
  moveTo(maximum);
  plus->click();
  const bool maximum_disabled = !plus->isEnabled();
  passed &= maximum_disabled;
  capture(panel, control, QString("maximum-%1").arg(maximum), maximum);
  moveTo(initial);
  capture(panel, control, "final-restored", initial);
  reopen(initial, "reopened");
  std::printf("%s limits: min-disabled=%d max-disabled=%d original-restored=%d\n",
              qPrintable(title), minimum_disabled, maximum_disabled, Params().get(key) == std::to_string(initial));
  QFile report(output + "/" + prefix + "-result.json");
  if (!report.open(QIODevice::WriteOnly)) return false;
  report.write(QJsonDocument(QJsonObject{{"passed", passed}, {"param", key},
                                       {"minimum_disabled", minimum_disabled}, {"maximum_disabled", maximum_disabled},
                                       {"steps", steps}}).toJson());
  return passed;
}

// Feed the real HUD in memory; do not publish messages or run a vehicle stack.
static bool verifyCameraControlFreshness(UIState &state, const QString &output) {
  const std::vector<const char *> services{
    "controlsState", "carState", "carControl", "radarState", "gpsLocationExternal", "gpsLocation",
    "liveTorqueParameters", "carParams", "carParamsSP", "longitudinalPlanSP", "liveMapDataSP",
    "liveParameters", "deviceState", "selfdriveState", "driverStateV2",
  };
  Params params;
  const std::string original_enabled = params.get("SpeedCameraEnabled");
  const std::string original_distance = params.get("SpeedCameraAlertDistanceMeters");
  params.putBool("SpeedCameraEnabled", true);
  params.put("SpeedCameraAlertDistanceMeters", "200");
  state.sm = std::make_unique<SubMaster>(services);
  state.scene.is_metric = true;
  state.scene.speed_limit_mode = 1;
  state.scene.dev_ui_info = 0;
  state.scene.road_name = false;
  state.scene.turn_signals = false;
  state.scene.hideVEgoUI = false;
  state.status = STATUS_DISENGAGED;
  std::map<std::string, std::unique_ptr<MessageBuilder>> fixtures;
  std::vector<std::pair<std::string, cereal::Event::Reader>> initial_messages;
  for (const char *name : services) {
    auto message = std::make_unique<MessageBuilder>();
    auto event = message->initEvent();
    capnp::toDynamic(event).init(name);
    initial_messages.emplace_back(name, event.asReader());
    fixtures.emplace(name, std::move(message));
  }
  auto plan = fixtures.at("longitudinalPlanSP")->getRoot<cereal::Event>();
  auto control = plan.getLongitudinalPlanSP().initSpeedCameraControl();
  control.setEnabled(true);
  control.setActive(true);
  control.setSpeedCap(79.0f / 3.6f);
  control.setState("approach");
  auto resolver = plan.getLongitudinalPlanSP().initSpeedLimit().initResolver();
  resolver.setSpeedLimit(60.0f / 3.6f);
  resolver.setSpeedLimitLast(60.0f / 3.6f);
  resolver.setSpeedLimitFinalLast(60.0f / 3.6f);
  resolver.setSpeedLimitValid(true);
  resolver.setSpeedLimitLastValid(true);
  resolver.setSource(cereal::LongitudinalPlanSP::SpeedLimit::Source::MAP);
  auto map = fixtures.at("liveMapDataSP")->getRoot<cereal::Event>();
  auto lmd = map.getLiveMapDataSP();
  lmd.setLocationValid(true);
  lmd.setSpeedCameraAheadValid(true);
  lmd.setSpeedCameraAheadDistance(201);
  lmd.setSpeedCameraSpeedLimit(60.0f / 3.6f);
  lmd.setSpeedCameraLatitude(55.75);
  lmd.setSpeedCameraLongitude(37.62);
  lmd.setSpeedCameraType("speed_camera");
  auto car = fixtures.at("carState")->getRoot<cereal::Event>();
  car.getCarState().setVEgo(97.0f / 3.6f);
  car.getCarState().setVCruiseCluster(130);
  auto cruise = car.getCarState().initCruiseState();
  cruise.setSpeedCluster(130.0f / 3.6f);
  cruise.setAvailable(true);
  cruise.setEnabled(true);
  auto selfdrive = fixtures.at("selfdriveState")->getRoot<cereal::Event>();
  selfdrive.getSelfdriveState().setEnabled(true);
  auto car_control = fixtures.at("carControl")->getRoot<cereal::Event>();
  car_control.getCarControl().setEnabled(true);
  fixtures.at("carParamsSP")->getRoot<cereal::Event>().getCarParamsSP().setPcmCruiseSpeed(true);
  uint64_t now = nanos_since_boot();
  state.sm->update_msgs(now, initial_messages);
  const QString original_cwd = QDir::currentPath();
  QDir::setCurrent(original_cwd + "/selfdrive/ui");
  HudRendererSP hud;  // Load the production assist-arrow assets at their normal paths.
  QDir::setCurrent(original_cwd);
  bool passed = true;
  QJsonArray checks;
  auto check = [&](const QString &name, bool ok) {
    passed &= ok;
    checks.append(QJsonObject{{"check", name}, {"passed", ok}});
    std::printf("Camera UI %s: %s\n", qPrintable(name), ok ? "PASS" : "FAIL");
  };
  auto feed = [&]() {
    state.sm->update(0);
    now += 50000000ULL;
    state.sm->update_msgs(now, {{"longitudinalPlanSP", plan.asReader()},
                              {"liveMapDataSP", map.asReader()}, {"carState", car.asReader()},
                              {"selfdriveState", selfdrive.asReader()},
                              {"carControl", car_control.asReader()},
                              {"carParamsSP", fixtures.at("carParamsSP")->getRoot<cereal::Event>().asReader()}});
  };
  auto render = [&](const QString &name, int width = 2100) {
    hud.updateState(state);
    QImage image(width, 1080, QImage::Format_ARGB32_Premultiplied);
    QPainter painter(&image);
    // Synthetic high/low contrast road backdrop; every UI pixel is the real HUD.
    QLinearGradient sky(0, 0, 0, 1080);
    sky.setColorAt(0, QColor(130, 165, 190));
    sky.setColorAt(0.45, QColor(205, 210, 195));
    sky.setColorAt(0.46, QColor(90, 108, 80));
    sky.setColorAt(1, QColor(45, 55, 50));
    painter.fillRect(image.rect(), sky);
    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(100, 107, 112));
    painter.drawPolygon(QPolygon({QPoint(width / 2 - 70, 420), QPoint(width / 2 + 70, 420),
                                  QPoint(width, 1080), QPoint(0, 1080)}));
    painter.setPen(QPen(QColor(235, 235, 210), 12));
    painter.drawLine(width / 2 - 45, 440, width / 6, 1080);
    painter.drawLine(width / 2 + 45, 440, width * 5 / 6, 1080);
    hud.draw(painter, image.rect());
    painter.end();
    passed &= image.save(output + "/hud-" + name + ".png");
    return image;
  };
  auto section = [](const QImage &image) {
    const QRect r = cameraOverlayRect(image.rect());
    return image.copy(QRect(r.left() + 16, r.top() + 260, 648, 185));
  };
  auto warning = [](const QImage &image) {
    const QRect r = cameraOverlayRect(image.rect());
    return image.copy(QRect(r.left() + 16, r.top() + 266, 648, 100));
  };
  auto target = [](const QImage &image) {
    const QRect r = cameraOverlayRect(image.rect());
    return image.copy(QRect(r.left() + 16, r.top() + 365, 648, 64));
  };
  const QImage outside = render("outside-200m-active-cap");
  lmd.setSpeedCameraAheadValid(false); feed();
  const QImage empty = render("no-camera");
  check("active-cap-does-not-open-before-warning-threshold", section(outside) == section(empty));
  lmd.setSpeedCameraAheadValid(true);
  lmd.setSpeedCameraAheadDistance(200); feed();
  check("selected-200m-boundary", section(render("boundary-200m")) != section(empty));
  lmd.setSpeedCameraAheadDistance(198); feed();
  const QImage enabled = render("gps-198m-acc-enabled");
  auto receivedState = [&](bool available, bool enabled, bool braking) {
    const auto cs = (*state.sm)["carState"].getCarState();
    const auto sd = (*state.sm)["selfdriveState"].getSelfdriveState();
    return state.sm->updated("carState") && state.sm->valid("carState") &&
           state.sm->updated("selfdriveState") && state.sm->valid("selfdriveState") &&
           cs.getCruiseState().getAvailable() == available && cs.getCruiseState().getEnabled() == enabled &&
           cs.getBrakePressed() == braking && sd.getEnabled() == enabled;
  };
  check("enabled-fixture-received-cruise-and-selfdrive-true", receivedState(true, true, false));
  control.setManualTakeover(true);  // Controller output fixture; controller itself is not under test.
  car.getCarState().setBrakePressed(true);
  cruise.setEnabled(false);
  selfdrive.getSelfdriveState().setEnabled(false);
  car_control.getCarControl().setEnabled(false); feed();
  const QImage brake = render("gps-198m-brake");
  check("brake-fixture-received-cruise-and-selfdrive-false", receivedState(true, false, true));
  check("brake-retains-live-warning-hides-target", warning(brake) == warning(enabled) && target(brake) != target(enabled));
  control.setEnabled(false); control.setActive(false);
  cruise.setAvailable(false);
  cruise.setEnabled(false);
  car.getCarState().setBrakePressed(false);
  selfdrive.getSelfdriveState().setEnabled(false);
  car_control.getCarControl().setEnabled(false); feed();
  const QImage acc_off = render("gps-198m-acc-off");
  check("ACC-off-fixture-received-unavailable-disabled-brake-released", receivedState(false, false, false));
  check("ACC-off-retains-live-warning", warning(acc_off) == warning(brake));
  QImage previous = acc_off;
  for (int distance : {150, 80, 20}) {
    lmd.setSpeedCameraAheadDistance(distance); feed();
    QImage actual = render(QString("gps-%1m-acc-off").arg(distance));
    QImage expected = empty.copy();
    QPainter p(&expected);
    drawCameraOverlay(p, expected.rect(), true, distance, 60, true);
    p.end();
    check(QString("live-GPS-distance-%1-changes").arg(distance), warning(actual) != warning(previous) && warning(actual) == warning(expected));
    previous = actual;
  }
  // A real increasing provider sample is displayed too, never clamped by UI.
  lmd.setSpeedCameraAheadDistance(35); feed();
  check("provider-distance-can-increase", warning(render("gps-35m-provider-correction")) != warning(previous));
  map.setValid(false); lmd.setLocationValid(false); feed();
  const QImage lost = render("position-lost-after-visible");
  check("GPS-loss-replaces-live-distance", warning(lost) != warning(previous) && section(lost) != section(empty));
  map.setValid(true); lmd.setLocationValid(true); lmd.setSpeedCameraAheadValid(false); feed();
  check("provider-passed-clears-warning", section(render("provider-passed")) == section(empty));
  lmd.setLocationValid(false); map.setValid(false); feed();
  check("position-loss-before-visible-stays-hidden", section(render("position-lost-before-visible")) == section(empty));
  map.setValid(true); lmd.setLocationValid(true); lmd.setSpeedCameraAheadValid(true);
  lmd.setSpeedCameraLongitude(37.63); lmd.setSpeedCameraAheadDistance(900); feed();
  check("next-camera-awaits-threshold", section(render("next-camera-900m")) == section(empty));
  params.put("SpeedCameraAlertDistanceMeters", "1000");
  check("selected-1000m-opens-at-900m", section(render("threshold-1000m-at-900m")) != section(empty));
  lmd.setSpeedCameraAheadDistance(1001); feed();
  check("selected-1000m-excludes-1001m", section(render("threshold-1000m-at-1001m")) == section(empty));
  lmd.setSpeedCameraAheadDistance(150); feed();
  control.setEnabled(true); control.setActive(true); control.setManualTakeover(false);
  car.getCarState().setBrakePressed(false);
  cruise.setAvailable(true); cruise.setEnabled(true);
  selfdrive.getSelfdriveState().setEnabled(true);
  car_control.getCarControl().setEnabled(true); feed();
  const QImage active = render("final-transparent-2100");
  {
    QJsonArray samples_by_scenario;
    for (int width : {2100, 1050}) {
      for (bool camera_enabled : {true, false}) {
        params.putBool("SpeedCameraEnabled", camera_enabled);
        hud.updateState(state);
        QImage canvas(width, 1080, QImage::Format_ARGB32_Premultiplied);
        auto paint_once_ms = [&]() {
          canvas.fill(QColor(130, 165, 190));
          QPainter painter(&canvas);
          QElapsedTimer paint_clock;
          paint_clock.start();
          hud.draw(painter, canvas.rect());
          painter.end();  // Include completion of synchronous QImage raster painting.
          return paint_clock.nsecsElapsed() / 1000000.0;
        };
        const double first_paint_ms = paint_once_ms();  // Existing HUD/font caches; not process-cold.
        for (int warmup = 0; warmup < 12; ++warmup) paint_once_ms();
        std::vector<double> samples;
        QElapsedTimer budget;
        budget.start();
        while (samples.size() < 120 && budget.elapsed() < 3000) samples.push_back(paint_once_ms());
        std::sort(samples.begin(), samples.end());
        if (samples.empty()) { passed = false; continue; }
        double sum = 0;
        for (double sample : samples) sum += sample;
        auto percentile = [&](double fraction) { return samples[static_cast<size_t>(std::ceil(fraction * samples.size())) - 1]; };
        samples_by_scenario.append(QJsonObject{
          {"camera_enabled", camera_enabled}, {"width", width}, {"height", 1080},
          {"first_paint_ms_existing_caches", first_paint_ms}, {"warmup_frames", 12},
          {"sample_count", static_cast<int>(samples.size())}, {"sample_limit", 120}, {"sampling_budget_ms", 3000},
          {"sampling_wall_ms", static_cast<double>(budget.elapsed())},
          {"mean_ms", sum / samples.size()}, {"p50_ms", percentile(0.50)}, {"p95_ms", percentile(0.95)},
          {"p99_ms", percentile(0.99)}, {"max_ms", samples.back()},
        });
        std::printf("PC HUD paint width=%d camera=%d n=%zu p50=%.3f p95=%.3f max=%.3f ms\n",
                    width, camera_enabled, samples.size(), percentile(0.50), percentile(0.95), samples.back());
      }
    }
    params.putBool("SpeedCameraEnabled", true);
    hud.updateState(state);
    QFile latency_report(output + "/hud-paint-latency-pc.json");
    const QJsonObject result{
      {"environment", "Local PC Docker, Qt offscreen, synchronous QImage raster"},
      {"architecture", QSysInfo::currentCpuArchitecture()}, {"qt_version", qVersion()},
      {"timed_region", "HudRendererSP::draw plus QPainter::end; excludes updateState, image allocation/fill, PNG I/O and event loop"},
      {"comparison", "Current HUD camera on versus current HUD camera off; not an old-renderer comparison"},
      {"limitations", "No embedded/device performance or watchdog validation; first paint is not process-cold"},
      {"scenarios", samples_by_scenario},
    };
    const QByteArray json = QJsonDocument(result).toJson();
    const bool saved = latency_report.open(QIODevice::WriteOnly) && latency_report.write(json) == json.size();
    passed &= saved;
  }
  lmd.setSpeedCameraSpeedLimit(40.0f / 3.6f); feed();
  const QImage other_limit = render("raw-limit40-target79");
  check("warning-limit-separate-from-offset-ACC-target", warning(active) != warning(other_limit) && target(active) == target(other_limit));
  lmd.setSpeedCameraSpeedLimit(0); feed(); render("unknown-camera-limit");
  lmd.setSpeedCameraSpeedLimit(60.0f / 3.6f); feed();
  plan.setValid(false); feed();
  const QImage invalid_plan = render("invalid-plan");
  check("invalid-plan-hides-only-control-target", warning(invalid_plan) == warning(active) && target(invalid_plan) != target(active));
  plan.setValid(true); feed();
  state.sm->update(0); now += 2000000000ULL;
  state.sm->update_msgs(now, {{"liveMapDataSP", map.asReader()}, {"carState", car.asReader()}});
  const bool plan_stale = !state.sm->alive("longitudinalPlanSP");
  const QImage stale_plan = render("stale-plan");
  check("stale-plan-hides-only-control-target", plan_stale && warning(stale_plan) == warning(active) && target(stale_plan) != target(active));
  feed(); const QImage recovered_plan = render("recovered-plan");
  check("fresh-plan-recovers-control-target", target(recovered_plan) == target(active));
  map.setValid(false); feed();
  check("invalid-map-clears-warning", section(render("invalid-map")) == section(empty));
  map.setValid(true); feed();
  params.putBool("SpeedCameraEnabled", false);
  check("toggle-off-hides-even-active-cap", section(render("toggle-off")) == section(empty));
  params.putBool("SpeedCameraEnabled", true);
  for (int width : {2100, 1700, 1050}) {
    const QImage actual = render(QString("final-transparent-%1").arg(width), width);
    const QRect card = cameraOverlayRect(actual.rect()).adjusted(-4, -4, 4, 4);
    QRect speed_rect = QFontMetrics(InterFont(176, QFont::Bold)).boundingRect("888");
    speed_rect.moveCenter({actual.rect().center().x(), 210 - speed_rect.height() / 2});
    check(QString("layout-%1-no-current-speed-overlap").arg(width), actual.rect().contains(card) && !card.intersects(speed_rect));
  }
  const QRect wide_card = cameraOverlayRect(active.rect());
  const QRect road_header(wide_card.left() + 345, wide_card.top() + 10, 315, 165);
  std::vector<QImage> road_modes;
  for (int mode : {0, 1, 2, 3}) {
    state.scene.speed_limit_mode = mode;
    road_modes.push_back(render(QString("road-limit-mode-%1").arg(mode)));
  }
  check("road-modes-Off-Information-Warning-Assist", road_modes[0].copy(road_header) != road_modes[1].copy(road_header) &&
        road_modes[1].copy(road_header) != road_modes[2].copy(road_header) && road_modes[2].copy(road_header) == road_modes[3].copy(road_header));
  state.scene.speed_limit_mode = 1;
  fixtures.at("carParamsSP")->getRoot<cereal::Event>().getCarParamsSP().setPcmCruiseSpeed(false);
  fixtures.at("carControl")->getRoot<cereal::Event>().getCarControl().setEnabled(true);
  car.getCarState().getCruiseState().setSpeedCluster(110.0f / 3.6f); feed();
  const QImage icbm = render("icbm-native-cluster110-set130-target79");
  const QRect max_label(wide_card.left() + 20, wide_card.top() + 10, 310, 50);
  const QRect set_value(wide_card.left() + 20, wide_card.top() + 65, 310, 105);
  check("ICBM-native-cluster-distinct-from-set-speed-and-camera-target", icbm.copy(max_label) != active.copy(max_label) &&
        icbm.copy(set_value) == active.copy(set_value) && target(icbm) == target(active));
  fixtures.at("carParamsSP")->getRoot<cereal::Event>().getCarParamsSP().setPcmCruiseSpeed(true);
  fixtures.at("carControl")->getRoot<cereal::Event>().getCarControl().setEnabled(false); feed();
  state.scene.speed_limit_mode = 3;
  // Existing upcoming sign and assist arrow get distinct slots below the card.
  lmd.setSpeedLimitAheadValid(true); lmd.setSpeedLimitAhead(40.0f / 3.6f);
  lmd.setSpeedLimitAheadDistance(300); feed(); render("ahead-seed");
  lmd.setSpeedLimitAheadDistance(290); feed(); render("upcoming-limit");
  plan.getLongitudinalPlanSP().getSpeedLimit().initAssist().setState(cereal::LongitudinalPlanSP::SpeedLimit::AssistState::PRE_ACTIVE);
  feed();
  const QImage with_arrow = render("upcoming-plus-assist-1050", 1050);
  const QRect narrow_card = cameraOverlayRect(with_arrow.rect());
  const QRect ahead_region(narrow_card.left() + 350, narrow_card.bottom() + 20, 246, 168);
  const QRect arrow_region(narrow_card.left() + 230, narrow_card.bottom() + 24, 110, 204);
  plan.getLongitudinalPlanSP().getSpeedLimit().getAssist().setState(cereal::LongitudinalPlanSP::SpeedLimit::AssistState::INACTIVE);
  feed(); const QImage no_arrow = render("upcoming-without-assist-1050", 1050);
  check("assist-arrow-below-card-no-overlap", with_arrow.copy(arrow_region) != no_arrow.copy(arrow_region) &&
        !arrow_region.intersects(narrow_card) && !arrow_region.intersects(ahead_region));
  check("upcoming-stays-visible-during-assist-pulse", with_arrow.copy(ahead_region) == no_arrow.copy(ahead_region));
  lmd.setSpeedLimitAheadValid(false); feed(); const QImage no_ahead = render("without-upcoming-1050", 1050);
  check("upcoming-sign-below-card-no-overlap", no_arrow.copy(ahead_region) != no_ahead.copy(ahead_region) &&
        !ahead_region.intersects(narrow_card) && no_arrow.rect().contains(ahead_region));
  state.sm->update(0); now += 20000000000ULL;
  state.sm->update_msgs(now, {});
  state.scene.speed_limit_mode = 1;
  check("dead-map-hides-warning", section(render("stale-map")) == section(empty));
  // Transparent interior (not just an alpha-blended opaque rectangle).
  QImage alpha(2100, 1080, QImage::Format_ARGB32_Premultiplied); alpha.fill(Qt::transparent);
  QPainter painter(&alpha); drawCameraOverlay(painter, alpha.rect(), true, 150, 60, true); painter.end();
  const QRect card = cameraOverlayRect(alpha.rect());
  check("camera-panel-has-no-background-fill", qAlpha(alpha.pixel(card.left() + 10, card.top() + 300)) == 0 &&
                                                        qAlpha(alpha.pixel(card.left() + 300, card.top() + 440)) == 0);
  alpha.save(output + "/camera-transparent-alpha.png");
  if (original_enabled.empty()) params.remove("SpeedCameraEnabled"); else params.put("SpeedCameraEnabled", original_enabled);
  if (original_distance.empty()) params.remove("SpeedCameraAlertDistanceMeters"); else params.put("SpeedCameraAlertDistanceMeters", original_distance);
  QFile report(output + "/camera-ui-checks.json");
  passed &= report.open(QIODevice::WriteOnly);
  if (report.isOpen()) report.write(QJsonDocument(QJsonObject{{"passed", passed}, {"checks", checks}}).toJson());
  return passed;
}

// Only the real widget's click writes the update request. Ready/count/hash must
// come from the separately running updater sharing this isolated Params root.
static int runUpdaterE2E(QApplication &app, SpeedLimitSettings &panel, const QString &output,
                         const QString &original_url, const QString &database_path,
                         int expected_count, const QString &expected_sha, int timeout_seconds) {
  Params params;
  QTimer poll;
  QElapsedTimer elapsed;
  elapsed.start();
  bool clicked = false;
  bool finished = false;
  double previous_updated = 0;
  double request_time = 0;
  QString last_status;
  QString cache_sha;
  int cache_count = -1;
  auto get = [&](const char *key) { return QString::fromStdString(params.get(key)); };
  auto scroll_to = [&](bool bottom) {
    for (auto *area : panel.findChildren<QScrollArea *>()) {
      if (area->isVisible()) {
        auto *bar = area->verticalScrollBar();
        bar->setValue(bottom ? bar->maximum() : (bar->minimum() + bar->maximum()) / 2);
        return;
      }
    }
  };
  auto finish = [&](bool passed, const QString &reason) {
    if (finished) return;
    finished = true;
    poll.stop();
    panel.refresh();
    // Cold-cache labels acquire their first text here. Let Qt process their
    // layout/resize/paint events, then settle the final scroll before grabbing.
    QTimer::singleShot(150, &app, [&, passed, reason]() {
      scroll_to(true);
      QTimer::singleShot(100, &app, [&, passed, reason]() mutable {
        const QString screenshot = output + (passed ? "/speed-limit-updater-ready.png" : "/speed-limit-updater-failed.png");
        passed = panel.grab().save(screenshot) && passed;
        QJsonObject result{
          {"passed", passed}, {"reason", reason}, {"clicked", clicked},
          {"status", get("SpeedCameraDatabaseStatus")}, {"count", get("SpeedCameraDatabaseCount")},
          {"version", get("SpeedCameraDatabaseVersion")}, {"updated_at", get("SpeedCameraDatabaseUpdatedAt")},
          {"error", get("SpeedCameraDatabaseError")}, {"url_preserved", get("SpeedCameraDatabaseUrl") == original_url},
          {"expected_count", expected_count}, {"expected_sha256", expected_sha},
          {"cache_count", cache_count}, {"cache_sha256", cache_sha}, {"database_path", database_path},
          {"request_time", request_time}, {"previous_updated_at", previous_updated},
          {"elapsed_ms", static_cast<double>(elapsed.elapsed())}, {"screenshot", screenshot},
          {"capture_settle_ms", 250},
        };
        const QByteArray json = QJsonDocument(result).toJson(QJsonDocument::Indented);
        QFile report(output + "/updater-e2e-result.json");
        const bool report_saved = report.open(QIODevice::WriteOnly) && report.write(json) == json.size();
        std::fprintf(passed && report_saved ? stdout : stderr, "UPDATER E2E %s: %s\n%s",
                     passed && report_saved ? "PASS" : "FAIL", qPrintable(reason), json.constData());
        std::fflush(stdout);
        std::fflush(stderr);
        app.exit(passed && report_saved ? 0 : 1);
      });
    });
  };
  QObject::connect(&poll, &QTimer::timeout, &app, [&]() {
    const QString status = get("SpeedCameraDatabaseStatus");
    if (status != last_status) {
      std::printf("UPDATER E2E status=%s\n", qPrintable(status));
      std::fflush(stdout);
      last_status = status;
    }
    if (get("SpeedCameraDatabaseUrl") != original_url) {
      finish(false, "Prepopulated source URL changed");
      return;
    }
    if (elapsed.elapsed() >= timeout_seconds * 1000LL) {
      finish(false, clicked ? "Timeout waiting for a fresh backend ready/count/SHA after UI click"
                            : "Timeout waiting for an enabled Update Database button");
      return;
    }
    if (!clicked) {
      panel.refresh();
      for (auto *button : panel.findChildren<QPushButton *>()) {
        if (button->isVisible() && button->text() == "Update Database") {
          if (!button->isEnabled()) return;
          scroll_to(false);
          panel.grab().save(output + "/speed-limit-updater-source.png");
          scroll_to(true);
          previous_updated = get("SpeedCameraDatabaseUpdatedAt").toDouble();
          request_time = QDateTime::currentMSecsSinceEpoch() / 1000.0;
          clicked = true;
          button->click();
          std::printf("UPDATER E2E clicked Update Database; waiting for backend\n");
          std::fflush(stdout);
          return;
        }
      }
      finish(false, "Update Database button not found");
      return;
    }
    if (status == "error") {
      finish(false, "Backend reported an update error");
      return;
    }
    bool updated_ok = false;
    const double updated = get("SpeedCameraDatabaseUpdatedAt").toDouble(&updated_ok);
    if (status != "ready" || !updated_ok || !std::isfinite(updated) ||
        updated <= previous_updated || updated < request_time) return;

    QFile cache(database_path);
    if (!cache.open(QIODevice::ReadOnly)) {
      finish(false, "Backend reports ready but database cache cannot be read");
      return;
    }
    QJsonParseError parse_error;
    const auto document = QJsonDocument::fromJson(cache.readAll(), &parse_error);
    if (parse_error.error != QJsonParseError::NoError || !document.isObject()) {
      finish(false, "Backend reports ready but database cache is not a JSON object");
      return;
    }
    const auto payload = document.object();
    cache_sha = payload.value("sha256").toString();
    cache_count = payload.value("data").isArray() ? payload.value("data").toArray().size() : -1;
    bool count_ok = false;
    const int count = get("SpeedCameraDatabaseCount").toInt(&count_ok);
    if (!count_ok || count != expected_count || cache_count != expected_count || cache_sha != expected_sha ||
        get("SpeedCameraDatabaseVersion") != expected_sha.left(12) || !get("SpeedCameraDatabaseError").isEmpty() ||
        !payload.value("fetched_at").isDouble() || std::abs(payload.value("fetched_at").toDouble() - updated) > 0.001) {
      finish(false, "Ready/count/SHA/version/timestamp assertions failed");
      return;
    }
    panel.refresh();
    QStringList visible_values;
    for (auto *label : panel.findChildren<QLabel *>()) {
      if (label->isVisible()) visible_values.append(label->text());
    }
    const bool ui_values = visible_values.contains("ready") && visible_values.contains(QString::number(expected_count)) &&
                           visible_values.contains(expected_sha.left(12));
    finish(ui_values, ui_values ? "Fresh backend snapshot and rendered ready/count/SHA verified"
                               : "Backend data passed but Qt labels did not refresh");
  });
  poll.setInterval(100);
  QTimer::singleShot(1000, &poll, [&]() { poll.start(); });
  return app.exec();
}

int main(int argc, char **argv) {
  if (qEnvironmentVariableIsEmpty("PARAMS_ROOT")) {
    std::fprintf(stderr, "Preview requires an isolated PARAMS_ROOT\n");
    return 2;
  }
  QApplication app(argc, argv);
  const QDir fonts("selfdrive/assets/fonts");
  for (const auto &font : fonts.entryList({"*.ttf"}, QDir::Files)) {
    QFontDatabase::addApplicationFont(fonts.filePath(font));
  }
  QCommandLineParser parser;
  parser.setApplicationDescription("Native settings/HUD preview; optional real UI-to-updater integration test.");
  parser.addHelpOption();
  parser.addPositionalArgument("output", "Screenshot/report directory (default .native-build/preview).", "[output]");
  parser.addOption({"updater-e2e", "Preserve prepopulated Params/URL, click Update Database, and await the real backend."});
  parser.addOption({"database", "Backend-generated JSON cache path, required with --updater-e2e.", "path"});
  parser.addOption({"expected-count", "Expected accepted camera count.", "count", "74016"});
  parser.addOption({"expected-sha256", "Full SHA-256 of the source download, checked against cache.sha256 and the UI prefix.", "hex"});
  parser.addOption({"timeout-seconds", "E2E timeout including waiting for the update button (1-3600 seconds).", "seconds", "120"});
  parser.process(app);
  const bool updater_e2e = parser.isSet("updater-e2e");
  bool count_ok = false, timeout_ok = false;
  const int expected_count = parser.value("expected-count").toInt(&count_ok);
  const int timeout_seconds = parser.value("timeout-seconds").toInt(&timeout_ok);
  const QString expected_sha = parser.value("expected-sha256").toLower();
  const QString source_before = QString::fromStdString(Params().get("SpeedCameraDatabaseUrl"));
  if (parser.positionalArguments().size() > 1 || (updater_e2e &&
      (!count_ok || expected_count <= 0 || !timeout_ok || timeout_seconds < 1 || timeout_seconds > 3600 ||
       parser.value("database").isEmpty() || source_before.isEmpty() ||
       !QRegularExpression("^[0-9a-f]{64}$").match(expected_sha).hasMatch()))) {
    std::fprintf(stderr, "E2E requires a prepopulated SpeedCameraDatabaseUrl, --database, --expected-sha256 (64 hex), a positive count and timeout 1-3600.\n");
    return 2;
  }
  app.setStyleSheet(
    "QWidget { background: #20252b; color: #f2f4f7; font-family: Inter; font-size: 34px; }"
    "QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget { background: #20252b; }"
    "QLabel { background: transparent; color: #f2f4f7; }"
    "QPushButton { background: #303b49; color: #f2f4f7; border: 1px solid #526274;"
      " border-radius: 16px; padding: 10px 22px; }"
    "QPushButton:hover { background: #39495b; }"
    "QPushButton:pressed { background: #48627d; }"
    "QScrollBar:vertical { background: #1a1f24; width: 18px; margin: 2px; }"
    "QScrollBar::handle:vertical { background: #60758c; border-radius: 8px; min-height: 40px; }"
  );
  SpeedLimitSettings panel;
  panel.resize(1700, 1080);
  panel.show();
  const QString output = parser.positionalArguments().isEmpty() ? ".native-build/preview" : parser.positionalArguments().first();
  if (!QDir().mkpath(output)) return 2;
  if (updater_e2e) {
    return runUpdaterE2E(app, panel, output, source_before, parser.value("database"),
                         expected_count, expected_sha, timeout_seconds);
  }
  // UIState's PrimeState posts an initial callback; keep it alive through exec().
  UIState hud_state;
  bool hud_freshness_ok = verifyCameraControlFreshness(hud_state, output);
  {
    QTranslator russian;
    const bool russian_loaded = russian.load(":/main_ru");
    const QString russian_output = output + "/ru";
    hud_freshness_ok &= russian_loaded && QDir().mkpath(russian_output);
    if (russian_loaded) {
      app.installTranslator(&russian);
      hud_freshness_ok &= verifyCameraControlFreshness(hud_state, russian_output);
      app.removeTranslator(&russian);
    }
  }
  for (int distance : {600, 250, 75, -1}) {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), distance >= 0, distance, 60, true);
    painter.end();
    image.save(output + QString("/camera-%1m.png").arg(distance));
  }
  CameraControlOverlay target60;
  target60.enabled = true;
  target60.active = true;
  target60.speedCapMps = 60.0f / 3.6f;
  target60.stateText = "approach";
  {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), true, 250, 60, true, target60);
    painter.end();
    image.save(output + "/camera-target-60kmh.png");
  }
  CameraControlOverlay target60Imperial = target60;
  target60Imperial.speedCapMps = 60.0f / 2.23694f;
  {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), true, 250, 60, false, target60Imperial);
    painter.end();
    image.save(output + "/camera-target-60mph-display.png");
  }
  CameraControlOverlay offsetTarget = target60;
  offsetTarget.speedCapMps = 79.0f / 3.6f;
  auto offsetImage = [&](float warning_limit_kph, bool metric) {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), true, 150,
                       metric ? warning_limit_kph : warning_limit_kph / 3.6f * 2.23694f, metric, offsetTarget);
    painter.end();
    return image;
  };
  const QImage offset_metric = offsetImage(60, true);
  const QImage other_warning = offsetImage(40, true);
  const QRect banner = cameraOverlayRect(offset_metric.rect());
  const QRect warning_line(banner.left() + 16, banner.top() + 266, 648, 100);
  const QRect target_line(banner.left() + 16, banner.top() + 365, 648, 64);
  const bool separate_limit_target = offset_metric.copy(warning_line) != other_warning.copy(warning_line) &&
                                      offset_metric.copy(target_line) == other_warning.copy(target_line) &&
                                      QFontMetrics(InterFont(44, QFont::DemiBold)).horizontalAdvance(QObject::tr("ACC TARGET %1 %2").arg("79", QObject::tr("km/h"))) <= 632;
  offset_metric.save(output + "/camera-limit60-target79-150m.png");
  offsetImage(60, false).save(output + "/camera-limit60-target79-150m-imperial.png");
  other_warning.save(output + "/camera-other-warning40-target79.png");
  std::printf("Camera HUD: warning-limit-separate-from-ACC-target=%d\n", separate_limit_target);
  CameraControlOverlay heldPosition;
  heldPosition.enabled = true;
  heldPosition.active = true;
  heldPosition.stateText = "hold";
  heldPosition.reasonText = "gps_unreliable";
  {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), true, 75, 60, true, heldPosition);
    painter.end();
    image.save(output + "/camera-held-position.png");
  }
  CameraControlOverlay awaitingMap = heldPosition;
  awaitingMap.speedCapMps = 50.0f / 3.6f;
  awaitingMap.reasonText = "awaiting_map_update";
  {
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    drawCameraOverlay(painter, image.rect(), true, 40, 50, true, awaitingMap);
    painter.end();
    image.save(output + "/camera-awaiting-map-update.png");
  }
  QTimer::singleShot(1000, [&]() {
    auto hasVisibleText = [&panel](const QString &needle) {
      for (auto *label : panel.findChildren<QLabel *>()) {
        if (label->isVisible() && label->text().contains(needle, Qt::CaseInsensitive)) return true;
      }
      for (auto *button : panel.findChildren<QPushButton *>()) {
        if (button->isVisible() && button->text().contains(needle, Qt::CaseInsensitive)) return true;
      }
      return false;
    };

    QScrollArea *scroll = nullptr;
    for (auto *area : panel.findChildren<QScrollArea *>()) {
      if (area->isVisible()) {
        scroll = area;
        break;
      }
    }
    if (scroll != nullptr) scroll->verticalScrollBar()->setValue(scroll->verticalScrollBar()->minimum());

    const bool sco_default = Params().get("SpeedCameraDatabaseUrl") == "https://speedcamonline.ru/cg77/Rus/comment/";
    const bool sco_attribution_visible = hasVisibleText("SpeedCamOnline");
    const bool camera_control_toggle_visible = hasVisibleText("Automatic Camera Speed Control");

    if (scroll != nullptr) {
      panel.grab().save(output + "/speed-limit-top.png");
      scroll->verticalScrollBar()->setValue(scroll->verticalScrollBar()->maximum() / 2);
      panel.grab().save(output + "/speed-limit-middle.png");
      scroll->verticalScrollBar()->setValue(scroll->verticalScrollBar()->maximum());
      panel.grab().save(output + "/speed-limit-bottom.png");
    }

    bool found = false;
    for (auto *button : panel.findChildren<QPushButton *>()) {
      if (button->isVisible() && button->text().contains("Update Database", Qt::CaseInsensitive)) {
        found = true;
        button->click();
      }
    }
    const bool requested = Params().get("SpeedCameraDatabaseStatus") == "update requested";
    std::printf("SCO default=%d attribution=%d camera-control-toggle=%d update-visible=%d request-persisted=%d\n",
                sco_default, sco_attribution_visible, camera_control_toggle_visible, found, requested);
    const bool warning_distance_ok = verifyCameraSetting(panel, output, "Warning Distance",
      "SpeedCameraAlertDistanceMeters", "%1 m", 100, 2000, 50, "warning-distance", 150);
    const bool camera_offset_ok = verifyCameraSetting(panel, output, "Camera Speed Offset",
      "SpeedCameraSpeedOffsetKph", "+%1 km/h", 0, 30, 1, "camera-offset", 19);
    app.exit(hud_freshness_ok && sco_default && sco_attribution_visible && camera_control_toggle_visible && found &&
             requested && warning_distance_ok && camera_offset_ok && separate_limit_target ? 0 : 1);
  });
  return app.exec();
}
