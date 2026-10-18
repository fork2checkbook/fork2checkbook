#include "DockGatt.h"

#include <Arduino.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <SPIFFS.h>
#include <TapProtocol.h>
#include <cstring>

#include <ArtifactRecoveryPolicy.h>
#include "config.h"

namespace {
constexpr char CHUNK_PART_PATH[] = "/chunk-in.part";
constexpr char CHUNK_READY_PATH[] = "/chunk-in.ready";
constexpr char CHUNK_META_PATH[] = "/chunk-in.meta";
// Bluedroid's createService default is 15 handles. This service needs at
// least 16: one service, six characteristic declaration/value pairs, and
// three CCCDs. Reserve a little headroom so CHUNK_OUT always gets its CCCD.
constexpr uint16_t DOCK_SERVICE_HANDLES = 22;
// Leave headroom for SPIFFS metadata and garbage collection in the 896 KiB
// partition instead of treating every partition byte as payload capacity.
constexpr size_t MAX_TRANSFER_BYTES = 640U * 1024U;
constexpr uint16_t OUTBOUND_PAYLOAD_BYTES = 180;
constexpr uint32_t ACK_TIMEOUT_MS = 1000;
constexpr uint8_t MAX_ACK_RETRIES = 3;
// A bill acting as a tap central also receives the GATTS disconnect callback.
// Restarting advertising from inside that callback races the Bluedroid client
// teardown and can leave the receiver undiscoverable until reset.
constexpr uint32_t ADVERTISING_RESTART_DELAY_MS = 250;

uint16_t readLe16(const uint8_t* input) {
  return static_cast<uint16_t>(input[0]) |
         (static_cast<uint16_t>(input[1]) << 8U);
}

uint32_t readLe32(const uint8_t* input) {
  return static_cast<uint32_t>(input[0]) |
         (static_cast<uint32_t>(input[1]) << 8U) |
         (static_cast<uint32_t>(input[2]) << 16U) |
         (static_cast<uint32_t>(input[3]) << 24U);
}

void writeLe32(uint8_t* output, uint32_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFFU);
  output[1] = static_cast<uint8_t>((value >> 8U) & 0xFFU);
  output[2] = static_cast<uint8_t>((value >> 16U) & 0xFFU);
  output[3] = static_cast<uint8_t>((value >> 24U) & 0xFFU);
}
}  // namespace

DockGatt::ServerCallbacks::ServerCallbacks(DockGatt& owner) : owner_(owner) {}

void DockGatt::ServerCallbacks::onConnect(BLEServer*) { owner_.handleConnect(); }

void DockGatt::ServerCallbacks::onDisconnect(BLEServer* server) {
  owner_.handleDisconnect(server);
}

DockGatt::CommandCallbacks::CommandCallbacks(DockGatt& owner) : owner_(owner) {}

void DockGatt::CommandCallbacks::onWrite(BLECharacteristic* characteristic) {
  owner_.handleCommand(characteristic->getValue());
}

DockGatt::ChunkCallbacks::ChunkCallbacks(DockGatt& owner) : owner_(owner) {}

void DockGatt::ChunkCallbacks::onWrite(BLECharacteristic* characteristic) {
  owner_.handleChunk(characteristic->getValue());
}

DockGatt::DockGatt()
    : callbacks_(*this),
      commandCallbacks_(*this),
      chunkCallbacks_(*this),
      inboundSession_(MAX_TRANSFER_BYTES) {}

