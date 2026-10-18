from collections import deque

import pytest

from scripts.offline_bearer_fallback import (
    DemoError,
    HardwareState,
    exact_confirmation,
    require_clean,
    wait_for_offline_tap,
)


def state(direction="empty", kind=None, denomination=0, size=0):
    return HardwareState(
        dock_url="http://dock",
        address="AA:BB",
        identity="bill-test",
        status="idle",
        denomination=denomination,
        direction=direction,
        kind=kind,
        size=size,
        denomination_cents=denomination,
    )


class FakeApi:
    def __init__(self, states):
        self.base_url = "http://fake"
        self.states = deque(states)

    def post(self, path, payload=None):
        assert path == "/api/device/connect"
        current = self.states[0] if len(self.states) == 1 else self.states.popleft()
        return {
            "ok": True,
            "connection": {
                "connected": True,
                "address": current.address,
                "identity": current.identity,
                "status": current.status,
                "denomination": current.denomination,
                "artifact": {
                    "direction": current.direction,
                    "kind": current.kind,
                    "size": current.size,
                    "session_id": 1 if current.size else 0,
                    "denomination_cents": current.denomination_cents,
                },
            },
        }


def test_clean_requires_all_empty_fields():
    require_clean(state(), "bill")
    with pytest.raises(DemoError, match="not clean"):
        require_clean(state("bearer-held", 4, 500, 603), "bill")
    with pytest.raises(DemoError, match="recovery-blocked"):
        require_clean(state("blocked"), "bill")


def test_wait_for_tap_observes_whole_value_move():
    issuer = FakeApi(
        [state("bearer-held", 4, 500, 603), state("empty")]
    )
    redeemer = FakeApi(
        [state("empty"), state("bearer-held", 4, 500, 603)]
    )
    clock = iter((0.0, 0.0, 1.0, 2.0, 3.0, 4.0))
    source, destination = wait_for_offline_tap(
        issuer,
        redeemer,
        timeout=10,
        quiet_window=1,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock),
        emit=lambda _message: None,
    )
    assert source.empty
    assert destination.bearer_held


def test_wait_for_tap_fails_closed_if_note_disappears():
    clock = iter((0.0, 0.0, 1.0, 2.0))
    with pytest.raises(DemoError, match="both bills report empty"):
        wait_for_offline_tap(
            FakeApi([state("empty")]),
            FakeApi([state("empty")]),
            timeout=10,
            quiet_window=1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(clock),
            emit=lambda _message: None,
        )


def test_confirmation_must_be_exact_and_is_never_invented():
    pending = {
        "id": "plan-123",
        "action": "bearer-redemption",
        "cents": 500,
        "confirmation_phrase": "CONFIRM plan-123",
    }
    assert exact_confirmation(
        pending, input_fn=lambda _prompt: "CONFIRM plan-123"
    ) == ("plan-123", "CONFIRM plan-123")
    with pytest.raises(DemoError, match="did not match"):
        exact_confirmation(pending, input_fn=lambda _prompt: "yes")


def test_confirmation_rejects_malformed_server_boundary():
    with pytest.raises(DemoError, match="invalid exact-confirmation"):
        exact_confirmation(
            {
                "id": "plan-123",
                "action": "bearer-redemption",
                "cents": 500,
                "confirmation_phrase": "CONFIRM another-plan",
            },
            input_fn=lambda _prompt: "CONFIRM another-plan",
        )
