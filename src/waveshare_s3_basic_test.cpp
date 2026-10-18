#if defined(FORK2CHECKBOOK_WAVESHARE_BASIC_TEST)

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <BLEClient.h>
#include <BLEDevice.h>
#include <BLERemoteCharacteristic.h>
#include <BLERemoteService.h>
#include <BLEScan.h>
#include <BLESecurity.h>
#include <SensorPCF85063.hpp>
#include <Wire.h>
#include <esp_gap_ble_api.h>

#include "BillPeerSelector.h"
#include "TouchContactGate.h"
#include "config.h"
#include "waveshare_s3_pins.h"

namespace {

using namespace WaveshareS3Pins;

constexpr uint32_t kSerialBaud = 115200;
constexpr uint32_t kWakeScreenMs = 1800;
constexpr uint32_t kTouchBannerMs = 1000;
constexpr uint32_t kTouchDebounceMs = 120;
constexpr uint32_t kBleScanSeconds = 5;
constexpr uint32_t kPairingWaitMs = 6000;
constexpr uint8_t kDisplayBrightness = 56;
constexpr uint8_t kTouchPowerModeRegister = 0xA5;
constexpr uint8_t kTouchMonitorMode = 0x01;
constexpr uint8_t kTouchFingerRegister = 0x02;

constexpr uint16_t kBlack = 0x0000;
constexpr uint16_t kPaper = 0xE71C;
constexpr uint16_t kGreen = 0x4E69;
constexpr uint16_t kGold = 0xD5A3;
constexpr uint16_t kMuted = 0x7BEF;
constexpr uint16_t kPanel = 0x10A2;

Arduino_DataBus *display_bus = new Arduino_ESP32QSPI(
    LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3);
Arduino_CO5300 *display = new Arduino_CO5300(
    display_bus, LCD_RESET, 0, false, LCD_WIDTH, LCD_HEIGHT, 22, 0, 0, 0);
SensorPCF85063 rtc;
TouchContactGate touch_contact;

enum class BleTestState {
  kReady,
  kScanning,
  kCandidate,
  kConnecting,
  kBonded,
  kVerifiedOnly,
  kNoPeer,
  kFailed,
};

class PairingCallbacks : public BLESecurityCallbacks {
 public:
  uint32_t onPassKeyRequest() override { return 0; }
  void onPassKeyNotify(uint32_t pass_key) override {
    Serial.printf("WAVESHARE_BASIC: BLE passkey notification %06lu\n",
                  static_cast<unsigned long>(pass_key));
  }
  bool onSecurityRequest() override { return true; }
  bool onConfirmPIN(uint32_t pin) override {
    Serial.printf("WAVESHARE_BASIC: BLE numeric confirmation %06lu accepted\n",
                  static_cast<unsigned long>(pin));
    return true;
  }
  void onAuthenticationComplete(esp_ble_auth_cmpl_t result) override {
    authentication_success = result.success;
    authentication_complete = true;
    Serial.printf("WAVESHARE_BASIC: BLE authentication %s reason=0x%02X\n",
                  result.success ? "PASS" : "FAILED", result.fail_reason);
  }

  void reset() {
    authentication_complete = false;
    authentication_success = false;
  }

