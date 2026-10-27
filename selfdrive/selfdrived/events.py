#!/usr/bin/env python3
import math
import os

from cereal import log, car
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.git import get_short_branch
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.locationd.calibrationd import MIN_SPEED_FILTER
from openpilot.system.micd import SAMPLE_RATE, SAMPLE_BUFFER
from openpilot.selfdrive.ui.feedback.feedbackd import FEEDBACK_MAX_DURATION

from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EventsBase, Priority, ET, Alert, \
  NoEntryAlert, SoftDisableAlert, UserSoftDisableAlert, ImmediateDisableAlert, EngagementAlert, NormalPermanentAlert, \
  StartupAlert, AlertCallbackType, wrong_car_mode_alert


AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
EventName = log.OnroadEvent.EventName


# get event name from enum
EVENT_NAME = {v: k for k, v in EventName.schema.enumerants.items()}


class Events(EventsBase):
  def __init__(self):
    super().__init__()
    self.event_counters = dict.fromkeys(EVENTS.keys(), 0)

  def get_events_mapping(self) -> dict[int, dict[str, Alert | AlertCallbackType]]:
    return EVENTS

  def get_event_name(self, event: int):
    return EVENT_NAME[event]

  def get_event_msg_type(self):
    return log.OnroadEvent



# ********** helper functions **********
def get_display_speed(speed_ms: float, metric: bool) -> str:
  speed = int(round(speed_ms * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH)))
  unit = 'км/ч' if metric else 'миль/ч'
  return f"{speed} {unit}"


# ********** alert callback functions **********


