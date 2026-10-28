#pragma once
#include <stddef.h>
#ifndef GRA_EXTERNAL_PACKET
#include "can_declarations.h"
#endif
/* TEST ONLY. No USB opcode, firmware patch, native send implementation or IO. */
typedef enum {
  GRA_NONE=0,
  GRA_UP=1,
  GRA_DOWN=2,
  GRA_SET=3,
  GRA_RES=4
} gra_direction;
typedef enum { GRA_STOCK, GRA_CHANGED, GRA_DRIVER, GRA_INVALID, GRA_GUARD,
               GRA_TIMEOUT, GRA_RELEASE, GRA_TX_DENIED, GRA_QUEUE_ERROR } gra_reason;
typedef struct {
  bool controls_allowed, brake, gas, relay_ok, heartbeat_alive, current_rx_validated;
} gra_guards;
typedef struct {
  bool active, seen, release_required, pressed_outstanding;
  uint8_t last_counter, frames_left;
  uint32_t last_rx_us, started_us, timeout_us;
  gra_direction direction;
  gra_reason reason;
} gra_state;
typedef enum { GRA_ENQUEUED, GRA_DENIED_NOT_QUEUED, GRA_QUEUE_FAILED } gra_send_status;
/* Enforced queue boundary. For changed
 * packets (skip=false), the adapter MUST run actual safety_tx_hook immediately
 * before enqueue; do not pre-authorize and later send through skip=true. */
typedef gra_send_status (*gra_enqueue)(void *context, const CANPacket_t *packet,
                                      uint8_t destination, bool skip_tx_hook);
bool gra_request(gra_state *state, gra_direction direction, uint32_t now_us,
                 uint32_t timeout_us, uint8_t frame_budget);
bool gra_forward(gra_state *state, uint32_t now_us, const CANPacket_t *native,
                 int destination, gra_guards guards,
                 gra_enqueue enqueue, void *context);
/* Mandatory adapter contract: call from a bounded periodic safety/heartbeat
 * loop (<=125ms, historical main.c tick_handler 8Hz), even with no CAN RX.
 * Reset on mode/heartbeat transitions and before a periodic-timer suspension
 * or restart; do not retain a lease across an unobserved timer revolution.
 * 32-bit subtraction cannot detect an entire unobserved clock revolution.
 * The deployed loop binding is NOT implemented by this host-only prototype. */
void gra_tick(gra_state *state, uint32_t now_us);
void gra_reset(gra_state *state);
/* Reject oversized DLC without touching the packet or reading past data[]. */
bool gra_packet_checksum(CANPacket_t *packet);
