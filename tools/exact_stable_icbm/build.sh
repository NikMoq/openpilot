#!/bin/sh
# Exact original stable + integer-only ICBM extension. No signer/device access.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=${OUT:-"$ROOT/build"}
CC=${CC:-arm-none-eabi-gcc}
OBJCOPY=${OBJCOPY:-arm-none-eabi-objcopy}
SIZE=${SIZE:-arm-none-eabi-size}
NM=${NM:-arm-none-eabi-nm}
OBJDUMP=${OBJDUMP:-arm-none-eabi-objdump}
test ! -e "$OUT" || { echo 'Build output must be a NEW directory' >&2; exit 1; }
mkdir -p "$OUT"
FLAGS='-mcpu=cortex-m4 -mthumb -mfloat-abi=soft -std=gnu11 -Os -Wall -Wextra -Werror -ffreestanding -fno-builtin -fno-unwind-tables -fno-asynchronous-unwind-tables -fstack-usage -ffunction-sections -fdata-sections'
$CC $FLAGS -I"$ROOT" -c "$ROOT/exact_icbm.c" -o "$OUT/exact_icbm.o"
$CC -mcpu=cortex-m4 -mthumb -mfloat-abi=soft -c "$ROOT/veneers.S" -o "$OUT/veneers.o"
$CC -mcpu=cortex-m4 -mthumb -mfloat-abi=soft -nostdlib -Wl,-Map="$OUT/module.map" -T"$ROOT/module.ld" "$OUT/exact_icbm.o" "$OUT/veneers.o" -o "$OUT/module.elf"
$OBJCOPY -O binary -j .text "$OUT/module.elf" "$OUT/module.bin"
$SIZE "$OUT/module.elf"
$NM -n "$OUT/module.elf" > "$OUT/symbols.txt"
$OBJDUMP -d "$OUT/module.elf" > "$OUT/module-disassembly.txt"
sha256sum "$OUT/module.elf" "$OUT/module.bin"
