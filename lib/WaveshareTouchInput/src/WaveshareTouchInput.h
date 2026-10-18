#pragma once

#include <cstdint>

#include "ScreenTouchGate.h"

class WaveshareTouchInput {
 public:
  bool begin();
  void poll();
  bool takePressedEdge(int16_t& x, int16_t& y);
  bool available() const { return available_; }

 private:
  bool writeRegister(uint8_t reg, uint8_t value);
  bool readSample(uint8_t& fingers, int16_t& x, int16_t& y);

  ScreenTouchGate gate_;
  bool available_ = false;
  bool edgePending_ = false;
  uint32_t lastPollAtMs_ = 0;
  int16_t edgeX_ = -1;
  int16_t edgeY_ = -1;
};