void DockGatt::begin(bool storageMounted) {
  chunkMutex_ = xSemaphoreCreateMutex();
  filesystemMounted_ = storageMounted;
  storageReady_ = filesystemMounted_;
  if (!storageReady_) {
    storageBlockedByOrphan_ = true;
    Serial.println(
        "bill: SPIFFS unavailable; chunk transfers recovery-blocked");
  }

  const bool hasPart = storageReady_ && SPIFFS.exists(CHUNK_PART_PATH);
  const bool hasReady = storageReady_ && SPIFFS.exists(CHUNK_READY_PATH);
  const bool hasMetadata = storageReady_ && SPIFFS.exists(CHUNK_META_PATH);
  bool restored = false;
  rgbbill::tap::Metadata metadata;
  if (storageReady_ && !hasPart && hasReady && hasMetadata) {
    File metadataFile = SPIFFS.open(CHUNK_META_PATH, FILE_READ);
    File artifactFile = SPIFFS.open(CHUNK_READY_PATH, FILE_READ);
    uint8_t encoded[rgbbill::tap::METADATA_SIZE] = {};
    restored = metadataFile && artifactFile &&
        metadataFile.read(encoded, sizeof(encoded)) == sizeof(encoded) &&
        rgbbill::tap::decodeMetadata(encoded, sizeof(encoded), metadata) &&
        metadata.state == rgbbill::tap::ReadyState::kReady &&
        metadata.size == artifactFile.size() &&
        (metadata.kind == static_cast<uint8_t>(rgbbill::chunk::Kind::kInvoice) ||
         metadata.kind ==
             static_cast<uint8_t>(rgbbill::chunk::Kind::kConsignment) ||
         metadata.kind ==
             static_cast<uint8_t>(rgbbill::chunk::Kind::kBearerNote));
    if (metadataFile) metadataFile.close();
    if (artifactFile) artifactFile.close();
  }

  if (storageReady_) {
    const auto bootState = rgbbill::storage::classifyArtifactBootState(
        hasPart, hasReady, hasMetadata, restored);
    if (bootState == rgbbill::storage::ArtifactBootState::kReady) {
      inboundReady_ = true;
      inboundKind_ = metadata.kind;
      inboundSessionId_ = metadata.sessionId;
      inboundSize_ = metadata.size;
      inboundDenominationCents_ = metadata.denominationCents;
      Serial.printf(
          "bill: restored dock artifact kind=%u session=%08lx bytes=%u\n",
          inboundKind_, static_cast<unsigned long>(inboundSessionId_),
          static_cast<unsigned>(inboundSize_));
    } else if (bootState ==
               rgbbill::storage::ArtifactBootState::kRecoveryBlocked) {
      storageBlockedByOrphan_ = true;
      storageReady_ = false;
      Serial.println(
          "bill: incomplete or inconsistent dock artifact blocks transfers; use chunk:discard explicitly");
    }
  }

  BLEDevice::init(BLE_DEVICE_NAME);
  BLEDevice::setMTU(247);
  server_ = BLEDevice::createServer();
  server_->setCallbacks(&callbacks_);

  BLEService* service = server_->createService(
      BLEUUID(BLE_UUIDs::DOCK_SERVICE), DOCK_SERVICE_HANDLES);
  BLECharacteristic* identity = service->createCharacteristic(
      BLE_UUIDs::IDENTITY, BLECharacteristic::PROPERTY_READ);
  identity->setValue(BLE_IDENTITY);

  statusCharacteristic_ = service->createCharacteristic(
      BLE_UUIDs::STATUS,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  statusCharacteristic_->setValue("idle");
  statusCharacteristic_->addDescriptor(new BLE2902());

  denominationCharacteristic_ = service->createCharacteristic(
      BLE_UUIDs::DENOMINATION,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  denominationCharacteristic_->setValue("0");
  denominationCharacteristic_->addDescriptor(new BLE2902());

  BLECharacteristic* command = service->createCharacteristic(
      BLE_UUIDs::CMD, BLECharacteristic::PROPERTY_WRITE);
  command->setCallbacks(&commandCallbacks_);

  BLECharacteristic* chunkIn = service->createCharacteristic(
      BLE_UUIDs::CHUNK_IN, BLECharacteristic::PROPERTY_WRITE);
  chunkIn->setCallbacks(&chunkCallbacks_);

  chunkOutCharacteristic_ = service->createCharacteristic(
      BLE_UUIDs::CHUNK_OUT, BLECharacteristic::PROPERTY_NOTIFY);
  chunkOutCccd_ = new BLE2902();
  chunkOutCharacteristic_->addDescriptor(chunkOutCccd_);

  artifactMetaCharacteristic_ = service->createCharacteristic(
      BLE_UUIDs::ARTIFACT_META, BLECharacteristic::PROPERTY_READ);
  uint8_t emptyArtifact[16] = {ArtifactInventory::VERSION};
  artifactMetaCharacteristic_->setValue(emptyArtifact, sizeof(emptyArtifact));

  service->start();

  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_UUIDs::DOCK_SERVICE);
  advertising->setScanResponse(true);
  advertising->start();
  Serial.println("bill: dock GATT advertising");
}

bool DockGatt::storageAvailable() const {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  const bool available = filesystemMounted_ && storageReady_ &&
                         !storageBlockedByOrphan_;
  xSemaphoreGive(chunkMutex_);
  return available;
}

void DockGatt::poll() {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return;
  }

  const uint32_t now = millis();
  if (advertisingRestartPending_ && !connected_ &&
      now - advertisingRestartRequestedAtMs_ >=
          ADVERTISING_RESTART_DELAY_MS) {
    advertisingRestartPending_ = false;
    xSemaphoreGive(chunkMutex_);
    BLEDevice::getAdvertising()->start();
    Serial.println("bill: dock advertising restarted after BLE teardown");
    return;
  }
  if (inboundSession_.active() && !inboundSession_.complete() &&
      now - inboundLastActivityMs_ >= SESSION_TIMEOUT_MS) {
    Serial.println("bill: inbound chunk session timed out");
    resetInboundLocked(false);
  }

  // BLE notifications are emitted from the Arduino loop task. Sending an ACK
  // synchronously from the GATT onWrite callback can be dropped by the
  // classic ESP32 BLE stack before the write transaction has unwound.
  const bool dockNotificationsEnabled =
      chunkOutCccd_ != nullptr && chunkOutCccd_->getNotifications();
  if (connected_ && dockNotificationsEnabled && ackPending_) {
    chunkOutCharacteristic_->setValue(ackEncoded_, ackEncodedLength_);
    chunkOutCharacteristic_->notify();
    ackPending_ = false;
    ackEncodedLength_ = 0;
    xSemaphoreGive(chunkMutex_);
    return;
  }

  const bool dockReceiveReady =
      dockReceiveRequested_ || dockNotificationsEnabled;
  if (!connected_ || !dockReceiveReady || !outboundActive_) {
    xSemaphoreGive(chunkMutex_);
    return;
  }

  if (outboundWaitingAck_) {
    if (now - outboundSentAtMs_ < ACK_TIMEOUT_MS) {
      xSemaphoreGive(chunkMutex_);
      return;
    }
    if (outboundRetries_ >= MAX_ACK_RETRIES) {
      Serial.println("bill: outbound chunk ACK timeout");
      finishOutboundLocked(false);
      xSemaphoreGive(chunkMutex_);
      return;
    }
    ++outboundRetries_;
    chunkOutCharacteristic_->setValue(outboundEncoded_, outboundEncodedLength_);
    chunkOutCharacteristic_->notify();
    outboundSentAtMs_ = now;
    xSemaphoreGive(chunkMutex_);
    return;
  }

  uint8_t payload[OUTBOUND_PAYLOAD_BYTES];
  const size_t payloadLength = outboundFile_.read(payload, sizeof(payload));
  if (payloadLength == 0 ||
      !rgbbill::chunk::encodeFrame(
          outboundKind_, outboundSessionId_, outboundSequence_, outboundTotal_,
          payload, payloadLength, outboundEncoded_, sizeof(outboundEncoded_),
          outboundEncodedLength_)) {
    Serial.println("bill: outbound chunk read/encode failed");
    finishOutboundLocked(false);
    xSemaphoreGive(chunkMutex_);
    return;
  }

  outboundWaitingAck_ = true;
  outboundRetries_ = 0;
  outboundSentAtMs_ = now;
  chunkOutCharacteristic_->setValue(outboundEncoded_, outboundEncodedLength_);
  chunkOutCharacteristic_->notify();
  xSemaphoreGive(chunkMutex_);
}

