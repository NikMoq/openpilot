#pragma once
/* Offline-validated ICBM native forwarding adapter. Included after can_common
 * and C safety. Explicit build capability; correlated control API A9/AA.
 * These artifacts do not establish bench or road readiness. */
#define GRA_EXTERNAL_PACKET
#include "gra_transform.h"
#ifndef GRA_REFERENCE_CAPABILITY
#define GRA_REFERENCE_CAPABILITY 0
#endif
#ifndef PQ35_ICBM_ENABLE
#define PQ35_ICBM_ENABLE 0
#endif
#if PQ35_ICBM_ENABLE && !GRA_REFERENCE_CAPABILITY
#error "PQ35 ICBM requires the native forwarding implementation"
#endif
typedef enum {
  GRA_TXN_IDLE = 0, GRA_TXN_ACCEPTED = 1, GRA_TXN_WAIT_RELEASE = 2,
  GRA_TXN_COMPLETE = 3, GRA_TXN_REJECTED = 4,
  GRA_TXN_ABORTED_PENDING_RELEASE = 5, GRA_TXN_ABORTED = 6,
  GRA_TXN_ABORTED_NO_PRESS = 7,
} gra_txn_state;
typedef struct __attribute__((packed)) {
  uint8_t version, state, button, reason;
  uint16_t request_id, tx_counter;
} gra_txn_status;
static gra_state gra_adapter_state;
static uint16_t gra_adapter_request_id, gra_adapter_tx_counter;
static uint8_t gra_adapter_txn_state=GRA_TXN_IDLE, gra_adapter_button, gra_adapter_txn_reason;
static bool gra_adapter_release_echo_waiting=false;
static CANPacket_t gra_adapter_expected_release;
static inline bool gra_adapter_mode(void);
// Explicit offline build variant. Default builds retain stock forwarding.
static bool gra_adapter_capability=(PQ35_ICBM_ENABLE != 0);
static bool gra_adapter_have_native=false;
static uint32_t gra_adapter_native_us,gra_adapter_native_raw;
static struct {uint32_t accepted,coalesced,refused,tx_denied,changed,queue_failed,raw;} gra_adapter_stats;
static inline uint32_t gra_adapter_raw(const CANPacket_t *p) {
  return (uint32_t)p->data[0]|((uint32_t)p->data[1]<<8)|((uint32_t)p->data[2]<<16)|((uint32_t)p->data[3]<<24);
}
static inline void gra_adapter_reset(void) {
  ENTER_CRITICAL();
  bool unresolved=gra_adapter_state.pressed_outstanding||gra_adapter_state.release_required||gra_adapter_release_echo_waiting;
  if(gra_adapter_txn_state==GRA_TXN_ACCEPTED||gra_adapter_txn_state==GRA_TXN_WAIT_RELEASE) {
    gra_adapter_txn_state=unresolved?GRA_TXN_ABORTED_PENDING_RELEASE:GRA_TXN_ABORTED_NO_PRESS;
  }
  gra_reset(&gra_adapter_state);gra_adapter_have_native=false;
  EXIT_CRITICAL();
}
static inline uint16_t gra_adapter_next_request_id(uint16_t id) {
  return id==UINT16_MAX?1U:(uint16_t)(id+1U);
}
static inline void gra_adapter_get_status(gra_txn_status *out) {
  ENTER_CRITICAL();
  out->version=1U;out->state=gra_adapter_txn_state;out->button=gra_adapter_button;
  out->reason=gra_adapter_txn_reason;out->request_id=gra_adapter_request_id;
  out->tx_counter=gra_adapter_tx_counter;
  EXIT_CRITICAL();
}
static inline bool gra_adapter_issue(uint16_t request_id, uint8_t button) {
  ENTER_CRITICAL();
  if(request_id==0U||button<1U||button>4U) {
    EXIT_CRITICAL();return false;
  }
  if(request_id==gra_adapter_request_id) {
    bool same=button==gra_adapter_button;
    EXIT_CRITICAL();return same; /* idempotent retry: never starts a second lease */
  }
  if(gra_adapter_txn_state==GRA_TXN_ACCEPTED||gra_adapter_txn_state==GRA_TXN_WAIT_RELEASE||
     gra_adapter_txn_state==GRA_TXN_ABORTED_PENDING_RELEASE) {
    EXIT_CRITICAL();return false;
  }
  if(request_id!=gra_adapter_next_request_id(gra_adapter_request_id)) {
    gra_adapter_request_id=request_id;gra_adapter_button=button;
    gra_adapter_txn_reason=1U;gra_adapter_txn_state=GRA_TXN_REJECTED;
    EXIT_CRITICAL();return false;
  }
  gra_adapter_request_id=request_id;gra_adapter_button=button;gra_adapter_txn_reason=0U;
  uint32_t now=microsecond_timer_get();
  uint32_t buttons=(31U<<9)|(3U<<16)|(15U<<24);
  bool eligible=gra_adapter_mode()&&gra_adapter_have_native&&
      now-gra_adapter_native_us<=60000U&&!(gra_adapter_native_raw&buttons)&&
      !(gra_adapter_native_raw&((1U<<14)|(1U<<31)))&&(gra_adapter_native_raw&(1U<<8))&&
      controls_allowed&&!gas_pressed_prev&&!brake_pressed_prev&&!heartbeat_lost&&
      !relay_malfunction&&!safety_rx_checks_invalid&&((button<3U)||vehicle_moving);
  gra_direction direction=button==1U?GRA_UP:button==2U?GRA_DOWN:button==3U?GRA_SET:GRA_RES;
  if(!eligible||!gra_request(&gra_adapter_state,direction,now,200000U,7U)) {
    gra_adapter_txn_reason=2U;gra_adapter_txn_state=GRA_TXN_REJECTED;
    EXIT_CRITICAL();return false;
  }
  gra_adapter_txn_state=GRA_TXN_ACCEPTED;
  EXIT_CRITICAL();return true;
}
static inline void gra_adapter_tx_return(const CANPacket_t *echo) {
  ENTER_CRITICAL();
  bool match=gra_adapter_release_echo_waiting&&echo->returned&&echo->bus==gra_adapter_expected_release.bus&&
     echo->addr==gra_adapter_expected_release.addr&&echo->extended==gra_adapter_expected_release.extended&&
     echo->data_len_code==gra_adapter_expected_release.data_len_code;
  if(match&&echo->data_len_code<=8U) {
    for(uint8_t i=0U;i<dlc_to_len[echo->data_len_code];i++) {
      if(echo->data[i]!=gra_adapter_expected_release.data[i])match=false;
    }
  } else { match=false; }
  if(match) {
    gra_adapter_tx_counter=(uint16_t)((echo->data[2]>>4U)&0xFU);
    gra_adapter_txn_state=(gra_adapter_txn_state==GRA_TXN_ABORTED_PENDING_RELEASE)?GRA_TXN_ABORTED:GRA_TXN_COMPLETE;
    gra_adapter_release_echo_waiting=false;
  }
  EXIT_CRITICAL();
}
static inline bool gra_adapter_mode(void) {
  return GRA_REFERENCE_CAPABILITY && gra_adapter_capability && current_safety_mode==21 &&
         current_safety_param==0 && !volkswagen_longitudinal;
}
static inline bool gra_adapter_enabled_for(const CANPacket_t *p) {
  ENTER_CRITICAL();
  bool enabled=gra_adapter_mode() && p->bus==0 && p->addr==0x38A;
  EXIT_CRITICAL();return enabled;
}
static inline bool gra_adapter_payload_valid(const CANPacket_t *p) {
  if(p->data_len_code!=4||p->extended||p->returned||p->rejected||(((const uint8_t *)p)[0]&1U))return false;
  uint8_t sum=0;for(size_t i=0;i<10;i++)sum^=((const uint8_t *)p)[i];
  return !sum && p->data[0]==(uint8_t)(p->data[1]^p->data[2]^p->data[3]);
}
static inline bool gra_adapter_validate_once(const CANPacket_t *p) {
  ENTER_CRITICAL();
  bool valid_rx=safety_rx_hook(p);
  if(valid_rx && gra_adapter_payload_valid(p)) {
    gra_adapter_have_native=true;gra_adapter_native_us=microsecond_timer_get();
    gra_adapter_native_raw=gra_adapter_raw(p);
  } else {gra_adapter_reset();}
  if(!controls_allowed||brake_pressed_prev||gas_pressed_prev||relay_malfunction||
      heartbeat_lost||safety_rx_checks_invalid)gra_adapter_reset();
  EXIT_CRITICAL();return valid_rx;
}
static inline gra_send_status gra_adapter_enqueue(void *unused,const CANPacket_t *p,uint8_t bus,bool skip) {
  (void)unused;CANPacket_t candidate=*p;
  can_send_result result=can_send_enforced(&candidate,bus,skip);
  if(result==CAN_SEND_QUEUED)return GRA_ENQUEUED;
  if(result==CAN_SEND_TX_DENIED)return GRA_DENIED_NOT_QUEUED;
  return GRA_QUEUE_FAILED;
}
static inline void gra_adapter_forward(CANPacket_t *p,int destination,bool valid_rx) {
  ENTER_CRITICAL();
  /* enabled_for/validation are separate entry points: mode/capability may
   * change between them. Recheck at the final ownership boundary under lock.
   * Invalid mode never turns this previously selected stock route into a
   * transformed command or re-enters the host interceptor. */
  if(!gra_adapter_mode()||p->bus!=0||p->addr!=0x38A||destination!=2) {
    gra_adapter_reset();
    bool queued=(destination==0||destination==2)&&
        gra_adapter_enqueue(NULL,p,(uint8_t)destination,true)==GRA_ENQUEUED;
    if(queued)gra_adapter_stats.raw++;
    else gra_adapter_stats.queue_failed++;
    EXIT_CRITICAL();return;
  }
  bool fine_lease=gra_adapter_state.active&&
      (gra_adapter_state.direction==GRA_SET||gra_adapter_state.direction==GRA_RES);
  bool was_pressed=gra_adapter_state.pressed_outstanding;
  gra_guards g={controls_allowed,brake_pressed_prev,gas_pressed_prev,!relay_malfunction,
                !heartbeat_lost,valid_rx&&!safety_rx_checks_invalid&&(!fine_lease||vehicle_moving)};
  bool queued=gra_forward(&gra_adapter_state,microsecond_timer_get(),p,destination,g,gra_adapter_enqueue,NULL);
  if(!queued)gra_adapter_stats.queue_failed++;
  else if(gra_adapter_state.reason==GRA_CHANGED)gra_adapter_stats.changed++;
  else gra_adapter_stats.raw++;
  if(gra_adapter_txn_state==GRA_TXN_ACCEPTED&&gra_adapter_state.reason!=GRA_CHANGED&&
     gra_adapter_state.reason!=GRA_RELEASE&&!gra_adapter_state.active) {
    bool unresolved=was_pressed||gra_adapter_state.release_required||gra_adapter_release_echo_waiting;
    gra_adapter_txn_state=unresolved?GRA_TXN_ABORTED_PENDING_RELEASE:GRA_TXN_ABORTED_NO_PRESS;
    gra_adapter_txn_reason=(uint8_t)gra_adapter_state.reason;
  }
  if(queued&&was_pressed&&!gra_adapter_state.pressed_outstanding) {
    gra_adapter_expected_release=*p;gra_adapter_expected_release.bus=(uint8_t)destination;
    gra_adapter_expected_release.returned=0U;gra_adapter_expected_release.rejected=0U;
    gra_adapter_release_echo_waiting=true;
    if(gra_adapter_txn_state==GRA_TXN_ACCEPTED)gra_adapter_txn_state=GRA_TXN_WAIT_RELEASE;
  }
  EXIT_CRITICAL();
}
static inline void gra_adapter_tick(void) {
  ENTER_CRITICAL();
  uint32_t now=microsecond_timer_get();
  bool was_active=gra_adapter_state.active;
  if(!gra_adapter_mode()||heartbeat_lost||!controls_allowed||gas_pressed_prev||brake_pressed_prev||
     relay_malfunction||safety_rx_checks_invalid||
     (gra_adapter_have_native&&now-gra_adapter_native_us>60000U))gra_adapter_reset();
  else {
    gra_tick(&gra_adapter_state,now);
    if(was_active&&!gra_adapter_state.active&&gra_adapter_txn_state==GRA_TXN_ACCEPTED) {
      bool unresolved=gra_adapter_state.pressed_outstanding||gra_adapter_state.release_required||gra_adapter_release_echo_waiting;
      gra_adapter_txn_state=unresolved?GRA_TXN_ABORTED_PENDING_RELEASE:GRA_TXN_ABORTED_NO_PRESS;
      gra_adapter_txn_reason=(uint8_t)gra_adapter_state.reason;
    }
  }
  EXIT_CRITICAL();
}
/* Called ONLY by the old host/non-bypass wrapper, never native checked send.
 * Modified native GRA calls can_send_enforced directly, preventing recursion. */