  volatile bool authentication_complete = false;
  volatile bool authentication_success = false;
};

BLEScan *ble_scan = nullptr;
BLEClient *ble_client = nullptr;
PairingCallbacks pairing_callbacks;
BLESecurity ble_security;
BleTestState ble_state = BleTestState::kReady;
char ble_candidate_name[32] = {};
char ble_candidate_address[18] = {};
esp_ble_addr_type_t ble_candidate_address_type = BLE_ADDR_TYPE_PUBLIC;
int ble_candidate_rssi = -127;
char ble_identity[40] = {};

bool display_ready = false;
bool rtc_ready = false;
bool rtc_seeded = false;
bool touch_ready = false;
uint32_t last_clock_second = UINT32_MAX;
uint32_t touch_banner_until_ms = 0;
uint32_t last_touch_ms = 0;
uint32_t completed_touch_count = 0;
int16_t last_touch_x = -1;
int16_t last_touch_y = -1;
volatile bool touch_interrupt_pending = false;

void IRAM_ATTR onTouchInterrupt() { touch_interrupt_pending = true; }

const char *bleStateLabel() {
  switch (ble_state) {
    case BleTestState::kScanning:
      return "SCANNING";
    case BleTestState::kCandidate:
      return "FOUND";
    case BleTestState::kConnecting:
      return "PAIRING";
    case BleTestState::kBonded:
      return "BONDED";
    case BleTestState::kVerifiedOnly:
      return "VERIFIED";
    case BleTestState::kNoPeer:
      return "NO PEER";
    case BleTestState::kFailed:
      return "FAILED";
    case BleTestState::kReady:
    default:
      return "READY";
  }
}

void setBleState(BleTestState state) {
  ble_state = state;
  last_clock_second = UINT32_MAX;
}

bool validDateTime(const RTC_DateTime &dt) {
  return dt.getYear() >= 2024 && dt.getYear() <= 2099 &&
         dt.getMonth() >= 1 && dt.getMonth() <= 12 && dt.getDay() >= 1 &&
         dt.getDay() <= 31 && dt.getHour() <= 23 && dt.getMinute() <= 59 &&
         dt.getSecond() <= 59;
}

void centeredText(const char *text, int16_t y, uint8_t size,
                  uint16_t color) {
  int16_t x1 = 0;
  int16_t y1 = 0;
  uint16_t width = 0;
  uint16_t height = 0;
  display->setTextSize(size);
  display->setTextColor(color, kBlack);
  display->getTextBounds(text, 0, y, &x1, &y1, &width, &height);
  const int16_t x = static_cast<int16_t>((LCD_WIDTH - width) / 2);
  display->setCursor(x < 0 ? 0 : x, y);
  display->print(text);
}

void renderWakeScreen(const char *reason) {
  if (!display_ready) {
    return;
  }
  display->fillScreen(kBlack);
  centeredText("fork2checkbook", 88, 3, kPaper);
  centeredText("HARDWARE TEST", 142, 2, kGold);

  display->fillRoundRect(45, 222, 320, 112, 18, kPanel);
  centeredText("READY", 242, 3, kGreen);
  centeredText(reason, 286, 2, kPaper);

  centeredText("USB / POWER BANK", 382, 2, kMuted);
}

void renderClockFrame() {
  if (!display_ready) {
    return;
  }
  display->fillScreen(kBlack);
  display->setTextColor(kGold, kBlack);
  display->setTextSize(2);
  display->setCursor(24, 24);
  display->print("fork2checkbook");
  display->drawFastHLine(24, 60, LCD_WIDTH - 48, kPanel);
  display->drawRoundRect(22, 102, LCD_WIDTH - 44, 184, 20, kGreen);
  display->setTextColor(kMuted, kBlack);
  display->setCursor(32, 324);
  display->print("RTC");
  display->setCursor(32, 362);
  display->print("BLE");
  last_clock_second = UINT32_MAX;
}

void renderClockValues() {
  if (!display_ready) {
    return;
  }

  RTC_DateTime dt;
  if (rtc_ready) {
    dt = rtc.getDateTime();
  }

  const uint32_t second_key = rtc_ready ? dt.getSecond() : millis() / 1000;
  const bool banner_active =
      static_cast<int32_t>(touch_banner_until_ms - millis()) > 0;
  if (second_key == last_clock_second && !banner_active) {
    return;
  }
  last_clock_second = second_key;

  display->fillRect(42, 128, LCD_WIDTH - 84, 132, kBlack);
  char time_text[16];
  char date_text[24];
  if (rtc_ready && validDateTime(dt)) {
    snprintf(time_text, sizeof(time_text), "%02u:%02u:%02u", dt.getHour(),
             dt.getMinute(), dt.getSecond());
    snprintf(date_text, sizeof(date_text), "%02u-%02u-%04u", dt.getDay(),
             dt.getMonth(), dt.getYear());
  } else {
    snprintf(time_text, sizeof(time_text), "--:--:--");
    snprintf(date_text, sizeof(date_text), "RTC unavailable");
  }
  centeredText(time_text, 150, 5, kPaper);
  centeredText(date_text, 224, 2, kMuted);

  display->fillRect(158, 308, 226, 94, kBlack);
  display->setTextSize(2);
  display->setTextColor(rtc_ready ? kGreen : kGold, kBlack);
  display->setCursor(158, 324);
  display->print(rtc_ready ? (rtc_seeded ? "SEEDED" : "OK") : "FAILED");
  display->setTextColor(
      ble_state == BleTestState::kFailed ? kGold : kGreen, kBlack);
  display->setCursor(158, 362);
  display->print(bleStateLabel());

  display->fillRect(24, 404, LCD_WIDTH - 48, 82, kBlack);
  if (banner_active) {
    char banner[42];
    snprintf(banner, sizeof(banner), "TAP #%lu  X%d Y%d",
             static_cast<unsigned long>(completed_touch_count), last_touch_x,
             last_touch_y);
    centeredText(banner, 456, 2, kGold);
  } else if (ble_state == BleTestState::kCandidate) {
    char peer[48];
    snprintf(peer, sizeof(peer), "%s  %d dBm", ble_candidate_name,
             ble_candidate_rssi);
    centeredText(peer, 410, 2, kPaper);
    centeredText("Tap again to pair", 456, 2, kGold);
  } else if (ble_state == BleTestState::kBonded ||
             ble_state == BleTestState::kVerifiedOnly) {
    centeredText(ble_identity, 410, 2, kPaper);
    centeredText(ble_state == BleTestState::kBonded ? "Tap to rescan"
                                                    : "No bond - tap retry",
                 456, 2, kGold);
  } else if (ble_state == BleTestState::kScanning) {
    centeredText("RgbBill service only", 430, 2, kMuted);
  } else if (ble_state == BleTestState::kConnecting) {
    centeredText(ble_candidate_name, 430, 2, kMuted);
  } else if (ble_state == BleTestState::kNoPeer) {
    centeredText("No authorized bill found", 410, 2, kPaper);
    centeredText("Tap to scan again", 456, 2, kGold);
  } else if (ble_state == BleTestState::kFailed) {
    centeredText("See serial log", 410, 2, kPaper);
    centeredText("Tap to scan again", 456, 2, kGold);
  } else {
    centeredText("Tap screen to scan", 430, 2, kGold);
  }
}

bool peerIsBonded(const char *address) {
  int count = esp_ble_get_bond_device_num();
  if (count <= 0) {
    return false;
  }

  esp_ble_bond_dev_t *devices = new esp_ble_bond_dev_t[count];
  int returned = count;
  const esp_err_t result = esp_ble_get_bond_device_list(&returned, devices);
  if (result != ESP_OK) {
    delete[] devices;
    return false;
  }

  BLEAddress target{std::string(address)};
  bool found = false;
  for (int index = 0; index < returned; ++index) {
    if (memcmp(devices[index].bd_addr, *target.getNative(), ESP_BD_ADDR_LEN) ==
        0) {
      found = true;
      break;
    }
  }
  delete[] devices;
  return found;
}

void scanForBill() {
  setBleState(BleTestState::kScanning);
  renderClockValues();
  Serial.printf("WAVESHARE_BASIC: BLE scan started duration=%lu seconds\n",
                static_cast<unsigned long>(kBleScanSeconds));

  BillPeerSelector selector;
  BLEScanResults results = ble_scan->start(kBleScanSeconds, false);
  for (int index = 0; index < results.getCount(); ++index) {
    BLEAdvertisedDevice device = results.getDevice(index);
    const std::string name = device.haveName() ? device.getName() : "";
    const std::string address = device.getAddress().toString();
    const bool authorized_service = device.isAdvertisingService(
        BLEUUID(BLE_UUIDs::DOCK_SERVICE));
    const bool became_best = selector.consider(
        name.c_str(), authorized_service, device.getRSSI(), address.c_str());
    if (authorized_service && name.rfind("RgbBill", 0) == 0) {
      Serial.printf(
          "WAVESHARE_BASIC: BLE authorized peer name=%s address=%s RSSI=%d\n",
          name.c_str(), address.c_str(), device.getRSSI());
    }
    if (became_best) {
      ble_candidate_address_type = device.getAddressType();
    }
  }

  if (!selector.hasPeer()) {
    ble_candidate_name[0] = '\0';
    ble_candidate_address[0] = '\0';
    setBleState(BleTestState::kNoPeer);
    Serial.printf(
        "WAVESHARE_BASIC: BLE scan complete total=%d authorized=0\n",
        results.getCount());
  } else {
    snprintf(ble_candidate_name, sizeof(ble_candidate_name), "%s",
             selector.name());
    snprintf(ble_candidate_address, sizeof(ble_candidate_address), "%s",
             selector.address());
    ble_candidate_rssi = selector.rssi();
    setBleState(BleTestState::kCandidate);
    Serial.printf(
        "WAVESHARE_BASIC: BLE scan complete total=%d authorized=%u selected=%s "
        "address=%s RSSI=%d\n",
        results.getCount(), selector.matchCount(), ble_candidate_name,
        ble_candidate_address, ble_candidate_rssi);
  }
  ble_scan->clearResults();
  renderClockValues();
}

void pairWithSelectedBill() {
  if (ble_candidate_address[0] == '\0') {
    setBleState(BleTestState::kFailed);
    return;
  }

  setBleState(BleTestState::kConnecting);
  renderClockValues();
  pairing_callbacks.reset();
  ble_identity[0] = '\0';
  Serial.printf(
      "WAVESHARE_BASIC: BLE connect started name=%s address=%s RSSI=%d\n",
      ble_candidate_name, ble_candidate_address, ble_candidate_rssi);

  const bool connected = ble_client->connect(
      BLEAddress(std::string(ble_candidate_address)),
      ble_candidate_address_type);
  if (!connected) {
    setBleState(BleTestState::kFailed);
    Serial.println("WAVESHARE_BASIC: BLE connect FAILED");
    renderClockValues();
    return;
  }

  BLERemoteService *service =
      ble_client->getService(BLEUUID(BLE_UUIDs::DOCK_SERVICE));
  BLERemoteCharacteristic *identity =
      service == nullptr
          ? nullptr
          : service->getCharacteristic(BLEUUID(BLE_UUIDs::IDENTITY));
  if (identity == nullptr || !identity->canRead()) {
    Serial.println(
        "WAVESHARE_BASIC: BLE verification FAILED missing dock identity");
    ble_client->disconnect();
    setBleState(BleTestState::kFailed);
    renderClockValues();
    return;
  }

  const std::string identity_value = identity->readValue();
  snprintf(ble_identity, sizeof(ble_identity), "%s", identity_value.c_str());

  const uint32_t wait_started = millis();
  while (!pairing_callbacks.authentication_complete &&
         millis() - wait_started < kPairingWaitMs) {
    delay(25);
  }
  const bool bonded = peerIsBonded(ble_candidate_address);
  const bool authentication_passed =
      pairing_callbacks.authentication_complete &&
      pairing_callbacks.authentication_success;
  ble_client->disconnect();

  if (bonded) {
    setBleState(BleTestState::kBonded);
  } else {
    setBleState(BleTestState::kVerifiedOnly);
  }
  Serial.printf(
      "WAVESHARE_BASIC: BLE verification PASS identity=%s auth=%s bonded=%s "
      "stored_bonds=%d\n",
      ble_identity, authentication_passed ? "PASS" : "NOT_CONFIRMED",
      bonded ? "yes" : "no", esp_ble_get_bond_device_num());
  renderClockValues();
}

void initializeBle() {
  BLEDevice::init("fork2checkbook-watch-test");
  BLEDevice::setMTU(247);
  BLEDevice::setEncryptionLevel(ESP_BLE_SEC_ENCRYPT_NO_MITM);
  BLEDevice::setSecurityCallbacks(&pairing_callbacks);
  ble_security.setAuthenticationMode(ESP_LE_AUTH_REQ_SC_BOND);
  ble_security.setCapability(ESP_IO_CAP_NONE);
  ble_security.setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK |
                                    ESP_BLE_ID_KEY_MASK);
  ble_security.setRespEncryptionKey(ESP_BLE_ENC_KEY_MASK |
                                    ESP_BLE_ID_KEY_MASK);