bool DockGatt::takeDisplayUpdate(uint32_t& cents, char* status,
                                 size_t statusSize) {
  if (status == nullptr || statusSize == 0) {
    return false;
  }

  portENTER_CRITICAL(&displayMux_);
  if (!displayDirty_) {
    portEXIT_CRITICAL(&displayMux_);
    return false;
  }
  cents = displayCents_;
  strncpy(status, displayStatus_, statusSize - 1);
  status[statusSize - 1] = '\0';
  displayDirty_ = false;
  portEXIT_CRITICAL(&displayMux_);
  return true;
}

void DockGatt::handleConnect() {
  if (chunkMutex_ != nullptr &&
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
    connected_ = true;
    advertisingRestartPending_ = false;
    dockReceiveRequested_ = false;
    if (chunkOutCccd_ != nullptr) {
      chunkOutCccd_->setNotifications(false);
    }
    xSemaphoreGive(chunkMutex_);
  }
  Serial.println("bill: dock connected");
}

void DockGatt::handleDisconnect(BLEServer* server) {
  (void)server;
  if (chunkMutex_ != nullptr &&
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
    connected_ = false;
    dockReceiveRequested_ = false;
    if (chunkOutCccd_ != nullptr) {
      chunkOutCccd_->setNotifications(false);
    }
    echoNextTransfer_ = false;
    ackPending_ = false;
    ackEncodedLength_ = 0;
    if (inboundSession_.active() && !inboundSession_.complete()) {
      resetInboundLocked(false);
    }
    preparedKind_ = 0;
    preparedDenominationCents_ = 0;
    if (outboundActive_) {
      finishOutboundLocked(false);
    }
    advertisingRestartPending_ = true;
    advertisingRestartRequestedAtMs_ = millis();
    xSemaphoreGive(chunkMutex_);
  }
  Serial.println("bill: dock disconnected; advertising restart scheduled");
}

