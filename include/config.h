#pragma once

#include <Arduino.h>

// Board-specific hardware. The production default remains the WEMOS LOLIN32;
// the TTGO profile is selected only by the ttgo-lora32-test environment.
#if defined(RGB_BILL_WAVESHARE_S3)
constexpr char BLE_DEVICE_NAME[] = "RgbBill-Watch";
constexpr char BLE_IDENTITY[] = "bill-waveshare-s3-v1";
#elif defined(RGB_BILL_TTGO_LORA32_V13)
#if defined(RGB_BILL_TTGO_EXTERNAL_OLED)
// Active TTGO bill profile: replacement four-wire SSD1306-compatible OLED on
// GPIO21/GPIO22 plus the accepted active-high TTP223 input on GPIO12.
constexpr uint8_t LED_STATUS_PIN = 2;
constexpr uint8_t TAP_BUTTON_PIN = 12;
constexpr uint8_t TAP_BUTTON_MODE = INPUT;
constexpr uint8_t TAP_BUTTON_ACTIVE_LEVEL = HIGH;
constexpr char TAP_INPUT_NAME[] = "TTP223";
#elif defined(RGB_BILL_TTGO_TFT_ILI9341)
// External ILI9341 test profile. The TFT consumes the old onboard-OLED pins
// GPIO4/GPIO15, and GPIO2 is D/C, so there is no separate status LED. GPIO12
// reads the TTP223 OUT signal. GPIO12 is also the MTDI flash-voltage strap:
// never touch the sensor or otherwise drive it HIGH during reset.
constexpr uint8_t TAP_BUTTON_PIN = 12;
constexpr uint8_t TAP_BUTTON_MODE = INPUT;
constexpr uint8_t TAP_BUTTON_ACTIVE_LEVEL = HIGH;
constexpr char TAP_INPUT_NAME[] = "TTP223";
constexpr uint8_t TFT_BACKLIGHT_PIN = 32;
constexpr uint8_t TFT_SCK_PIN = 18;
constexpr uint8_t TFT_MOSI_PIN = 23;
constexpr uint8_t TFT_DC_PIN = 2;
constexpr uint8_t TFT_RESET_PIN = 4;
constexpr uint8_t TFT_CS_PIN = 15;
#else
constexpr uint8_t LED_STATUS_PIN = 2;
constexpr uint8_t TAP_BUTTON_PIN = 0;
constexpr uint8_t TAP_BUTTON_MODE = INPUT_PULLUP;
constexpr uint8_t TAP_BUTTON_ACTIVE_LEVEL = LOW;
constexpr char TAP_INPUT_NAME[] = "button";
#endif

#if defined(RGB_BILL_TTGO_EXTERNAL_OLED) || \
    defined(RGB_BILL_TTGO_OLED_21_22)
// Current LilyGo T3 V1.3 SX1276 documentation and utilities.h profile.
// LilyGo calls reset UNUSED_PIN (numeric 0); use -1 here so BillDisplay does
// not pulse the ESP32 boot-strapping GPIO0 as if it were a display reset.
constexpr uint8_t OLED_SDA_PIN = 21;
constexpr uint8_t OLED_SCL_PIN = 22;
constexpr int8_t OLED_RESET_PIN = -1;
#else
// Earlier TTGO LoRa32 V1.3 board/reference profile.
constexpr uint8_t OLED_SDA_PIN = 4;
constexpr uint8_t OLED_SCL_PIN = 15;
constexpr int8_t OLED_RESET_PIN = 16;
#endif
constexpr char BLE_DEVICE_NAME[] = "RgbBill-TTGO";
constexpr char BLE_IDENTITY[] = "bill-ttgo-v1.3-test";
#else
// WEMOS LOLIN32 V1.0.0 / ESP32-WROOM-32 prototype pin map.
constexpr uint8_t LED_STATUS_PIN = 5;
// GPIO12 reads the active-HIGH TTP223 OUT signal. GPIO12 is also the MTDI
// flash-voltage strap: never touch the sensor or otherwise drive it HIGH
// during power-up, reset, or flashing.
constexpr uint8_t TAP_BUTTON_PIN = 12;
constexpr uint8_t TAP_BUTTON_MODE = INPUT;
constexpr uint8_t TAP_BUTTON_ACTIVE_LEVEL = HIGH;
constexpr char TAP_INPUT_NAME[] = "TTP223";

constexpr uint8_t OLED_SDA_PIN = 21;
constexpr uint8_t OLED_SCL_PIN = 22;
constexpr int8_t OLED_RESET_PIN = -1;
constexpr char BLE_DEVICE_NAME[] = "RgbBill";
constexpr char BLE_IDENTITY[] = "bill-unprovisioned";
#endif

#if defined(RGB_BILL_TTGO_EXTERNAL_OLED)
// Match the electrically accepted replacement-panel diagnostic first. The
// slower clock is negligible for this 1 KiB framebuffer and maximizes margin.
constexpr uint32_t OLED_I2C_CLOCK_HZ = 100000;
#else
constexpr uint32_t OLED_I2C_CLOCK_HZ = 400000;
#endif

constexpr int16_t OLED_WIDTH = 128;
constexpr int16_t OLED_HEIGHT = 64;
constexpr uint8_t OLED_ADDRESS_PRIMARY = 0x3C;
constexpr uint8_t OLED_ADDRESS_SECONDARY = 0x3D;

constexpr uint32_t SESSION_TIMEOUT_MS = 300000;

namespace ArtifactInventory {
constexpr uint8_t VERSION = 0x03;
constexpr uint8_t EMPTY = 0;
constexpr uint8_t TO_PEER = 1;
constexpr uint8_t TO_DOCK = 2;
constexpr uint8_t RECOVERY_BLOCKED = 3;
constexpr uint8_t BEARER_HELD = 4;
}  // namespace ArtifactInventory

// Dock core v1 UUIDs, frozen after the first successful hardware session.
namespace BLE_UUIDs {
constexpr char DOCK_SERVICE[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c001";
constexpr char IDENTITY[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c002";
constexpr char STATUS[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c003";
constexpr char DENOMINATION[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c004";
constexpr char CMD[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c005";
constexpr char CHUNK_IN[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c006";
constexpr char CHUNK_OUT[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c007";
constexpr char ARTIFACT_META[] = "7c3e9a10-4b1e-4c8f-9d2a-00b111d0c008";
constexpr char TAP_SERVICE[] = "7c3e9a10-4b1e-4c8f-9d2a-00b1117a0002";
constexpr char TAP_META[] = "7c3e9a10-4b1e-4c8f-9d2a-00b1117a0003";
constexpr char TAP_CONTROL[] = "7c3e9a10-4b1e-4c8f-9d2a-00b1117a0004";
constexpr char TAP_CHUNK_IN[] = "7c3e9a10-4b1e-4c8f-9d2a-00b1117a0005";
constexpr char TAP_CHUNK_OUT[] = "7c3e9a10-4b1e-4c8f-9d2a-00b1117a0006";
}
