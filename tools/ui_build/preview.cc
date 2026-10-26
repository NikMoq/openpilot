// Exercises the actual native SpeedLimitSettings widget in an isolated Params root.
#include <QApplication>
#include <QDir>
#include <QScrollArea>
#include <QScrollBar>
#include <QTimer>
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
  state.sm = std::make_unique<SubMaster>(services);
  state.scene.is_metric = true;
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
  auto map = fixtures.at("liveMapDataSP")->getRoot<cereal::Event>();
  map.getLiveMapDataSP().setSpeedCameraAheadValid(true);
  map.getLiveMapDataSP().setSpeedCameraAheadDistance(250);
  map.getLiveMapDataSP().setSpeedCameraSpeedLimit(60.0f / 3.6f);
  fixtures.at("carState")->getRoot<cereal::Event>().getCarState().setVEgo(130.0f / 3.6f);
  const uint64_t now = nanos_since_boot();
  state.sm->update_msgs(now, initial_messages);
  HudRendererSP hud;
  auto render = [&](const QString &name) {
    hud.updateState(state);
    QImage image(1700, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    QPainter painter(&image);
    hud.draw(painter, image.rect());
    painter.end();
    image.save(output + "/hud-control-" + name + ".png");
    return image.copy(cameraOverlayRect(image.rect(), 180).adjusted(-4, -4, 4, 4));
  };
  const QImage warning = render("warning-only");
  control.setEnabled(true);
  control.setActive(true);
  control.setSpeedCap(60.0f / 3.6f);
  control.setState("approach");
  state.sm->update_msgs(now, {{"longitudinalPlanSP", plan.asReader()}});
  const QImage active = render("active");

  state.sm->update(0);  // Reset updated flags as on a normal UI tick.
  state.sm->update_msgs(now + 2000000000ULL, {{"liveMapDataSP", map.asReader()}});
  const bool plan_stale = !state.sm->alive("longitudinalPlanSP");
  const QImage stale = render("stale");

  plan.setValid(false);  // Even a newly received active/cap payload is untrusted.
  state.sm->update_msgs(now + 3000000000ULL,
                       {{"longitudinalPlanSP", plan.asReader()}, {"liveMapDataSP", map.asReader()}});
  const QImage invalid = render("invalid");
  plan.setValid(true);
  state.sm->update_msgs(now + 4000000000ULL,
                       {{"longitudinalPlanSP", plan.asReader()}, {"liveMapDataSP", map.asReader()}});
  const QImage recovered = render("recovered");

  // Composite the real wheel widget over the real HUD, as Qt does on device.
  // The old camera-only preview could not expose this overlapping child widget.
  bool header_layout_ok = true;
  ExperimentalButtonSP wheel;
  for (int width : {2100, 1700, 1050}) {
    QImage image(width, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 51, 73));
    const QRect surface = image.rect();
    const QRect camera = cameraOverlayRect(surface, 180).adjusted(-2, -2, 2, 2);
    const QPoint wheel_position(width - UI_BORDER_SIZE - btn_size, UI_BORDER_SIZE);
    const QRect wheel_rect(wheel_position, wheel.size());
    QRect speed_rect = QFontMetrics(InterFont(176, QFont::Bold)).boundingRect("130");
    speed_rect.moveCenter({surface.center().x(), 210 - speed_rect.height() / 2});
    QPainter painter(&image);
    hud.draw(painter, surface);
    painter.end();
    const QImage camera_before_wheel = image.copy(camera);
    painter.begin(&image);
    wheel.render(&painter, wheel_position);
    painter.end();
    const bool clear = surface.contains(camera) && !camera.intersects(wheel_rect) &&
                       !camera.intersects(speed_rect) && camera_before_wheel == image.copy(camera);
    header_layout_ok &= clear;
    image.save(output + QString("/hud-camera-wheel-%1.png").arg(width));
    std::printf("Camera header width=%d: no-overlap=%d camera=(%d,%d,%d,%d)\n",
                width, clear, camera.x(), camera.y(), camera.width(), camera.height());
  }

  state.sm->update(0);
  state.sm->update_msgs(now + 20000000000ULL, {});
  const QImage both_stale = render("both-stale");
  map.getLiveMapDataSP().setSpeedCameraAheadValid(false);
  control.setEnabled(false);
  control.setActive(false);
  state.sm->update_msgs(now + 21000000000ULL,
                       {{"longitudinalPlanSP", plan.asReader()}, {"liveMapDataSP", map.asReader()}});
  const QImage empty = render("no-camera");
  const bool passed = header_layout_ok && active != warning && plan_stale && stale == warning && invalid == warning &&
                      recovered == active && both_stale == empty;
  std::printf("HUD freshness: target-visible=%d plan-stale=%d stale-hidden=%d invalid-hidden=%d recovered=%d map-stale-hidden=%d\n",
              active != warning, plan_stale, stale == warning, invalid == warning, recovered == active, both_stale == empty);
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
  const bool hud_freshness_ok = verifyCameraControlFreshness(hud_state, output);
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
  const QRect banner = cameraOverlayRect(offset_metric.rect(), 165);
  const QRect warning_line(banner.left() + 16, banner.top() + 52, 328, 35);
  const QRect target_line(banner.left() + 16, banner.top() + 113, 328, 40);
  const bool separate_limit_target = offset_metric.copy(warning_line) != other_warning.copy(warning_line) &&
                                      offset_metric.copy(target_line) == other_warning.copy(target_line) &&
                                      QFontMetrics(InterFont(25, QFont::Normal)).horizontalAdvance("ACC TARGET 79 km/h") <= 328;
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
