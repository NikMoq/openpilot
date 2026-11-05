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
import com.yandex.runtime.logging.LogListener;
import com.yandex.runtime.logging.LogMessage;
import com.yandex.runtime.logging.Logging;
import com.yandex.runtime.logging.LoggingFactory;
import java.lang.ref.WeakReference;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** Diagnostic only: one normal Full MapKit request, with a caller-owned API key. */
public final class RouteProbe implements DrivingSession.DrivingRouteListener {
  public interface Callback {
    void onSnapshot(String json);
    void onFailure(String category);
    default void onDiagnostics(String sanitizedJson) { }
    default void onSdkInitialized() { }
    default void onSdkStopping() { }
  }

  private final Handler handler = new Handler(Looper.getMainLooper());
  private final Callback callback;
  private final Runnable timeout = () -> finish(null, "timeout");
  private DrivingRouter router;
  private DrivingSession session;
  private boolean startedLifecycle;
  private boolean done;
  private final String[] hints = {"forbidden", "auth_rejected", "tls_certificate", "dns",
      "connection_failed", "timeout", "config_failed", "signature_invalid", "api_key_mentioned",
      "invalid_key_hint", "missing_key_hint", "activation_hint", "service_hint", "quota_hint", "package_hint", "region_hint"};
  private final int[] diagnosticCounts = new int[hints.length + 200];
  private int diagnosticMessages;
  private boolean diagnosticsClosed;
  private Logging logging;
  private final LogListener logListener = this::classifyMessage;
  private final WeakReference<LogListener> logReference = new WeakReference<>(logListener);
  private static final Pattern HTTP_STATUS = Pattern.compile(
      "\\b(?:http(?:/[0-9.]+)?(?:\\s+status)?|status(?:\\s+code)?)(?:\\s+|\\s*[:=]\\s*)([45][0-9]{2})\\b",
      Pattern.CASE_INSENSITIVE);

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
      MapKitFactory.setLocale("ru_RU");
      MapKitFactory.setApiKey(ownApiKey);
      MapKitFactory.initialize(context.getApplicationContext());
      probe.logging = LoggingFactory.getLogging();
      probe.logging.subscribe(probe.logReference);
      callback.onSdkInitialized();
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
    try { if (logging != null) logging.unsubscribe(logReference); } catch (RuntimeException ignored) { }
    try { callback.onDiagnostics(diagnosticSnapshot()); } catch (RuntimeException ignored) { }
    try { if (session != null) session.cancel(); } catch (RuntimeException ignored) { }
    session = null;
    router = null;
    try { callback.onSdkStopping(); } catch (RuntimeException ignored) { }
    try { if (startedLifecycle) MapKitFactory.getInstance().onStop(); } catch (RuntimeException ignored) { }
    if (snapshot != null) callback.onSnapshot(snapshot); else callback.onFailure(error);
  }

  private synchronized void classifyMessage(LogMessage message) {
    if (diagnosticsClosed || diagnosticMessages >= 4096 || message == null) return;
    diagnosticMessages++;
    String raw = message.getMessage(); // Never request scope/verbose info or retain raw SDK text.
    if (raw == null) return;
    String text = raw.substring(0, Math.min(raw.length(), 8192)).toLowerCase(Locale.ROOT);
    boolean failed = text.contains("fail") || text.contains("error") || text.contains("invalid");
    boolean[] matches = {text.contains("forbidden"), text.contains("unauthorized") || text.contains("not authorized")
        || text.contains("invalid api key") || text.contains("authentication failed"),
        text.contains("certificate") && (failed || text.contains("verify") || text.contains("not ok")),
        text.contains("could not resolve") || text.contains("name resolution failed") || text.contains("unknown host"),
        text.contains("connection refused") || text.contains("connection reset") || text.contains("failed to connect")
        || text.contains("couldn't connect") || text.contains("connection failed"),
        text.contains("timed out") || text.contains("timeout"), text.contains("config") && failed,
        text.contains("signature") && (failed || text.contains("mismatch")),
        text.contains("api key") || text.contains("api-key") || text.contains("api_key") || text.contains("apikey"),
        text.contains("invalid key") || text.contains("invalid api") || text.contains("key is invalid"),
        text.contains("missing key") || text.contains("key is missing") || text.contains("empty key"),
        text.contains("activation") || text.contains("not activated"), text.contains("service") && failed,
        text.contains("quota") || text.contains("limit exceeded"), text.contains("package") || text.contains("application id"),
        text.contains("region") || text.contains("country")};
    for (int i = 0; i < matches.length; i++) if (matches[i]) diagnosticCounts[i]++;
    Matcher status = HTTP_STATUS.matcher(text);
    for (int i = 0; i < 16 && status.find(); i++) {
      int index = hints.length + Integer.parseInt(status.group(1)) - 400;
      diagnosticCounts[index] = Math.min(65535, diagnosticCounts[index] + 1);
    }
  }

  private synchronized String diagnosticSnapshot() {
    diagnosticsClosed = true;
    JSONObject output = new JSONObject(), counters = new JSONObject(), statuses = new JSONObject();
    try {
      for (int i = 0; i < hints.length; i++) counters.put(hints[i], diagnosticCounts[i]);
      for (int i = hints.length; i < diagnosticCounts.length; i++) if (diagnosticCounts[i] != 0) {
        statuses.put(Integer.toString(400 + i - hints.length), diagnosticCounts[i]);
      }
      return output.put("messages_inspected", diagnosticMessages).put("message_budget", 4096)
          .put("classification_hints", counters).put("explicit_http_status_counts", statuses).toString();
    } catch (JSONException ignored) { return "{\"diagnostics_unavailable\":true}"; }
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
