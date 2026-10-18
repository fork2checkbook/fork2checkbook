#pragma once

#include <stdint.h>

class DebouncedButton {
 public:
  struct Event {
    bool level_changed = false;
    bool pressed = false;
    bool pressed_edge = false;
    bool released_edge = false;
    bool completed_press = false;
  };

  explicit DebouncedButton(bool active_high = true,
                           uint8_t required_stable_samples = 3);

  Event update(int raw_level);
  void reset(int raw_level = -1);
  bool stablePressed() const;
  int stableLevel() const;

 private:
  enum class Phase : uint8_t {
    WaitForInitialRelease,
    WaitForPress,
    WaitForFinalRelease,
  };

  bool isPressed(int level) const;

  bool active_high_;
  uint8_t required_stable_samples_;
  int candidate_level_ = -1;
  int stable_level_ = -1;
  uint8_t stable_samples_ = 0;
  Phase phase_ = Phase::WaitForInitialRelease;
};