  ble_scan = BLEDevice::getScan();
  ble_scan->setActiveScan(true);
  ble_scan->setInterval(100);
  ble_scan->setWindow(80);
  ble_client = BLEDevice::createClient();
  Serial.printf(
      "WAVESHARE_BASIC: BLE PASS central ready; stored_bonds=%d; tap to scan\n",
      esp_ble_get_bond_device_num());
}

bool writeTouchRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readTouchSample(uint8_t &fingers, int16_t &x, int16_t &y) {
  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  Wire.write(kTouchFingerRegister);
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
  const uint8_t x_high = Wire.read();
  const uint8_t x_low = Wire.read();
  const uint8_t y_high = Wire.read();
  const uint8_t y_low = Wire.read();
  if (fingers == 0) {
    x = -1;
    y = -1;
    return true;
  }

  x = static_cast<int16_t>(((x_high & 0x0F) << 8) | x_low);
  y = static_cast<int16_t>(((y_high & 0x0F) << 8) | y_low);
  return true;
}

bool initializeTouch() {
  pinMode(TOUCH_INTERRUPT, INPUT_PULLUP);
  pinMode(TOUCH_RESET, OUTPUT);
  digitalWrite(TOUCH_RESET, HIGH);
  delay(1);
  digitalWrite(TOUCH_RESET, LOW);
  delay(20);
  digitalWrite(TOUCH_RESET, HIGH);
  delay(50);

  Wire.beginTransmission(TOUCH_I2C_ADDRESS);
  if (Wire.endTransmission() != 0 ||
      !writeTouchRegister(kTouchPowerModeRegister, kTouchMonitorMode)) {
    return false;
  }
  attachInterrupt(digitalPinToInterrupt(TOUCH_INTERRUPT), onTouchInterrupt,
                  FALLING);
  return true;
}

