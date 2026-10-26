// Snapshot of tools/comma_ui_capture_hook_qt.cpp; linked UI QA, opt-in only.
#ifndef COMMA_UI_QA_LINKED
#error "Linked QA source requires COMMA_UI_QA_LINKED for helper and main.cc"
#endif
/* Qt5 raster-aware opt-in screenshot prototype. Does not load itself.
 * Shared build:
 * g++ -std=c++17 -O2 -Wall -Wextra -Werror -fPIC -shared
 *     -Wl,-z,relro,-z,now SOURCE -o capture_hook.so
 *     $(pkg-config --cflags --libs Qt5Widgets) -ldl -pthread
 * Codec-only test: replace -shared with -DCOMMA_UI_QA_SELF_TEST, keep -fPIC.
 * The test main uses QImage only: no QApplication, display, GL or Params access.
 */
#define _GNU_SOURCE 1
#ifndef COMMA_UI_QA_LINKED
#include <EGL/egl.h>
#endif
#include <QApplication>
#include <QCryptographicHash>
#include <QMouseEvent>
#include <QPointer>
#include <ctime>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QScreen>
#include <QStackedWidget>
#include <QWindow>
#include <QMetaObject>
#include <QPixmap>
#include <QScopedValueRollback>
#include <QTimer>
#include <QWidget>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <dlfcn.h>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace {
constexpr int kMaxWidth = 4096;
constexpr int kMaxHeight = 2160;
constexpr size_t kMaxRgba = 32u * 1024u * 1024u;
#ifndef COMMA_UI_QA_LINKED
using Swap = EGLBoolean (*)(EGLDisplay, EGLSurface);
#endif
std::atomic<bool> timerQueued{false};

class Fd {
 public:
  explicit Fd(int value = -1) : value_(value) {}
  ~Fd() { if (value_ >= 0) close(value_); }
  Fd(const Fd &) = delete;
  Fd &operator=(const Fd &) = delete;
  int get() const { return value_; }
 private:
  int value_;
};

bool optedIn() {
  const char *value = std::getenv("COMMA_UI_QA_CAPTURE");
  return value && std::strcmp(value, "1") == 0;
}

bool flagIs(const char *path, char expected) {
  Fd fd(open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
  if (fd.get() < 0) return false;
  struct stat info {};
  char bytes[2] {};
  return fstat(fd.get(), &info) == 0 && S_ISREG(info.st_mode) &&
         read(fd.get(), bytes, sizeof(bytes)) == 1 && bytes[0] == expected;
}

bool offroad() {
  return flagIs("/data/params/d/IsOnroad", '0') && flagIs("/data/params/d/IsOffroad", '1');
}

int privateDirectory() {
  char path[96];
  const int count = std::snprintf(path, sizeof(path), "/tmp/comma-ui-qa-%lu", static_cast<unsigned long>(geteuid()));
  if (count < 0 || static_cast<size_t>(count) >= sizeof(path)) return -1;
  if (mkdir(path, 0700) < 0 && errno != EEXIST) return -1;
  const int fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) return -1;
  struct stat info {};
  if (fstat(fd, &info) || info.st_uid != geteuid() || (info.st_mode & 0777) != 0700) {
    close(fd);
    return -1;
  }
  return fd;
}

