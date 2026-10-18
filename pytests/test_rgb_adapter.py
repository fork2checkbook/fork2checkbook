from pathlib import Path

import pytest

from web.rgb.adapter import (
    AdapterError,
    ConfirmationRequired,
    RgbBillAdapter,
    RoleViolation,
)
from web.rgb.backend import BackendSendResult, ParsedInvoice, ReceiveIntent
from web.rgb.config import BillRole, RgbSettings, SettingsError
from web.rgb.lock import WalletInUseError, WalletProcessLock


class FakeBackend:
    def __init__(self, tmp_path: Path, balance: int = 500) -> None:
        self.balance = balance
        self.consignment = tmp_path / "consignment_out"
        self.consignment.write_bytes(b"rgb-consignment")
        self.receive_calls = []
        self.send_calls = []
        self.import_paths = []
        self.closed = False

    def spendable_balance(self, asset_id):
        assert asset_id == "rgb:usdt"
        return self.balance

    def witness_receive(self, asset_id, expiration_timestamp, min_confirmations):
        self.receive_calls.append(
            (asset_id, expiration_timestamp, min_confirmations)
        )
        return ReceiveIntent("invoice", "recipient", expiration_timestamp, 4)

    def parse_invoice(self, invoice):
        return ParsedInvoice(
            invoice=invoice,
            recipient_id="recipient",
            asset_id="rgb:usdt",
            expiration_timestamp=1900,
            transport_endpoints=(),
            unknown_query_params={},
        )

    def send_donation(self, **kwargs):
        self.send_calls.append(kwargs)
        return BackendSendResult("txid", 7, self.consignment)

    def import_consignment(self, consignment_path):
        self.import_paths.append(consignment_path)
        return {4: "waitingconfirmations"}

    def close(self):
        self.closed = True


def settings(tmp_path: Path, role: BillRole) -> RgbSettings:
    return RgbSettings(
        role=role,
        data_dir=tmp_path / f"bill-{role.value.lower()}",
        peer_data_dir=tmp_path / f"bill-{'b' if role is BillRole.A else 'a'}",
        inbox_dir=tmp_path / f"inbox-{role.value.lower()}",
        electrum_url="https://mutinynet.com/api",
    )


def env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "BILL_ROLE": "A",
        "RGB_DATA_DIR_A": str(tmp_path / "bill-a"),
        "RGB_DATA_DIR_B": str(tmp_path / "bill-b"),
        "RGB_OOB_CONSIGNMENT": "1",
        "ELECTRUM_URL": "https://esplora.signet.kaleidoswap.com",
    }
    values.update(overrides)
    return values


def test_settings_select_only_active_role_and_keep_peer_separate(tmp_path):
    configured = RgbSettings.from_env(
        env(tmp_path, BILL_ROLE="B", RGB_WALLET_LABEL_B="Recipient wallet")
    )

    assert configured.role is BillRole.B
    assert configured.data_dir == (tmp_path / "bill-b").resolve()
    assert configured.peer_data_dir == (tmp_path / "bill-a").resolve()
    assert configured.wallet_label == "Recipient wallet"


def test_settings_default_wallet_label_is_role_specific(tmp_path):
    configured = RgbSettings.from_env(env(tmp_path, BILL_ROLE="A"))

    assert configured.wallet_label == "Wallet A"
    assert configured.fee_rate == 1
    assert configured.witness_amount_sat == 1_000


def test_settings_reject_witness_below_supported_minimum(tmp_path):
    with pytest.raises(SettingsError, match="at least 1000 sats"):
        RgbSettings.from_env(env(tmp_path, RGB_WITNESS_AMOUNT_SAT="999"))


def test_settings_reject_unsafe_wallet_label(tmp_path):
    with pytest.raises(SettingsError, match="RGB_WALLET_LABEL_A"):
        RgbSettings.from_env(env(tmp_path, RGB_WALLET_LABEL_A="unsafe\nlabel"))


def test_settings_reject_overlapping_wallet_directories(tmp_path):
    with pytest.raises(SettingsError, match="must not overlap"):
        RgbSettings.from_env(
            env(
                tmp_path,
                RGB_DATA_DIR_A=str(tmp_path / "wallet"),
                RGB_DATA_DIR_B=str(tmp_path / "wallet" / "bill-b"),
            )
        )


def test_settings_reject_non_mutinynet_indexer(tmp_path):
    with pytest.raises(SettingsError, match="Mutinynet"):
        RgbSettings.from_env(
            env(tmp_path, ELECTRUM_URL="ssl://blockstream.info:50002")
        )


def test_settings_allow_public_mutinynet_esplora_fallback(tmp_path):
    configured = RgbSettings.from_env(
        env(tmp_path, ELECTRUM_URL="https://mutinynet.com/api")
    )

    assert configured.electrum_url == "https://mutinynet.com/api"


def test_settings_require_role_scoped_credential_paths_for_live_open(tmp_path):
    with pytest.raises(SettingsError, match="RGB_MASTER_FINGERPRINT_A"):
        RgbSettings.from_env(env(tmp_path), require_credentials=True)


def test_wallet_process_lock_excludes_second_owner(tmp_path):
    first = WalletProcessLock(tmp_path / "wallet")
    second = WalletProcessLock(tmp_path / "wallet")
    first.acquire()
    try:
        with pytest.raises(WalletInUseError):
            second.acquire()
    finally:
        first.release()


def test_bill_b_creates_receive_intent_but_cannot_plan_spend(tmp_path):
    backend = FakeBackend(tmp_path)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.B), backend, clock=lambda: 1000)

    receive = adapter.create_receive_intent(asset_id="rgb:usdt")

    assert receive.recipient_id == "recipient"
    assert backend.receive_calls == [("rgb:usdt", 1900, 1)]
    with pytest.raises(RoleViolation):
        adapter.plan_whole_balance_transfer(
            asset_id="rgb:usdt", recipient_id="recipient"
        )


