"""UTC timestamps for source freshness; every network deadline uses monotonic time."""
import time


def utc_now_s() -> float:
  # Published Waze times and filesystem mtimes use the UTC epoch, not uptime.
  return time.time()  # noqa: TID251