static inline bool gra_adapter_host_intercept_locked(CANPacket_t *p,uint8_t destination) {
  if(!gra_adapter_mode())return false;
  if(p->addr!=0x38A||p->bus!=2||destination!=2)return false;
  /* Automatic PQ GRA requests must use the correlated 0xa9 control command.
   * A bulk sendcan frame has no request ID and can arrive late or be replayed;
   * consume and report it as rejected instead of creating an untracked lease. */
  if(p->data_len_code!=4||p->extended||p->returned||p->rejected)return false;
  uint32_t raw=gra_adapter_raw(p);
  uint32_t selected=raw&((31U<<9)|(3U<<16)|(15U<<24));
  bool coarse=selected==(1U<<10)||selected==(1U<<11);
  bool fine=selected==(1U<<16)||selected==(1U<<17);
  /* Preserve stock standstill resume; it still traverses the normal TX hook. */
  if(selected==(1U<<17)&&!vehicle_moving)return false;
  if(!coarse&&!fine)return false; /* keep stock cancel and unrelated controls */
  (void)can_report_tx_denied(p);
  gra_adapter_stats.refused++;
  return true;
}
static inline bool gra_adapter_host_intercept(CANPacket_t *p,uint8_t destination) {
  ENTER_CRITICAL();
  bool consumed=gra_adapter_host_intercept_locked(p,destination);
  EXIT_CRITICAL();return consumed;
}
#ifdef GRA_REFERENCE_TEST
static inline void gra_adapter_test_enable(bool enabled) {
  ENTER_CRITICAL();
  gra_adapter_reset();gra_adapter_capability=enabled;
  EXIT_CRITICAL();
}
#endif

#ifdef GRA_REFERENCE_TEST
void gra_adapter_test_tx_return(const CANPacket_t *echo) { gra_adapter_tx_return(echo); }
void gra_adapter_test_tick(void) { gra_adapter_tick(); }
#endif
