// Offline layout proposals, not an onroad implementation or live vehicle state.
#include <QApplication>
#include <QDir>
#include <QFontDatabase>
#include <QImage>
#include <QPainter>

static void text(QPainter &p, const QRect &r, const QString &value, int size,
                 QColor color = Qt::white, int align = Qt::AlignLeft | Qt::AlignVCenter) {
  p.setFont(QFont("Inter", -1, QFont::DemiBold));
  QFont font = p.font(); font.setPixelSize(size); p.setFont(font);
  p.setPen(color); p.drawText(r, align, value);
}

int main(int argc, char **argv) {
  QApplication app(argc, argv);
  if (argc != 2) return 2;
  const QDir fonts("selfdrive/assets/fonts");
  for (const auto &file : fonts.entryList({"*.ttf"}, QDir::Files)) QFontDatabase::addApplicationFont(fonts.filePath(file));
  const QString output = argv[1];
  if (!QDir().mkpath(output)) return 2;
  const QColor amber(255, 190, 60), muted(185, 195, 203);
  for (int variant = 1; variant <= 3; ++variant) {
    QImage image(2100, 1080, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(23, 43, 57));
    QPainter p(&image); p.setRenderHint(QPainter::Antialiasing);
    text(p, {850, 45, 400, 180}, "97", 176, Qt::white, Qt::AlignCenter);
    text(p, {850, 225, 400, 90}, "км/ч", 66, muted, Qt::AlignCenter);
    p.translate(40, 40);
    const int width = variant == 1 ? 520 : variant == 2 ? 760 : 680;
    const int height = variant == 1 ? 440 : variant == 2 ? 340 : 470;
    p.setBrush(QColor(0, 0, 0, 205)); p.setPen(QPen(amber, 3));
    p.drawRoundedRect(QRect(0, 0, width, height), 24, 24);
    if (variant == 2) {
      p.setPen(QPen(QColor(120, 120, 120), 2)); p.drawLine(245, 20, 245, height - 20);
      text(p, {22, 15, 200, 44}, "КРУИЗ", 32, muted);
      text(p, {22, 60, 200, 110}, "130", 90);
      text(p, {22, 188, 200, 40}, "ЛИМИТ ДОРОГИ", 24, muted);
      text(p, {22, 225, 200, 80}, "60", 70);
      text(p, {270, 18, 460, 58}, "КАМЕРА", 44, amber);
      text(p, {270, 80, 460, 95}, "150 м", 80, amber);
      text(p, {270, 178, 460, 68}, "60 км/ч", 58, amber);
      text(p, {270, 265, 460, 55}, "ЦЕЛЬ ACC 79 км/ч", 38, amber);
    } else {
      const int half = width / 2;
      text(p, {24, 14, half - 30, 44}, "КРУИЗ", 32, muted);
      text(p, {24, 62, half - 30, 112}, "130", 90);
      text(p, {half + 12, 14, half - 24, 44}, "ЛИМИТ ДОРОГИ", variant == 1 ? 24 : 28, muted);
      text(p, {half + 12, 62, half - 24, 112}, "60", 90);
      p.setPen(QPen(amber, 2)); p.drawLine(20, 185, width - 20, 185);
      text(p, {24, 202, width - 48, 52}, "КАМЕРА", variant == 1 ? 38 : 46, amber);
      const int large = variant == 1 ? 62 : 82;
      text(p, {24, 266, half - 24, 100}, "60", large, amber);
      text(p, {half, 266, half - 24, 100}, "150 м", large, amber, Qt::AlignRight | Qt::AlignVCenter);
      text(p, {24, 365, width - 48, 60}, "ЦЕЛЬ ACC 79 км/ч", variant == 1 ? 34 : 44, amber);
      if (variant == 3) text(p, {160, 285, 135, 66}, "км/ч", 34, amber);
    }
    p.end();
    if (!image.save(output + QString("/camera-layout-%1.png").arg(variant))) return 1;
  }
  return 0;
}
