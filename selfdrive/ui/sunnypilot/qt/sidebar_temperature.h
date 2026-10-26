#pragma once

#include <cmath>
#include <limits>

namespace sidebar_temperature {

enum class Source { NONE, CPU, GPU, MEMORY };
struct Reading {
  float value;
  Source source;
};

// hardwared also emits zero for a missing thermal zone.
inline bool usable(float value) {
  return std::isfinite(value) && value != 0.0f && value >= -40.0f && value <= 150.0f;
}

inline float hotter(float first, float second) {
  if (!usable(first)) return usable(second) ? second : std::numeric_limits<float>::quiet_NaN();
  return usable(second) && second > first ? second : first;
}

template <typename List>
float highest(const List &readings) {
  float maximum = std::numeric_limits<float>::quiet_NaN();
  for (float value : readings) maximum = hotter(maximum, value);
  return maximum;
}

template <typename DeviceState>
Reading select(int mode, const DeviceState &state) {
  if (mode == 1) return {highest(state.getCpuTempC()), Source::CPU};
  if (mode == 2) return {highest(state.getGpuTempC()), Source::GPU};
  if (mode == 3) return {state.getMemoryTempC(), Source::MEMORY};
  if (mode == 4) {
    Reading result{std::numeric_limits<float>::quiet_NaN(), Source::NONE};
    const float cpu = highest(state.getCpuTempC());
    const float gpu = highest(state.getGpuTempC());
    const float memory = state.getMemoryTempC();
    if (usable(cpu)) result = {cpu, Source::CPU};
    if (usable(gpu) && (!usable(result.value) || gpu > result.value)) result = {gpu, Source::GPU};
    if (usable(memory) && (!usable(result.value) || memory > result.value)) result = {memory, Source::MEMORY};
    return result;
  }
  return {std::numeric_limits<float>::quiet_NaN(), Source::NONE};
}

}  // namespace sidebar_temperature
