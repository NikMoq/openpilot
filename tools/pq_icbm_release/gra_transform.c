#include "gra_transform.h"
#ifndef GRA_EXTERNAL_PACKET
#include <string.h>
#endif

#define GRA_ADDR 0x38AU
#define EDIT_MASK ((1U<<10)|(1U<<11)|(1U<<16)|(1U<<17))
#define DRIVER_MASK ((1U<<9)|(1U<<10)|(1U<<11)|(1U<<12)|(1U<<13)|(1U<<16)|(1U<<17)|(15U<<24))
/* Fixture limits; no ECU timing specification is claimed. */
#define MAX_TIMEOUT_US 200000U
#define MAX_RX_GAP_US 60000U

static uint32_t payload(const CANPacket_t *p) {
  return (uint32_t)p->data[0] | ((uint32_t)p->data[1]<<8) |
         ((uint32_t)p->data[2]<<16) | ((uint32_t)p->data[3]<<24);
}
static uint32_t button_mask(gra_direction direction) {
  switch (direction) {
    case GRA_UP: return 1U<<11;
    case GRA_DOWN: return 1U<<10;
    case GRA_SET: return 1U<<16;
    case GRA_RES: return 1U<<17;
    default: return 0U;
  }
}
bool gra_packet_checksum(CANPacket_t *p) {
  if (GET_LEN(p)>sizeof(p->data)) return false;
  p->checksum=0;
  uint8_t checksum=0;
  for (size_t i=0; i<CANPACKET_HEAD_SIZE+GET_LEN(p); ++i) checksum^=((uint8_t *)p)[i];
  p->checksum=checksum;
  return true;
}
static bool valid(const CANPacket_t *p) {
  if (p->data_len_code!=4 || p->extended || p->rejected || p->returned || (((const uint8_t *)p)[0]&1U)) return false;
  uint8_t checksum=0;
  for (size_t i=0; i<10; ++i) checksum^=((const uint8_t *)p)[i];
  return checksum==0 && p->data[0]==(uint8_t)(p->data[1]^p->data[2]^p->data[3]);
}
static void stop(gra_state *s, gra_reason reason) {
  s->active=false; s->reason=reason;
  if (s->pressed_outstanding) s->release_required=true;
}
void gra_tick(gra_state *s, uint32_t now) {
  if (s->active && now-s->started_us>=s->timeout_us) stop(s,GRA_TIMEOUT);
}
void gra_reset(gra_state *s) {
  bool need_release=s->pressed_outstanding || s->release_required;
  memset(s,0,sizeof(*s));
  s->pressed_outstanding=need_release; s->release_required=need_release;
}
bool gra_request(gra_state *s, gra_direction direction, uint32_t now,
                 uint32_t timeout, uint8_t frames) {
  if (button_mask(direction)==0U || !timeout || timeout>MAX_TIMEOUT_US ||
      !frames || frames>32 || s->active || s->release_required) return false;
  s->active=true; s->direction=direction; s->started_us=now;
  s->timeout_us=timeout; s->frames_left=frames;
  return true;
}
bool gra_forward(gra_state *s, uint32_t now, const CANPacket_t *native,
                 int destination, gra_guards g, gra_enqueue send, void *context) {
  s->reason=GRA_STOCK;
  gra_tick(s,now); /* Also expire on RX; separate periodic calls remain required. */
  bool eligible_release=false;
  /* Only invoke at a real approved forwarding route; never cast -1 to255. */
  if (destination!=0 && destination!=2) { stop(s,GRA_INVALID); return false; }
  if (native->bus!=0 || native->addr!=GRA_ADDR || destination!=2) goto stock;
  if (!valid(native)) { stop(s,GRA_INVALID); goto stock; }
  uint32_t raw=payload(native);
  uint8_t counter=(uint8_t)((raw>>20)&15U);
  bool sequence=!s->seen || (counter==(uint8_t)((s->last_counter+1U)&15U) &&
                 now-s->last_rx_us>0 && now-s->last_rx_us<=MAX_RX_GAP_US);
  s->seen=true; s->last_counter=counter; s->last_rx_us=now;
  if (!sequence) { stop(s,GRA_INVALID); goto stock; }
  eligible_release=!(raw&(DRIVER_MASK|(1U<<14)|(1U<<31)));
  if (!(raw&(1U<<8)) || (raw&(DRIVER_MASK|(1U<<14)|(1U<<31)))) { stop(s,GRA_DRIVER); goto stock; }
  if (!g.controls_allowed || g.brake || g.gas || !g.relay_ok || !g.heartbeat_alive || !g.current_rx_validated) {
    stop(s,GRA_GUARD); goto stock;
  }
  if (s->release_required) { s->reason=GRA_RELEASE; goto stock; }
  if (!s->active) goto stock;
  if (now-s->started_us>=s->timeout_us) { stop(s,GRA_TIMEOUT); goto stock; }
  CANPacket_t changed;
  memcpy(&changed,native,sizeof(changed));
  changed.bus=destination; /* TX whitelist must see destination, not native bus0. */
  uint32_t edited=raw | button_mask(s->direction);
  for (size_t i=1;i<4;++i) changed.data[i]=(uint8_t)(edited>>(8U*i));
  changed.data[0]=changed.data[1]^changed.data[2]^changed.data[3];
  gra_packet_checksum(&changed);
  gra_send_status result=send(context,&changed,destination,false);
  if (result==GRA_ENQUEUED) {
    s->reason=GRA_CHANGED;
    s->pressed_outstanding=true;
    if (--s->frames_left==0) { s->active=false; s->release_required=true; }
    return true;
  }
  if (result!=GRA_DENIED_NOT_QUEUED) { stop(s,GRA_QUEUE_ERROR); return false; }
  /* Only explicit TX denial proves no frame was queued and permits stock
   * fallback. Queue failure must not cause a second enqueue attempt. */
  stop(s,GRA_TX_DENIED);
stock:
  ;
  bool queued=send(context,native,destination,true)==GRA_ENQUEUED;
  if (!queued) s->reason=GRA_QUEUE_ERROR;
  if (queued && eligible_release) {
    s->release_required=false;
    s->pressed_outstanding=false;
  }
  return queued;
}
