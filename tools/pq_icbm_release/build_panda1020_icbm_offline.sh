#!/bin/sh
# Offline STM32F413 Panda 10.2.0 source build. No signer or device access.
set -eu
PANDA_1020_SOURCE=${PANDA_1020_SOURCE:?set to the Panda 10.2.0 source root}
OPENDBC_1000_ROOT=${OPENDBC_1000_ROOT:?set to the opendbc_1000 repository root}
OUT=${OUT:-"$PWD/build-panda1020-icbm"}
CC=${CC:-arm-none-eabi-gcc}
OBJCOPY=${OBJCOPY:-arm-none-eabi-objcopy}
SIZE=${SIZE:-arm-none-eabi-size}
mkdir -p "$OUT"
cd "$PANDA_1020_SOURCE"
FLAGS="-mcpu=cortex-m4 -mthumb -mhard-float -mfpu=fpv4-sp-d16 -DSTM32F4 -DSTM32F413xx -DALLOW_DEBUG -I. -Iboard -Iboard/drivers -Iboard/stm32f4/inc -I$OPENDBC_1000_ROOT -std=gnu11 -Os -Wall -Wextra -Wstrict-prototypes -Werror -mlittle-endian -nostdlib -fno-builtin -fsingle-precision-constant -fstack-usage"
for variant in stock icbm; do
  capability=0
  enabled=0
  if [ "$variant" = icbm ]; then capability=1; enabled=1; fi
  # FLAGS is intentionally expanded as compiler flags; roots are supplied by the caller.
  $CC $FLAGS -DGRA_REFERENCE_CAPABILITY=$capability -DPQ35_ICBM_ENABLE=$enabled -c board/main.c -o "$OUT/$variant-main.o"
  $CC $FLAGS -DGRA_REFERENCE_CAPABILITY=$capability -c board/stm32f4/startup_stm32f413xx.s -o "$OUT/$variant-startup.o"
  $CC -mcpu=cortex-m4 -mthumb -mhard-float -mfpu=fpv4-sp-d16 -nostdlib \
    -Tboard/stm32f4/stm32f4_flash.ld -Wl,--section-start,.isr_vector=0x8004000 \
    "$OUT/$variant-startup.o" "$OUT/$variant-main.o" -o "$OUT/panda-$variant.elf"
  $OBJCOPY -O binary "$OUT/panda-$variant.elf" "$OUT/panda-$variant.unsigned.bin"
done
$SIZE "$OUT/panda-stock.elf" "$OUT/panda-icbm.elf"
sha256sum "$OUT/panda-stock.elf" "$OUT/panda-stock.unsigned.bin" \
          "$OUT/panda-icbm.elf" "$OUT/panda-icbm.unsigned.bin"