void DockGatt::handleCommand(const std::string& command) {
  unsigned preparedKind = 0;
  unsigned long preparedCents = 0;
  if (sscanf(command.c_str(), "artifact:prepare:%u:%lu", &preparedKind,
             &preparedCents) == 2 &&
      (preparedKind == static_cast<unsigned>(rgbbill::chunk::Kind::kInvoice) ||
       preparedKind ==
           static_cast<unsigned>(rgbbill::chunk::Kind::kConsignment) ||
       preparedKind ==
           static_cast<unsigned>(rgbbill::chunk::Kind::kBearerNote)) &&
      preparedCents > 0) {
    if (chunkMutex_ != nullptr &&
        xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
      preparedKind_ = static_cast<uint8_t>(preparedKind);
      preparedDenominationCents_ = static_cast<uint32_t>(preparedCents);
      xSemaphoreGive(chunkMutex_);
    }
    Serial.printf("bill: prepared artifact kind=%u denomination=%lu cents\n",
                  preparedKind, preparedCents);
    return;
  }

  if (command == "diag:display:empty") {
    setState(0, "idle", "DISPLAY TEST");
    Serial.println("bill: diagnostic display set without an amount");
    return;
  }

  if (command == "diag:chunk:echo") {
    bool armed = false;
    if (chunkMutex_ != nullptr &&
        xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
      echoNextTransfer_ = !inboundReady_ && !outboundActive_;
      armed = echoNextTransfer_;
      xSemaphoreGive(chunkMutex_);
    }
    Serial.println(armed
                       ? "bill: next chunk transfer will be echoed"
                       : "bill: chunk echo not armed; staged transfer is busy");
    return;
  }

  if (command == "chunk:receive") {
    bool armed = false;
    if (chunkMutex_ != nullptr &&
        xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
      dockReceiveRequested_ = connected_ && !outboundActive_;
      armed = dockReceiveRequested_;
      xSemaphoreGive(chunkMutex_);
    }
    Serial.println(armed ? "bill: dock outbound requested"
                         : "bill: dock outbound request rejected");
    return;
  }

  if (command == "chunk:discard") {
    if (chunkMutex_ != nullptr &&
        xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
      if (outboundActive_) {
        finishOutboundLocked(false);
      }
      resetInboundLocked(true);
      if (storageBlockedByOrphan_) {
        storageReady_ = filesystemMounted_;
        storageBlockedByOrphan_ = !filesystemMounted_;
      }
      echoNextTransfer_ = false;
      preparedKind_ = 0;
      preparedDenominationCents_ = 0;
      tapDiscardRequested_ = true;
      xSemaphoreGive(chunkMutex_);
    }
    Serial.println("bill: staged chunk transfer discarded");
    return;
  }

  Serial.println("bill: rejected unknown dock command");
}

