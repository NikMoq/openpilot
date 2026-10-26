// Host-only constructor/show/grab/hide dialog coverage. No exec(), input or accept().
#include <QApplication>
#include <QAbstractButton>
#include <QDir>
#include <QFile>
#include <QFontDatabase>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QTranslator>
#include <memory>
#include "common/params.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/qt_window.h"

static QString trc(const char *context,const char *source) {return QCoreApplication::translate(context,source);}
int main(int argc,char **argv) {
  const QString params=QString::fromLocal8Bit(qgetenv("PARAMS_ROOT"));
  if(argc!=2 || !params.startsWith("/tmp/ru-dialog-preview-") || QDir::cleanPath(params)!=params || QFile::exists(params))return 2;
  QDir().mkpath(params);Params().put("LanguageSetting","main_ru");
  QApplication app(argc,argv);QTranslator russian;
  if(!russian.load(":/main_ru") || !app.installTranslator(&russian))return 3;
  for(const auto &name:QDir("../assets/fonts").entryList({"Inter-*.ttf"},QDir::Files))
    if(QFontDatabase::addApplicationFont("../assets/fonts/"+name)<0)return 4;
  QWidget root;root.setStyleSheet("background:black;");setMainWindow(&root);
  const QString out=argv[1];QDir().mkpath(out);QJsonArray captures;
  auto capture=[&](QString name,QWidget *dialog) {
    std::unique_ptr<QWidget> owned(dialog);setMainWindow(dialog);dialog->move(0,0);app.processEvents();
    QJsonArray captions,trees,inputFields;
    for(auto *line:dialog->findChildren<QLineEdit *>()) {
      const QPoint pos=line->mapTo(dialog,QPoint());
      inputFields.append(QJsonObject{{"echoMode",int(line->echoMode())},{"bounds",QJsonArray{pos.x(),pos.y(),line->width(),line->height()}},{"outsideParent",!line->parentWidget()->rect().contains(line->geometry())}});
    }
    for(auto *tree:dialog->findChildren<QTreeWidget *>()) {
      QJsonArray rows;
      for(QTreeWidgetItemIterator it(tree);*it;++it) {
        QFont font=(*it)->font(0);if(!font.resolve())font=tree->font();
        QFontMetrics fm(font);QRect rect=tree->visualItemRect(*it);
        rows.append(QJsonObject{{"text",(*it)->text(0)},{"effectivePixelHeight",fm.height()},{"fontPixelSize",font.pixelSize()},{"fontPointSize",font.pointSizeF()},{"width",rect.width()},{"textWidth",fm.horizontalAdvance((*it)->text(0))}});
      }
      trees.append(QJsonObject{{"fontPixelSize",tree->font().pixelSize()},{"fontPointSize",tree->font().pointSizeF()},{"fontMetricHeight",tree->fontMetrics().height()},{"rows",rows}});
    }
    for(auto *w:dialog->findChildren<QWidget *>()) {
      QString text;bool wrap=false;
      if(auto *l=qobject_cast<QLabel *>(w)){text=l->text();wrap=l->wordWrap();}
      else if(auto *b=qobject_cast<QAbstractButton *>(w)){text=b->text();if(text.isEmpty()&&!b->icon().isNull())text="[icon-only]";}
      if(text.isEmpty() || !w->isVisible())continue;
      const QPoint pos=w->mapTo(dialog,QPoint());QRect visible(pos,w->size());
      captions.append(QJsonObject{{"class",w->metaObject()->className()},{"text",text},{"wordWrap",wrap},
        {"bounds",QJsonArray{pos.x(),pos.y(),w->width(),w->height()}},{"contentsWidth",w->contentsRect().width()},
        {"singleLineWidth",w->fontMetrics().horizontalAdvance(text)},
        {"heightForWidth",w->heightForWidth(w->width())},{"outsideParent",!w->parentWidget()->rect().contains(w->geometry())},{"outsideDialog",!dialog->rect().contains(visible)}});
    }
    QString file=out+"/"+name+".png";bool saved=dialog->grab().save(file);
    captures.append(QJsonObject{{"file",file},{"class",dialog->metaObject()->className()},{"width",dialog->width()},{"height",dialog->height()},{"saved",saved},{"captions",captions},{"trees",trees},{"inputFields",inputFields}});
    dialog->hide();app.processEvents();
  };
  capture("01-camera-url",new InputDialog(trc("SpeedLimitSettings","Camera database URL"),&root,
    trc("SpeedLimitSettings","SpeedCamOnline CityGuide 7.7 default: %1. Enter another HTTPS source to customize.").arg("https://speedcamonline.ru/cg/77"),false));
  capture("02-state-search",new InputDialog(trc("OsmPanel","Search State"),&root,trc("OsmPanel","Enter search keywords, or leave blank to list all states."),false));
  capture("03-reset-warning",new ConfirmationDialog(trc("DevicePanelSP","Are you sure you want to reset all sunnypilot settings to default? Once the settings are reset, there is no going back."),trc("DevicePanelSP","Reset"),trc("ConfirmationDialog","Cancel"),false,&root));
  capture("04-irreversible",new ConfirmationDialog(trc("DevicePanelSP","The reset cannot be undone. You have been warned."),trc("DevicePanelSP","Confirm"),trc("ConfirmationDialog","Cancel"),false,&root));
  capture("05-update-reboot",new ConfirmationDialog(trc("SoftwarePanelSP","%1 updates requires a reboot.<br>Reboot now?").arg("AGNOS"),trc("SoftwarePanelSP","Reboot"),trc("ConfirmationDialog","Cancel"),false,&root));
  capture("06-restore",new ConfirmationDialog(trc("SunnylinkPanel","Are you sure you want to restore the last backed up sunnypilot settings?"),trc("SunnylinkPanel","Restore"),trc("ConfirmationDialog","Cancel"),false,&root));
  capture("07-language",new MultiOptionDialog(trc("DevicePanelSP","Select a language"),{"Русский","English","中文"},"Русский",&root));
  capture("08-policy",new MultiOptionDialog(trc("SpeedLimitPolicy","Speed Limit Source"),{"Только автомобиль","Сначала автомобиль","Сначала карта","Автомобиль и карта"},"Сначала автомобиль",&root));
  QList<TreeFolder> items={{"Модели — (обновлено: 26.10.2026)",{{"","Firehose (по умолчанию)","Firehose (Default)",-1},{"","Модель для проверки длинного отображаемого названия","synthetic-ref",1}}}};
  capture("09-model-tree",new TreeOptionDialog(trc("ModelsPanel","Select a Model"),items,"Firehose (Default)","DialogPreview_Favorites",&root));
  capture("10-confirm-rich",new ConfirmationDialog(trc("DevicePanelSP","Are you sure you want to review the training guide?"),trc("DevicePanelSP","Review"),trc("ConfirmationDialog","Cancel"),true,&root));
  auto *wifi = new InputDialog(trc("Networking","Enter password"),&root,trc("Networking","for \"%1\"").arg(QString(32,QChar('W'))),true);
  wifi->setMinLength(8);
  capture("11-wifi-max-ssid",wifi);
  auto *tether = new InputDialog(trc("AdvancedNetworking","Enter new tethering password"),&root,"",true);
  tether->setMinLength(8);
  capture("12-tether-password",tether);
  QFile report(out+"/report.json");if(!report.open(QIODevice::WriteOnly))return 5;
  report.write(QJsonDocument(QJsonObject{{"qt_version",qVersion()},{"platform",app.platformName()},{"captures",captures},{"inputEvents",0},{"acceptCalls",0},{"scope","Actual constructors; synthetic selections; show/grab/hide only"}}).toJson());return 0;
}
