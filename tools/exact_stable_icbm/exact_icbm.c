/* Exact-stable extension only. Original code/data and DF/F3 are not replaced. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct __attribute__((packed,aligned(4))) {
  uint8_t fd:1,bus:3,data_len_code:4;
  uint32_t rejected:1,returned:1,extended:1,addr:29;
  uint8_t checksum,data[8];
} CANPacket_t;
_Static_assert(sizeof(CANPacket_t)==16,"original packet ABI");
_Static_assert(offsetof(CANPacket_t,checksum)==5,"original checksum ABI");
_Static_assert(offsetof(CANPacket_t,data)==6,"original data ABI");
#define GRA_EXTERNAL_PACKET
#define CANPACKET_HEAD_SIZE 6U
#define GET_LEN(p) ((p)->data_len_code)
static void *ext_memset(void *dst,int value,size_t len) {uint8_t *p=dst;while(len--)*p++=(uint8_t)value;return dst;}
static void *ext_memcpy(void *dst,const void *src,size_t len) {uint8_t *d=dst;const uint8_t *s=src;while(len--)*d++=*s++;return dst;}
#define memset ext_memset
#define memcpy ext_memcpy
#include "gra_transform.h"
#include "gra_transform.c"

#define BYTE(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define HALF(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define WORD(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define ORIG_MODE 0x20004D20U
#define ORIG_PARAM 0x20004D26U
#define ORIG_CONTROLS 0x20004130U
#define ORIG_MOVING 0x20004135U
#define ORIG_BRAKE 0x2000413DU
#define ORIG_GAS 0x2000413FU
#define ORIG_BRAKE_PREV 0x20004D0CU
#define ORIG_GAS_PREV 0x20004CECU
#define ORIG_RELAY 0x20004D18U
#define ORIG_RX_INVALID 0x20004D24U
#define ORIG_HEARTBEAT_LOST 0x20004EB0U
#define ORIG_CRITICAL_DEPTH 0x20004278U
#define ORIG_TX_BLOCKED 0x20004EA0U
#define ORIG_TX_QUEUES 0x20002134U
#define ORIG_GRA_CHECK 0x2000278CU
#define MICROSECONDS WORD(0x40000024U)

typedef void (*original_send_fn)(CANPacket_t *,uint8_t,bool);
typedef void (*original_checksum_fn)(CANPacket_t *);
static void original_send(CANPacket_t *p,uint8_t bus,bool skip) {((original_send_fn)(uintptr_t)0x0800AC41U)(p,bus,skip);}
static void original_checksum(CANPacket_t *p) {((original_checksum_fn)(uintptr_t)0x0800A6E5U)(p);}

typedef struct {uint32_t primask;uint8_t depth;} lock_state;
static lock_state lock_enter(void) {
  lock_state saved;
  __asm__ volatile("mrs %0, primask\ncpsid i":"=r"(saved.primask)::"memory");
  saved.depth=BYTE(ORIG_CRITICAL_DEPTH);
  BYTE(ORIG_CRITICAL_DEPTH)=(uint8_t)(saved.depth+1U);
  return saved;
}
static void lock_exit(lock_state saved) {
  BYTE(ORIG_CRITICAL_DEPTH)=saved.depth;
  __asm__ volatile("dmb\nmsr primask, %0"::"r"(saved.primask):"memory");
}

enum {IDLE,ACCEPTED,WAIT_RELEASE,COMPLETE,REJECTED,ABORTED_PENDING_RELEASE,ABORTED,ABORTED_NO_PRESS};
typedef struct __attribute__((packed)) {uint8_t version,state,button,reason;uint16_t request_id,tx_counter;} status_t;
typedef struct {
  gra_state core;
  status_t status;
  CANPacket_t expected_release;
  uint32_t native_us,native_raw;
  uint8_t native_counter;
  bool initialized,have_native,stream_seen,wait_return;
} extension_state;
static extension_state extension;
_Static_assert(sizeof(extension_state)<=1024,"extension state budget");

static bool mode_valid(void) {return HALF(ORIG_MODE)==21U && HALF(ORIG_PARAM)==0U;}
static bool original_guards(void) {
  return mode_valid() && BYTE(ORIG_CONTROLS) && !BYTE(ORIG_BRAKE) && !BYTE(ORIG_BRAKE_PREV) &&
    !BYTE(ORIG_GAS) && !BYTE(ORIG_GAS_PREV) && !BYTE(ORIG_RELAY) &&
    !BYTE(ORIG_RX_INVALID) && !BYTE(ORIG_HEARTBEAT_LOST);
}
static bool original_native_status_valid(uint32_t now,uint8_t previous_counter) {
  /* Original status is observed only. No speculative original RX call and
   * no original counter/status writes occur. A mode reset clears seen/CRC. */
  uint32_t elapsed=now-WORD(ORIG_GRA_CHECK+80U);
  return BYTE(ORIG_GRA_CHECK+60U) && WORD(ORIG_GRA_CHECK+64U)==0U &&
    BYTE(ORIG_GRA_CHECK+68U) && WORD(ORIG_GRA_CHECK+72U)==0U &&
    BYTE(ORIG_GRA_CHECK+77U)==previous_counter && !BYTE(ORIG_GRA_CHECK+84U) &&
    elapsed<=60000U;
}
static void stop_transaction(uint8_t reason) {
  bool outstanding=extension.core.pressed_outstanding || extension.core.release_required || extension.wait_return;
  gra_reset(&extension.core);
  extension.have_native=false;
  if(extension.status.state==ACCEPTED || extension.status.state==WAIT_RELEASE) {
    extension.status.state=outstanding?ABORTED_PENDING_RELEASE:ABORTED_NO_PRESS;
    extension.status.reason=reason;
  }
}
static void refresh(uint32_t now) {
  if(!original_guards() || (extension.have_native && now-extension.native_us>60000U)) {
    stop_transaction(GRA_GUARD);return;
  }
  gra_tick(&extension.core,now);
  if(extension.status.state==ACCEPTED && !extension.core.active && extension.core.reason==GRA_TIMEOUT) {
    extension.status.state=(extension.core.pressed_outstanding||extension.core.release_required||extension.wait_return)?ABORTED_PENDING_RELEASE:ABORTED_NO_PRESS;
    extension.status.reason=GRA_TIMEOUT;
  }
}

