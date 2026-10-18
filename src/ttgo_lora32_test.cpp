#include <Arduino.h>
#if defined(RGB_BILL_TTGO_TFT_ILI9341)
#include <TftBillDisplay.h>
#else
#include <BillDisplay.h>
#endif
#include <BillStorage.h>
#include <DockGatt.h>
#include <TapGatt.h>

#include "config.h"

// LilyGO TTGO LoRa32 V1.3-868 combined hardware diagnostic.
// This intentionally leaves the SX1276 radio uninitialized. It verifies the
// selected display and the same dock-core BLE surface used by the bill
// firmware. LoRa remains outside the RGB bill protocol; the legacy TFT profile
// also reuses radio pins.

namespace {
#if defined(RGB_BILL_TTGO_TFT_ILI9341)
TftBillDisplay billDisplay;
#else
BillDisplay billDisplay;
#endif
BillStorage billStorage;
DockGatt dockGatt;
TapGatt tapGatt;
uint32_t displayedCents = 0;
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("TTGO_TEST: LilyGO TTGO LoRa32 V1.3-868");
#if defined(RGB_BILL_TTGO_TFT_ILI9341)
  Serial.printf(
      "TTGO_TEST: ILI9341 SCK=%u MOSI=%u DC=%u RESET=%u CS=%u LED=%u\n",
      TFT_SCK_PIN, TFT_MOSI_PIN, TFT_DC_PIN, TFT_RESET_PIN, TFT_CS_PIN,
      TFT_BACKLIGHT_PIN);
#else
  Serial.printf("TTGO_TEST: OLED SDA=%u SCL=%u RESET=%d\n", OLED_SDA_PIN,
                OLED_SCL_PIN, OLED_RESET_PIN);

  pinMode(LED_STATUS_PIN, OUTPUT);
  digitalWrite(LED_STATUS_PIN, LOW);
#endif
  Serial.printf("TTGO_TEST: touch GPIO=%u active=%s input=%s\n",
                TAP_BUTTON_PIN,
                TAP_BUTTON_ACTIVE_LEVEL == HIGH ? "HIGH" : "LOW",
                TAP_INPUT_NAME);

  if (billDisplay.begin()) {
#if defined(RGB_BILL_TTGO_TFT_ILI9341)
    Serial.println(
        "TTGO_TEST: ILI9341 INIT PASS (write-only; confirm image visually)");
#else
    Serial.printf("TTGO_TEST: OLED PASS at 0x%02X\n", billDisplay.address());
#endif
    billDisplay.showAmount(0, "STARTING BLE");
  } else {
#if defined(RGB_BILL_TTGO_TFT_ILI9341)
    Serial.println("TTGO_TEST: ILI9341 FAIL; BLE test will continue");
#else
    Serial.println("TTGO_TEST: OLED FAIL; BLE test will continue");
#endif
  }

  const bool storageMounted = billStorage.begin();
  dockGatt.begin(storageMounted);
  tapGatt.begin(dockGatt.server(), storageMounted);
  Serial.printf("TTGO_TEST: BLE PASS - advertising as %s\n", BLE_DEVICE_NAME);
  Serial.printf("TTGO_TEST: identity=%s\n", BLE_IDENTITY);
  billDisplay.showAmount(0, "BLE READY");
#if !defined(RGB_BILL_TTGO_TFT_ILI9341)
  digitalWrite(LED_STATUS_PIN, HIGH);
#endif
}

void loop() {
  dockGatt.poll();
  tapGatt.poll();

  if (dockGatt.takeTapDiscardRequest()) {
    tapGatt.discardStaged();
  }

  if (tapGatt.canStageOutbound()) {
    DockGatt::InboundTransfer transfer;
    if (dockGatt.moveInboundTransfer(transfer, TapGatt::OUTBOUND_PATH) &&
        !tapGatt.stageOutbound(transfer.kind, transfer.sessionId,
                               transfer.size, transfer.denominationCents,
                               transfer.path)) {
      Serial.println("TTGO_TEST: tap handoff failed after dock artifact move");
    }
  }

  TapGatt::InboundTransfer tapped;
  if (tapGatt.peekInboundTransfer(tapped)) {
    dockGatt.queueOutboundTransfer(tapped.kind, tapped.sessionId, tapped.path,
                                   true);
  }

  TapGatt::InboundTransfer bearer;
  if (tapGatt.peekOutboundTransfer(bearer)) {
    dockGatt.queueOutboundTransfer(bearer.kind, bearer.sessionId, bearer.path,
                                   true);
  }


  TapGatt::ArtifactState artifact;
  if (tapGatt.artifactState(artifact)) {
    DockGatt::InboundTransfer retained;
    if (dockGatt.peekInboundTransfer(retained)) {
      dockGatt.setArtifactState(3, retained.kind, retained.sessionId,
                                retained.size, retained.denominationCents);
    } else {
      dockGatt.setArtifactState(
          artifact.direction, artifact.kind, artifact.sessionId, artifact.size,
          artifact.denominationCents);
    }
  }

  uint32_t cents = 0;
  char status[16];
  if (dockGatt.takeDisplayUpdate(cents, status, sizeof(status))) {
    displayedCents = cents;
    billDisplay.showAmount(cents, status);
  }
  if (tapGatt.takeStatusUpdate(status, sizeof(status))) {
    billDisplay.showAmount(displayedCents, status);
  }
  delay(20);
}