void printI2cInventory() {
  Serial.print("WAVESHARE_BASIC: I2C devices");
  uint8_t found = 0;
  for (uint8_t address = 1; address < 0x7F; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf(" 0x%02X", address);
      ++found;
    }
  }
  if (found == 0) {
    Serial.print(" none");
  }
  Serial.println();
}

void initializeRtc() {
  Wire.begin(I2C_SDA, I2C_SCL);
  rtc_ready = rtc.begin(Wire, I2C_SDA, I2C_SCL);
  if (!rtc_ready) {
    Serial.println("WAVESHARE_BASIC: rtc FAILED");
    return;
  }

  RTC_DateTime dt = rtc.getDateTime();
  if (!rtc.isClockIntegrityGuaranteed() || !rtc.isRunning() ||
      !validDateTime(dt)) {
    rtc.setDateTime(RTC_DateTime(__DATE__, __TIME__));
    rtc.start();
    rtc_seeded = true;
    dt = rtc.getDateTime();
  }

  rtc_ready = validDateTime(dt);
  Serial.printf("WAVESHARE_BASIC: rtc %s%s %04u-%02u-%02u %02u:%02u:%02u\n",
                rtc_ready ? "PASS" : "FAILED", rtc_seeded ? " SEEDED" : "",
                dt.getYear(), dt.getMonth(), dt.getDay(), dt.getHour(),
                dt.getMinute(), dt.getSecond());
}

}  // namespace

