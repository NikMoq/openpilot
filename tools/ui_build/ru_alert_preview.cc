// Isolated host-only synthetic alert fixture. No publishers, hardware or controllers.
#include <QApplication>
#include <QFontDatabase>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QFontMetrics>
#include <algorithm>
#include <QSet>
#include <QTimer>
#include "selfdrive/ui/sunnypilot/qt/onroad/alerts.h"
#include "common/params.h"
#include "selfdrive/ui/qt/onroad/alerts_ru.h"
#include "selfdrive/ui/qt/util.h"

class AlertFixture : public OnroadAlertsSP {
public:
  void set(const QString &a, const QString &b, const QString &size) {
    alert = {a,b,"host-fixture", size == "FULL" ? cereal::SelfdriveState::AlertSize::FULL : size == "MID" ? cereal::SelfdriveState::AlertSize::MID : cereal::SelfdriveState::AlertSize::SMALL,
      size == "FULL" ? cereal::SelfdriveState::AlertStatus::CRITICAL : cereal::SelfdriveState::AlertStatus::USER_PROMPT};
    update();
  }
};

int main(int argc, char **argv) {
  const QString paramsRoot=QString::fromLocal8Bit(qgetenv("PARAMS_ROOT"));
  if(!paramsRoot.startsWith("/tmp/ru-alert-preview-") || QDir::cleanPath(paramsRoot)!=paramsRoot || QFile::exists(paramsRoot))return 6;
  QDir().mkpath(paramsRoot); Params().put("LanguageSetting","main_ru"); Params().put("DevUIInfo","0");
  QApplication app(argc, argv);
  auto *state=uiStateSP();state->scene.dev_ui_info=0;
  for(auto *timer:state->findChildren<QTimer *>())timer->stop();
  if(argc!=4) return 2;
  for(const auto &name: QDir(argv[2]).entryList({"Inter-*.ttf"},QDir::Files))
    if(QFontDatabase::addApplicationFont(QString(argv[2])+"/"+name)<0) return 3;
  QFile input(argv[1]);if(!input.open(QIODevice::ReadOnly))return 4;
  QJsonArray rows=QJsonDocument::fromJson(input.readAll()).array(), reports;
  QDir().mkpath(argv[3]); AlertFixture widget;widget.resize(2160,1080);
  struct Candidate { QJsonObject data; double ratio; QString a,b,size; };
  QVector<Candidate> candidates;
  for(const auto &value:rows) {
    auto row=value.toObject();QString size=row["size"].toString();
    QString a=CommaRu::presentAlertText(row["text1"].toString(),"main_ru"),b=CommaRu::presentAlertText(row["text2"].toString(),"main_ru");
    a.replace("openpilot","sunnypilot");b.replace("openpilot","sunnypilot");
    QJsonArray metrics;double worst=0;
    const bool large=a.length()>15;
    const int margin=40;QFontMetrics topFm(InterFont(size=="SMALL"?74:88,size=="SMALL"?QFont::DemiBold:QFont::Bold));
    QRect topBounds=topFm.boundingRect(QRect(40,0,2080,0),Qt::TextWordWrap,a);
    QFontMetrics bottomFm(InterFont(66));QRect bottomBounds=bottomFm.boundingRect(QRect(40,0,2080,0),Qt::TextWordWrap,b);
    int dynH=size=="SMALL"?topBounds.height():topBounds.height()+bottomBounds.height()+80;
    QRect spRect(40,1080-dynH-80,2080,dynH+40);
    for(int i=0;i<(size=="SMALL"?1:2);++i) {
      QRect rect;int px,weight=QFont::Normal,flags=Qt::AlignHCenter;
      if(size=="SMALL") {rect=spRect;px=74;weight=QFont::DemiBold;flags=Qt::AlignCenter|Qt::TextWordWrap;}
      else if(size=="MID") {rect=i==0?QRect(spRect.x(),spRect.top()+40,spRect.width(),topBounds.height()):QRect(spRect.x(),spRect.top()+40+topBounds.height()-1+40,spRect.width(),bottomBounds.height());px=i==0?88:66;weight=i==0?QFont::Bold:QFont::Normal;flags|=Qt::TextWordWrap|Qt::AlignCenter;if(i==0)flags|=Qt::AlignTop;}
      else {rect=i==0?QRect(0,large?240:270,2160,600):QRect(0,1080-(large?361:420),2160,300);px=i==0?(large?132:177):88;weight=i==0?QFont::Bold:QFont::Normal;flags|=Qt::TextWordWrap;}
      QFontMetrics fm(InterFont(px,static_cast<QFont::Weight>(weight)));QRect bounds=fm.boundingRect(rect,flags,i?b:a);
      double ratio=std::max(double(bounds.width())/rect.width(),double(bounds.height())/rect.height());worst=std::max(worst,ratio);
      metrics.append(QJsonObject{{"role",i?"text2":"text1"},{"px",px},{"fit",rect.contains(bounds)},
        {"rect",QJsonArray{rect.x(),rect.y(),rect.width(),rect.height()}},{"bounds",QJsonArray{bounds.x(),bounds.y(),bounds.width(),bounds.height()}}});
    }
    row["translated1"]=a;row["translated2"]=b;row["metrics"]=metrics;row["worst_ratio"]=worst;candidates.append({row,worst+double(a.size()+b.size())/1000000.0,a,b,size});
  }
  std::sort(candidates.begin(),candidates.end(),[](const Candidate&a,const Candidate&b){return a.ratio>b.ratio;});
  int captured=0;QMap<QString,int> count;QSet<QString> seen;
  for(auto &c:candidates) {
    const QString key=c.size+"|"+c.a+"|"+c.b;
    if(count[c.size]<4 && !seen.contains(key)) {seen.insert(key);widget.set(c.a,c.b,c.size);widget.show();app.processEvents();QString name=QString("%1_%2.png").arg(captured++,2,10,QChar('0')).arg(c.size);widget.grab().save(QString(argv[3])+"/"+name);c.data["screenshot"]=name;++count[c.size];}
    reports.append(c.data);
  }
  QFile out(QString(argv[3])+"/report.json");if(!out.open(QIODevice::WriteOnly))return 5;
  out.write(QJsonDocument(QJsonObject{{"qt_version",qVersion()},{"renderer","Actual OnroadAlertsSP::paintEvent; actual CommaRu helper then SUNNYPILOT replacement"},{"scope","Host synthetic fixture; deployed SP adaptive geometry dev_ui_info=0; isolated Params/SubMaster only; timers stopped"},{"cases",reports},{"screenshots",captured}}).toJson());
  return 0;
}
