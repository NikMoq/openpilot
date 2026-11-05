package org.sunnypilot.yandexprobe;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import com.yandex.mapkit.MapKitFactory;
import com.yandex.mapkit.RequestPoint;
import com.yandex.mapkit.RequestPointType;
import com.yandex.mapkit.directions.DirectionsFactory;
import com.yandex.mapkit.directions.driving.DrivingOptions;
import com.yandex.mapkit.directions.driving.DrivingRoute;
import com.yandex.mapkit.directions.driving.DrivingRouter;
import com.yandex.mapkit.directions.driving.DrivingRouterType;
import com.yandex.mapkit.directions.driving.DrivingSession;
import com.yandex.mapkit.directions.driving.Event;
import com.yandex.mapkit.directions.driving.VehicleOptions;
import com.yandex.mapkit.geometry.Point;
import com.yandex.mapkit.geometry.PolylinePosition;
import com.yandex.mapkit.road_events.EventTag;
import java.util.Arrays;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** Diagnostic only: one normal Full MapKit request, with a caller-owned API key. */
public final class RouteProbe implements DrivingSession.DrivingRouteListener {
  public interface Callback {
    void onSnapshot(String json);
    void onFailure(String category);
  }

  private final Handler handler = new Handler(Looper.getMainLooper());
  private final Callback callback;
  private final Runnable timeout = () -> finish(null, "timeout");
  private DrivingRouter router;
  private DrivingSession session;
  private boolean startedLifecycle;
  private boolean done;

  private RouteProbe(Callback callback) { this.callback = callback; }

  /** Invoke once on the Android main thread; the returned object must be retained. */
  public static RouteProbe start(Context context, String ownApiKey, Point from, Point to,
                                 long timeoutMs, Callback callback) {
    requireMainThread();
    if (context == null || ownApiKey == null || ownApiKey.trim().isEmpty() || callback == null
        || !valid(from) || !valid(to) || timeoutMs < 1000 || timeoutMs > 60000) {
      throw new IllegalArgumentException("Invalid probe arguments");
    }
    RouteProbe probe = new RouteProbe(callback);
    probe.handler.postDelayed(probe.timeout, timeoutMs);
    try {
      // No key field, log, file, intent or result serialization in this probe.
      MapKitFactory.setApiKey(ownApiKey);
      MapKitFactory.initialize(context.getApplicationContext());
      MapKitFactory.getInstance().onStart();
      probe.startedLifecycle = true;
      probe.router = DirectionsFactory.getInstance().createDrivingRouter(DrivingRouterType.ONLINE);
      List<RequestPoint> points = Arrays.asList(
          new RequestPoint(from, RequestPointType.WAYPOINT, null, null, null),
          new RequestPoint(to, RequestPointType.WAYPOINT, null, null, null));
      DrivingSession created = probe.router.requestRoutes(points,
          new DrivingOptions().setRoutesCount(1), new VehicleOptions(), probe);
      if (probe.done) created.cancel(); else probe.session = created;
    } catch (RuntimeException | AssertionError | LinkageError error) {
      probe.finish(null, "initialization_or_request:" + error.getClass().getSimpleName());
    }
    return probe;
  }

  public void cancel() { requireMainThread(); finish(null, "cancelled"); }

  @Override public void onDrivingRoutes(List<DrivingRoute> routes) {
    if (done) return;
    try {
      if (routes == null || routes.size() != 1) {
        finish(null, "expected_one_route");
        return;
      }
      DrivingRoute route = routes.get(0);
      List<Point> geometry = route.getGeometry().getPoints();
      List<Event> events = route.getEvents();
      List<Float> limits = route.getSpeedLimits();
      if (geometry.size() > 100000 || events.size() > 10000 || limits.size() > 100000) {
        finish(null, "snapshot_budget_exceeded");
        return;
      }
      JSONArray points = new JSONArray(), rawEvents = new JSONArray(), rawLimits = new JSONArray();
      for (Point point : geometry) points.put(point(point));
      for (Float limit : limits) rawLimits.put(rawNumber(limit));
      for (Event event : events) {
        JSONArray tags = new JSONArray();
        for (EventTag tag : event.getTags()) {
          tags.put(new JSONObject().put("name", tag.name()).put("sdk_ordinal", tag.ordinal()));
        }
        PolylinePosition position = event.getPolylinePosition();
        rawEvents.put(new JSONObject().put("event_id", event.getEventId())
            .put("location", point(event.getLocation())).put("tags", tags)
            .put("speed_limit_raw", rawNumber(event.getSpeedLimit()))
            .put("polyline_segment_index", position.getSegmentIndex())
            .put("polyline_segment_position", position.getSegmentPosition()));
      }
      String result = new JSONObject().put("schema_version", 1)
          .put("source", "yandex_mapkit_full_sdk").put("sdk_build", "4.45.0-full")
          .put("observed_at_ms", System.currentTimeMillis()).put("speed_unit", "unknown")
          .put("conditions_outdated", route.isAreConditionsOutdated())
          .put("route_geometry_lat_lon", points).put("segment_speed_limits_raw", rawLimits)
          .put("events", rawEvents).toString();
      finish(result, null);
    } catch (RuntimeException | JSONException error) {
      finish(null, "snapshot:" + error.getClass().getSimpleName());
    }
  }

  @Override public void onDrivingRoutesError(com.yandex.runtime.Error error) {
    finish(null, "sdk_error:" + (error == null ? "unknown" : error.getClass().getSimpleName()));
  }

  private void finish(String snapshot, String error) {
    if (done) return;
    done = true;
    handler.removeCallbacks(timeout);
    try { if (session != null) session.cancel(); } catch (RuntimeException ignored) { }
    session = null;
    router = null;
    try { if (startedLifecycle) MapKitFactory.getInstance().onStop(); } catch (RuntimeException ignored) { }
    if (snapshot != null) callback.onSnapshot(snapshot); else callback.onFailure(error);
  }

  private static JSONArray point(Point p) throws JSONException {
    return new JSONArray().put(p.getLatitude()).put(p.getLongitude());
  }
  private static Object rawNumber(Float value) {
    return value == null || !Float.isFinite(value) ? JSONObject.NULL : value;
  }
  private static boolean valid(Point p) {
    return p != null && Double.isFinite(p.getLatitude()) && Double.isFinite(p.getLongitude())
        && Math.abs(p.getLatitude()) <= 85 && Math.abs(p.getLongitude()) <= 180;
  }
  private static void requireMainThread() {
    if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("Android main thread required");
  }
}