void exact_icbm_init(void) {
  ext_memset(&extension,0,sizeof(extension));
  extension.status.version=1U;extension.initialized=true;
}

static uint32_t raw_payload(const CANPacket_t *p) {
  return (uint32_t)p->data[0]|((uint32_t)p->data[1]<<8)|((uint32_t)p->data[2]<<16)|((uint32_t)p->data[3]<<24);
}
static bool packet_valid(const CANPacket_t *p) {
  if(p->fd || p->bus!=0U || p->addr!=0x38AU || p->data_len_code!=4U || p->returned || p->rejected || p->extended)return false;
  uint8_t sum=0;
  for(unsigned i=0;i<10U;i++)sum^=((const uint8_t *)p)[i];
  return sum==0U && p->data[0]==(uint8_t)(p->data[1]^p->data[2]^p->data[3]);
}
static gra_send_status enqueue(void *unused,const CANPacket_t *p,uint8_t destination,bool skip) {
  (void)unused;
  CANPacket_t copy=*p;
  uintptr_t queue=WORD(ORIG_TX_QUEUES+4U*destination);
  uint32_t before=WORD(queue),blocked=WORD(ORIG_TX_BLOCKED);
  original_send(&copy,destination,skip);
  if(WORD(queue)!=before)return GRA_ENQUEUED;
  return WORD(ORIG_TX_BLOCKED)!=blocked?GRA_DENIED_NOT_QUEUED:GRA_QUEUE_FAILED;
}

