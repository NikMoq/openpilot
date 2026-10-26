from pathlib import Path

from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, load_database, save_database
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera


def test_camera_database_round_trip(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  database = CameraDatabase(
    version="replay-1",
    fetched_at=1_700_000_000,
    cameras=(SpeedCamera(55.75, 37.61, 60, 180, "1"),),
  )
  save_database(str(path), database)
  loaded = load_database(str(path))
  assert loaded is not None
  assert loaded.version == "replay-1"
  assert loaded.cameras == database.cameras


def test_camera_database_rejects_corrupt_snapshot(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  path.write_text('{"version": "broken", "data": [', encoding="utf-8")
  assert load_database(str(path)) is None
