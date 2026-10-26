// QtCore-only display contract test: c++ -std=c++17 -fPIC -I. ... $(pkg-config --cflags --libs Qt5Core)
#include <QCoreApplication>
#include <QJsonArray>
#include <cassert>
#include <cstdio>
#include "selfdrive/ui/sunnypilot/qt/widgets/drive_stats_data.h"

int main(int argc, char **argv) {
  QCoreApplication app(argc, argv);
  using namespace DriveStatsData;
  Snapshot stats;
  auto all = QJsonObject{{"routes", 2}, {"distance_m", 1000.0}, {"duration_s", 900.0}};
  auto week = QJsonObject{{"routes", 1}, {"distance_m", 500.0}, {"duration_s", 450.0}};
  auto root = QJsonObject{{"version", 1}, {"all", all}, {"week", week}};
  auto bytes = [&]() { return QJsonDocument(root).toJson(); };
  assert(parse(bytes(), stats));
  assert(stats.all.routes == 2 && stats.all.distance_m == 1000 && stats.week.duration_s == 450);
  root["coverage"] = QJsonObject{{"routes_incomplete",true},{"week_routes_incomplete",false}};
  assert(parse(bytes(), stats) && stats.all_routes_incomplete && !stats.week_routes_incomplete);
  root["coverage"] = QJsonObject{{"routes_incomplete","unknown"}};
  assert(!parse(bytes(), stats));
  root["coverage"] = "corrupt"; assert(!parse(bytes(), stats));
  root.remove("coverage");
  const Snapshot original = stats;
  root["all"] = QJsonObject{}; assert(!parse(bytes(), stats));
  assert(stats.all.distance_m == original.all.distance_m);  // No partial output.
  root["all"] = all; root["version"] = 1.5; assert(!parse(bytes(), stats));
  root["version"] = 1;
  for (const QJsonValue bad : {QJsonValue(-1), QJsonValue("123"), QJsonValue(), QJsonValue(1e20)}) {
    all["distance_m"] = bad; root["all"] = all; assert(!parse(bytes(), stats));
  }
  all["distance_m"] = 1000; all["routes"] = 1.5; root["all"] = all; assert(!parse(bytes(), stats));
  all["routes"] = 2; root["all"] = all; week["distance_m"] = 1001; root["week"] = week; assert(!parse(bytes(), stats));
  assert(!parse("{", stats)); assert(!parse("[]", stats));
  assert(!parse(QByteArray(16 * 1024 * 1024 + 1, ' '), stats));
  std::puts("TRIP_STATS_DISPLAY_CONTRACT_PASS");
}
