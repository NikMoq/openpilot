// QtCore-only offline test. Pass the newly built main_ru.qm as argv[1].
// c++ -std=c++17 -fPIC -I. tools/ui_build/camera_error_test.cc
//     $(pkg-config --cflags --libs Qt5Core) -o /tmp/camera-error-test
#include <QCoreApplication>
#include <QTranslator>
#include <QFile>
#include <QXmlStreamReader>
#include <cstdio>
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/camera_error_ru.h"

int main(int argc, char **argv) {
  QCoreApplication app(argc, argv);
  QTranslator translator;
  if (argc != 3 || !translator.load(QString::fromLocal8Bit(argv[1])) || !app.installTranslator(&translator)) return 1;
  const QString ru = QStringLiteral("main_ru");
  if (CameraErrorRu::present(QStringLiteral("invalid iGo row 42"), ru) != QString::fromUtf8("Ошибка в строке iGo 42")) return 2;
  if (CameraErrorRu::present(QStringLiteral("speed-camera snapshot exceeds size limit"), ru) != QString::fromUtf8("База камер превышает допустимый размер")) return 3;
  const QString diagnostic = QStringLiteral("SSL: https://example.test/a?b=1; %1; errno=2");
  const QString input = QStringLiteral("speed-camera download read timeout; body_bytes=77; ") + diagnostic;
  const QString result = CameraErrorRu::present(input, ru);
  if (!result.endsWith(diagnostic) || !result.contains(QStringLiteral("body_bytes=77")) || result == input) return 4;
  for (const QString &unknown : {QStringLiteral("HTTP Error 403: Forbidden"), QStringLiteral("invalid iGo row 3\n"), QStringLiteral("prefix invalid iGo row 3")}) {
    if (CameraErrorRu::present(unknown, ru) != unknown) return 5;
  }
  if (CameraErrorRu::present(input, QStringLiteral("main_en")) != input) return 6;
  const QString connection = CameraErrorRu::present(QStringLiteral("speed-camera download connection timeout; body_bytes=0; speed-camera snapshot exceeds size limit"), ru);
  if (!connection.startsWith(QString::fromUtf8("Истекло время подключения")) || !connection.endsWith(QString::fromUtf8("База камер превышает допустимый размер"))) return 7;
  if (CameraErrorRu::present(QStringLiteral("speed-camera endpoint requires complete HTTP 200; got 503"), ru) != QString::fromUtf8("Для загрузки базы камер нужен HTTP 200; получен 503")) return 8;
  if (CameraErrorRu::present(QStringLiteral("speed-camera HTTP deadline/read timeout after 12.345s; wire_bytes=123"), ru) != QString::fromUtf8("Истекло время ожидания HTTP базы камер: 12.345 с; wire_bytes=123")) return 9;
  QFile sourceFile(QString::fromLocal8Bit(argv[2]));
  if (!sourceFile.open(QIODevice::ReadOnly)) return 10;
  QXmlStreamReader xml(&sourceFile);
  QString context, source, expected;
  int checked = 0;
  while (!xml.atEnd()) {
    xml.readNext();
    if (xml.isStartElement()) {
      if (xml.name() == QStringLiteral("context")) context.clear();
      else if (xml.name() == QStringLiteral("name")) context = xml.readElementText();
      else if (xml.name() == QStringLiteral("message")) { source.clear(); expected.clear(); }
      else if (context == QStringLiteral("CameraError") && xml.name() == QStringLiteral("source")) source = xml.readElementText();
      else if (context == QStringLiteral("CameraError") && xml.name() == QStringLiteral("translation")) expected = xml.readElementText();
    } else if (xml.isEndElement() && xml.name() == QStringLiteral("message") && context == QStringLiteral("CameraError") && !source.contains(QStringLiteral("%1"))) {
      if (expected.isEmpty() || CameraErrorRu::present(source, ru) != expected) return 11;
      ++checked;
    }
  }
  if (xml.hasError() || checked != 38) return 12;
  std::printf("CAMERA_ERROR_STATIC_CONTEXT_COUNT=%d DYNAMIC_TEMPLATE_COUNT=5\n", checked);
  std::puts("CAMERA_ERROR_PRESENTATION_TEST_PASS");
  return 0;
}
