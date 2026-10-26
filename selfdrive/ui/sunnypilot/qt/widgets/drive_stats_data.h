#pragma once

#include <cmath>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>

// Presentation contract shared by both local trip cards. Distances are metres.
// Reject incomplete records instead of showing missing fields as a measured zero.
namespace DriveStatsData {
struct Totals {
  qint64 routes = 0;
  double distance_m = 0;
  double duration_s = 0;
};
struct Snapshot { Totals all, week; bool all_routes_incomplete = false, week_routes_incomplete = false; };

inline bool nonnegative(const QJsonValue &value, double maximum) {
  return value.isDouble() && std::isfinite(value.toDouble()) && value.toDouble() >= 0 && value.toDouble() <= maximum;
}

inline bool parseTotals(const QJsonValue &value, Totals &out) {
  if (!value.isObject()) return false;
  const auto object = value.toObject();
  const auto routes = object.value("routes");
  const auto distance = object.value("distance_m");
  const auto duration = object.value("duration_s");
  if (!nonnegative(routes, 1e9) || std::floor(routes.toDouble()) != routes.toDouble() ||
      !nonnegative(distance, 1e12) || !nonnegative(duration, 1e12)) return false;
  out = {qint64(routes.toDouble()), distance.toDouble(), duration.toDouble()};
  return out.distance_m <= 1e12 && out.duration_s <= 1e12;
}

inline bool parse(const QByteArray &bytes, Snapshot &out) {
  if (bytes.size() > 16 * 1024 * 1024) return false;
  QJsonParseError error;
  const auto document = QJsonDocument::fromJson(bytes, &error);
  if (error.error != QJsonParseError::NoError || !document.isObject()) return false;
  const auto object = document.object();
  if (!object.value("version").isDouble() || object.value("version").toDouble() != 1) return false;
  Snapshot candidate;
  if (!parseTotals(object.value("all"), candidate.all) || !parseTotals(object.value("week"), candidate.week)) return false;
  if (candidate.week.routes > candidate.all.routes || candidate.week.distance_m > candidate.all.distance_m + 0.001 ||
      candidate.week.duration_s > candidate.all.duration_s + 0.001) return false;
  if (object.contains("coverage") && !object.value("coverage").isObject()) return false;
  const auto coverage = object.value("coverage").toObject();
  for (const auto &key : {"routes_incomplete", "week_routes_incomplete"}) {
    if (coverage.contains(key) && !coverage.value(key).isBool()) return false;
  }
  candidate.all_routes_incomplete = coverage.value("routes_incomplete").toBool(false);
  candidate.week_routes_incomplete = coverage.value("week_routes_incomplete").toBool(candidate.all_routes_incomplete);
  out = candidate;
  return true;
}

inline bool loadLocal(Snapshot &out) {
  const QByteArray configured = qgetenv("TRIP_STATS_ROOT");
  const QString root = configured.isEmpty() ? QStringLiteral("/data/trip_stats") : QString::fromLocal8Bit(configured);
  QFile file(root + "/stats.json");
  if (!file.open(QIODevice::ReadOnly) || file.size() > 16 * 1024 * 1024) return false;
  return parse(file.readAll(), out);
}
}  // namespace DriveStatsData
