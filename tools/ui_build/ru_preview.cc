// Actual native Qt Russian settings screenshots. Container-only; no device actions.
// Build by replacing preview.cc with this source in the existing HOST preview target.
#include <QApplication>
#include <QAbstractScrollArea>
#include <QAbstractButton>
#include <QScrollArea>
#include <QDir>
#include <QFile>
#include <QEventLoop>
#include <QFontDatabase>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMouseEvent>
#include <QWindow>
#include <QtTest/QTest>
#include <QScrollBar>
#include <QScroller>
#include <QStackedLayout>
#include <QSet>
#include <algorithm>
#include <QTimer>
#include <QTranslator>
#include <capnp/message.h>
#include <capnp/serialize.h>
#include <cstdio>
#include <functional>
#include <vector>
#include <map>
#include "common/params.h"
#include "selfdrive/ui/sunnypilot/qt/widgets/ru_title_button.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/settings.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal_panel.h"
#include "selfdrive/ui/sunnypilot/qt/offroad/settings/longitudinal/speed_limit/speed_limit_settings.h"

static void seedFixtures() {
  Params params;
  for (const auto &pair : std::vector<std::pair<std::string,std::string>>{
      {"LanguageSetting","main_ru"},{"IsMetric","1"},{"IsOffroad","1"},{"IsOnroad","0"},
      {"OpenpilotEnabledToggle","1"},{"ShowAdvancedControls","1"},{"IntelligentCruiseButtonManagement","1"},
      {"SpeedLimitMode","3"},{"SpeedLimitOffsetType","0"},{"SpeedLimitValueOffset","0"},
      {"SpeedCameraEnabled","1"},{"SpeedCameraControlEnabled","0"},
      {"SpeedCameraDatabaseStatus","ready"},{"SpeedCameraDatabaseCount","74016"},
      {"SpeedCameraDatabaseUpdatedAt","1793020000"},{"SpeedCameraDatabaseVersion","fixture-ru-preview"},
      {"SpeedCameraDatabaseError",""},{"Version","0.9.8-fixture"},{"GitBranch","dev"},
      {"GitCommit","fd454f15fbf247e2094d149c3456e8198e5af216"},{"DongleId","0123456789abcdef"}}) params.put(pair.first,pair.second);
  capnp::MallocMessageBuilder message;
  auto cp=message.initRoot<cereal::CarParams>();
  cp.setBrand("volkswagen"); cp.setCarFingerprint("VOLKSWAGEN GOLF");
  cp.setOpenpilotLongitudinalControl(false); cp.setPcmCruise(true);
  auto bytes=capnp::messageToFlatArray(message); auto data=bytes.asBytes();
  params.put("CarParamsPersistent",reinterpret_cast<const char *>(data.begin()),data.size());
  capnp::MallocMessageBuilder custom;
  custom.initRoot<cereal::CarParamsSP>().setIntelligentCruiseButtonManagementAvailable(true);
  auto customBytes=capnp::messageToFlatArray(custom); auto customData=customBytes.asBytes();
  params.put("CarParamsSPPersistent",reinterpret_cast<const char *>(customData.begin()),customData.size());
}

static QAbstractScrollArea *mainScrollArea(QWidget *scope);
static void scroll(QWidget *root, double fraction) {
  if(auto *area=mainScrollArea(root)) {
    auto *bar=area->verticalScrollBar();
    bar->setValue(bar->minimum()+qRound((bar->maximum()-bar->minimum())*fraction));
  }
}

static QJsonObject rectJson(const QRect &rect) {
  return {{"x",rect.x()},{"y",rect.y()},{"width",rect.width()},{"height",rect.height()}};
}