void DockGatt::handleChunk(const std::string& encoded) {
  const auto* bytes = reinterpret_cast<const uint8_t*>(encoded.data());
  const size_t length = encoded.size();
  rgbbill::chunk::FrameView frame;
  const rgbbill::chunk::Result decodeResult =
      rgbbill::chunk::decodeFrame(bytes, length, frame);
  if (decodeResult != rgbbill::chunk::Result::kOk) {
    if (length >= rgbbill::chunk::HEADER_SIZE) {
      queueAck(readLe32(bytes + 2), readLe16(bytes + 6), readLe16(bytes + 8),
               decodeResult);
    }
    Serial.println("bill: rejected malformed chunk frame");
    return;
  }

  if (frame.kind == rgbbill::chunk::Kind::kAck) {
    if (chunkMutex_ != nullptr &&
        xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
      handleAckLocked(frame);
      xSemaphoreGive(chunkMutex_);
    }
    return;
  }

  rgbbill::chunk::Result result = rgbbill::chunk::Result::kBusy;
  if (chunkMutex_ != nullptr &&
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) == pdTRUE) {
    const uint32_t frameCrc = readLe32(bytes + length - 4);
    const bool readyDuplicate =
        inboundReady_ && frame.sessionId == inboundSessionId_ &&
        static_cast<uint8_t>(frame.kind) == inboundKind_ &&
        frame.total == inboundSession_.total() &&
        frame.sequence + 1U == frame.total &&
        frameCrc == inboundLastFrameCrc_;
    const bool activeDuplicate =
        inboundSession_.active() && !inboundSession_.complete() &&
        frame.sessionId == inboundSession_.sessionId() &&
        frame.kind == inboundSession_.kind() &&
        frame.total == inboundSession_.total() &&
        frame.sequence + 1U == inboundSession_.nextSequence() &&
        frameCrc == inboundLastFrameCrc_;

    if (readyDuplicate || activeDuplicate) {
      result = rgbbill::chunk::Result::kOk;
      inboundLastActivityMs_ = millis();
    } else if (inboundReady_) {
      result = rgbbill::chunk::Result::kBusy;
    } else if (!storageReady_) {
      result = rgbbill::chunk::Result::kStorageFailure;
    } else {
      result = inboundSession_.accept(frame);
      if (result == rgbbill::chunk::Result::kOk) {
        if (frame.sequence == 0) {
          inboundDenominationCents_ =
              preparedKind_ == static_cast<uint8_t>(frame.kind)
                  ? preparedDenominationCents_
                  : 0;
          SPIFFS.remove(CHUNK_PART_PATH);
          inboundFile_ = SPIFFS.open(CHUNK_PART_PATH, FILE_WRITE);
        }
        if (!inboundFile_ ||
            inboundFile_.write(frame.payload, frame.payloadLength) !=
                frame.payloadLength) {
          result = rgbbill::chunk::Result::kStorageFailure;
          resetInboundLocked(false);
        } else {
          inboundLastActivityMs_ = millis();
          inboundLastFrameCrc_ = frameCrc;
          if (inboundSession_.complete()) {
            inboundFile_.flush();
            inboundFile_.close();
            SPIFFS.remove(CHUNK_READY_PATH);
            if (!SPIFFS.rename(CHUNK_PART_PATH, CHUNK_READY_PATH)) {
              result = rgbbill::chunk::Result::kStorageFailure;
              resetInboundLocked(false);
            } else {
              rgbbill::tap::Metadata metadata;
              metadata.state = rgbbill::tap::ReadyState::kReady;
              metadata.kind = static_cast<uint8_t>(frame.kind);
              metadata.sessionId = frame.sessionId;
              metadata.size = inboundSession_.bytesReceived();
              metadata.denominationCents = inboundDenominationCents_;
              uint8_t metadataEncoded[rgbbill::tap::METADATA_SIZE] = {};
              SPIFFS.remove(CHUNK_META_PATH);
              File metadataFile = SPIFFS.open(CHUNK_META_PATH, FILE_WRITE);
              const bool metadataStored = metadataFile &&
                  rgbbill::tap::encodeMetadata(
                      metadata, metadataEncoded, sizeof(metadataEncoded)) &&
                  metadataFile.write(metadataEncoded, sizeof(metadataEncoded)) ==
                      sizeof(metadataEncoded);
              if (metadataFile) {
                metadataFile.flush();
                metadataFile.close();
              }
              if (!metadataStored) {
                SPIFFS.remove(CHUNK_META_PATH);
                SPIFFS.remove(CHUNK_READY_PATH);
                result = rgbbill::chunk::Result::kStorageFailure;
                resetInboundLocked(false);
                xSemaphoreGive(chunkMutex_);
                queueAck(frame.sessionId, frame.sequence, frame.total, result);
                return;
              }
              inboundReady_ = true;
              inboundKind_ = static_cast<uint8_t>(frame.kind);
              inboundSessionId_ = frame.sessionId;
              inboundSize_ = inboundSession_.bytesReceived();
              preparedKind_ = 0;
              preparedDenominationCents_ = 0;
              Serial.printf(
                  "bill: chunk transfer complete kind=%u session=%08lx bytes=%u\n",
                  inboundKind_, static_cast<unsigned long>(inboundSessionId_),
                  static_cast<unsigned>(inboundSize_));
              if (echoNextTransfer_) {
                echoNextTransfer_ = false;
                if (!queueOutboundLocked(frame.kind, frame.sessionId,
                                         CHUNK_READY_PATH, true)) {
                  Serial.println("bill: could not queue chunk echo");
                }
              }
            }
          }
        }
      }
    }
    xSemaphoreGive(chunkMutex_);
  }

  queueAck(frame.sessionId, frame.sequence, frame.total, result);
}

