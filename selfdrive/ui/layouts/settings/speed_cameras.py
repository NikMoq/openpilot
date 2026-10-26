import datetime

from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import button_item, text_item
from openpilot.system.ui.widgets.scroller import Scroller


class SpeedCameraLayout(Widget):
  """Offroad controls and status for the mapd speed-camera snapshot."""

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._scroller = Scroller([
      text_item("Status", self._status),
      text_item("Last update", self._last_update),
      text_item("Database version", lambda: self._params.get("SpeedCameraDatabaseVersion") or "default"),
      button_item("Update database", "UPDATE NOW", description=self._update_description, callback=self._request_update),
    ], line_separator=True, spacing=0)

  def _status(self) -> str:
    return self._params.get("SpeedCameraDatabaseStatus") or "not downloaded"

  def _last_update(self) -> str:
    raw = self._params.get("SpeedCameraDatabaseUpdatedAt") or "0"
    try:
      timestamp = float(raw)
      return "never" if timestamp <= 0 else datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError):
      return "unknown"

  def _update_description(self) -> str:
    error = self._params.get("SpeedCameraDatabaseError")
    return f"Last error: {error}" if error else "Downloads the configured regional camera database on the next mapd cycle."

  def _request_update(self):
    # mapd refreshes on its normal loop; resetting the timestamp makes the
    # next cycle perform an immediate download without touching the cache.
    self._params.put("SpeedCameraDatabaseUpdatedAt", "0")
    self._params.put("SpeedCameraDatabaseStatus", "update requested")

  def _render(self, rect):
    self._scroller.render(rect)