static QJsonObject captionGeometry(QWidget *widget,QWidget *window,const QString &text) {
  const QRect bounds(widget->mapTo(window,QPoint(0,0)),widget->size());
  QRect clip=window->rect();
  for(QWidget *ancestor=widget->parentWidget();ancestor;ancestor=ancestor->parentWidget()) {
    clip=clip.intersected(QRect(ancestor->mapTo(window,ancestor->contentsRect().topLeft()),ancestor->contentsRect().size()));
    if(ancestor==window) break;
  }
  const QRect painted=bounds.intersected(clip);
  return {{"text",text},{"class",widget->metaObject()->className()},
    {"parentClass",widget->parentWidget()?widget->parentWidget()->metaObject()->className():""},
    {"bounds",rectJson(bounds)},{"ancestorClip",rectJson(clip)},{"paintedBounds",rectJson(painted)},
    {"horizontalClipped",!painted.isEmpty()&&(painted.left()>bounds.left()||painted.right()<bounds.right())},
    {"verticalClipped",!painted.isEmpty()&&(painted.top()>bounds.top()||painted.bottom()<bounds.bottom())},
    {"offViewport",painted.isEmpty()},{"minimumSizeHintWidth",widget->minimumSizeHint().width()},
    {"sizeHintWidth",widget->sizeHint().width()},{"minimumWidth",widget->minimumWidth()},
    {"contentsWidth",widget->contentsRect().width()},
    {"singleLineTextWidthEstimate",widget->fontMetrics().horizontalAdvance(text)},
    {"singleLineTextExceedsContents",widget->fontMetrics().horizontalAdvance(text)>widget->contentsRect().width()},
    {"heightForWidth",widget->hasHeightForWidth()},
    {"labelWordWrap",qobject_cast<QLabel *>(widget)?qobject_cast<QLabel *>(widget)->wordWrap():false}};
}


static QWidget *widgetClass(QWidget *root,const QString &name) {
  for(auto *widget:root->findChildren<QWidget *>()) if(name==widget->metaObject()->className()) return widget;
  return nullptr;
}

static bool displayNested(QWidget *target,QWidget *root) {
  std::vector<QWidget *> chain;
  for(QWidget *child=target;child&&child!=root;child=child->parentWidget()) chain.push_back(child);
  std::reverse(chain.begin(),chain.end());
  bool selected=false;
  for(QWidget *child:chain) {
    QWidget *parent=child->parentWidget();
    if(auto *stack=qobject_cast<QStackedWidget *>(parent)) {
      if(stack->indexOf(child)>=0) {stack->setCurrentWidget(child);selected=true;}
    }
    if(parent) if(auto *layout=qobject_cast<QStackedLayout *>(parent->layout())) {
      if(layout->indexOf(child)>=0) {layout->setCurrentWidget(child);selected=true;}
    }
  }
  return selected&&target->isVisible();
}

static QAbstractScrollArea *mainScrollArea(QWidget *scope) {
  auto candidates=scope->findChildren<QAbstractScrollArea *>();
  // SettingsWindow wraps ListWidget panels in a ScrollView OUTSIDE that panel.
  for(QWidget *parent=scope->parentWidget();parent;parent=parent->parentWidget())
    if(auto *area=qobject_cast<QAbstractScrollArea *>(parent)) candidates.append(area);
  QAbstractScrollArea *best=nullptr;
  qint64 bestScore=-1;
  for(auto *area:candidates) {
    if(!area->isVisible()||area->viewport()->width()<500) continue;
    const qint64 score=(area->verticalScrollBar()->maximum()>0?1000000000000LL:0)+
      qint64(area->viewport()->width())*area->viewport()->height();
    if(score>bestScore) {best=area;bestScore=score;}
  }
  return best;
}


static void settleHelpLayout(QPushButton *button) {
  QApplication::sendPostedEvents(nullptr,QEvent::LayoutRequest);
  std::vector<QWidget *> ancestors;
  for(QWidget *widget=button;widget;widget=widget->parentWidget()) ancestors.push_back(widget);
  std::reverse(ancestors.begin(),ancestors.end());
  for(QWidget *widget:ancestors) if(widget->layout()) widget->layout()->activate();
  QApplication::sendPostedEvents(nullptr,QEvent::LayoutRequest);
}

static QJsonObject helpClickDiagnostic(QPushButton *button,QLabel *description) {
  QJsonArray ancestors;
  for(QWidget *parent=button->parentWidget();parent;parent=parent->parentWidget()) {
    QJsonObject value{{"class",parent->metaObject()->className()},{"visible",parent->isVisible()},{"bounds",rectJson(parent->geometry())}};
    if(auto *stack=qobject_cast<QStackedWidget *>(parent)) value.insert("stackIndex",stack->currentIndex());
    if(auto *layout=qobject_cast<QStackedLayout *>(parent->layout())) value.insert("layoutIndex",layout->currentIndex());
    ancestors.append(value);
  }
  return {{"titleVisible",button->isVisible()},{"titleEnabled",button->isEnabled()},{"rect",rectJson(button->rect())},
    {"globalBounds",rectJson(QRect(button->mapToGlobal(QPoint(0,0)),button->size()))},
    {"descriptionVisible",description->isVisible()},{"descriptionExplicitlyHidden",description->isHidden()},{"ancestors",ancestors}};
}


