#include <cassert>
#include <cmath>
#include <limits>
#include <vector>

#include "selfdrive/ui/sunnypilot/qt/sidebar_temperature.h"

struct DeviceStateFixture {
  std::vector<float> cpu;
  std::vector<float> gpu;
  float memory;
  const std::vector<float> &getCpuTempC() const { return cpu; }
  const std::vector<float> &getGpuTempC() const { return gpu; }
  float getMemoryTempC() const { return memory; }
};

int main() {
  using sidebar_temperature::select;
  const float nan = std::numeric_limits<float>::quiet_NaN();
  using sidebar_temperature::Source;
  const auto cpu = select(4, DeviceStateFixture{{41, 44}, {40}, 42});
  assert(cpu.value == 44 && cpu.source == Source::CPU);
  const auto gpu = select(4, DeviceStateFixture{{41}, {46, 43}, 42});
  assert(gpu.value == 46 && gpu.source == Source::GPU);
  const auto memory = select(4, DeviceStateFixture{{41}, {40}, 48});
  assert(memory.value == 48 && memory.source == Source::MEMORY);
  const auto partial = select(4, DeviceStateFixture{{0, nan}, {}, 39});
  assert(partial.value == 39 && partial.source == Source::MEMORY);
  const auto absent = select(4, DeviceStateFixture{{}, {0, nan}, 0});
  assert(std::isnan(absent.value) && absent.source == Source::NONE);
  assert(select(1, DeviceStateFixture{{39, 43}, {100}, 101}).value == 43);
  assert(select(2, DeviceStateFixture{{100}, {38, 41}, 101}).value == 41);
  assert(select(3, DeviceStateFixture{{100}, {99}, 40}).value == 40);
  assert(std::isnan(select(0, DeviceStateFixture{{100}, {99}, 40}).value));
}