void setup() {
  Serial.begin(kSerialBaud);
  delay(500);
  Serial.println();
  Serial.println("WAVESHARE_BASIC: ESP32-S3-Touch-AMOLED-2.06");
  Serial.printf("WAVESHARE_BASIC: flash=%lu bytes psram=%lu bytes found=%s\n",
                static_cast<unsigned long>(ESP.getFlashChipSize()),
                static_cast<unsigned long>(ESP.getPsramSize()),
                psramFound() ? "yes" : "no");

  display_ready = display->begin();
  if (!display_ready) {
    Serial.println("WAVESHARE_BASIC: display FAILED");
    return;
  }
  if (display->width() != LCD_WIDTH || display->height() != LCD_HEIGHT) {
    Serial.printf("WAVESHARE_BASIC: display geometry FAILED expected=%dx%d actual=%dx%d\n",
                  LCD_WIDTH, LCD_HEIGHT, display->width(), display->height());
    display_ready = false;
    return;
  }
  display->setBrightness(kDisplayBrightness);
  display->fillScreen(kBlack);
  Serial.printf("WAVESHARE_BASIC: display PASS %dx%d\n", display->width(),
                display->height());

  initializeRtc();
  touch_ready = initializeTouch();
  printI2cInventory();
  Serial.printf(
      "WAVESHARE_BASIC: touch %s address=0x%02X irq=GPIO%d active=LOW\n",
      touch_ready ? "PASS" : "FAILED", TOUCH_I2C_ADDRESS, TOUCH_INTERRUPT);
  initializeBle();
  renderWakeScreen("POWER ON");
  delay(kWakeScreenMs);
  renderClockFrame();
  Serial.println(
      "WAVESHARE_BASIC: setup PASS; always-on; BLE central initialized; Wi-Fi "
      "not initialized");
}

void loop() {
  if (!display_ready) {
    delay(1000);
    return;
  }

  const uint32_t now = millis();
  if (touch_ready && touch_interrupt_pending &&
      now - last_touch_ms >= kTouchDebounceMs) {
    touch_interrupt_pending = false;
    uint8_t fingers = 0;
    int16_t x = -1;
    int16_t y = -1;
    if (readTouchSample(fingers, x, y)) {
      last_touch_ms = now;
      if (touch_contact.update(fingers)) {
        last_touch_x = x;
        last_touch_y = y;
        ++completed_touch_count;
        touch_banner_until_ms = now + kTouchBannerMs;
        last_clock_second = UINT32_MAX;
        Serial.printf("WAVESHARE_BASIC: screen tap count=%lu x=%d y=%d\n",
                      static_cast<unsigned long>(completed_touch_count), x, y);
        if (ble_state == BleTestState::kCandidate) {
          pairWithSelectedBill();
        } else {
          scanForBill();
        }
      } else if (fingers == 0) {
        Serial.println("WAVESHARE_BASIC: screen release");
      }
    } else {
      Serial.println("WAVESHARE_BASIC: touch IRQ read failed");
    }
  }

  renderClockValues();
  delay(2);
}

#endif  // FORK2CHECKBOOK_WAVESHARE_BASIC_TEST
