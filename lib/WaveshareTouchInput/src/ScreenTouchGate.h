#pragma once

#include <stdint.h>

// Converts sampled finger counts into one event per complete contact cycle.
// A release must be observed after boot before the first press is accepted.
class ScreenTouchGate {
 public:
  bool update(uint8_t fingerCount) {
    if (fingerCount == 0) {
      contactActive_ = false;
      releaseObserved_ = true;
      return false;
    }
    if (!releaseObserved_ || contactActive_) {
      return false;
    }
    contactActive_ = true;
    return true;
  }

  bool contactActive() const { return contactActive_; }
  bool releaseObserved() const { return releaseObserved_; }

 private:
  bool contactActive_ = false;
  bool releaseObserved_ = false;
};
