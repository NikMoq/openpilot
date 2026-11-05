package org.sunnypilot.yandexprobe;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Process;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import com.yandex.mapkit.geometry.Point;
import com.yandex.mapkit.map.CameraPosition;
import com.yandex.mapkit.mapview.MapView;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.Arrays;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONArray;
import org.json.JSONObject;

/** Ordinary installed-package harness. No map widgets, GPS or key in Intent extras. */
public final class ProbeActivity extends Activity {
  private RouteProbe probe;
  private boolean completed;
  private String requestId;
  private JSONObject sdkDiagnostics = new JSONObject();
  private MapView mapView;

  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    try {
      Intent input = getIntent();
      requestId = input.getStringExtra("requestId");
      if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{1,64}")) {
        throw new IllegalArgumentException("Invalid request identity");
      }
      if (input.getBooleanExtra("networkOnly", false)) {
        networkDiagnostic();
        return;
      }
      String[] required = {"fromLatitude", "fromLongitude", "toLatitude", "toLongitude"};
      for (String name : required) {
        if (!input.hasExtra(name)) throw new IllegalArgumentException("Missing coordinate input");
      }
      Point from = new Point(coordinate(input, required[0]), coordinate(input, required[1]));
      Point to = new Point(coordinate(input, required[2]), coordinate(input, required[3]));
      long timeout = input.getLongExtra("timeoutMillis", 45000);
      String key = readKey(new File(getFilesDir(), "mapkit.key"));
      probe = RouteProbe.start(this, key, from, to, timeout, new RouteProbe.Callback() {
        @Override public void onSdkInitialized() {
          if (input.getBooleanExtra("withMapView", false)) {
            mapView = new MapView(ProbeActivity.this);
            setContentView(mapView);
            mapView.getMapWindow().getMap().move(new CameraPosition(from, 14.0f, 0.0f, 0.0f));
            mapView.onStart();
          }
        }
        @Override public void onSdkStopping() {
          if (mapView != null) {
            mapView.onStop();
            mapView = null;
          }
        }
        @Override public void onDiagnostics(String json) {
          try { sdkDiagnostics = new JSONObject(json); } catch (Exception ignored) { }
        }
        @Override public void onSnapshot(String json) { complete(json); }
        @Override public void onFailure(String category) { failure(category); }
      });
      key = null;
    } catch (Exception | LinkageError | AssertionError error) {
      failure("startup:" + error.getClass().getSimpleName());
    }
  }

  private void networkDiagnostic() {
    Thread worker = new Thread(() -> {
      try {
        JSONArray results = new JSONArray();
        String[] endpoints = {"https://yandex.ru/",
            "https://proxy.mob.maps.yandex.net/mapkit2/driving/2.x/route"};
        for (String endpoint : endpoints) {
          URL url = new URL(endpoint);
          JSONObject result = new JSONObject().put("host", url.getHost());
          HttpsURLConnection connection = null;
          try {
            connection = (HttpsURLConnection) url.openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(8000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            result.put("http_status", connection.getResponseCode());
          } catch (Exception error) {
            JSONArray causes = new JSONArray();
            Throwable current = error;
            for (int depth = 0; current != null && depth < 8; depth++, current = current.getCause()) {
              causes.put(current.getClass().getSimpleName());
            }
            result.put("cause_classes", causes);
          } finally {
            if (connection != null) connection.disconnect();
          }
          results.put(result);
        }
        String json = new JSONObject().put("source", "android_https_diagnostic")
            .put("status", "completed").put("results", results).toString();
        runOnUiThread(() -> complete(json));
      } catch (Exception error) {
        String category = "network_diagnostic:" + error.getClass().getSimpleName();
        runOnUiThread(() -> failure(category));
      }
    }, "yandex-https-diagnostic");
    worker.setDaemon(true);
    worker.start();
  }

  private static double coordinate(Intent input, String name) {
    Object value = input.getExtras().get(name);
    if (value instanceof Double) return (Double) value;
    // adb am start has string/float extras, but no double-extra switch.
    if (value instanceof String && ((String) value).length() <= 32) {
      return Double.parseDouble((String) value);
    }
    throw new IllegalArgumentException("Invalid coordinate type");
  }

  private String readKey(File file) throws Exception {
    FileDescriptor descriptor = Os.open(file.getAbsolutePath(), OsConstants.O_RDONLY | OsConstants.O_NOFOLLOW, 0);
    byte[] bytes = new byte[257];
    try (FileInputStream input = new FileInputStream(descriptor)) {
      StructStat status = Os.fstat(descriptor);
      if (!OsConstants.S_ISREG(status.st_mode) || (status.st_mode & 0777) != 0600
          || status.st_uid != Process.myUid() || status.st_size < 1 || status.st_size > 256) {
        throw new IllegalArgumentException("Invalid private key input");
      }
      int length = 0, count;
      while (length < bytes.length && (count = input.read(bytes, length, bytes.length - length)) != -1) length += count;
      if (length < 1 || length > 256) throw new IllegalArgumentException("Invalid key size");
      String value = new String(bytes, 0, length, StandardCharsets.UTF_8).trim();
      if (value.isEmpty()) throw new IllegalArgumentException("Empty key input");
      return value;
    } finally { Arrays.fill(bytes, (byte) 0); }
  }

  private void failure(String category) {
    try { complete(new JSONObject().put("status", "not_completed").put("category", category).toString()); }
    catch (Exception ignored) { finish(); }
  }

  private void complete(String json) {
    if (completed) return;
    completed = true;
    File temporary = new File(getFilesDir(), "result.json.tmp");
    try {
      String result = new JSONObject(json).put("request_id", requestId == null ? "unassigned" : requestId)
          .put("with_map_view", mapView != null || getIntent().getBooleanExtra("withMapView", false))
          .put("sdk_diagnostics", sdkDiagnostics)
          .put("completed_at_ms", System.currentTimeMillis()).toString();
      try (FileOutputStream output = new FileOutputStream(temporary)) {
        output.write(result.getBytes(StandardCharsets.UTF_8));
        output.flush();
        output.getFD().sync();
      }
      Os.chmod(temporary.getAbsolutePath(), 0600);
      Files.move(temporary.toPath(), new File(getFilesDir(), "result.json").toPath(),
          StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    } catch (Exception ignored) {
      temporary.delete(); // No exception text or key contents enter logs/results.
    } finally { finish(); }
  }

  @Override protected void onDestroy() {
    if (probe != null) probe.cancel();
    super.onDestroy();
  }
}