def soft_disable_alert(alert_text_2: str) -> AlertCallbackType:
  def func(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
    if soft_disable_time < int(0.5 / DT_CTRL):
      return ImmediateDisableAlert(alert_text_2)
    return SoftDisableAlert(alert_text_2)
  return func

def user_soft_disable_alert(alert_text_2: str) -> AlertCallbackType:
  def func(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
    if soft_disable_time < int(0.5 / DT_CTRL):
      return ImmediateDisableAlert(alert_text_2)
    return UserSoftDisableAlert(alert_text_2)
  return func

def startup_master_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  branch = get_short_branch()  # Ensure get_short_branch is cached to avoid lags on startup
  if "REPLAY" in os.environ:
    branch = "replay"

  return StartupAlert('ВНИМАНИЕ: эта ветка не протестирована', branch, alert_status=AlertStatus.userPrompt)

def below_engage_speed_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  return NoEntryAlert(f"Двигайтесь быстрее {get_display_speed(CP.minEnableSpeed, metric)} для включения")


def below_steer_speed_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  return Alert(
    f"Рулевое управление недоступно ниже {get_display_speed(CP.minSteerSpeed, metric)}",
    "",
    AlertStatus.userPrompt, AlertSize.small,
    Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 0.4)


def calibration_incomplete_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  first_word = 'Повторная калибровка' if sm['liveCalibration'].calStatus == log.LiveCalibrationData.Status.recalibrating else 'Калибровка'
  return Alert(
    f"{first_word}: {sm['liveCalibration'].calPerc:.0f}%",
    f"Двигайтесь быстрее {get_display_speed(MIN_SPEED_FILTER, metric)}",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .2)


def audio_feedback_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  duration = FEEDBACK_MAX_DURATION - ((sm['audioFeedback'].blockNum + 1) * SAMPLE_BUFFER / SAMPLE_RATE)
  return NormalPermanentAlert(
    'Запись голосового отзыва',
    f"{round(duration)} с{'' if round(duration) != 1 else ''}. Нажмите ещё раз для досрочного сохранения.",
    priority=Priority.LOW)


# *** debug alerts ***

def out_of_space_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  full_perc = round(100. - sm['deviceState'].freeSpacePercent)
  return NormalPermanentAlert('Недостаточно места для хранения', f"{full_perc}% заполнено")


def posenet_invalid_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  mdl = sm['modelV2'].velocity.x[0] if len(sm['modelV2'].velocity.x) else math.nan
  err = CS.vEgo - mdl
  msg = f"Ошибка скорости: {err:.1f} м/с"
  return NoEntryAlert(msg, alert_text_1='Недостоверная скорость модели Posenet')


def process_not_running_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  not_running = [p.name for p in sm['managerState'].processes if not p.running and p.shouldBeRunning]
  msg = ', '.join(not_running)
  return NoEntryAlert(msg, alert_text_1='Процесс не запущен')


def comm_issue_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  bs = [s for s in sm.data.keys() if not sm.all_checks([s, ])]
  msg = ', '.join(bs[:4])  # can't fit too many on one line
  return NoEntryAlert(msg, alert_text_1='Ошибка связи между процессами')


def camera_malfunction_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  all_cams = ('roadCameraState', 'driverCameraState', 'wideRoadCameraState')
  bad_cams = [s.replace('State', '') for s in all_cams if s in sm.data.keys() and not sm.all_checks([s, ])]
  return NormalPermanentAlert('Неисправность камеры', ', '.join(bad_cams))


def calibration_invalid_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  rpy = sm['liveCalibration'].rpyCalib
  yaw = math.degrees(rpy[2] if len(rpy) == 3 else math.nan)
  pitch = math.degrees(rpy[1] if len(rpy) == 3 else math.nan)
  angles = f"Переустановите устройство (наклон: {pitch:.1f}°, рыскание: {yaw:.1f}°)"
  return NormalPermanentAlert('Калибровка недействительна', angles)


def paramsd_invalid_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  if not sm['liveParameters'].angleOffsetValid:
    angle_offset_deg = sm['liveParameters'].angleOffsetDeg
    title = 'Обнаружено смещение рулевого управления'
    text = f"Слишком большое смещение угла (смещение: {angle_offset_deg:.1f}°)"
  elif not sm['liveParameters'].steerRatioValid:
    steer_ratio = sm['liveParameters'].steerRatio
    title = 'Несоответствие передаточного отношения рулевого управления'
    text = f"Возможно нарушение геометрии рулевой рейки (отношение: {steer_ratio:.1f})"
  elif not sm['liveParameters'].stiffnessFactorValid:
    stiffness_factor = sm['liveParameters'].stiffnessFactor
    title = 'Необычная жёсткость шин'
    text = f"Проверьте шины, давление и углы установки колёс (коэффициент: {stiffness_factor:.1f})"
  else:
    return NoEntryAlert('Временная ошибка paramsd')

  return NoEntryAlert(alert_text_1=title, alert_text_2=text)

def overheat_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  cpu = max(sm['deviceState'].cpuTempC, default=0.)
  gpu = max(sm['deviceState'].gpuTempC, default=0.)
  temp = max((cpu, gpu, sm['deviceState'].memoryTempC))
  return NormalPermanentAlert('Перегрев системы', f"{temp:.0f} °C")


def low_memory_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  return NormalPermanentAlert('Мало оперативной памяти', f"{sm['deviceState'].memoryUsagePercent}% использовано")


def high_cpu_usage_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  x = max(sm['deviceState'].cpuUsagePercent, default=0.)
  return NormalPermanentAlert('Высокая загрузка процессора', f"{x}% использовано")


def modeld_lagging_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  return NormalPermanentAlert('Модель вождения работает с задержкой', f"{sm['modelV2'].frameDropPerc:.1f}% кадров пропущено")


def joystick_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  gb = sm['carControl'].actuators.accel / 4.
  steer = sm['carControl'].actuators.torque
  vals = f"Газ: {round(gb * 100.)}%, руль: {round(steer * 100.)}%"
  return NormalPermanentAlert('Режим джойстика', vals)


def longitudinal_maneuver_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  ad = sm['alertDebug']
  audible_alert = AudibleAlert.prompt if 'Active' in ad.alertText1 else AudibleAlert.none
  alert_status = AlertStatus.userPrompt if 'Active' in ad.alertText1 else AlertStatus.normal
  alert_size = AlertSize.mid if ad.alertText2 else AlertSize.small
  return Alert(ad.alertText1, ad.alertText2,
               alert_status, alert_size,
               Priority.LOW, VisualAlert.none, audible_alert, 0.2)


def personality_changed_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  personality = str(personality).title()
  return NormalPermanentAlert(f"Стиль вождения: {personality}", duration=1.5)


def invalid_lkas_setting_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  text = 'Для включения переключите штатную LKAS'
  if CP.brand == "tesla":
    text = 'Для включения переключитесь на круиз-контроль с учётом движения'
  elif CP.brand == "mazda":
    text = 'Для включения активируйте LKAS автомобиля'
  elif CP.brand == "nissan":
    text = 'Для включения отключите штатную LKAS автомобиля'
  return NormalPermanentAlert('Неверная настройка LKAS', text)



EVENTS: dict[int, dict[str, Alert | AlertCallbackType]] = {
  # ********** events with no alerts **********

  EventName.stockFcw: {},
  EventName.actuatorsApiUnavailable: {},

  # ********** events only containing alerts displayed in all states **********

  EventName.joystickDebug: {
    ET.WARNING: joystick_alert,
    ET.PERMANENT: NormalPermanentAlert('Режим джойстика'),
  },

  EventName.longitudinalManeuver: {
    ET.WARNING: longitudinal_maneuver_alert,
    ET.PERMANENT: NormalPermanentAlert('Режим продольного маневрирования',
                                       'Убедитесь, что дорога впереди свободна'),
  },

  EventName.selfdriveInitializing: {
    ET.NO_ENTRY: NoEntryAlert('Инициализация системы'),
  },

  EventName.startup: {
    ET.PERMANENT: StartupAlert('Будьте готовы в любой момент взять управление на себя')
  },

  EventName.startupMaster: {
    ET.PERMANENT: startup_master_alert,
  },

  EventName.startupNoControl: {
    ET.PERMANENT: StartupAlert('Режим видеорегистратора'),
    ET.NO_ENTRY: NoEntryAlert('Режим видеорегистратора'),
  },

  EventName.startupNoCar: {
    ET.PERMANENT: StartupAlert('Режим видеорегистратора: автомобиль не поддерживается'),
  },

  EventName.startupNoSecOcKey: {
    ET.PERMANENT: NormalPermanentAlert('Режим видеорегистратора',
                                       'Ключ безопасности недоступен',
                                       priority=Priority.HIGH),
  },

  EventName.dashcamMode: {
    ET.PERMANENT: NormalPermanentAlert('Режим видеорегистратора',
                                       priority=Priority.LOWEST),
  },

  EventName.invalidLkasSetting: {
    ET.PERMANENT: invalid_lkas_setting_alert,
    ET.NO_ENTRY: NoEntryAlert('Неверная настройка LKAS'),
  },

  EventName.cruiseMismatch: {
    #ET.PERMANENT: ImmediateDisableAlert("openpilot failed to cancel cruise"),
  },

  # openpilot doesn't recognize the car. This switches openpilot into a
  # read-only mode. This can be solved by adding your fingerprint.
  # See https://github.com/commaai/openpilot/wiki/Fingerprinting for more information
  EventName.carUnrecognized: {
    ET.PERMANENT: NormalPermanentAlert('Режим видеорегистратора',
                                       'Автомобиль не распознан',
                                       priority=Priority.LOWEST),
  },

  EventName.aeb: {
    ET.PERMANENT: Alert(
      'ТОРМОЗИТЕ!',
      'Экстренное торможение: опасность столкновения',
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.none, 2.),
    ET.NO_ENTRY: NoEntryAlert('AEB: опасность столкновения'),
  },

  EventName.stockAeb: {
    ET.PERMANENT: Alert(
      'ТОРМОЗИТЕ!',
      'Штатная AEB: опасность столкновения',
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.none, 2.),
    ET.NO_ENTRY: NoEntryAlert('Штатная AEB: опасность столкновения'),
  },

  EventName.fcw: {
    ET.PERMANENT: Alert(
      'ТОРМОЗИТЕ!',
      'Опасность столкновения',
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.warningSoft, 2.),
  },

  EventName.ldw: {
    ET.PERMANENT: Alert(
      'Обнаружен выход из полосы',
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.ldw, AudibleAlert.prompt, 3.),
  },

  # ********** events only containing alerts that display while engaged **********

  EventName.steerTempUnavailableSilent: {
    ET.WARNING: Alert(
      'Рулевое управление временно недоступно',
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.prompt, 1.8),
  },

  EventName.preDriverDistracted: {
    ET.PERMANENT: Alert(
      'Следите за дорогой',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.promptDriverDistracted: {
    ET.PERMANENT: Alert(
      'Следите за дорогой',
      'Водитель отвлёкся',
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.promptDistracted, .1),
  },

  EventName.driverDistracted: {
    ET.PERMANENT: Alert(
      'НЕМЕДЛЕННО ОТКЛЮЧИТЕ СИСТЕМУ',
      'Водитель отвлёкся',
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.warningImmediate, .1),
  },

  EventName.preDriverUnresponsive: {
    ET.PERMANENT: Alert(
      'Возьмитесь за руль: лицо не обнаружено',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.none, .1),
  },

  EventName.promptDriverUnresponsive: {
    ET.PERMANENT: Alert(
      'Возьмитесь за руль',
      'Водитель не реагирует',
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.promptDistracted, .1),
  },

  EventName.driverUnresponsive: {
    ET.PERMANENT: Alert(
      'НЕМЕДЛЕННО ОТКЛЮЧИТЕ СИСТЕМУ',
      'Водитель не реагирует',
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.warningImmediate, .1),
  },

  EventName.manualRestart: {
    ET.WARNING: Alert(
      'ВОЗЬМИТЕ УПРАВЛЕНИЕ НА СЕБЯ',
      'Продолжайте движение самостоятельно',
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .2),
  },

  EventName.resumeRequired: {
    ET.WARNING: Alert(
      'Нажмите RESUME для продолжения движения',
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .2),
  },

  EventName.belowSteerSpeed: {
    ET.WARNING: below_steer_speed_alert,
  },

  EventName.preLaneChangeLeft: {
    ET.WARNING: Alert(
      'Когда будет безопасно, поверните руль влево для перестроения',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.preLaneChangeRight: {
    ET.WARNING: Alert(
      'Когда будет безопасно, поверните руль вправо для перестроения',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.laneChangeBlocked: {
    ET.WARNING: Alert(
      'Автомобиль в слепой зоне',
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, .1),
  },

  EventName.laneChange: {
    ET.WARNING: Alert(
      'Перестроение',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.steerSaturated: {
    ET.WARNING: Alert(
      'Возьмите управление на себя',
      'Поворот превышает возможности рулевого управления',
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.promptRepeat, 2.),
  },

  # Thrown when the fan is driven at >50% but is not rotating
  EventName.fanMalfunction: {
    ET.PERMANENT: NormalPermanentAlert('Неисправность вентилятора', 'Вероятна неисправность оборудования'),
  },

  # Camera is not outputting frames
  EventName.cameraMalfunction: {
    ET.PERMANENT: camera_malfunction_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Неисправность камеры'),
    ET.NO_ENTRY: NoEntryAlert('Неисправность камеры: перезагрузите устройство'),
  },
  # Camera framerate too low
  EventName.cameraFrameRate: {
    ET.PERMANENT: NormalPermanentAlert('Низкая частота кадров камеры', 'Перезагрузите устройство'),
    ET.SOFT_DISABLE: soft_disable_alert('Низкая частота кадров камеры'),
    ET.NO_ENTRY: NoEntryAlert('Низкая частота кадров камеры: перезагрузите устройство'),
  },

  # Unused

  EventName.locationdTemporaryError: {
    ET.NO_ENTRY: NoEntryAlert('Временная ошибка locationd'),
    ET.SOFT_DISABLE: soft_disable_alert('Временная ошибка locationd'),
  },

  EventName.locationdPermanentError: {
    ET.NO_ENTRY: NoEntryAlert('Постоянная ошибка locationd'),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Постоянная ошибка locationd'),
    ET.PERMANENT: NormalPermanentAlert('Постоянная ошибка locationd'),
  },

  # openpilot tries to learn certain parameters about your car by observing
  # how the car behaves to steering inputs from both human and openpilot driving.
  # This includes:
  # - steer ratio: gear ratio of the steering rack. Steering angle divided by tire angle
  # - tire stiffness: how much grip your tires have
  # - angle offset: most steering angle sensors are offset and measure a non zero angle when driving straight
  # This alert is thrown when any of these values exceed a sanity check. This can be caused by
  # bad alignment or bad sensor data. If this happens consistently consider creating an issue on GitHub
  EventName.paramsdTemporaryError: {
    ET.NO_ENTRY: paramsd_invalid_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Временная ошибка paramsd'),
  },

  EventName.paramsdPermanentError: {
    ET.NO_ENTRY: NoEntryAlert('Постоянная ошибка paramsd'),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Постоянная ошибка paramsd'),
    ET.PERMANENT: NormalPermanentAlert('Постоянная ошибка paramsd'),
  },

  # ********** events that affect controls state transitions **********

  EventName.pcmEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.engage),
  },

  EventName.buttonEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.engage),
  },

  EventName.pcmDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
  },

  EventName.buttonCancel: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: NoEntryAlert('Нажата кнопка отмены'),
  },

  EventName.brakeHold: {
    ET.WARNING: Alert(
      'Нажмите RESUME для выхода из удержания тормоза',
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .2),
  },

  EventName.parkBrake: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: NoEntryAlert('Стояночный тормоз включён'),
  },

  EventName.pedalPressed: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: NoEntryAlert('Нажата педаль',
                              visual_alert=VisualAlert.brakePressed),
  },

  EventName.steerDisengage: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: NoEntryAlert('Водитель воздействует на руль'),
  },

  EventName.preEnableStandstill: {
    ET.PRE_ENABLE: Alert(
      'Для включения отпустите тормоз',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .1, creation_delay=1.),
  },

  EventName.gasPressedOverride: {
    ET.OVERRIDE_LONGITUDINAL: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.steerOverride: {
    ET.OVERRIDE_LATERAL: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .1),
  },

  EventName.wrongCarMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: wrong_car_mode_alert,
  },

  EventName.resumeBlocked: {
    ET.NO_ENTRY: NoEntryAlert('Для включения нажмите SET'),
  },

  EventName.wrongCruiseMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    ET.NO_ENTRY: NoEntryAlert('Адаптивный круиз-контроль отключён'),
  },

  EventName.steerTempUnavailable: {
    ET.SOFT_DISABLE: soft_disable_alert('Рулевое управление временно недоступно'),
    ET.NO_ENTRY: NoEntryAlert('Рулевое управление временно недоступно'),
  },

  EventName.steerTimeLimit: {
    ET.SOFT_DISABLE: soft_disable_alert('Достигнут лимит времени рулевого управления автомобиля'),
    ET.NO_ENTRY: NoEntryAlert('Достигнут лимит времени рулевого управления автомобиля'),
  },

  EventName.outOfSpace: {
    ET.PERMANENT: out_of_space_alert,
    ET.NO_ENTRY: NoEntryAlert('Недостаточно места для хранения'),
  },

  EventName.belowEngageSpeed: {
    ET.NO_ENTRY: below_engage_speed_alert,
  },

  EventName.sensorDataInvalid: {
    ET.PERMANENT: Alert(
      'Недостоверные данные датчиков',
      'Возможна неисправность оборудования',
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, .2, creation_delay=1.),
    ET.NO_ENTRY: NoEntryAlert('Недостоверные данные датчиков'),
    ET.SOFT_DISABLE: soft_disable_alert('Недостоверные данные датчиков'),
  },

  EventName.noGps: {
  },

  EventName.tooDistracted: {
    ET.NO_ENTRY: NoEntryAlert('Слишком частое отвлечение внимания'),
  },

  EventName.excessiveActuation: {
    ET.SOFT_DISABLE: soft_disable_alert('Чрезмерное управляющее воздействие'),
    ET.NO_ENTRY: NoEntryAlert('Чрезмерное управляющее воздействие'),
  },

  EventName.overheat: {
    ET.PERMANENT: overheat_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Перегрев системы'),
    ET.NO_ENTRY: NoEntryAlert('Перегрев системы'),
  },

  EventName.wrongGear: {
    ET.SOFT_DISABLE: user_soft_disable_alert('Выбрана передача, отличная от D'),
    ET.NO_ENTRY: NoEntryAlert('Выбрана передача, отличная от D'),
  },

  # This alert is thrown when the calibration angles are outside of the acceptable range.
  # For example if the device is pointed too much to the left or the right.
  # Usually this can only be solved by removing the mount from the windshield completely,
  # and attaching while making sure the device is pointed straight forward and is level.
  # See https://comma.ai/setup for more information
  EventName.calibrationInvalid: {
    ET.PERMANENT: calibration_invalid_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Калибровка недействительна: переустановите устройство и повторите калибровку'),
    ET.NO_ENTRY: NoEntryAlert('Калибровка недействительна: переустановите устройство и повторите калибровку'),
  },

  EventName.calibrationIncomplete: {
    ET.PERMANENT: calibration_incomplete_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Калибровка не завершена'),
    ET.NO_ENTRY: NoEntryAlert('Выполняется калибровка'),
  },

  EventName.calibrationRecalibrating: {
    ET.PERMANENT: calibration_incomplete_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Изменено крепление устройства: повторная калибровка'),
    ET.NO_ENTRY: NoEntryAlert('Изменено крепление: повторная калибровка'),
  },

  EventName.doorOpen: {
    ET.SOFT_DISABLE: user_soft_disable_alert('Дверь открыта'),
    ET.NO_ENTRY: NoEntryAlert('Дверь открыта'),
  },

  EventName.seatbeltNotLatched: {
    ET.SOFT_DISABLE: user_soft_disable_alert('Ремень безопасности не пристёгнут'),
    ET.NO_ENTRY: NoEntryAlert('Ремень безопасности не пристёгнут'),
  },

  EventName.espDisabled: {
    ET.SOFT_DISABLE: soft_disable_alert('Система стабилизации отключена'),
    ET.NO_ENTRY: NoEntryAlert('Система стабилизации отключена'),
  },

  EventName.lowBattery: {
    ET.SOFT_DISABLE: soft_disable_alert('Низкий заряд аккумулятора'),
    ET.NO_ENTRY: NoEntryAlert('Низкий заряд аккумулятора'),
  },

  # Different openpilot services communicate between each other at a certain
  # interval. If communication does not follow the regular schedule this alert
  # is thrown. This can mean a service crashed, did not broadcast a message for
  # ten times the regular interval, or the average interval is more than 10% too high.
  EventName.commIssue: {
    ET.SOFT_DISABLE: soft_disable_alert('Ошибка связи между процессами'),
    ET.NO_ENTRY: comm_issue_alert,
  },
  EventName.commIssueAvgFreq: {
    ET.SOFT_DISABLE: soft_disable_alert('Низкая частота обмена между процессами'),
    ET.NO_ENTRY: NoEntryAlert('Низкая частота обмена между процессами'),
  },

  EventName.selfdrivedLagging: {
    ET.SOFT_DISABLE: soft_disable_alert('Система работает с задержкой'),
    ET.NO_ENTRY: NoEntryAlert('Задержка процесса управления: перезагрузите устройство'),
  },

  # Thrown when manager detects a service exited unexpectedly while driving
  EventName.processNotRunning: {
    ET.NO_ENTRY: process_not_running_alert,
    ET.SOFT_DISABLE: soft_disable_alert('Процесс не запущен'),
  },

  EventName.radarFault: {
    ET.SOFT_DISABLE: soft_disable_alert('Ошибка радара: перезапустите автомобиль'),
    ET.NO_ENTRY: NoEntryAlert('Ошибка радара: перезапустите автомобиль'),
  },

  EventName.radarTempUnavailable: {
    ET.SOFT_DISABLE: soft_disable_alert('Радар временно недоступен'),
    ET.NO_ENTRY: NoEntryAlert('Радар временно недоступен'),
  },

  # Every frame from the camera should be processed by the model. If modeld
  # is not processing frames fast enough they have to be dropped. This alert is
  # thrown when over 20% of frames are dropped.
  EventName.modeldLagging: {
    ET.SOFT_DISABLE: soft_disable_alert('Модель вождения работает с задержкой'),
    ET.NO_ENTRY: NoEntryAlert('Модель вождения работает с задержкой'),
    ET.PERMANENT: modeld_lagging_alert,
  },

  # Besides predicting the path, lane lines and lead car data the model also
  # predicts the current velocity and rotation speed of the car. If the model is
  # very uncertain about the current velocity while the car is moving, this
  # usually means the model has trouble understanding the scene. This is used
  # as a heuristic to warn the driver.
  EventName.posenetInvalid: {
    ET.SOFT_DISABLE: soft_disable_alert('Недостоверная скорость модели Posenet'),
    ET.NO_ENTRY: posenet_invalid_alert,
  },

  # When the localizer detects an acceleration of more than 40 m/s^2 (~4G) we
  # alert the driver the device might have fallen from the windshield.
  EventName.deviceFalling: {
    ET.SOFT_DISABLE: soft_disable_alert('Устройство выпало из крепления'),
    ET.NO_ENTRY: NoEntryAlert('Устройство выпало из крепления'),
  },

  EventName.lowMemory: {
    ET.SOFT_DISABLE: soft_disable_alert('Мало оперативной памяти: перезагрузите устройство'),
    ET.PERMANENT: low_memory_alert,
    ET.NO_ENTRY: NoEntryAlert('Мало оперативной памяти: перезагрузите устройство'),
  },

  EventName.accFaulted: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Ошибка круиз-контроля: перезапустите автомобиль'),
    ET.PERMANENT: NormalPermanentAlert('Ошибка круиз-контроля: для включения перезапустите автомобиль'),
    ET.NO_ENTRY: NoEntryAlert('Ошибка круиз-контроля: перезапустите автомобиль'),
  },

  EventName.espActive: {
    ET.SOFT_DISABLE: soft_disable_alert('Система стабилизации активна'),
    ET.NO_ENTRY: NoEntryAlert('Система стабилизации активна'),
  },

  EventName.controlsMismatch: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Несогласованность управления'),
    ET.NO_ENTRY: NoEntryAlert('Несогласованность управления'),
  },

  # Sometimes the USB stack on the device can get into a bad state
  # causing the connection to the panda to be lost
  EventName.usbError: {
    ET.SOFT_DISABLE: soft_disable_alert('Ошибка USB: перезагрузите устройство'),
    ET.PERMANENT: NormalPermanentAlert('Ошибка USB: перезагрузите устройство'),
    ET.NO_ENTRY: NoEntryAlert('Ошибка USB: перезагрузите устройство'),
  },

  # This alert can be thrown for the following reasons:
  # - No CAN data received at all
  # - CAN data is received, but some message are not received at the right frequency
  # If you're not writing a new car port, this is usually cause by faulty wiring
  EventName.canError: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Ошибка CAN'),
    ET.PERMANENT: Alert(
      'Ошибка CAN: проверьте соединения',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1., creation_delay=1.),
    ET.NO_ENTRY: NoEntryAlert('Ошибка CAN: проверьте соединения'),
  },

  EventName.canBusMissing: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Шина CAN отключена'),
    ET.PERMANENT: Alert(
      'Шина CAN отключена: возможна неисправность кабеля',
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1., creation_delay=1.),
    ET.NO_ENTRY: NoEntryAlert('Шина CAN отключена: проверьте соединения'),
  },

  EventName.steerUnavailable: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Ошибка LKAS: перезапустите автомобиль'),
    ET.PERMANENT: NormalPermanentAlert('Ошибка LKAS: для включения перезапустите автомобиль'),
    ET.NO_ENTRY: NoEntryAlert('Ошибка LKAS: перезапустите автомобиль'),
  },

  EventName.reverseGear: {
    ET.PERMANENT: Alert(
      'Задняя\nпередача',
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .2, creation_delay=0.5),
    ET.USER_DISABLE: ImmediateDisableAlert('Задняя передача'),
    ET.NO_ENTRY: NoEntryAlert('Задняя передача'),
  },

  # On cars that use stock ACC the car can decide to cancel ACC for various reasons.
  # When this happens we can no long control the car so the user needs to be warned immediately.
  EventName.cruiseDisabled: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Круиз-контроль выключен'),
  },

  # When the relay in the harness box opens the CAN bus between the LKAS camera
  # and the rest of the car is separated. When messages from the LKAS camera
  # are received on the car side this usually means the relay hasn't opened correctly
  # and this alert is thrown.
  EventName.relayMalfunction: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Неисправность реле жгута проводки'),
    ET.PERMANENT: NormalPermanentAlert('Неисправность реле жгута проводки', 'Проверьте оборудование'),
    ET.NO_ENTRY: NoEntryAlert('Неисправность реле жгута проводки'),
  },

  EventName.speedTooLow: {
    ET.IMMEDIATE_DISABLE: Alert(
      'openpilot отменён',
      'Слишком низкая скорость',
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.disengage, 3.),
  },

  # When the car is driving faster than most cars in the training data, the model outputs can be unpredictable.
  EventName.speedTooHigh: {
    ET.WARNING: Alert(
      'Слишком высокая скорость',
      'Низкая уверенность модели на этой скорости',
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.promptRepeat, 4.),
    ET.NO_ENTRY: NoEntryAlert('Для включения снизьте скорость'),
  },

  EventName.vehicleSensorsInvalid: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert('Недостоверные данные датчиков автомобиля'),
    ET.PERMANENT: NormalPermanentAlert('Калибровка датчиков автомобиля', 'Начните движение для калибровки'),
    ET.NO_ENTRY: NoEntryAlert('Калибровка датчиков автомобиля'),
  },

  EventName.personalityChanged: {
    ET.WARNING: personality_changed_alert,
  },

  EventName.userBookmark: {
    ET.PERMANENT: NormalPermanentAlert('Метка сохранена', duration=1.5),
  },

  EventName.audioFeedback: {
    ET.PERMANENT: audio_feedback_alert,
  },
}


if __name__ == '__main__':
  # print all alerts by type and priority
  from cereal.services import SERVICE_LIST
  from collections import defaultdict

  event_names = {v: k for k, v in EventName.schema.enumerants.items()}
  alerts_by_type: dict[str, dict[Priority, list[str]]] = defaultdict(lambda: defaultdict(list))

  CP = car.CarParams.new_message()
  CS = car.CarState.new_message()
  sm = messaging.SubMaster(list(SERVICE_LIST.keys()))

  for i, alerts in EVENTS.items():
    for et, alert in alerts.items():
      if callable(alert):
        alert = alert(CP, CS, sm, False, 1, log.LongitudinalPersonality.standard)
      alerts_by_type[et][alert.priority].append(event_names[i])

  all_alerts: dict[str, list[tuple[Priority, list[str]]]] = {}
  for et, priority_alerts in alerts_by_type.items():
    all_alerts[et] = sorted(priority_alerts.items(), key=lambda x: x[0], reverse=True)

  for status, evs in sorted(all_alerts.items(), key=lambda x: x[0]):
    print(f"**** {status} ****")
    for p, alert_list in evs:
      print(f"  {repr(p)}:")
      print("   ", ', '.join(alert_list), "\n")
