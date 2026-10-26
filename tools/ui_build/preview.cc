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
    return image.copy(QRect(1280, 30, 410, 220));
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

  state.sm->update(0);
  state.sm->update_msgs(now + 20000000000ULL, {});
  const QImage both_stale = render("both-stale");
  map.getLiveMapDataSP().setSpeedCameraAheadValid(false);
  control.setEnabled(false);
  control.setActive(false);
  state.sm->update_msgs(now + 21000000000ULL,
                       {{"longitudinalPlanSP", plan.asReader()}, {"liveMapDataSP", map.asReader()}});
  const QImage empty = render("no-camera");
  const bool passed = active != warning && plan_stale && stale == warning && invalid == warning &&
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
    "QWidget { background: #20252b; color: #f2f4f7; font-size: 34px; }"
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
    app.exit(hud_freshness_ok && sco_default && sco_attribution_visible && camera_control_toggle_visible && found && requested ? 0 : 1);
  });
  return app.exec();
}