static QJsonObject lastHelpDelivery;
class HelpInputObserver final : public QObject {
public:
  QPushButton *title=nullptr;
  QWidget *window=nullptr;
  QJsonArray events;
  bool eventFilter(QObject *receiver,QEvent *event) override {
    if(events.size()>=128) return false;
    const int type=event->type();
    if(type!=QEvent::MouseButtonPress&&type!=QEvent::MouseButtonRelease&&type!=QEvent::MouseMove&&
       type!=QEvent::MouseButtonDblClick&&type!=QEvent::Gesture&&type!=QEvent::GrabMouse&&type!=QEvent::UngrabMouse) return false;
    auto *widget=qobject_cast<QWidget *>(receiver);
    if(widget&&widget->window()!=window) return false;
    QJsonObject value{{"type",type},{"receiverClass",receiver->metaObject()->className()},
      {"intendedTitle",receiver==title},{"windowSystemMouseButtons",int(QApplication::mouseButtons())},
      {"titleDown",title->isDown()},{"acceptedBeforeHandler",event->isAccepted()}};
    if(type==QEvent::MouseButtonPress||type==QEvent::MouseButtonRelease||type==QEvent::MouseMove||type==QEvent::MouseButtonDblClick) {
      auto *mouse=static_cast<QMouseEvent *>(event);
      value.insert("x",mouse->pos().x());value.insert("y",mouse->pos().y());
      value.insert("globalX",mouse->globalPos().x());value.insert("globalY",mouse->globalPos().y());
      value.insert("buttons",int(mouse->buttons()));value.insert("source",int(mouse->source()));
    }
    events.append(value);
    return false; // Observational only; never accept, consume or redirect an event.
  }
};

static QJsonArray helpScrollerStates(QPushButton *button) {
  QJsonArray result;
  for(QWidget *parent=button->parentWidget();parent;parent=parent->parentWidget()) if(auto *area=qobject_cast<QAbstractScrollArea *>(parent)) {
    const bool existing=QScroller::hasScroller(area->viewport());
    result.append(QJsonObject{{"class",area->metaObject()->className()},{"viewportBounds",rectJson(QRect(area->viewport()->mapToGlobal(QPoint(0,0)),area->viewport()->size()))},
      {"hasScroller",existing},{"state",existing?int(QScroller::scroller(area->viewport())->state()):-1}});
  }
  return result;
}

static void clickHelpTitle(QPushButton *button) {
  settleHelpLayout(button);
  std::vector<QScrollArea *> scrollAncestors;
  for(QWidget *parent=button->parentWidget();parent;parent=parent->parentWidget())
    if(auto *area=qobject_cast<QScrollArea *>(parent)) scrollAncestors.push_back(area);
  std::reverse(scrollAncestors.begin(),scrollAncestors.end());
  for(auto *area:scrollAncestors) area->ensureWidgetVisible(button,20,20);
  settleHelpLayout(button);
  QWidget *top=button->window();
  QWindow *window=top->windowHandle();
  if(!window) {lastHelpDelivery={{"windowMissing",true}};return;}
  const QPoint global=button->mapToGlobal(button->rect().center());
  const QPoint point=window->mapFromGlobal(global);
  QWidget *hit=top->childAt(top->mapFromGlobal(global));
  QWidget *widgetAt=QApplication::widgetAt(global);
  HelpInputObserver observer;observer.title=button;observer.window=top;
  qApp->installEventFilter(&observer);
  lastHelpDelivery={{"globalX",global.x()},{"globalY",global.y()},
    {"childAtClass",hit?hit->metaObject()->className():"none"},{"childAtIsTitle",hit==button},
    {"widgetAtClass",widgetAt?widgetAt->metaObject()->className():"none"},{"widgetAtIsTitle",widgetAt==button},
    {"transparentForMouse",button->testAttribute(Qt::WA_TransparentForMouseEvents)},
    {"underMouse",button->underMouse()},{"activeWindowIsTop",QApplication::activeWindow()==top},
    {"activeModalClass",QApplication::activeModalWidget()?QApplication::activeModalWidget()->metaObject()->className():"none"},
    {"clip",captionGeometry(button,top,button->text())},{"scrollersBefore",helpScrollerStates(button)}};
  QTest::mousePress(window,Qt::LeftButton,Qt::NoModifier,point,0);
  lastHelpDelivery.insert("downImmediatelyAfterPress",button->isDown());
  QTest::qWait(40);
  lastHelpDelivery.insert("downAfterPressHold",button->isDown());
  lastHelpDelivery.insert("mouseButtonsAfterHold",int(QApplication::mouseButtons()));
  lastHelpDelivery.insert("scrollersAfterHold",helpScrollerStates(button));
  QTest::mouseRelease(window,Qt::LeftButton,Qt::NoModifier,point,0);
  QTest::qWait(40);
  lastHelpDelivery.insert("downAfterRelease",button->isDown());
  lastHelpDelivery.insert("scrollersAfterRelease",helpScrollerStates(button));
  qApp->removeEventFilter(&observer);
  lastHelpDelivery.insert("events",observer.events);
}

