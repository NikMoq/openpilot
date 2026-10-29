.syntax unified
.thumb
.section .text.veneers,"ax",%progbits
.global exact_icbm_usb_veneer
.thumb_func
exact_icbm_usb_veneer:
  mov r0,r5
  mov r1,r4
  bl exact_icbm_control
  pop {r3,r4,r5,r6,r7,pc}

.global exact_icbm_startup_veneer
.thumb_func
exact_icbm_startup_veneer:
  push {r0,r1,r2,r3,r12,lr}
  mrs r0,APSR
  push {r0,r1}
  bl exact_icbm_init
  pop {r0,r1}
  msr APSR_nzcvq,r0
  pop {r0,r1,r2,r3,r12,lr}
  b.w original_main
