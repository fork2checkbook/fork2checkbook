#pragma once

#include <stdint.h>

class TouchContactGate {
 public:
  bool update(uint8_t finger_count) {
    if (finger_count == 0) {
      contact_active_ = false;
      return false;
    }
    if (contact_active_) {
      return false;
    }
    contact_active_ = true;
    return true;
  }

  bool contactActive() const { return contact_active_; }

 private:
  bool contact_active_ = false;
};
