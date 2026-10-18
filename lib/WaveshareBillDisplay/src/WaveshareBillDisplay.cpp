#include "WaveshareBillDisplay.h"

#if defined(RGB_BILL_WAVESHARE_S3)

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <SensorPCF85063.hpp>
#include <Wire.h>
#include <cstring>

#include "waveshare_s3_pins.h"

namespace {
using namespace WaveshareS3Pins;

constexpr uint8_t DISPLAY_BRIGHTNESS = 56;
constexpr uint16_t kBlack = 0x0000;
constexpr uint16_t kPaper = 0xE71C;
constexpr uint16_t kGreen = 0x4E69;
constexpr uint16_t kGold = 0xD5A3;
constexpr uint16_t kMuted = 0x7BEF;
constexpr uint16_t kPanel = 0x10A2;

bool validDateTime(const RTC_DateTime& value) {
  return value.getYear() >= 2024 && value.getYear() <= 2099 &&
         value.getMonth() >= 1 && value.getMonth() <= 12 &&
         value.getDay() >= 1 && value.getDay() <= 31 &&
         value.getHour() <= 23 && value.getMinute() <= 59 &&
         value.getSecond() <= 59;
}
}  // namespace

WaveshareBillDisplay::WaveshareBillDisplay() {
  bus_ = new Arduino_ESP32QSPI(LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1,
                               LCD_SDIO2, LCD_SDIO3);
  display_ = new Arduino_CO5300(bus_, LCD_RESET, 0, false, LCD_WIDTH,
                                LCD_HEIGHT, 22, 0, 0, 0);
  rtc_ = new SensorPCF85063();
}

bool WaveshareBillDisplay::begin() {
  available_ = display_ != nullptr && display_->begin();
  if (!available_ || display_->width() != LCD_WIDTH ||
      display_->height() != LCD_HEIGHT) {
    Serial.println("watch display: CO5300 initialization failed");
    available_ = false;
    return false;
  }

  display_->setBrightness(DISPLAY_BRIGHTNESS);
  display_->fillScreen(kBlack);

  Wire.begin(I2C_SDA, I2C_SCL);
  rtcReady_ = rtc_ != nullptr && rtc_->begin(Wire, I2C_SDA, I2C_SCL);
  if (rtcReady_) {
    RTC_DateTime value = rtc_->getDateTime();
    if (!rtc_->isClockIntegrityGuaranteed() || !rtc_->isRunning() ||
        !validDateTime(value)) {
      rtc_->setDateTime(RTC_DateTime(__DATE__, __TIME__));
      rtc_->start();
      value = rtc_->getDateTime();
    }
    rtcReady_ = validDateTime(value);
  }

  Serial.printf("watch display: CO5300 ready %dx%d RTC=%s\n",
                display_->width(), display_->height(),
                rtcReady_ ? "ready" : "unavailable");
  dirty_ = true;
  render();
  return true;
}

void WaveshareBillDisplay::showAmount(uint32_t cents, const char* status) {
  cents_ = cents;
  std::strncpy(status_, status == nullptr ? "" : status,
               sizeof(status_) - 1);
  status_[sizeof(status_) - 1] = '\0';
  dirty_ = true;
}

void WaveshareBillDisplay::poll() {
  if (!available_) {
    return;
  }
  uint32_t second = millis() / 1000U;
  if (rtcReady_) {
    second = rtc_->getDateTime().getSecond();
  }
  if (dirty_ || second != lastSecond_) {
    render();
  }
}

void WaveshareBillDisplay::centeredText(const char* text, int16_t y,
                                        uint8_t size, uint16_t color) {
  int16_t x1 = 0;
  int16_t y1 = 0;
  uint16_t width = 0;
  uint16_t height = 0;
  display_->setTextSize(size);
  display_->setTextColor(color, kBlack);
  display_->getTextBounds(text, 0, y, &x1, &y1, &width, &height);
  const int16_t x = static_cast<int16_t>((LCD_WIDTH - width) / 2);
  display_->setCursor(x < 0 ? 0 : x, y);
  display_->print(text);
}

void WaveshareBillDisplay::render() {
  if (!available_) {
    return;
  }

  RTC_DateTime value;
  char clockText[16] = "--:--";
  if (rtcReady_) {
    value = rtc_->getDateTime();
    if (validDateTime(value)) {
      snprintf(clockText, sizeof(clockText), "%02u:%02u", value.getHour(),
               value.getMinute());
      lastSecond_ = value.getSecond();
    }
  } else {
    lastSecond_ = millis() / 1000U;
  }

  display_->fillScreen(kBlack);
  display_->setTextColor(kGold, kBlack);
  display_->setTextSize(2);
  display_->setCursor(24, 24);
  display_->print("fork2checkbook");
  display_->setTextColor(kMuted, kBlack);
  display_->setCursor(300, 24);
  display_->print(clockText);
  display_->drawFastHLine(24, 62, LCD_WIDTH - 48, kPanel);

  display_->drawRoundRect(22, 98, LCD_WIDTH - 44, 272, 20, kGreen);
  centeredText("BEARER VALUE", 126, 2, kMuted);
  if (cents_ > 0) {
    char amount[24];
    snprintf(amount, sizeof(amount), "$%lu.%02lu",
             static_cast<unsigned long>(cents_ / 100U),
             static_cast<unsigned long>(cents_ % 100U));
    centeredText(amount, 190, 5, kPaper);
    centeredText("WHOLE NOTE", 278, 2, kGreen);
  } else {
    centeredText("EMPTY", 190, 5, kPaper);
    centeredText("NO NOTE LOADED", 278, 2, kMuted);
  }

  centeredText(status_, 402, 2,
               std::strstr(status_, "RECOVER") != nullptr ||
                       std::strstr(status_, "CLEAR") != nullptr
                   ? kGold
                   : kGreen);
  const bool recovery = std::strstr(status_, "RECOVER") != nullptr ||
                        std::strstr(status_, "CLEAR") != nullptr ||
                        std::strstr(status_, "ERROR") != nullptr;
  const bool armed = std::strstr(status_, "SCAN") != nullptr ||
                     std::strstr(status_, "RECEIVE") != nullptr;
  if (recovery) {
    centeredText("USE DOCK TO RECOVER", 456, 2, kGold);
  } else if (armed) {
    centeredText("TAP AGAIN TO CANCEL", 456, 2, kGold);
  } else if (cents_ > 0) {
    centeredText("ARM EMPTY BILL TO PASS", 456, 2, kMuted);
  } else {
    centeredText("TAP SCREEN TO RECEIVE", 456, 2, kGold);
  }
  dirty_ = false;
}

#endif