void DockGatt::handleAckLocked(const rgbbill::chunk::FrameView& frame) {
  if (!outboundActive_ || !outboundWaitingAck_ ||
      frame.sessionId != outboundSessionId_ ||
      frame.sequence != outboundSequence_ || frame.total != outboundTotal_ ||
      frame.payloadLength != 1) {
    return;
  }

  const auto status = static_cast<rgbbill::chunk::Result>(frame.payload[0]);
  if (status != rgbbill::chunk::Result::kOk) {
    Serial.printf("bill: outbound chunk rejected status=%u\n", frame.payload[0]);
    finishOutboundLocked(false);
    return;
  }

  outboundWaitingAck_ = false;
  ++outboundSequence_;
  if (outboundSequence_ == outboundTotal_) {
    finishOutboundLocked(true);
  }
}

void DockGatt::queueAck(uint32_t sessionId, uint16_t sequence, uint16_t total,
                        rgbbill::chunk::Result result) {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return;
  }
  const uint8_t payload = static_cast<uint8_t>(result);
  if (!rgbbill::chunk::encodeFrame(
          rgbbill::chunk::Kind::kAck, sessionId, sequence, total, &payload, 1,
          ackEncoded_, sizeof(ackEncoded_), ackEncodedLength_)) {
    ackEncodedLength_ = 0;
    xSemaphoreGive(chunkMutex_);
    return;
  }
  ackPending_ = true;
  xSemaphoreGive(chunkMutex_);
}

bool DockGatt::queueOutboundTransfer(uint8_t kind, uint32_t sessionId,
                                     const char* path, bool removeAfterSend) {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  // A tap peer creates a bidirectional BLE connection and therefore also
  // triggers this device's GATT-server callbacks. Only a laptop dock enables
  // the dock CHUNK_OUT CCCD, so do not consume/queue the tap artifact merely
  // because another bill is connected.
  const bool dockSubscribed =
      connected_ &&
      (dockReceiveRequested_ ||
       (chunkOutCccd_ != nullptr && chunkOutCccd_->getNotifications()));
  const bool queued =
      dockSubscribed &&
      queueOutboundLocked(static_cast<rgbbill::chunk::Kind>(kind), sessionId,
                          path, removeAfterSend);
  xSemaphoreGive(chunkMutex_);
  return queued;
}

