#include <Arduino.h>
#include <BillDisplay.h>
#include <BillStorage.h>
#include <DockGatt.h>
#include <TapGatt.h>
#include "config.h"

namespace {
BillDisplay billDisplay;
BillStorage billStorage;
DockGatt dockGatt;
TapGatt tapGatt;
uint32_t displayedCents = 0;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_STATUS_PIN, OUTPUT);
  digitalWrite(LED_STATUS_PIN, LOW);

  if (billDisplay.begin()) {
    billDisplay.showAmount(0, "STARTING");
  }

  const bool storageMounted = billStorage.begin();
  dockGatt.begin(storageMounted);
  tapGatt.begin(dockGatt.server(), storageMounted);

  billDisplay.showAmount(0, "BLE READY");
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
      Serial.println("bill: tap handoff failed after dock artifact move");
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
      // A complete dock artifact that could not move into TapGatt must never
      // look empty. Direction 3 is an explicit operator-recovery boundary.
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
