#pragma once

#include <QPushButton>
#include <QStyleOptionButton>
#include <QStylePainter>

// A title remains the same clickable QPushButton; only Russian text layout changes.
class RussianControlTitleButton final : public QPushButton {
public:
  explicit RussianControlTitleButton(const QString &text) : QPushButton(text) {
    setMinimumWidth(1);
    setMinimumHeight(120);
    QSizePolicy policy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    policy.setHeightForWidth(true);
    setSizePolicy(policy);
  }
  QSize minimumSizeHint() const override { return {1,120}; }
  QSize sizeHint() const override { return {700,heightForWidth(700)}; }
  bool hasHeightForWidth() const override { return true; }
  int heightForWidth(int width) const override {
    if(!icon().isNull()) return QPushButton::sizeHint().height();
    const int textWidth=qMax(1,width-contentsMargins().left()-contentsMargins().right());
    const QRect textRect=fontMetrics().boundingRect(QRect(0,0,textWidth,10000),Qt::TextWordWrap|Qt::AlignLeft,text());
    return qMax(120,textRect.height()+contentsMargins().top()+contentsMargins().bottom()+12);
  }
protected:
  void paintEvent(QPaintEvent *event) override {
    if(!icon().isNull()) { QPushButton::paintEvent(event); return; }
    QStylePainter painter(this);
    QStyleOptionButton option;
    initStyleOption(&option);
    const QString caption=option.text;
    option.text.clear();
    painter.drawControl(QStyle::CE_PushButton,option);
    const QRect textRect=style()->subElementRect(QStyle::SE_PushButtonContents,&option,this);
    painter.drawItemText(textRect,Qt::AlignLeft|Qt::AlignVCenter|Qt::TextWordWrap,
                         option.palette,isEnabled(),caption,QPalette::ButtonText);
  }
};

inline QPushButton *makeControlTitleButton(const QString &title,bool russian) {
  if(russian) return new RussianControlTitleButton(title);
  auto *button=new QPushButton(title);
  button->setFixedHeight(120);
  return button;
}
