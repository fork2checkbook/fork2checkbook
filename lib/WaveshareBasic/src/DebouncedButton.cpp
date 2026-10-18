#include "DebouncedButton.h"

DebouncedButton::DebouncedButton(bool active_high,
                                 uint8_t required_stable_samples)
    : active_high_(active_high),
      required_stable_samples_(required_stable_samples == 0
                                   ? 1
                                   : required_stable_samples) {}

void DebouncedButton::reset(int raw_level) {
  candidate_level_ = raw_level;
  stable_level_ = -1;
  stable_samples_ = raw_level < 0 ? 0 : 1;
  phase_ = Phase::WaitForInitialRelease;
}

bool DebouncedButton::isPressed(int level) const {
  return active_high_ ? level == 1 : level == 0;
}

bool DebouncedButton::stablePressed() const {
  return stable_level_ >= 0 && isPressed(stable_level_);
}

int DebouncedButton::stableLevel() const { return stable_level_; }

DebouncedButton::Event DebouncedButton::update(int raw_level) {
  Event event;
  if (raw_level != 0 && raw_level != 1) {
    reset();
    return event;
  }

  if (raw_level != candidate_level_) {
    candidate_level_ = raw_level;
    stable_samples_ = 1;
  } else if (stable_samples_ < required_stable_samples_) {
    ++stable_samples_;
  }

  if (stable_samples_ < required_stable_samples_ ||
      stable_level_ == candidate_level_) {
    event.pressed = stablePressed();
    return event;
  }

  const bool was_pressed = stablePressed();
  stable_level_ = candidate_level_;
  const bool now_pressed = stablePressed();
  event.level_changed = true;
  event.pressed = now_pressed;
  event.pressed_edge = now_pressed && !was_pressed;
  event.released_edge = !now_pressed && was_pressed;

  switch (phase_) {
    case Phase::WaitForInitialRelease:
      if (!now_pressed) {
        phase_ = Phase::WaitForPress;
      }
      break;
    case Phase::WaitForPress:
      if (now_pressed) {
        phase_ = Phase::WaitForFinalRelease;
      }
      break;
    case Phase::WaitForFinalRelease:
      if (!now_pressed) {
        phase_ = Phase::WaitForPress;
        event.completed_press = true;
      }
      break;
  }

  return event;
}
