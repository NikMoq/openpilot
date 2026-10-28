#!/bin/sh
# QCOM2 ARM64 offline build. Execute in staged source directory.
set -eu
cross_flags=""
if test -n "${CROSS_SYSROOT:-}"; then
  cross_flags="--target=aarch64-linux-gnu --sysroot=$CROSS_SYSROOT -isystem $CROSS_SYSROOT/usr/include/c++/9 -isystem $CROSS_SYSROOT/usr/include/aarch64-linux-gnu/c++/9"
else
  test "$(uname -m)" = aarch64
fi
object_dir="${ARM_OBJ_DIR:-$(mktemp -d "$PWD/arm_obj.XXXXXX")}"
mkdir -p "$object_dir"
for source in \
  selfdrive/pandad/main.cc selfdrive/pandad/pandad.cc \
  selfdrive/pandad/panda_safety.cc selfdrive/pandad/panda.cc \
  selfdrive/pandad/panda_comms.cc selfdrive/pandad/spi.cc \
  common/params.cc common/swaglog.cc common/util.cc common/watchdog.cc common/ratekeeper.cc \
  cereal/messaging/socketmaster.cc \
  msgq_repo/msgq/ipc.cc msgq_repo/msgq/msgq.cc msgq_repo/msgq/impl_msgq.cc \
  msgq_repo/msgq/impl_zmq.cc msgq_repo/msgq/impl_fake.cc msgq_repo/msgq/event.cc \
  cereal/gen/cpp/*.c++ third_party/json11/json11.cpp
do
  object="$object_dir/$(echo "$source" | tr / _).o"
  # Always compile: headers, schemas and flags are build inputs too.
  if true; then
    echo "Compile $source"
    clang++ $cross_flags -std=c++17 -O1 -pthread -DQCOM2 -DSUNNYPILOT -mcpu=cortex-a57 \
      -I. -Imsgq_repo -Ithird_party -c "$source" -o "$object"
  fi
done
if test -n "$cross_flags"; then
  echo "ARM64 objects ready in $object_dir; link with ARM64 sysroot libraries."
  exit 0
fi
clang++ -pthread -o pandad_icbm_aarch64_qcom2 "$object_dir"/*.o \
  -lusb-1.0 -lzmq -Wl,-Bstatic -lcapnp -lkj -Wl,-Bdynamic -ldl