static QPushButton *helpTitle(AbstractControlSP *control) {
  for(auto *button:control->findChildren<QPushButton *>(QString(),Qt::FindDirectChildrenOnly))
    if(dynamic_cast<RussianControlTitleButton *>(button)) return button;
  return nullptr;
}

static QJsonObject checkCruiseHelpInteraction(SettingsWindowSP &window) {
  window.setCurrentPanel(7);
  auto *panel=window.findChild<LongitudinalPanel *>();
  const QString expected=QCoreApplication::translate("LongitudinalPanel","Smart Cruise Control - Vision");
  ParamControlSP *control=nullptr;QPushButton *title=nullptr;QLabel *description=nullptr;
  if(panel) for(auto *candidate:panel->findChildren<ParamControlSP *>()) {
    auto *button=helpTitle(candidate);
    if(button&&button->text()==expected) {control=candidate;title=button;break;}
  }
  if(control) for(auto *label:control->findChildren<QLabel *>()) if(label->text()==control->getDescription()) description=label;
  if(!control||!title||!description||!title->isEnabled()) return {{"passed",false},{"reason","actual enabled cruise title/description not found"}};
  const std::string before=Params().get("SmartCruiseControlVision");
  const bool initialToggle=control->isToggled();
  int sliderSignals=0;
  auto connection=QObject::connect(control,&ToggleControlSP::toggleFlipped,[&](bool){++sliderSignals;});
  const bool initiallyHidden=!description->isVisible();
  clickHelpTitle(title);const bool opens=description->isVisible();
  clickHelpTitle(title);const bool closes=!description->isVisible();
  clickHelpTitle(title);window.setCurrentPanel(0);window.setCurrentPanel(7);
  const bool leaveCollapses=!description->isVisible();
  clickHelpTitle(title);
  // Emit the QObject signal only: no display-power setter or hardware call.
  device()->displayPowerChanged(false);
  const bool screenOffCollapses=!description->isVisible();
  description->ensurePolished();
  const int fontPixels=description->font().pixelSize();
  const bool independent=sliderSignals==0&&control->isToggled()==initialToggle&&Params().get("SmartCruiseControlVision")==before;
  QObject::disconnect(connection);
  QJsonObject nestedProof;
  auto *speed=panel->findChild<SpeedLimitSettings *>();
  bool nestedPassed=false;
  if(speed&&displayNested(speed,&window)) {
    ButtonParamControlSP *mode=nullptr;QPushButton *modeTitle=nullptr;QLabel *modeDescription=nullptr;
    for(auto *candidate:speed->findChildren<ButtonParamControlSP *>()) {
      auto *button=helpTitle(candidate);
      if(button&&button->text()==QCoreApplication::translate("SpeedLimitSettings","Speed Limit")) {mode=candidate;modeTitle=button;break;}
    }
    if(mode) for(auto *label:mode->findChildren<QLabel *>()) if(label->text()==mode->getDescription()) modeDescription=label;
    if(mode&&modeTitle&&modeDescription) {
      const std::string modeBefore=Params().get("SpeedLimitMode");
      int buttonSignals=0;
      auto modeConnection=QObject::connect(mode,&ButtonParamControlSP::buttonClicked,[&](){++buttonSignals;});
      const bool startsClosed=!modeDescription->isVisible();
      int titleClickedSignals=0;
      auto titleConnection=QObject::connect(modeTitle,&QPushButton::clicked,[&](){++titleClickedSignals;});
      const QJsonObject beforeClick=helpClickDiagnostic(modeTitle,modeDescription);
      clickHelpTitle(modeTitle);const bool titleOpens=modeDescription->isVisible();
      const QJsonObject afterClick=helpClickDiagnostic(modeTitle,modeDescription);
      const QJsonObject delivery=lastHelpDelivery;
      const int firstClickSignalCount=titleClickedSignals;
      QObject::disconnect(titleConnection);
      clickHelpTitle(modeTitle);speed->refresh();const bool staysClosed=!modeDescription->isVisible();
      clickHelpTitle(modeTitle);device()->displayPowerChanged(false);
      const bool powerCollapses=!modeDescription->isVisible();
      modeDescription->ensurePolished();
      const int modeFont=modeDescription->font().pixelSize();
      const bool modeUnchanged=buttonSignals==0&&Params().get("SpeedLimitMode")==modeBefore;
      QObject::disconnect(modeConnection);
      nestedPassed=startsClosed&&titleOpens&&staysClosed&&powerCollapses&&modeFont==34&&modeUnchanged&&firstClickSignalCount==1;
      nestedProof={{"passed",nestedPassed},{"startsClosed",startsClosed},{"titleOpens",titleOpens},
        {"refreshDoesNotReopen",staysClosed},{"screenOffCollapses",powerCollapses},{"helpFontPixels",modeFont},
        {"modeButtonSignalCount",buttonSignals},{"modeParamUnchanged",modeUnchanged},
        {"firstTitleClickedSignalCount",firstClickSignalCount},{"fixturePressHoldMs",40},{"fixtureDeliveryPumpMs",40},{"fixtureDeliveryBackend","Qt5Test_QWindow"},{"beforeClick",beforeClick},{"afterClick",afterClick},{"delivery",delivery}};
    }
  }
  window.setCurrentPanel(0);
  return {{"passed",initiallyHidden&&opens&&closes&&leaveCollapses&&screenOffCollapses&&independent&&fontPixels==34&&nestedPassed},
    {"initiallyHidden",initiallyHidden},{"titleOpens",opens},{"titleCloses",closes},{"leaveCollapses",leaveCollapses},
    {"screenOffSignalCollapses",screenOffCollapses},{"toggleSignalCount",sliderSignals},{"parameterAndSliderUnchanged",independent},
    {"helpFontPixels",fontPixels},{"nestedSpeedLimit",nestedProof},{"physicalDisplayPowerChanged",false}};
}


