"""Offline guards for presentation-only Russian additions; no control imports."""
import ast
from pathlib import Path
import re
import hashlib
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]
CAMERA = 'selfdrive/ui/sunnypilot/qt/onroad/camera_warning.h'
SETTINGS = 'selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.cc'


BASELINE = json.loads((Path(__file__).with_name('russian_presentation_baseline.json')).read_text(encoding='utf-8'))


def assert_normalized(test, path, text):
  test.assertEqual(BASELINE['normalizedSourceSha256'][path], hashlib.sha256(text.encode('utf-8')).hexdigest())


def control_math_calls(source):
  calls = []
  pattern = r'\b(?:std::(?:atoi|atof|clamp|isfinite|round|floor|ceil)|qRound|hasLongitudinalControl|hasIntelligentCruiseButtonManagement)\s*\('
  for match in re.finditer(pattern, source):
    depth, quote, escaped = 1, '', False
    end = match.end()
    while end < len(source) and depth:
      char = source[end]
      if quote:
        if escaped:
          escaped = False
        elif char == '\\':
          escaped = True
        elif char == quote:
          quote = ''
      elif char in ['"', "'"]:
        quote = char
      elif char == '(':
        depth += 1
      elif char == ')':
        depth -= 1
      end += 1
    calls.append(source[match.start():end])
  return calls


class RussianPresentationTest(unittest.TestCase):
  def test_cruise_help_is_only_approved_presentation(self):
    path = 'selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal_panel.cc'
    current = (ROOT / path).read_text(encoding='utf-8')
    current = current.replace('  russian_help = params.get("LanguageSetting") == "main_ru";\n', '')
    current = current.replace('Intelligent Cruise Button Management (ICBM)', 'Intelligent Cruise Button Management (ABCM)')
    current = re.sub(r'  if \(russian_help\) {\n    for \(auto \*control : findChildren<AbstractControlSP \*>\(\)\) control->setDescriptionFontSize\(34\);\n    connect\(device\(\), &Device::displayPowerChanged, this, \[this\]\(bool on\) {\n      if \(!on\) collapseRussianHelp\(\);\n    }\);\n  }\n', '', current)
    current = re.sub(r'void LongitudinalPanel::collapseRussianHelp\(\) {\n  if \(!russian_help\) return;\n  for \(auto \*control : findChildren<AbstractControlSP \*>\(\)\) control->hideDescription\(\);\n}\n\n', '', current)
    current = current.replace('  collapseRussianHelp();\n', '').replace('if (!russian_help) ', '')
    for name in ['onroadOnlyDescription', 'accPcmCruiseDisabledDescription', 'accNoLongDescription']:
      current = current.replace('russian_help ? accEnabledDescription + "<br><br>" + ' + name + ' : ' + name, name)
    assert_normalized(self, path, current)

  def test_ordered_control_calls_and_signal_emissions(self):
    self.assertGreater(len(BASELINE['files']), 20)  # Never silently skip after a commit.
    for path, before in BASELINE['files'].items():
      after = (ROOT / path).read_text(encoding='utf-8')
      for index, pattern in enumerate(BASELINE['patterns']):
        with self.subTest(path=path, pattern=pattern):
          self.assertEqual(before['orderedCalls'][index], re.findall(pattern, after))
      with self.subTest(path=path, invariant='ordered control math calls'):
        self.assertEqual(before['orderedMath'], control_math_calls(after))
      # Existing signal/slot references remain present in their original order.
      remaining = iter(re.findall(r'&\w+::\w+', after))
      self.assertTrue(all(any(candidate == expected for candidate in remaining) for expected in before['signalSlotReferences']))
    for path in BASELINE['newPresentationHelpers']:
      text = (ROOT / path).read_text(encoding='utf-8')
      with self.subTest(helper=path):
        self.assertIsNone(re.search(r'\b(?:params\w*|Params\(\))\.(?:put\w*|remove)\s*\(', text))
        self.assertIsNone(re.search(r'\btoggleFlipped\s*\(', text))

  def test_camera_changes_are_only_translation_wrappers(self):
    current = (ROOT / CAMERA).read_text(encoding='utf-8')
    current = re.sub(r'QObject::tr\(("[^"\n]*")\)', r'\1', current)
    assert_normalized(self, CAMERA, current)

  def test_camera_params_and_enable_conditions_unchanged(self):
    current = (ROOT / SETTINGS).read_text(encoding='utf-8')
    # Exercise the exact C++ regex as a decoded string, including delimiters.
    encoded = re.search(r'QRegularExpression retry\(("[^\n]+")\)', current).group(1)
    retry = re.compile(ast.literal_eval(encoded))
    self.assertEqual(retry.fullmatch('retrying in 5s (1/2)').groups(), ('5', '1', '2'))
    for technical in ['retrying in -1s (1/2)', 'prefix retrying in 5s (1/2)', 'ready: custom']:
      self.assertIsNone(retry.fullmatch(technical))
    self.assertIn('return status;', current)
    # Full-file round trip protects function boundaries and every upstream
    # camera refresh statement, not just selected parameter calls.
    normalized = current.replace('#include <QRegularExpression>\n', '')
    normalized = normalized.replace('#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/camera_error_ru.h"\n', '')
    normalized = re.sub(r'// Display only: persisted updater protocol values stay unchanged\.\nQString cameraStatus\(.*?\n}\n\n', '', normalized, flags=re.S)
    normalized = normalized.replace('cameraStatus(status)', 'status.isEmpty() ? tr("not downloaded") : status')
    normalized = normalized.replace('tr("km/h")', '"km/h"').replace('tr("mph")', '"mph"')
    normalized = normalized.replace('CameraErrorRu::present(error, QString::fromStdString(params.get("LanguageSetting")))', 'error')
    normalized = normalized.replace('if (params.get("LanguageSetting") != "main_ru") ', '')
    assert_normalized(self, SETTINGS, normalized)

  def test_mapper_caller_changes_only_text_fields(self):
    path = 'selfdrive/ui/qt/onroad/alerts.cc'
    current = (ROOT / path).read_text(encoding='utf-8')
    current = current.replace('#include "selfdrive/ui/qt/onroad/alerts_ru.h"\n', '')
    current = current.replace('#include "common/params.h"\n', '')
    current = current.replace('  const QString language = QString::fromStdString(Params().get("LanguageSetting"));\n', '')
    current = re.sub(r'  a\.text[12] = CommaRu::presentAlertText\(a\.text[12], language\);\n', '', current)
    assert_normalized(self, path, current)
    builder = (ROOT / 'tools/ui_build/build.py').read_text(encoding='utf-8')
    self.assertEqual(builder.count('sources.append("selfdrive/ui/qt/onroad/alerts_ru.cpp")'), 1)


if __name__ == '__main__':
  unittest.main()
