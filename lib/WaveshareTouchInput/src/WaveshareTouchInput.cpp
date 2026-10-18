#include "WaveshareTouchInput.h"

#if defined(RGB_BILL_WAVESHARE_S3)

#include <Arduino.h>
#include <Wire.h>

#include "waveshare_s3_pins.h"

namespace {
using namespace WaveshareS3Pins;

constexpr uint8_t kPowerModeRegister = 0xA5;
constexpr uint8_t kMonitorMode = 0x01;
constexpr uint8_t kFingerRegister = 0x02;
constexpr uint32_t kPollIntervalMs = 20;

volatile bool touchInterruptPending = false;

void IRAM_ATTR onTouchInterrupt() { touchInterruptPending = true; }
}  // namespace

bool WaveshareTouchInput::writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool WaveshareTouchInput::readSample(uint8_t& fingers, int16_t& x,
                                     int16_t& y) {
  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  Wire.write(kFingerRegister);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  constexpr uint8_t kFrameBytes = 5;
  const uint8_t received = Wire.requestFrom(
      static_cast<uint8_t>(TOUCH_I2C_ADDRESS), kFrameBytes);
  if (received != kFrameBytes) {
    while (Wire.available()) {
      Wire.read();
    }
    return false;
  }

  fingers = Wire.read() & 0x0F;
  const uint8_t xHigh = Wire.read();
  const uint8_t xLow = Wire.read();
  const uint8_t yHigh = Wire.read();
  const uint8_t yLow = Wire.read();
  if (fingers == 0) {
    x = -1;
    y = -1;
    return true;
  }

  x = static_cast<int16_t>(((xHigh & 0x0F) << 8) | xLow);
  y = static_cast<int16_t>(((yHigh & 0x0F) << 8) | yLow);
  return true;
}

bool WaveshareTouchInput::begin() {
  Wire.begin(I2C_SDA, I2C_SCL);
  pinMode(TOUCH_INTERRUPT, INPUT_PULLUP);
  pinMode(TOUCH_RESET, OUTPUT);
  digitalWrite(TOUCH_RESET, HIGH);
  delay(1);
  digitalWrite(TOUCH_RESET, LOW);
  delay(20);
  digitalWrite(TOUCH_RESET, HIGH);
  delay(50);

  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  available_ = Wire.endTransmission() == 0 &&
               writeRegister(kPowerModeRegister, kMonitorMode);
  if (available_) {
    uint8_t fingers = 0;
    int16_t x = -1;
    int16_t y = -1;
    if (!readSample(fingers, x, y)) {
      available_ = false;
    } else {
      // An untouched boot observes the mandatory initial release here. If the
      // user is holding the screen, no edge can be emitted until a later
      // release sample arrives.
      gate_.update(fingers);
      touchInterruptPending = false;
      attachInterrupt(digitalPinToInterrupt(TOUCH_INTERRUPT),
                      onTouchInterrupt, FALLING);
    }
  }
  Serial.printf("watch touch: FT3168 %s address=0x%02X\n",
                available_ ? "ready" : "failed", TOUCH_I2C_ADDRESS);
  return available_;
}

void WaveshareTouchInput::poll() {
  if (!available_) {
    return;
  }

  // GPIO38 reliably signals contact but does not reliably provide a second
  // edge for release. Stay interrupt-driven while idle, then sample only for
  // the short lifetime of an active contact until the release is observed.
  if (!touchInterruptPending && !gate_.contactActive()) {
    return;
  }
  const uint32_t now = millis();
  if (now - lastPollAtMs_ < kPollIntervalMs) {
    return;
  }
  touchInterruptPending = false;
  lastPollAtMs_ = now;

  uint8_t fingers = 0;
  int16_t x = -1;
  int16_t y = -1;
  if (!readSample(fingers, x, y)) {
    return;
  }
  if (gate_.update(fingers)) {
    edgeX_ = x;
    edgeY_ = y;
    edgePending_ = true;
  }
}

bool WaveshareTouchInput::takePressedEdge(int16_t& x, int16_t& y) {
  if (!edgePending_) {
    return false;
  }
  x = edgeX_;
  y = edgeY_;
  edgePending_ = false;
  return true;
}

#endif