void exact_icbm_forward(CANPacket_t *p,uint8_t destination,bool skip) {
  /* Only this native forwarding call is intercepted. Other traffic passes
   * directly to the unchanged original entry without extension locking. */
  if(!skip || destination!=2U || p->bus!=0U || p->addr!=0x38AU) {original_send(p,destination,skip);return;}
  lock_state saved=lock_enter();
  if(!extension.initialized) {original_send(p,destination,skip);lock_exit(saved);return;}
  uint32_t now=MICROSECONDS;
  refresh(now);
  bool valid=packet_valid(p),sequence=false;
  if(valid) {
    uint32_t raw=raw_payload(p);uint8_t counter=(uint8_t)((raw>>20)&15U);
    sequence=extension.stream_seen && counter==(uint8_t)((extension.native_counter+1U)&15U) &&
      now-extension.native_us>0U && now-extension.native_us<=60000U &&
      original_native_status_valid(now,extension.native_counter);
    extension.native_raw=raw;extension.native_counter=counter;extension.native_us=now;extension.stream_seen=true;
    extension.have_native=sequence;
  } else extension.have_native=false;
  bool was_pressed=extension.core.pressed_outstanding;
  bool fine=extension.core.direction==GRA_SET || extension.core.direction==GRA_RES;
  gra_guards guards={BYTE(ORIG_CONTROLS)!=0U,BYTE(ORIG_BRAKE)!=0U||BYTE(ORIG_BRAKE_PREV)!=0U,
    BYTE(ORIG_GAS)!=0U||BYTE(ORIG_GAS_PREV)!=0U,!BYTE(ORIG_RELAY),!BYTE(ORIG_HEARTBEAT_LOST),
    valid && sequence && mode_valid() && !BYTE(ORIG_RX_INVALID) && (!fine||BYTE(ORIG_MOVING))};
  bool queued=gra_forward(&extension.core,now,p,destination,guards,enqueue,NULL);
  if(extension.status.state==ACCEPTED && !extension.core.active && extension.core.reason!=GRA_CHANGED && extension.core.reason!=GRA_RELEASE) {
    extension.status.state=(was_pressed||extension.core.release_required||extension.wait_return)?ABORTED_PENDING_RELEASE:ABORTED_NO_PRESS;
    extension.status.reason=(uint8_t)extension.core.reason;
  }
  if(queued && was_pressed && !extension.core.pressed_outstanding) {
    extension.expected_release=*p;extension.expected_release.bus=destination;
    extension.expected_release.returned=0U;extension.expected_release.rejected=0U;
    extension.wait_return=true;
    if(extension.status.state==ACCEPTED)extension.status.state=WAIT_RELEASE;
  }
  lock_exit(saved);
}

void exact_icbm_checksum_observe(CANPacket_t *p) {
  original_checksum(p);
  if(p->addr!=0x38AU || !p->returned)return;
  lock_state saved=lock_enter();
  if(extension.initialized && extension.wait_return && p->bus==extension.expected_release.bus &&
     p->data_len_code==4U && !p->extended && !p->rejected) {
    bool match=true;
    for(unsigned i=0;i<4U;i++)if(p->data[i]!=extension.expected_release.data[i])match=false;
    if(match) {
      extension.status.tx_counter=(uint16_t)((p->data[2]>>4)&15U);
      extension.status.state=extension.status.state==ABORTED_PENDING_RELEASE?ABORTED:COMPLETE;
      extension.wait_return=false;
    }
  }
  lock_exit(saved);
}

uint32_t exact_icbm_control(const uint8_t *request,uint8_t *response) {
  lock_state saved=lock_enter();
  if(!extension.initialized) {lock_exit(saved);return 0U;}
  uint32_t now=MICROSECONDS;refresh(now);
  if(request[0]==0xAAU) {
    ext_memcpy(response,&extension.status,sizeof(extension.status));lock_exit(saved);return sizeof(extension.status);
  }
  if(request[0]==0xA9U) {
    uint16_t id=(uint16_t)request[1]|((uint16_t)request[2]<<8);
    uint16_t button=(uint16_t)request[3]|((uint16_t)request[4]<<8);
    if(id && button>=1U && button<=4U && id!=extension.status.request_id &&
       extension.status.state!=ACCEPTED && extension.status.state!=WAIT_RELEASE && extension.status.state!=ABORTED_PENDING_RELEASE) {
      uint16_t expected=extension.status.request_id==UINT16_MAX?1U:(uint16_t)(extension.status.request_id+1U);
      extension.status.request_id=id;extension.status.button=(uint8_t)button;extension.status.reason=0U;
      bool eligible=id==expected && original_guards() && extension.have_native && now-extension.native_us<=60000U &&
        original_native_status_valid(now,extension.native_counter) &&
        !(extension.native_raw&((31U<<9)|(3U<<16)|(15U<<24)|(1U<<14)|(1U<<31))) &&
        (extension.native_raw&(1U<<8)) && (button<3U||BYTE(ORIG_MOVING));
      if(eligible && gra_request(&extension.core,(gra_direction)button,now,200000U,7U))extension.status.state=ACCEPTED;
      else {extension.status.state=REJECTED;extension.status.reason=id!=expected?1U:2U;}
    }
  }
  lock_exit(saved);return 0U;
}
