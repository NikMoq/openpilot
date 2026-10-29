"""Native offset contract: camera limits use only standard SLA offset settings.

Legacy camera-only key storage remains backward-compatible but has no control
consumer. Actual resolver/SLA execution uses explicit native IO boundaries from
our fresh integration fixture; no prior build or generated modules are loaded.
"""
import re
from pathlib import Path

import pytest
from test_camera_resolver_integration import Params, control, update

ROOT = Path(__file__).resolve().parents[2]


def test_native_registry_reaction_distance_and_legacy_storage():
  registry = (ROOT / "common/params_keys.h").read_text(encoding="utf-8")
  for key, kind, default in [("SpeedCameraReactionDistance", "INT", "300"),
                             ("SpeedCameraSpeedOffsetKph", "STRING", "0")]:
    entries = re.findall(r'\{"' + key + r'",\s*\{([^}]+)\}\}', registry)
    assert len(entries) == 1
    flags, stored_kind, stored_default = [field.strip() for field in entries[0].split(",")]
    assert {flag.strip() for flag in flags.split("|")} == {"PERSISTENT", "BACKUP"}
    assert stored_kind == kind and stored_default.strip('"') == default


def test_legacy_camera_offset_has_no_production_control_consumer():
  for relative in ['longitudinal_planner.py', 'speed_limit/speed_limit_resolver.py', 'speed_limit/speed_limit_assist.py',
                   'camera_speed_adapter.py']:
    source = (ROOT / 'sunnypilot/selfdrive/controls/lib' / relative).read_text()
    assert 'SpeedCameraSpeedOffsetKph' not in source


@pytest.mark.parametrize('metric,kind,value,expected', [
  (True, 0, 15, 60/3.6), (True, 1, 15, 75/3.6), (True, 2, 10, 66/3.6),
  (False, 1, 15, 60/3.6 + 15*.44704), (False, 2, 10, 66/3.6),
])
def test_standard_offset_exactly_once(control, monkeypatch, metric, kind, value, expected):
  monkeypatch.setitem(Params.values, 'IsMetric', metric)
  monkeypatch.setitem(Params.values, 'SpeedLimitOffsetType', kind)
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', value)
  control.resolver.frame = 0
  assert update(control) == pytest.approx(expected)
  assert control.resolver.speed_limit == pytest.approx(60/3.6)
  assert control.resolver.speed_limit_final == pytest.approx(expected)
  assert control.cs.vCruise == 100


@pytest.mark.parametrize('value', range(31))
@pytest.mark.parametrize('metric', [True, False])
def test_all_fixed_offset_steps_in_native_units(control, monkeypatch, metric, value):
  monkeypatch.setitem(Params.values, 'IsMetric', metric)
  monkeypatch.setitem(Params.values, 'SpeedLimitOffsetType', 1)
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', value)
  control.resolver.frame = 0
  expected = 60/3.6 + value * (1/3.6 if metric else .44704)
  assert control.resolver.camera.output.state == 'IDLE'
  assert update(control) == pytest.approx(min(100/3.6, expected))
  assert control.resolver.speed_limit_final == pytest.approx(expected)


@pytest.mark.parametrize('legacy', [None, '', 'bad', 'nan', 'inf', '-inf', '-1', '30.01', '1.5', 'true', '17'])
def test_invalid_or_valid_legacy_offset_cannot_affect_native_control(control, monkeypatch, legacy):
  monkeypatch.setitem(Params.values, 'SpeedCameraSpeedOffsetKph', legacy)
  assert update(control) == pytest.approx(75/3.6)
