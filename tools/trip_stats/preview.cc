// Offline actual-widget fixture; no cloud identity, no CAN, no acceptance clicks.
#include <QApplication>
#include <QDir>
#include <QFile>
#include <QFontDatabase>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QTranslator>
#include <cstdio>
#include "common/params.h"
#include "selfdrive/ui/sunnypilot/qt/widgets/drive_stats.h"

int main(int argc, char **argv) {
  if (argc != 2 || qgetenv("PARAMS_ROOT").isEmpty() || qgetenv("TRIP_STATS_ROOT").isEmpty()) return 2;
  if (!qgetenv("PARAMS_ROOT").startsWith("/tmp/trip-stats-") || !qgetenv("TRIP_STATS_ROOT").startsWith("/tmp/trip-stats-")) return 2;
  if (QDir(QString::fromLocal8Bit(qgetenv("PARAMS_ROOT"))).exists()) return 3;
  QApplication app(argc, argv);
  QTranslator translator;
  if (!translator.load("../../.native-build/pc/resources/main_ru.qm")) return 4;
  app.installTranslator(&translator);
  for (const auto &name : {"Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-Bold.ttf"}) {
    if (QFontDatabase::addApplicationFont(QString("../assets/fonts/") + name) < 0) return 5;
  }
  app.setFont(QFont("Inter"));
  app.setStyleSheet("QLabel {color:white;} QFrame {background:#333333;}");
  Params params; params.putBool("IsMetric", true);
  params.put("LanguageSetting", "main_ru");
  const QString output = QString::fromLocal8Bit(argv[1]); QDir().mkpath(output);
  QDir().mkpath(QString::fromLocal8Bit(qgetenv("TRIP_STATS_ROOT")));
  const QString path = QString::fromLocal8Bit(qgetenv("TRIP_STATS_ROOT")) + "/stats.json";
  QJsonArray results;
  auto capture = [&](const QString &name, const QByteArray &data, bool metric, int width) {
    if (data.isEmpty()) QFile::remove(path);
    else { QFile file(path); if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false; file.write(data); }
    params.putBool("IsMetric", metric);
    DriveStats card;
    card.resize(width, 900); card.show(); app.processEvents();
    QJsonArray labels;
    for (auto *label : card.findChildren<QLabel *>()) {
      const bool wrap = label->wordWrap();
      const bool fit = wrap ? label->heightForWidth(label->width()) <= label->height() : label->fontMetrics().horizontalAdvance(label->text()) <= label->contentsRect().width();
      labels.append(QJsonObject{{"text", label->text()}, {"fit", fit}, {"width",label->width()}, {"height",label->height()}});
      if (!fit) return false;
    }
    const QString file = output + "/" + name + ".png";
    if (!card.grab().save(file)) return false;
    results.append(QJsonObject{{"case",name},{"width",width},{"height",900},{"labels",labels}});
    card.hide(); return true;
  };
  const QByteArray measured = R"({"version":1,"all":{"routes":42,"distance_m":1234500,"duration_s":128700},"week":{"routes":7,"distance_m":12400,"duration_s":6300}})";
  const QByteArray shortDrive = R"({"version":1,"all":{"routes":1,"distance_m":350,"duration_s":600},"week":{"routes":1,"distance_m":350,"duration_s":600}})";
  const QByteArray large = R"({"version":1,"all":{"routes":123456789,"distance_m":999999999999,"duration_s":999999999999},"week":{"routes":10000000,"distance_m":9999999999,"duration_s":9999999999}})";
  const QByteArray recovered = R"({"version":1,"all":{"routes":0,"distance_m":17946.87,"duration_s":778.47},"week":{"routes":0,"distance_m":17946.87,"duration_s":778.47},"coverage":{"routes_incomplete":true,"week_routes_incomplete":true}})";
  if (!capture("01-metric",measured,true,1000) || !capture("02-imperial",measured,false,1000) ||
      !capture("03-short-drive",shortDrive,true,1000) || !capture("04-unavailable",{},true,1000) ||
      !capture("05-settings-width",measured,true,1600) || !capture("06-large-values",large,true,1000) ||
      !capture("07-partial-recovery",recovered,true,1000)) return 10;
  QFile report(output+"/report.json"); if (!report.open(QIODevice::WriteOnly)) return 11;
  report.write(QJsonDocument(QJsonObject{{"qt",qVersion()},{"cases",results},{"inputEvents",0},{"deviceActions",false}}).toJson());
  std::puts("TRIP_STATS_NATIVE_PREVIEW_PASS");
}
