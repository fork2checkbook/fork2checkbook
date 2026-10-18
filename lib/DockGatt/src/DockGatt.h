#pragma once

#include <BLECharacteristic.h>
#include <BLE2902.h>
#include <BLEServer.h>
#include <ChunkProtocol.h>
#include <FS.h>
#include <cstddef>
#include <cstdint>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

class DockGatt {
 public:
  struct InboundTransfer {
    uint8_t kind = 0;
    uint32_t sessionId = 0;
    size_t size = 0;
    uint32_t denominationCents = 0;
    char path[32] = {};
  };

  DockGatt();
  void begin(bool storageMounted);
  void poll();
  BLEServer* server() const { return server_; }
  bool storageAvailable() const;
  bool takeDisplayUpdate(uint32_t& cents, char* status, size_t statusSize);
  bool takeInboundTransfer(InboundTransfer& transfer);
  bool peekInboundTransfer(InboundTransfer& transfer) const;
  bool moveInboundTransfer(InboundTransfer& transfer,
                           const char* destination);
  bool takeTapDiscardRequest();
  bool queueOutboundTransfer(uint8_t kind, uint32_t sessionId,
                             const char* path, bool removeAfterSend = false);
  void setArtifactState(uint8_t direction, uint8_t kind, uint32_t sessionId,
                        size_t size, uint32_t denominationCents);

 private:
  class ServerCallbacks : public BLEServerCallbacks {
   public:
    explicit ServerCallbacks(DockGatt& owner);
    void onConnect(BLEServer* server) override;
    void onDisconnect(BLEServer* server) override;

   private:
    DockGatt& owner_;
  };

  class CommandCallbacks : public BLECharacteristicCallbacks {
   public:
    explicit CommandCallbacks(DockGatt& owner);
    void onWrite(BLECharacteristic* characteristic) override;

   private:
    DockGatt& owner_;
  };

  class ChunkCallbacks : public BLECharacteristicCallbacks {
   public:
    explicit ChunkCallbacks(DockGatt& owner);
    void onWrite(BLECharacteristic* characteristic) override;

   private:
    DockGatt& owner_;
  };

  void handleConnect();
  void handleDisconnect(BLEServer* server);
  void handleCommand(const std::string& command);
  void handleChunk(const std::string& encoded);
  void handleAckLocked(const rgbbill::chunk::FrameView& frame);
  void queueAck(uint32_t sessionId, uint16_t sequence, uint16_t total,
                rgbbill::chunk::Result result);
  bool queueOutboundLocked(rgbbill::chunk::Kind kind, uint32_t sessionId,
                           const char* path, bool removeAfterSend);
  void finishOutboundLocked(bool success);
  void resetInboundLocked(bool removeReady);
  void setState(uint32_t cents, const char* status, const char* displayStatus);

  BLEServer* server_ = nullptr;
  BLECharacteristic* statusCharacteristic_ = nullptr;
  BLECharacteristic* denominationCharacteristic_ = nullptr;
  BLECharacteristic* artifactMetaCharacteristic_ = nullptr;
  BLECharacteristic* chunkOutCharacteristic_ = nullptr;
  BLE2902* chunkOutCccd_ = nullptr;
  ServerCallbacks callbacks_;
  CommandCallbacks commandCallbacks_;
  ChunkCallbacks chunkCallbacks_;

  SemaphoreHandle_t chunkMutex_ = nullptr;
  bool filesystemMounted_ = false;
  bool storageReady_ = false;
  bool storageBlockedByOrphan_ = false;
  bool connected_ = false;
  bool advertisingRestartPending_ = false;
  uint32_t advertisingRestartRequestedAtMs_ = 0;
  rgbbill::chunk::ReceiveSession inboundSession_;
  File inboundFile_;
  bool inboundReady_ = false;
  uint8_t inboundKind_ = 0;
  uint32_t inboundSessionId_ = 0;
  size_t inboundSize_ = 0;
  uint32_t inboundDenominationCents_ = 0;
  uint32_t inboundLastActivityMs_ = 0;
  uint32_t inboundLastFrameCrc_ = 0;
  bool echoNextTransfer_ = false;
  bool tapDiscardRequested_ = false;
  bool dockReceiveRequested_ = false;
  uint8_t preparedKind_ = 0;
  uint32_t preparedDenominationCents_ = 0;

  bool ackPending_ = false;
  uint8_t ackEncoded_[rgbbill::chunk::MAX_FRAME_SIZE] = {};
  size_t ackEncodedLength_ = 0;

  File outboundFile_;
  bool outboundActive_ = false;
  bool outboundWaitingAck_ = false;
  bool outboundRemoveAfterSend_ = false;
  rgbbill::chunk::Kind outboundKind_ = rgbbill::chunk::Kind::kInvoice;
  uint32_t outboundSessionId_ = 0;
  uint16_t outboundSequence_ = 0;
  uint16_t outboundTotal_ = 0;
  uint8_t outboundRetries_ = 0;
  uint32_t outboundSentAtMs_ = 0;
  char outboundPath_[32] = {};
  uint8_t outboundEncoded_[rgbbill::chunk::MAX_FRAME_SIZE] = {};
  size_t outboundEncodedLength_ = 0;

  portMUX_TYPE displayMux_ = portMUX_INITIALIZER_UNLOCKED;
  uint32_t displayCents_ = 0;
  char displayStatus_[16] = {};
  bool displayDirty_ = false;
  uint8_t artifactDirection_ = 0xFF;
  uint8_t artifactKind_ = 0;
  uint32_t artifactSessionId_ = 0;
  size_t artifactSize_ = 0;
  uint32_t artifactDenominationCents_ = 0;
};
