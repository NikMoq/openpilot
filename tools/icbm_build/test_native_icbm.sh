#!/bin/sh
set -eu
clang++ -std=c++17 -O1 -ffunction-sections -fdata-sections -I. -Imsgq_repo \
  selfdrive/pandad/tests/test_icbm_protocol.cc selfdrive/pandad/panda.cc \
  -Wl,--gc-sections -lcapnp -lkj -o test_icbm_protocol
./test_icbm_protocol
