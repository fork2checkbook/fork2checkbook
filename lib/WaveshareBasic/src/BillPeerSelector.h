#pragma once

#include <cstddef>
#include <cstring>

class BillPeerSelector {
 public:
  bool consider(const char* name, bool advertises_dock_service, int rssi,
                const char* address) {
    if (name == nullptr || address == nullptr ||
        !advertises_dock_service || std::strncmp(name, "RgbBill", 7) != 0) {
      return false;
    }

    ++match_count_;
    if (has_peer_ && rssi <= rssi_) {
      return false;
    }

    has_peer_ = true;
    rssi_ = rssi;
    copy(name_, sizeof(name_), name);
    copy(address_, sizeof(address_), address);
    return true;
  }

  bool hasPeer() const { return has_peer_; }
  unsigned matchCount() const { return match_count_; }
  int rssi() const { return rssi_; }
  const char* name() const { return name_; }
  const char* address() const { return address_; }

 private:
  static void copy(char* destination, std::size_t capacity,
                   const char* source) {
    if (capacity == 0) {
      return;
    }
    std::strncpy(destination, source, capacity - 1);
    destination[capacity - 1] = '\0';
  }

  bool has_peer_ = false;
  unsigned match_count_ = 0;
  int rssi_ = -127;
  char name_[32] = {};
  char address_[18] = {};
};
