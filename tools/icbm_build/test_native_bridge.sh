#!/bin/sh
# Run from an isolated source staging root; real native messaging, mock transport only.
set -eu
cd cereal
mkdir -p gen/cpp
capnp compile -I. -oc++:gen/cpp custom.capnp car.capnp legacy.capnp log.capnp
cd ..
clang++ -std=c++17 -O1 -pthread -ffunction-sections -fdata-sections \
  -I. -Imsgq_repo -Ithird_party \
  selfdrive/pandad/tests/test_icbm_bridge.cc selfdrive/pandad/pandad.cc \
  selfdrive/pandad/panda.cc cereal/messaging/socketmaster.cc \
  msgq_repo/msgq/ipc.cc msgq_repo/msgq/msgq.cc msgq_repo/msgq/impl_msgq.cc \
  msgq_repo/msgq/impl_zmq.cc msgq_repo/msgq/impl_fake.cc msgq_repo/msgq/event.cc \
  cereal/gen/cpp/*.c++ common/util.cc common/swaglog.cc \
  third_party/json11/json11.cpp -Wl,--gc-sections -lzmq -lcapnp -lkj \
  -o test_icbm_bridge
./test_icbm_bridge
