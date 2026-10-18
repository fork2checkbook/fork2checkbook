#if defined(FORK2CHECKBOOK_WAVESHARE_BILL)

#include <Arduino.h>
#include <BillStorage.h>
#include <DockGatt.h>
#include <TapGatt.h>
#include <WaveshareBillDisplay.h>
#include <WaveshareTouchInput.h>

#include "config.h"

namespace {
WaveshareBillDisplay billDisplay;
BillStorage billStorage;
DockGatt dockGatt;
TapGatt tapGatt;
WaveshareTouchInput touchInput;
uint32_t displayedCents = 0;
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("WATCH_BILL: ESP32-S3-Touch-AMOLED-2.06 production bill");

  if (billDisplay.begin()) {
    billDisplay.showAmount(0, "STARTING");
  }

  const bool storageMounted = billStorage.begin();
  dockGatt.begin(storageMounted);
  tapGatt.begin(dockGatt.server(), storageMounted);
  const bool touchReady = touchInput.begin();

  billDisplay.showAmount(0, touchReady ? "TAP READY" : "TOUCH ERROR");
  Serial.printf(
      "WATCH_BILL: GATT ready name=%s identity=%s tap=enabled touch=%s\n",
      BLE_DEVICE_NAME, BLE_IDENTITY, touchReady ? "ready" : "failed");
}

void loop() {
  touchInput.poll();
  int16_t touchX = -1;
  int16_t touchY = -1;
  if (touchInput.takePressedEdge(touchX, touchY)) {
    Serial.printf("WATCH_BILL: screen tap x=%d y=%d\n", touchX, touchY);
    tapGatt.requestReceiverToggle();
  }

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
      Serial.println("WATCH_BILL: tap handoff failed after dock artifact move");
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
      dockGatt.setArtifactState(ArtifactInventory::RECOVERY_BLOCKED,
                                retained.kind, retained.sessionId,
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
  billDisplay.poll();
  delay(10);
}

#endif
