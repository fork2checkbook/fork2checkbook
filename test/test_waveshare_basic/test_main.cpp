#include <unity.h>

#include "ArtifactRecoveryPolicy.h"
#include "DebouncedButton.h"
#include "BillPeerSelector.h"
#include "ScreenTouchGate.h"
#include "TouchContactGate.h"

void setUp() {}
void tearDown() {}

void test_active_high_requires_complete_debounced_cycle() {
  DebouncedButton button(true, 3);

  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);

  TEST_ASSERT_FALSE(button.update(1).pressed_edge);
  TEST_ASSERT_FALSE(button.update(1).pressed_edge);
  auto pressed = button.update(1);
  TEST_ASSERT_TRUE(pressed.pressed_edge);
  TEST_ASSERT_TRUE(pressed.pressed);

  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
  auto released = button.update(0);
  TEST_ASSERT_TRUE(released.released_edge);
  TEST_ASSERT_TRUE(released.completed_press);
  TEST_ASSERT_FALSE(released.pressed);
}

void test_bounce_does_not_create_a_press() {
  DebouncedButton button(true, 3);
  button.update(0);
  button.update(0);
  button.update(0);

  TEST_ASSERT_FALSE(button.update(1).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(1).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
  TEST_ASSERT_FALSE(button.update(0).completed_press);
}

void test_held_on_start_cannot_pass_until_released() {
  DebouncedButton button(true, 3);
  button.update(1);
  button.update(1);
  button.update(1);
  TEST_ASSERT_FALSE(button.update(1).completed_press);

  button.update(0);
  button.update(0);
  TEST_ASSERT_FALSE(button.update(0).completed_press);

  button.update(1);
  button.update(1);
  button.update(1);
  button.update(0);
  button.update(0);
  TEST_ASSERT_TRUE(button.update(0).completed_press);
}

void test_touch_contact_counts_only_first_sample_until_release() {
  TouchContactGate gate;
  TEST_ASSERT_TRUE(gate.update(1));
  TEST_ASSERT_TRUE(gate.contactActive());
  TEST_ASSERT_FALSE(gate.update(1));
  TEST_ASSERT_FALSE(gate.update(2));
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_FALSE(gate.contactActive());
  TEST_ASSERT_TRUE(gate.update(1));
}

void test_touch_release_without_press_is_inert() {
  TouchContactGate gate;
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_TRUE(gate.update(1));
}

void test_production_touch_requires_release_after_boot() {
  ScreenTouchGate gate;
  TEST_ASSERT_FALSE(gate.update(1));
  TEST_ASSERT_FALSE(gate.update(1));
  TEST_ASSERT_FALSE(gate.releaseObserved());
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_TRUE(gate.releaseObserved());
  TEST_ASSERT_TRUE(gate.update(1));
  TEST_ASSERT_FALSE(gate.update(1));
}

void test_production_touch_emits_one_edge_per_contact_cycle() {
  ScreenTouchGate gate;
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_TRUE(gate.update(1));
  TEST_ASSERT_FALSE(gate.update(2));
  TEST_ASSERT_FALSE(gate.update(0));
  TEST_ASSERT_TRUE(gate.update(1));
}

void test_bill_peer_selector_rejects_untrusted_advertisements() {
  BillPeerSelector selector;

  TEST_ASSERT_FALSE(selector.consider("Other", true, -20, "AA:00"));
  TEST_ASSERT_FALSE(selector.consider("RgbBill-Fake", false, -10, "AA:01"));
  TEST_ASSERT_FALSE(selector.hasPeer());
  TEST_ASSERT_EQUAL_UINT(0, selector.matchCount());
}

void test_bill_peer_selector_chooses_strongest_authorized_peer() {
  BillPeerSelector selector;

  TEST_ASSERT_TRUE(selector.consider("RgbBill", true, -62,
                                     "30:AE:A4:07:2D:2E"));
  TEST_ASSERT_TRUE(selector.consider("RgbBill-TTGO", true, -48,
                                     "30:AE:A4:4C:B1:6A"));
  TEST_ASSERT_FALSE(selector.consider("RgbBill", true, -70,
                                      "30:AE:A4:07:2D:2E"));

  TEST_ASSERT_TRUE(selector.hasPeer());
  TEST_ASSERT_EQUAL_UINT(3, selector.matchCount());
  TEST_ASSERT_EQUAL_INT(-48, selector.rssi());
  TEST_ASSERT_EQUAL_STRING("RgbBill-TTGO", selector.name());
  TEST_ASSERT_EQUAL_STRING("30:AE:A4:4C:B1:6A", selector.address());
}

void test_artifact_boot_is_empty_only_when_no_files_exist() {
  using rgbbill::storage::ArtifactBootState;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ArtifactBootState::kEmpty),
      static_cast<int>(rgbbill::storage::classifyArtifactBootState(
          false, false, false, false)));
}

void test_artifact_boot_restores_only_matching_payload_and_metadata() {
  using rgbbill::storage::ArtifactBootState;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(ArtifactBootState::kReady),
      static_cast<int>(rgbbill::storage::classifyArtifactBootState(
          false, true, true, true)));
}

void test_artifact_boot_blocks_every_partial_or_inconsistent_state() {
  using rgbbill::storage::ArtifactBootState;
  const bool states[][4] = {
      {true, false, false, false}, {false, true, false, false},
      {false, false, true, false}, {false, true, true, false},
      {true, true, true, true},
  };
  for (const auto& state : states) {
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(ArtifactBootState::kRecoveryBlocked),
        static_cast<int>(rgbbill::storage::classifyArtifactBootState(
            state[0], state[1], state[2], state[3])));
  }
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_active_high_requires_complete_debounced_cycle);
  RUN_TEST(test_bounce_does_not_create_a_press);
  RUN_TEST(test_held_on_start_cannot_pass_until_released);
  RUN_TEST(test_touch_contact_counts_only_first_sample_until_release);
  RUN_TEST(test_touch_release_without_press_is_inert);
  RUN_TEST(test_production_touch_requires_release_after_boot);
  RUN_TEST(test_production_touch_emits_one_edge_per_contact_cycle);
  RUN_TEST(test_bill_peer_selector_rejects_untrusted_advertisements);
  RUN_TEST(test_bill_peer_selector_chooses_strongest_authorized_peer);
  RUN_TEST(test_artifact_boot_is_empty_only_when_no_files_exist);
  RUN_TEST(test_artifact_boot_restores_only_matching_payload_and_metadata);
  RUN_TEST(test_artifact_boot_blocks_every_partial_or_inconsistent_state);
  return UNITY_END();
}