def test_bill_a_spend_requires_one_use_confirmation_and_sends_whole_balance(tmp_path):
    backend = FakeBackend(tmp_path, balance=500)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend, clock=lambda: 1000)
    plan = adapter.plan_whole_balance_transfer(
        asset_id="rgb:usdt", recipient_id="recipient"
    )

    with pytest.raises(ConfirmationRequired):
        adapter.execute_confirmed_transfer(plan.plan_id)
    assert backend.send_calls == []

    adapter.confirm_transfer(plan.plan_id)
    artifact = adapter.execute_confirmed_transfer(plan.plan_id)

    assert backend.send_calls[0]["amount"] == 500
    assert backend.send_calls[0]["witness_amount_sat"] == 1000
    assert artifact.txid == "txid"
    assert artifact.size == len(b"rgb-consignment")
    with pytest.raises(ConfirmationRequired):
        adapter.execute_confirmed_transfer(plan.plan_id)
    assert len(backend.send_calls) == 1


def test_bill_a_can_plan_a_partial_test_transfer(tmp_path):
    backend = FakeBackend(tmp_path, balance=500)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend, clock=lambda: 1000)

    plan = adapter.plan_transfer(
        asset_id="rgb:usdt",
        recipient_id="recipient",
        amount=200,
    )

    assert plan.amount == 200
    assert plan.balance_before == 500
    adapter.confirm_transfer(plan.plan_id)
    adapter.execute_confirmed_transfer(plan.plan_id)
    assert backend.send_calls[0]["amount"] == 200


def test_bill_a_plans_from_exact_transport_free_tap_invoice(tmp_path):
    backend = FakeBackend(tmp_path, balance=500)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend, clock=lambda: 1000)

    parsed, plan = adapter.plan_transfer_from_tap_invoice(
        b"invoice",
        expected_asset_id="rgb:usdt",
        amount=200,
    )

    assert parsed.recipient_id == "recipient"
    assert plan.recipient_id == "recipient"
    assert plan.amount == 200
    assert plan.expiration_timestamp == 1900


def test_tap_invoice_rejects_transport_endpoint_before_planning(tmp_path):
    class ProxyInvoiceBackend(FakeBackend):
        def parse_invoice(self, invoice):
            parsed = super().parse_invoice(invoice)
            return ParsedInvoice(
                **{
                    **parsed.__dict__,
                    "transport_endpoints": ("rpcs://forbidden.example",),
                }
            )

    adapter = RgbBillAdapter(
        settings(tmp_path, BillRole.A),
        ProxyInvoiceBackend(tmp_path),
        clock=lambda: 1000,
    )

    with pytest.raises(AdapterError, match="empty transport endpoints"):
        adapter.plan_transfer_from_tap_invoice(
            b"invoice", expected_asset_id="rgb:usdt", amount=None
        )


def test_partial_transfer_cannot_exceed_spendable_balance(tmp_path):
    backend = FakeBackend(tmp_path, balance=500)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend)

    with pytest.raises(AdapterError, match="exceeds"):
        adapter.plan_transfer(
            asset_id="rgb:usdt",
            recipient_id="recipient",
            amount=501,
        )


def test_failed_send_is_not_implicitly_retryable(tmp_path):
    class FailingBackend(FakeBackend):
        def send_donation(self, **kwargs):
            self.send_calls.append(kwargs)
            raise RuntimeError("unknown broadcast outcome")

    backend = FailingBackend(tmp_path)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend, clock=lambda: 1000)
    plan = adapter.plan_whole_balance_transfer(
        asset_id="rgb:usdt", recipient_id="recipient"
    )
    adapter.confirm_transfer(plan.plan_id)

    with pytest.raises(RuntimeError, match="unknown broadcast outcome"):
        adapter.execute_confirmed_transfer(plan.plan_id)
    with pytest.raises(ConfirmationRequired):
        adapter.execute_confirmed_transfer(plan.plan_id)


def test_changed_balance_consumes_confirmation_and_requires_new_plan(tmp_path):
    backend = FakeBackend(tmp_path, balance=500)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.A), backend, clock=lambda: 1000)
    plan = adapter.plan_whole_balance_transfer(
        asset_id="rgb:usdt", recipient_id="recipient"
    )
    adapter.confirm_transfer(plan.plan_id)
    backend.balance = 600

    with pytest.raises(ConfirmationRequired, match="balance changed"):
        adapter.execute_confirmed_transfer(plan.plan_id)
    assert backend.send_calls == []


def test_bill_b_imports_exact_oob_bytes_from_isolated_inbox(tmp_path):
    backend = FakeBackend(tmp_path)
    adapter = RgbBillAdapter(settings(tmp_path, BillRole.B), backend)

    result = adapter.import_consignment(b"over-ble-only")

    imported = backend.import_paths[0]
    assert imported.parent == settings(tmp_path, BillRole.B).inbox_dir
    assert imported.read_bytes() == b"over-ble-only"
    assert result == {4: "waitingconfirmations"}


def test_consignment_size_limit_is_enforced_before_wallet_call(tmp_path):
    backend = FakeBackend(tmp_path)
    configured = settings(tmp_path, BillRole.B)
    configured = RgbSettings(
        **{
            **configured.__dict__,
            "max_consignment_bytes": 3,
        }
    )
    adapter = RgbBillAdapter(configured, backend)

    with pytest.raises(AdapterError, match="exceeds"):
        adapter.import_consignment(b"four")
    assert backend.import_paths == []
