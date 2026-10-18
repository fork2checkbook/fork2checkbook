#pragma once

#include <cstddef>
#include <cstdint>

class Arduino_DataBus;
class Arduino_CO5300;
class SensorPCF85063;

class WaveshareBillDisplay {
 public:
  WaveshareBillDisplay();

  bool begin();
  void showAmount(uint32_t cents, const char* status);
  void poll();
  bool available() const { return available_; }

 private:
  void render();
  void centeredText(const char* text, int16_t y, uint8_t size,
                    uint16_t color);

  Arduino_DataBus* bus_ = nullptr;
  Arduino_CO5300* display_ = nullptr;
  SensorPCF85063* rtc_ = nullptr;
  bool available_ = false;
  bool rtcReady_ = false;
  bool dirty_ = true;
  uint32_t cents_ = 0;
  uint32_t lastSecond_ = UINT32_MAX;
  char status_[24] = "STARTING";
};
