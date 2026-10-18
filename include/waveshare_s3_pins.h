#pragma once

#include <stdint.h>

// Waveshare ESP32-S3-Touch-AMOLED-2.06 V1.0. The upstream reference is kept
// read-only at /media/kestl/andor/github/ESP32-S3-Touch-AMOLED-2.06.
namespace WaveshareS3Pins {

constexpr int8_t LCD_SDIO0 = 4;
constexpr int8_t LCD_SDIO1 = 5;
constexpr int8_t LCD_SDIO2 = 6;
constexpr int8_t LCD_SDIO3 = 7;
constexpr int8_t LCD_RESET = 8;
constexpr int8_t LCD_SCLK = 11;
constexpr int8_t LCD_CS = 12;
constexpr int16_t LCD_WIDTH = 410;
constexpr int16_t LCD_HEIGHT = 502;

constexpr int8_t I2C_SDA = 15;
constexpr int8_t I2C_SCL = 14;
constexpr int8_t TOUCH_INTERRUPT = 38;
constexpr int8_t TOUCH_RESET = 9;
constexpr uint8_t TOUCH_I2C_ADDRESS = 0x38;

// Schematic: PWRON -> inverter -> SYS_OUT -> GPIO10. The board supplies the
// pull-up and SYS_OUT is HIGH while PWR is pressed. Do not enable an internal
// pull resistor and do not hold PWR long enough to request hardware power-off.
constexpr int8_t PWR_SYS_OUT = 10;

}  // namespace WaveshareS3Pins