bool DockGatt::queueOutboundLocked(rgbbill::chunk::Kind kind,
                                   uint32_t sessionId, const char* path,
                                   bool removeAfterSend) {
  if (!storageReady_ || outboundActive_ || path == nullptr ||
      (kind != rgbbill::chunk::Kind::kInvoice &&
       kind != rgbbill::chunk::Kind::kConsignment &&
       kind != rgbbill::chunk::Kind::kBearerNote)) {
    return false;
  }

  File file = SPIFFS.open(path, FILE_READ);
  if (!file) {
    return false;
  }
  const size_t fileSize = file.size();
  const size_t frameCount =
      (fileSize + OUTBOUND_PAYLOAD_BYTES - 1U) / OUTBOUND_PAYLOAD_BYTES;
  if (fileSize == 0 || fileSize > MAX_TRANSFER_BYTES || frameCount > 0xFFFFU) {
    file.close();
    return false;
  }

  outboundFile_ = file;
  outboundActive_ = true;
  outboundWaitingAck_ = false;
  outboundRemoveAfterSend_ = removeAfterSend;
  outboundKind_ = kind;
  outboundSessionId_ = sessionId;
  outboundSequence_ = 0;
  outboundTotal_ = static_cast<uint16_t>(frameCount);
  outboundRetries_ = 0;
  strncpy(outboundPath_, path, sizeof(outboundPath_) - 1);
  outboundPath_[sizeof(outboundPath_) - 1] = '\0';
  return true;
}

void DockGatt::finishOutboundLocked(bool success) {
  if (outboundFile_) {
    outboundFile_.close();
  }
  const bool removeFile = success && outboundRemoveAfterSend_;
  if (removeFile) {
    SPIFFS.remove(outboundPath_);
    if (strcmp(outboundPath_, CHUNK_READY_PATH) == 0) {
      SPIFFS.remove(CHUNK_META_PATH);
      inboundReady_ = false;
      inboundSession_.reset();
      inboundSize_ = 0;
      inboundDenominationCents_ = 0;
    }
  }
  Serial.printf("bill: outbound chunk transfer %s\n",
                success ? "complete" : "aborted");
  outboundActive_ = false;
  outboundWaitingAck_ = false;
  outboundRemoveAfterSend_ = false;
  outboundEncodedLength_ = 0;
  outboundPath_[0] = '\0';
  dockReceiveRequested_ = false;
}

void DockGatt::resetInboundLocked(bool removeReady) {
  if (inboundFile_) {
    inboundFile_.close();
  }
  SPIFFS.remove(CHUNK_PART_PATH);
  inboundSession_.reset();
  inboundLastActivityMs_ = 0;
  inboundLastFrameCrc_ = 0;
  if (!inboundReady_) {
    inboundDenominationCents_ = 0;
  }
  if (removeReady) {
    SPIFFS.remove(CHUNK_READY_PATH);
    SPIFFS.remove(CHUNK_META_PATH);
    inboundReady_ = false;
    inboundKind_ = 0;
    inboundSessionId_ = 0;
    inboundSize_ = 0;
  }
}

bool DockGatt::takeInboundTransfer(InboundTransfer& transfer) {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  // A diagnostic echo already owns CHUNK_READY_PATH through outboundFile_.
  // Never hand the artifact off underneath the active dock sender.
  if (!inboundReady_ || outboundActive_) {
    xSemaphoreGive(chunkMutex_);
    return false;
  }
  transfer.kind = inboundKind_;
  transfer.sessionId = inboundSessionId_;
  transfer.size = inboundSize_;
  transfer.denominationCents = inboundDenominationCents_;
  strncpy(transfer.path, CHUNK_READY_PATH, sizeof(transfer.path) - 1);
  transfer.path[sizeof(transfer.path) - 1] = '\0';
  inboundReady_ = false;
  inboundSession_.reset();
  xSemaphoreGive(chunkMutex_);
  return true;
}

bool DockGatt::peekInboundTransfer(InboundTransfer& transfer) const {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  if (!inboundReady_) {
    xSemaphoreGive(chunkMutex_);
    return false;
  }
  transfer.kind = inboundKind_;
  transfer.sessionId = inboundSessionId_;
  transfer.size = inboundSize_;
  transfer.denominationCents = inboundDenominationCents_;
  strncpy(transfer.path, CHUNK_READY_PATH, sizeof(transfer.path) - 1);
  transfer.path[sizeof(transfer.path) - 1] = '\0';
  xSemaphoreGive(chunkMutex_);
  return true;
}