int main(int argc,char **argv) {
  const QString paramsRoot=QString::fromLocal8Bit(qgetenv("PARAMS_ROOT"));
  if (!paramsRoot.startsWith("/tmp/ru-ui-preview-") || QDir::cleanPath(paramsRoot)!=paramsRoot || QFile::exists(paramsRoot)) {
    std::fprintf(stderr,"Requires a NEW isolated PARAMS_ROOT=/tmp/ru-ui-preview-<unique>; refuses existing roots.\n"); return 2;
  }
  if(argc!=2) {std::fprintf(stderr,"Usage: ru_preview OUTPUT_DIRECTORY (cwd=selfdrive/ui)\n");return 2;}
  QDir().mkpath(paramsRoot);
  seedFixtures();
  QApplication app(argc,argv);
  QTranslator russian;
  if (!russian.load(":/main_ru")) {std::fprintf(stderr,"Embedded main_ru resource unavailable\n");return 3;}
  app.installTranslator(&russian);
  QJsonArray fonts;
  for(const QString &name:QDir("../assets/fonts").entryList({"Inter-*.ttf","JetBrainsMono-Medium.ttf"},QDir::Files)) {
    const QString path="../assets/fonts/"+name;
    if(QFontDatabase::addApplicationFont(path)<0) {std::fprintf(stderr,"Cannot load exact font\n");return 4;}
    fonts.append(path);
  }
  if(fonts.isEmpty()) {std::fprintf(stderr,"Exact UI fonts unavailable; cwd must be selfdrive/ui\n");return 4;}
  app.setStyleSheet("* {font-family: Inter; outline:none;}");
  const QString output=QString::fromLocal8Bit(argv[1]);
  if(!QDir().mkpath(output)) return 5;
  SettingsWindowSP window;
  window.resize(2160,1080); window.show();
  struct Page {QString name;int panel;double fraction;bool descriptions;bool speed;QString nestedClass;int scrollY=-1;};
  std::vector<Page> pages{
    {"01-device-top",0,0,false,false},{"02-device-bottom",0,1,false,false},
    {"03-network",1,0,false,false},{"04-toggles-top",3,0,false,false},
    {"05-toggles-bottom",3,1,false,false},{"06-toggles-descriptions-top",3,0,true,false},
    {"07-toggles-descriptions-middle",3,.5,true,false},{"08-toggles-descriptions-bottom",3,1,true,false},
    {"09-software-top",4,0,false,false},{"10-software-bottom",4,1,false,false},
    {"11-cruise-top",7,0,false,false},{"12-cruise-bottom",7,1,false,false},
    {"13-speed-camera-top",7,0,false,true},{"14-speed-camera-middle",7,.5,false,true},
    {"15-speed-camera-bottom",7,1,false,true},{"16-speed-descriptions-top",7,0,true,true},
    {"17-speed-descriptions-middle",7,.5,true,true},{"18-speed-descriptions-bottom",7,1,true,true},
  };
  // Fixed menu/class allowlist only: display navigation, no arbitrary clicks.
  const std::vector<std::pair<int,QString>> remaining{{2,"sunnylink"},{5,"models"},{6,"steering"},
    {8,"visuals"},{9,"display"},{10,"osm"},{11,"trips"},{12,"vehicle"},{13,"firehose"},{14,"developer"}};
  for(const auto &panel:remaining) for(int mode=0;mode<3;++mode) {
    const QString name=QString("%1-%2-%3").arg(pages.size()+1,2,10,QLatin1Char('0')).arg(panel.second,
      mode==0?"top":mode==1?"bottom":"descriptions");
    pages.push_back({name,panel.first,mode==1?1.0:0.0,mode==2,false});
  }
  pages.push_back({"49-cruise-help-top",7,0,true,false});
  pages.push_back({"50-cruise-help-middle",7,.5,true,false});
  pages.push_back({"51-cruise-help-bottom",7,1,true,false});
  const std::vector<std::pair<int,QString>> nested{{6,"MadsSettings"},{6,"LaneChangeSettings"},
    {6,"TorqueLateralControlSettings"},{7,"SpeedLimitPolicy"}};
  for(const auto &entry:nested) for(int mode=0;mode<3;++mode) {
    Page page{QString("%1-deep-%2-%3").arg(pages.size()+1,2,10,QLatin1Char('0')).arg(entry.second,
      mode==0?"top":mode==1?"bottom":"descriptions"),entry.first,mode==1?1.0:0.0,mode==2,false};
    page.nestedClass=entry.second;
    pages.push_back(page);
  }
  QSet<QString> sweepPlanned;
  QJsonArray captures, unreachableContexts, sweepCoverage;
  QJsonObject cruiseHelpInteraction;
  std::map<QString,Page> profileTemplates;
  std::map<QString,std::vector<std::pair<int,int>>> observedIntervals;
  std::map<QString,int> observedExtents;
  QJsonArray verifiedCoverage;
  int coverageRepairs=0;
  unreachableContexts.append(QJsonObject{{"context","value-edit dialogs / update-reset-consent confirmations"},
    {"reason","not opened: fixture never activates value changes, network updates, reset, consent or destructive buttons"}});
  unreachableContexts.append(QJsonObject{{"context","Display deep menu"},
    {"reason","actual DisplayPanel has inline brightness/screen controls, no separate QStackedLayout subpanel in this source"}});

  int index=0;
  std::function<void()> next;
  next=[&](){
    if(index>=int(pages.size())) {
      // Verify actual captured ranges, not just the positions planned before layout.
      // Late height-for-width/layout updates can grow a page after its first show.
      verifiedCoverage=QJsonArray();
      for(auto &entry:observedIntervals) {
        auto intervals=entry.second;std::sort(intervals.begin(),intervals.end());
        int end=0,missing=-1;
        QJsonArray ranges;
        for(const auto &range:intervals) {
          ranges.append(QJsonArray{range.first,range.second});
          if(range.first>end) {missing=end;break;}
          end=qMax(end,range.second);
        }
        const int extent=observedExtents[entry.first];
        if(missing<0&&end<extent) missing=end;
        if(missing>=0) {
          if(++coverageRepairs>100) {std::fprintf(stderr,"Unstable layout: full scroll coverage could not be verified\n");app.exit(10);return;}
          Page repair=profileTemplates.at(entry.first);
          repair.scrollY=missing;
          repair.name+=QString("-coverage-repair-%1-y%2").arg(coverageRepairs).arg(missing);
          pages.push_back(repair);QTimer::singleShot(0,next);return;
        }
        verifiedCoverage.append(QJsonObject{{"profile",entry.first},{"extent",extent},{"coveredThrough",end},{"ranges",ranges},{"passed",true}});
      }
      QFile report(output+"/ru_preview_manifest.json");
      if(!report.open(QIODevice::WriteOnly)) {app.exit(6);return;}
      report.write(QJsonDocument(QJsonObject{{"kind","ACTUAL_QT_WIDGET_HOST_PREVIEW"},{"language","main_ru"},
        {"paramsRoot",paramsRoot},{"vehicleFixture","VW/ICBM available; fixture only"},{"deviceActions",false},
        {"settingsMutationsByClicks",false},{"fonts",fonts},{"captures",captures},{"unreachableContexts",unreachableContexts},{"sweepCoverage",sweepCoverage},{"cruiseHelpInteraction",cruiseHelpInteraction},{"fullMainScrollCoverageVerified",true},{"coverageRepairCount",coverageRepairs},{"verifiedCoverage",verifiedCoverage}}).toJson());
      app.exit(0);return;
    }
    const Page page=pages[index++];
    window.setCurrentPanel(page.panel);
    const QStringList panelClasses{"DevicePanelSP","NetworkingSP","SunnylinkPanel","TogglesPanelSP","SoftwarePanelSP",
      "ModelsPanel","LateralPanel","LongitudinalPanel","VisualsPanel","DisplayPanel","OsmPanel","TripsPanel",
      "VehiclePanel","FirehosePanel","DeveloperPanelSP"};
    QWidget *scope=widgetClass(&window,panelClasses.at(page.panel));
    if(!scope) {unreachableContexts.append(QJsonObject{{"page",page.name},{"class",panelClasses.at(page.panel)},
      {"reason","actual top-level class not found; screenshot retained but description scope skipped"}});scope=&window;}
    if(page.speed) {
      auto *cruise=window.findChild<LongitudinalPanel *>();
      auto *speed=cruise?cruise->findChild<SpeedLimitSettings *>():nullptr;
      if(!speed) {app.exit(7);return;}
      // Preview-only navigation into the real nested widget; no control is clicked.
      auto *layout=qobject_cast<QStackedLayout *>(cruise->layout());
      if(!layout || layout->indexOf(speed)<0) {app.exit(7);return;}
      layout->setCurrentWidget(speed);speed->refresh();scope=speed;
    }
    if(!page.nestedClass.isEmpty()) {
      QWidget *nested=widgetClass(&window,page.nestedClass);
      if(!nested||!displayNested(nested,&window)) {
        unreachableContexts.append(QJsonObject{{"page",page.name},{"class",page.nestedClass},
          {"reason","actual class absent or not reachable through QStackedLayout/QStackedWidget"}});
        QTimer::singleShot(0,next);return;
      }
      scope=nested;
    }
    if(page.descriptions) for(auto *control:scope->findChildren<AbstractControl *>()) {
      if(!control->isVisible()||control->getDescription().isEmpty()) continue;
      if(page.panel==7) {
        if(auto *sp=qobject_cast<AbstractControlSP *>(control)) if(auto *title=helpTitle(sp)) {
          bool open=false;
          for(auto *label:control->findChildren<QLabel *>()) if(label->text()==control->getDescription()) open=label->isVisible();
          if(!open) clickHelpTitle(title);
        }
      } else control->showDescription();
    }
    QTimer::singleShot(120,[&,page,scope](){
      // Help-title clicks bring each target into view; restore this shot's requested position only after all expansion.
      if(page.scrollY>=0) {
        scroll(scope,0);
        if(auto *area=mainScrollArea(scope)) area->verticalScrollBar()->setValue(page.scrollY);
      } else scroll(scope,page.fraction);
      QTimer::singleShot(120,[&,page,scope](){
        QAbstractScrollArea *mainArea=mainScrollArea(scope);
        int actualScrollY=mainArea?mainArea->verticalScrollBar()->value():0;
        int scrollMaximum=mainArea?mainArea->verticalScrollBar()->maximum():0;
        int viewportHeight=mainArea?mainArea->viewport()->height():window.height();
        const QString profile=QString::number(page.panel)+"/"+(page.speed?"speed":page.nestedClass)+"/"+(page.descriptions?"descriptions":"normal");
        if(page.scrollY<0&&!sweepPlanned.contains(profile)) {
          sweepPlanned.insert(profile);
          const int step=qMax(1,viewportHeight-100);
          QJsonArray positions;
          std::vector<int> offsets;
          for(int y=0;y<scrollMaximum;y+=step) offsets.push_back(y);
          offsets.push_back(scrollMaximum);
          for(int y:offsets) {
            Page sweep=page;
            sweep.name=page.name+QString("-sweep-y%1").arg(y,5,10,QLatin1Char('0'));
            sweep.scrollY=y;pages.push_back(sweep);positions.append(y);
          }
          sweepCoverage.append(QJsonObject{{"profile",profile},{"viewportHeight",viewportHeight},
            {"maximum",scrollMaximum},{"step",step},{"overlapPixels",100},{"plannedPositions",positions}});
        }
        const QString path=output+"/"+page.name+".png";
        if(!window.grab().save(path)) {app.exit(8);return;}
        mainArea=mainScrollArea(scope);
        actualScrollY=mainArea?mainArea->verticalScrollBar()->value():0;
        scrollMaximum=mainArea?mainArea->verticalScrollBar()->maximum():0;
        viewportHeight=mainArea?mainArea->viewport()->height():window.height();
        if(!profileTemplates.count(profile)) profileTemplates.emplace(profile,page);
        observedIntervals[profile].push_back({actualScrollY,actualScrollY+viewportHeight});
        observedExtents[profile]=qMax(observedExtents[profile],scrollMaximum+viewportHeight);
        QJsonArray labels, captions, scrollAreas;
        for(auto *widget:window.findChildren<QWidget *>()) {
          if(!widget->isVisible()) continue;
          QString text;
          if(auto *label=qobject_cast<QLabel *>(widget)) {
            text=label->text();
            if(!text.isEmpty()) labels.append(QJsonObject{{"text",text},{"width",label->width()},{"height",label->height()}});
          } else if(auto *button=qobject_cast<QAbstractButton *>(widget)) text=button->text();
          if(!text.isEmpty()) captions.append(captionGeometry(widget,&window,text));
          if(auto *area=qobject_cast<QScrollArea *>(widget)) scrollAreas.append(QJsonObject{
            {"class",area->metaObject()->className()},{"viewport",rectJson(QRect(area->viewport()->mapTo(&window,QPoint(0,0)),area->viewport()->size()))},
            {"contentWidth",area->widget()?area->widget()->width():0},
            {"contentMinimumSizeHintWidth",area->widget()?area->widget()->minimumSizeHint().width():0},
            {"horizontalScrollMaximum",area->horizontalScrollBar()->maximum()},
            {"verticalScrollValue",area->verticalScrollBar()->value()},{"verticalScrollMaximum",area->verticalScrollBar()->maximum()}});
        }
        captures.append(QJsonObject{{"file",path},{"width",window.width()},{"height",window.height()},
          {"panelIndex",page.panel},{"nestedClass",page.nestedClass},{"scrollFraction",page.fraction},{"descriptionsExpanded",page.descriptions},
          {"profile",profile},{"sweep",page.scrollY>=0},{"scrollY",actualScrollY},{"scrollMaximum",scrollMaximum},{"viewportHeight",viewportHeight},
          {"visibleLabels",labels},{"captions",captions},{"scrollAreas",scrollAreas}});
        next();
      });
    });
  };
  QTimer::singleShot(400,[&](){
    cruiseHelpInteraction=checkCruiseHelpInteraction(window);
    QFile proof(output+"/cruise_help_interaction.json");
    if(proof.open(QIODevice::WriteOnly)) proof.write(QJsonDocument(cruiseHelpInteraction).toJson());
    if(!cruiseHelpInteraction.value("passed").toBool()) {app.exit(9);return;}
    next();
  });
  return app.exec();
}