bool claimRequest(int directory) {
  Fd request(openat(directory, "request", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
  if (request.get() < 0) return false;
  struct stat info {};
  char bytes[2] {};
  if (fstat(request.get(), &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
      (info.st_mode & 0777) != 0600 || info.st_nlink != 1 || info.st_size != 1 ||
      read(request.get(), bytes, sizeof(bytes)) != 1 || bytes[0] != '1') return false;
  return unlinkat(directory, "request", 0) == 0;
}

bool writeAll(int fd, const void *data, size_t length) {
  const auto *position = static_cast<const unsigned char *>(data);
  while (length) {
    const ssize_t count = write(fd, position, length);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    position += count;
    length -= static_cast<size_t>(count);
  }
  return true;
}

bool bounded(int width, int height) {
  return width > 0 && height > 0 && width <= kMaxWidth && height <= kMaxHeight &&
         static_cast<size_t>(width) * static_cast<size_t>(height) <= kMaxRgba / 4u;
}

bool writePpm(int fd, const QImage &source) {
  if (source.isNull() || !bounded(source.width(), source.height())) return false;
  const QImage rgb = source.convertToFormat(QImage::Format_RGB888);
  if (rgb.isNull()) return false;
  char header[64];
  const int count = std::snprintf(header, sizeof(header), "P6\n%d %d\n255\n", rgb.width(), rgb.height());
  if (count <= 0 || static_cast<size_t>(count) >= sizeof(header) ||
      !writeAll(fd, header, static_cast<size_t>(count))) return false;
  // QImage is already top-down. Its scanlines can have padding; never serialize it.
  for (int row = 0; row < rgb.height(); ++row) {
    if (!writeAll(fd, rgb.constScanLine(row), static_cast<size_t>(rgb.width()) * 3u)) return false;
  }
  return true;
}

QJsonObject rectangle(const QRect &rect) {
  return {{"x", rect.x()}, {"y", rect.y()}, {"width", rect.width()}, {"height", rect.height()}};
}

QJsonObject widgetGeometry(QWidget *widget) {
  if (!widget) return {};
  QJsonObject value {{"class", QString::fromLatin1(widget->metaObject()->className()).left(96)},
    {"visible", widget->isVisible()}, {"enabled", widget->isEnabled()},
    {"geometry", rectangle(widget->geometry())},
    {"globalGeometry", rectangle(QRect(widget->mapToGlobal(QPoint(0, 0)), widget->size()))}};
  if (auto *stack = qobject_cast<QStackedWidget *>(widget)) {
    value.insert("stackedIndex", stack->currentIndex());
    value.insert("stackedCount", stack->count());
    value.insert("currentClass", stack->currentWidget() ? QString::fromLatin1(stack->currentWidget()->metaObject()->className()).left(96) : QString());
  }
  return value;
}

QJsonObject widgetDiagnostics(QWidget *target) {
  QJsonArray nodes;
  int visited = 0;
  bool truncated = false;
  const auto walk = [&](const auto &self, QWidget *widget, int depth, int parent) -> void {
    if (!widget) return;
    if (depth > 12 || visited >= 128) { truncated = true; return; }
    const int index = visited++;
    QJsonObject node = widgetGeometry(widget);
    node.insert("index", index); node.insert("parent", parent); node.insert("depth", depth);
    nodes.append(node);
    for (QObject *child : widget->children()) {
      if (auto *nested = qobject_cast<QWidget *>(child)) {
        if (nested->isVisible()) self(self, nested, depth + 1, index);
      }
      if (visited >= 128) { truncated = true; break; }
    }
  };
  walk(walk, target, 0, -1);
  QJsonArray rootChildren;
  if (target) for (QObject *child : target->children()) {
    if (auto *widget = qobject_cast<QWidget *>(child)) {
      if (rootChildren.size() >= 32) break;
      rootChildren.append(widgetGeometry(widget));
    }
  }
  QJsonObject result {{"rootChildren", rootChildren}, {"treePolicy", "visible_descendants_only"}, {"target", widgetGeometry(target)}, {"activeWindow", widgetGeometry(QApplication::activeWindow())},
    {"activeModal", widgetGeometry(QApplication::activeModalWidget())}, {"widgets", nodes}, {"truncated", truncated}};
  QScreen *screen = target && target->windowHandle() ? target->windowHandle()->screen() : QApplication::primaryScreen();
  if (screen) result.insert("screen", QJsonObject {{"geometry", rectangle(screen->geometry())},
    {"availableGeometry", rectangle(screen->availableGeometry())}, {"devicePixelRatio", screen->devicePixelRatio()},
    {"orientation", static_cast<int>(screen->orientation())}, {"primaryOrientation", static_cast<int>(screen->primaryOrientation())},
    {"nativeOrientation", static_cast<int>(screen->nativeOrientation())}});
  if (target) result.insert("targetDevicePixelRatio", target->devicePixelRatioF());
  return result;
}

void status(int directory, const char *result, int width = 0, int height = 0, QWidget *target = nullptr) {
  char temporary[64];
  std::snprintf(temporary, sizeof(temporary), "qt-status-%ld.tmp", static_cast<long>(getpid()));
  Fd output(openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
  if (output.get() < 0) return;
  QJsonObject record {{"status", QString::fromLatin1(result)}, {"backend", "qt_widget_grab"},
    {"width", width}, {"height", height}, {"pid", static_cast<int>(getpid())}, {"offroad_checked", true}};
  if (target) record.insert("widgetDiagnostics", widgetDiagnostics(target));
  const QByteArray body = QJsonDocument(record).toJson(QJsonDocument::Compact);
  if (body.size() <= 65536 && writeAll(output.get(), body.constData(), static_cast<size_t>(body.size())) && fsync(output.get()) == 0) {
    renameat(directory, temporary, directory, "status.json");
  }
  unlinkat(directory, temporary, 0);
}

QWidget *captureTarget() {
  if (QWidget *modal = QApplication::activeModalWidget()) {
    if (modal->isVisible() && modal->width() > 0 && modal->height() > 0) return modal;
  }
  QWidget *largest = nullptr;
  qint64 area = 0;
  for (QWidget *widget : QApplication::topLevelWidgets()) {
    if (!widget || !widget->isVisible() || !widget->isWindow() || widget->isMinimized()) continue;
    if (widget->windowType() == Qt::ToolTip || widget->windowType() == Qt::Popup) continue;
    const qint64 candidate = static_cast<qint64>(widget->width()) * widget->height();
    if (candidate > area) { largest = widget; area = candidate; }
  }
  return largest;
}

double monotonicNow() {
  struct timespec now {}; clock_gettime(CLOCK_MONOTONIC, &now);
  return now.tv_sec + now.tv_nsec / 1000000000.0;
}

QByteArray currentFrameHash(int directory) {
  Fd fd(openat(directory, "frame.ppm", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
  struct stat info {};
  if (fd.get() < 0 || fstat(fd.get(), &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() || info.st_nlink != 1 || info.st_size <= 0 || info.st_size > static_cast<off_t>(kMaxRgba)) return {};
  QCryptographicHash hash(QCryptographicHash::Sha256);
  char bytes[16384]; ssize_t count; off_t total = 0;
  while ((count = read(fd.get(), bytes, sizeof(bytes))) > 0) {
    total += count;
    if (total > info.st_size || total > static_cast<off_t>(kMaxRgba)) return {};
    hash.addData(bytes, static_cast<int>(count));
  }
  struct stat after {};
  return count == 0 && total == info.st_size && fstat(fd.get(), &after) == 0 &&
    after.st_size == info.st_size && after.st_mtim.tv_sec == info.st_mtim.tv_sec && after.st_mtim.tv_nsec == info.st_mtim.tv_nsec ? hash.result().toHex() : QByteArray();
}

QPointer<QWidget> lastCaptureTarget;
QRect lastCaptureGeometry;
QByteArray lastCaptureHash;
double lastCaptureTime = 0;
bool navigationUsed = true;

void navigationResult(int directory, const QString &operation, const QString &result, bool sent) {
  Fd fd(openat(directory, "navigation-result.tmp", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
  if (fd.get() < 0) return;
  const QByteArray body = QJsonDocument(QJsonObject {{"operationId", operation}, {"status", result}, {"eventPairSent", sent},
    {"pid", static_cast<int>(getpid())}}).toJson(QJsonDocument::Compact);
  if (writeAll(fd.get(), body.constData(), static_cast<size_t>(body.size())) && fsync(fd.get()) == 0) renameat(directory, "navigation-result.tmp", directory, "navigation-result.json");
  unlinkat(directory, "navigation-result.tmp", 0);
}

void navigationRequest() {
  if (!optedIn() || !offroad()) return;
  Fd directory(privateDirectory());
  if (directory.get() < 0) return;
  Fd fd(openat(directory.get(), "navigation-request.json", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
  struct stat info {};
  if (fd.get() < 0 || fstat(fd.get(), &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() || info.st_nlink != 1 || (info.st_mode & 0777) != 0600 || info.st_size <= 0 || info.st_size > 2048) return;
  char bytes[2049] {}; const ssize_t count = read(fd.get(), bytes, sizeof(bytes));
  if (count != info.st_size || unlinkat(directory.get(), "navigation-request.json", 0)) return;
  const QJsonDocument document = QJsonDocument::fromJson(QByteArray(bytes, static_cast<int>(count)));
  if (!document.isObject()) return;
  const QJsonObject request = document.object();
  const QString operation = request.value("operationId").toString();
  if (operation.size() != 32) return;
  for (const QChar character : operation) if (!((character >= QLatin1Char('0') && character <= QLatin1Char('9')) || (character >= QLatin1Char('a') && character <= QLatin1Char('f')))) return;
  const QString action = request.value("action").toString();
  const double now = monotonicNow();
  const double expiresAt = request.value("expiresAt").toDouble(-1);
  QWidget *target = lastCaptureTarget.data();
  const auto refuse = [&](const char *reason) { navigationResult(directory.get(), operation, QString::fromLatin1(reason), false); };
  if ((action != "open_settings" && action != "close_settings") || request.value("pid").toInt() != static_cast<int>(getpid()) ||
      navigationUsed || !std::isfinite(expiresAt) || now >= expiresAt || !target || !target->isVisible() || target->geometry() != lastCaptureGeometry ||
      request.value("frameSha256").toString().toLatin1() != lastCaptureHash || currentFrameHash(directory.get()) != lastCaptureHash ||
      now - lastCaptureTime < 0 || now - lastCaptureTime > 30 ||
      now - request.value("idleCheckedAt").toDouble(-1) < 0 || now - request.value("idleCheckedAt").toDouble(-1) > 0.5 ||
      QApplication::activeModalWidget() || QApplication::mouseButtons() != Qt::NoButton || !offroad()) {
    refuse("qt_guard_refused"); return;
  }
  navigationUsed = true;
  const QPoint globalPoint(150, 93);
  QWidget *receiver = target->childAt(target->mapFromGlobal(globalPoint));
  if (!receiver || !receiver->isVisible() || !receiver->isEnabled()) { refuse("receiver_absent"); return; }
  const QByteArray receiverClass(receiver->metaObject()->className());
  bool accepted = action == "open_settings" && (receiverClass == "Sidebar" || receiverClass == "SidebarSP");
  if (action == "close_settings" && receiverClass == "QPushButton") {
    for (QWidget *parent = receiver->parentWidget(); parent; parent = parent->parentWidget()) {
      const QByteArray parentClass(parent->metaObject()->className());
      if (parent->isVisible() && (parentClass == "SettingsWindow" || parentClass == "SettingsWindowSP")) accepted = true;
    }
  }
  const QRect receiverGeometry(receiver->mapToGlobal(QPoint(0, 0)), receiver->size());
  if (!accepted || !receiverGeometry.contains(globalPoint) || !offroad()) { refuse("receiver_class_or_geometry_refused"); return; }
  QPointer<QWidget> safeReceiver(receiver);
  const QPoint local = receiver->mapFromGlobal(globalPoint);
  QMouseEvent press(QEvent::MouseButtonPress, local, globalPoint, Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
  QMouseEvent release(QEvent::MouseButtonRelease, local, globalPoint, Qt::LeftButton, Qt::NoButton, Qt::NoModifier);
  bool pressed = false;
  try {
    if (!offroad() || !safeReceiver || QRect(receiver->mapToGlobal(QPoint(0,0)), receiver->size()) != receiverGeometry) { refuse("last_moment_guard_refused"); return; }
    pressed = true;
    QApplication::sendEvent(safeReceiver, &press);
  } catch (...) {
    if (pressed && safeReceiver) QApplication::sendEvent(safeReceiver, &release);
    navigationResult(directory.get(), operation, "qt_event_exception", false); return;
  }
  if (pressed && safeReceiver) QApplication::sendEvent(safeReceiver, &release);
  navigationResult(directory.get(), operation, "qt_event_pair_delivered_unverified", pressed && !safeReceiver.isNull());
}

void captureRequest() {
  if (!optedIn() || !offroad()) return;
  Fd directory(privateDirectory());
  if (directory.get() < 0 || !claimRequest(directory.get())) return;
  unlinkat(directory.get(), "frame.ppm", 0);
  unlinkat(directory.get(), "status.json", 0);
  char temporary[64];
  std::snprintf(temporary, sizeof(temporary), "qt-frame-%ld.tmp", static_cast<long>(getpid()));
  try {
    QWidget *target = captureTarget();
    if (!target) { status(directory.get(), "no_visible_widget"); return; }
    const qreal ratio = target->devicePixelRatioF();
    if (!std::isfinite(ratio) || ratio < 1.0 || ratio > 4.0 ||
        !bounded(static_cast<int>(std::ceil(target->width() * ratio)),
                 static_cast<int>(std::ceil(target->height() * ratio)))) {
      status(directory.get(), "unsupported_widget_geometry"); return;
    }
    if (!offroad()) { status(directory.get(), "offroad_state_changed"); return; }
    const QPixmap pixels = target->grab();
    const QImage image = pixels.toImage();
    if (image.isNull() || !bounded(image.width(), image.height())) {
      status(directory.get(), "widget_grab_failed"); return;
    }
    if (!offroad()) { status(directory.get(), "offroad_state_changed"); return; }
    bool published = false;
    {
      Fd output(openat(directory.get(), temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
      if (output.get() < 0) { status(directory.get(), "output_unavailable"); return; }
      if (writePpm(output.get(), image) && fsync(output.get()) == 0 && offroad()) {
        published = renameat(directory.get(), temporary, directory.get(), "frame.ppm") == 0;
      }
    }
    unlinkat(directory.get(), temporary, 0);
    if (published) {
      lastCaptureTarget = target; lastCaptureGeometry = target->geometry();
      lastCaptureHash = currentFrameHash(directory.get()); lastCaptureTime = monotonicNow(); navigationUsed = false;
    }
    status(directory.get(), published ? "captured_unverified" : "output_failed", image.width(), image.height(), target);
  } catch (...) {
    unlinkat(directory.get(), temporary, 0);
    status(directory.get(), "qt_capture_exception");
  }
}

void queueTimer() {
  auto *application = qobject_cast<QApplication *>(QCoreApplication::instance());
  if (!application || QCoreApplication::closingDown()) return;
  bool expected = false;
  if (!timerQueued.compare_exchange_strong(expected, true)) return;
  const bool queued = QMetaObject::invokeMethod(application, [application]() {
    if (!optedIn() || QCoreApplication::closingDown()) return;
    try {
      auto timer = std::make_unique<QTimer>(application);
      timer->setInterval(100);
      QObject::connect(timer.get(), &QTimer::timeout, application, []() {
        static bool busy = false;
        if (busy) return;
        QScopedValueRollback<bool> recursionGuard(busy, true);
        try { captureRequest(); navigationRequest(); } catch (...) { /* Never unwind through Qt dispatch. */ }
      });
      timer->start();
      timer.release();  // QApplication owns the successfully installed timer.
    } catch (...) {
      timerQueued.store(false);
    }
  }, Qt::QueuedConnection);
  if (!queued) timerQueued.store(false);
}
}  // namespace

#ifndef COMMA_UI_QA_LINKED
// Raster-only offroad UIs may never swap EGL buffers. Enter through the
// application event loop on its own GUI thread, after construction is complete.
int QApplication::exec() {
  using Exec = int (*)();
  static const auto original = reinterpret_cast<Exec>(dlsym(RTLD_NEXT, "_ZN12QApplication4execEv"));
  if (!original) return -1;
  try { if (optedIn()) queueTimer(); } catch (...) { /* Always forward exec. */ }
  return original();
}

int QCoreApplication::exec() {
  using Exec = int (*)();
  static const auto original = reinterpret_cast<Exec>(dlsym(RTLD_NEXT, "_ZN16QCoreApplication4execEv"));
  if (!original) return -1;
  try { if (optedIn()) queueTimer(); } catch (...) { /* Always forward exec. */ }
  return original();
}

extern "C" EGLBoolean EGLAPIENTRY eglSwapBuffers(EGLDisplay display, EGLSurface surface) {
  static const auto original = reinterpret_cast<Swap>(dlsym(RTLD_NEXT, "eglSwapBuffers"));
  try {
    if (optedIn()) queueTimer();
  } catch (...) { /* Forward the real swap even if queueing fails. */ }
  return original ? original(display, surface) : EGL_FALSE;
}

#else
extern "C" void commaUiQaInstall() {
  // Called explicitly on the GUI thread after QApplication and the main window.
  // Normal startup does not touch files, params, timers, or display capture.
  if (!optedIn()) return;
  try { queueTimer(); } catch (...) { /* QA must never unwind into UI startup. */ }
}
#endif

#ifdef COMMA_UI_QA_SELF_TEST
int main() {
  QImage source(2, 2, QImage::Format_ARGB32);
  source.setPixel(0, 0, qRgba(255, 0, 0, 7));
  source.setPixel(1, 0, qRgba(0, 255, 0, 8));
  source.setPixel(0, 1, qRgba(0, 0, 255, 9));
  source.setPixel(1, 1, qRgba(255, 255, 255, 10));
  const QImage padded = source.convertToFormat(QImage::Format_RGB888);
  if (padded.bytesPerLine() <= padded.width() * 3) return 1;
  FILE *file = std::tmpfile();
  if (!file) return 2;
  const unsigned char expected[] = "P6\n2 2\n255\n\377\000\000\000\377\000\000\000\377\377\377\377";
  unsigned char actual[sizeof(expected)] {};
  const bool ok = writePpm(fileno(file), source) && lseek(fileno(file), 0, SEEK_SET) >= 0 &&
    read(fileno(file), actual, sizeof(actual)) == static_cast<ssize_t>(sizeof(expected) - 1) &&
    std::memcmp(actual, expected, sizeof(expected) - 1) == 0;
  std::fclose(file);
  if (!ok || bounded(0, 2) || bounded(4097, 2) || bounded(2, 2161) || bounded(4096, 2160)) return 3;
  std::puts("QT_IMAGE_PPM_TEST_PASS");
  return 0;
}
#endif
