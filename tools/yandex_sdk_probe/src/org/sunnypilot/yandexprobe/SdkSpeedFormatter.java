package org.sunnypilot.yandexprobe;

import com.yandex.runtime.i18n.CanonicalUnit;
import com.yandex.runtime.i18n.I18nManager;
import com.yandex.runtime.i18n.I18nManagerFactory;
import org.json.JSONException;
import org.json.JSONObject;

/** SDK formatter observation only; does not establish the caller field's units. */
public final class SdkSpeedFormatter {
  private SdkSpeedFormatter() { }

  public static JSONObject describe(float raw) throws JSONException {
    return describe(Float.valueOf(raw));
  }

  /** Call after normal SDK initialization on its main thread. No conversion factor. */
  public static JSONObject describe(Float raw) throws JSONException {
    JSONObject result = new JSONObject().put("raw", raw == null || !Float.isFinite(raw) ? JSONObject.NULL : raw)
        .put("input_unit", "unknown").put("source", "official_sdk_i18n_formatter");
    if (raw == null || !Float.isFinite(raw)) {
      return result.put("status", raw == null ? "missing" : "non_finite");
    }
    try {
      I18nManager formatter = I18nManagerFactory.getI18nManagerInstance();
      CanonicalUnit canonical = formatter.canonicalSpeed(raw.doubleValue());
      double value = canonical.getValue();
      return result.put("canonical_value", Double.isFinite(value) ? value : JSONObject.NULL)
          .put("canonical_unit", bounded(canonical.getUnit()))
          .put("localized", bounded(formatter.localizeSpeed(raw.doubleValue())))
          .put("status", "formatted");
    } catch (RuntimeException | LinkageError | AssertionError error) {
      return result.put("status", "formatter_unavailable").put("error_class", error.getClass().getSimpleName());
    }
  }

  private static Object bounded(String text) {
    return text == null ? JSONObject.NULL : text.substring(0, Math.min(256, text.length()));
  }
}