bool DockGatt::moveInboundTransfer(InboundTransfer& transfer,
                                   const char* destination) {
  if (destination == nullptr || destination[0] != '/' ||
      strlen(destination) >= sizeof(transfer.path) || chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  if (!inboundReady_ || outboundActive_) {
    xSemaphoreGive(chunkMutex_);
    return false;
  }

  SPIFFS.remove(destination);
  if (!SPIFFS.rename(CHUNK_READY_PATH, destination)) {
    xSemaphoreGive(chunkMutex_);
    return false;
  }
  SPIFFS.remove(CHUNK_META_PATH);

  transfer.kind = inboundKind_;
  transfer.sessionId = inboundSessionId_;
  transfer.size = inboundSize_;
  transfer.denominationCents = inboundDenominationCents_;
  strncpy(transfer.path, destination, sizeof(transfer.path) - 1);
  transfer.path[sizeof(transfer.path) - 1] = '\0';
  inboundReady_ = false;
  inboundSession_.reset();
  inboundKind_ = 0;
  inboundSessionId_ = 0;
  inboundSize_ = 0;
  inboundDenominationCents_ = 0;
  xSemaphoreGive(chunkMutex_);
  return true;
}

void DockGatt::setArtifactState(uint8_t direction, uint8_t kind,
                                uint32_t sessionId, size_t size,
                                uint32_t denominationCents) {
  const bool validDirection = direction <= 4;
  const bool validKind = kind == 0 || kind == 1 || kind == 2 || kind == 4;
  const bool validBearerState =
      (direction != ArtifactInventory::BEARER_HELD || kind == 4) &&
      (kind != 4 || direction == ArtifactInventory::BEARER_HELD ||
       direction == ArtifactInventory::RECOVERY_BLOCKED);
  if (artifactMetaCharacteristic_ == nullptr || !validDirection ||
      !validKind || !validBearerState) {
    return;
  }
  if (direction == artifactDirection_ && kind == artifactKind_ &&
      sessionId == artifactSessionId_ && size == artifactSize_ &&
      denominationCents == artifactDenominationCents_) {
    return;
  }
  artifactDirection_ = direction;
  artifactKind_ = kind;
  artifactSessionId_ = sessionId;
  artifactSize_ = size;
  artifactDenominationCents_ = denominationCents;

  uint8_t encoded[16] = {ArtifactInventory::VERSION, direction, kind, 0};
  writeLe32(encoded + 4, sessionId);
  writeLe32(encoded + 8, static_cast<uint32_t>(size));
  writeLe32(encoded + 12, denominationCents);
  artifactMetaCharacteristic_->setValue(encoded, sizeof(encoded));

  const char* status = direction == 0   ? "idle"
                       : direction == 1 ? "tap"
                       : direction == 2 ? "pending"
                       : direction == 4 ? "tap"
                                        : "error";
  const char* display = "EMPTY";
  if (direction == 1 && kind == 1) display = "INVOICE TAP";
  if (direction == 2 && kind == 1) display = "INVOICE DOCK";
  if (direction == 1 && kind == 2) display = "PROOF TAP";
  if (direction == 2 && kind == 2) display = "PROOF DOCK";
  if (direction == 3) display = kind == 0 ? "CLEAR STORAGE" : "RECOVERY";
  if (direction == ArtifactInventory::BEARER_HELD &&
      kind == static_cast<uint8_t>(rgbbill::chunk::Kind::kBearerNote)) {
    display = "NOTE";
  }
  setState(direction == 0 || (direction == 3 && kind == 0)
               ? 0
               : denominationCents,
           status, display);
}

bool DockGatt::takeTapDiscardRequest() {
  if (chunkMutex_ == nullptr ||
      xSemaphoreTake(chunkMutex_, portMAX_DELAY) != pdTRUE) {
    return false;
  }
  const bool requested = tapDiscardRequested_;
  tapDiscardRequested_ = false;
  xSemaphoreGive(chunkMutex_);
  return requested;
}

void DockGatt::setState(uint32_t cents, const char* status,
                        const char* displayStatus) {
  char denomination[16];
  snprintf(denomination, sizeof(denomination), "%lu",
           static_cast<unsigned long>(cents));
  denominationCharacteristic_->setValue(denomination);
  statusCharacteristic_->setValue(status);
  denominationCharacteristic_->notify();
  statusCharacteristic_->notify();

  portENTER_CRITICAL(&displayMux_);
  displayCents_ = cents;
  strncpy(displayStatus_, displayStatus, sizeof(displayStatus_) - 1);
  displayStatus_[sizeof(displayStatus_) - 1] = '\0';
  displayDirty_ = true;
  portEXIT_CRITICAL(&displayMux_);
}
