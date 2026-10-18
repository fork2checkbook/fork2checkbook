from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from web.rgb.bearer_capsule import decode_bearer_capsule
from web.rgb.config import BillRole, RgbSettings
from web.rgb.note_live import (
    LIVE_NOTE_VERSION,
    LiveFundingPlan,
    LiveCapacityPlan,
    LiveNoteConfirmationRequired,
    LiveNoteError,
    LiveNotePaths,
    LiveRedemptionPlan,
    _atomic_json,
    _note_settings,
    _validate_destination,
    _validate_sender,
    _write_private,
    abandon_live_unbroadcast_note,
    execute_live_funding_capacity_plan,
    execute_live_funding_btc_topup_plan,
    execute_live_redemption_plan,
    execute_live_redemption_capacity_plan,
    execute_live_funding_plan,
    load_funding_plan,
    prepare_live_bearer_capsule,
    prepare_live_funding_capacity_plan,
    prepare_live_funding_btc_topup_plan,
    prepare_live_redemption_plan,
    prepare_live_redemption_capacity_plan,
    prepare_live_redemption_after_capacity,
    recover_live_redemption,
    reconcile_live_funding_btc_topup_prebroadcast_rejection,
    reconcile_live_redemption_prebroadcast_rejection,
    refresh_live_redemption_settlement,
    refresh_live_redemption_capacity,
    redacted_plan,
    refresh_live_funding,
    refresh_live_funding_btc_topup,
    refresh_live_funding_capacity,
    resume_live_funding_plan,
    renew_live_funding_plan,
    renew_live_redemption_plan,
)


def _sender(tmp_path: Path) -> RgbSettings:
    return RgbSettings(
        role=BillRole.A,
        data_dir=tmp_path / "wallet-a",
        peer_data_dir=tmp_path / "wallet-b",
        inbox_dir=tmp_path / "inbox-a",
        electrum_url="https://mutinynet.com/api",
        master_fingerprint="fingerprint-a",
        mnemonic_file=tmp_path / "a.mnemonic",
    )


def _destination(tmp_path: Path) -> RgbSettings:
    return RgbSettings(
        role=BillRole.B,
        data_dir=tmp_path / "wallet-b",
        peer_data_dir=tmp_path / "wallet-a",
        inbox_dir=tmp_path / "inbox-b",
        electrum_url="https://mutinynet.com/api",
        master_fingerprint="fingerprint-b",
        mnemonic_file=tmp_path / "b.mnemonic",
    )


def _plan() -> LiveFundingPlan:
    return LiveFundingPlan(
        version=LIVE_NOTE_VERSION,
        plan_id="plan-1",
        network="mutinynet",
        operation="fund-disposable-note",
        note_id="note-1",
        planned_timestamp=1_000,
        source_wallet="wallet-a",
        asset_id="rgb:usdt",
        amount_base_units=5_000_000,
        denomination_cents=500,
        source_spendable_before=50_000_000,
        recipient_id="recipient",
        recipient_invoice_sha256="a" * 64,
        witness_amount_sat=1_000,
        fee_rate_sat_vb=2,
        min_confirmations=1,
        expiration_timestamp=2_000,
        donation=True,
        transport_endpoints=(),
        note_btc_address="tb1ptest",
        note_vanilla_spendable_before=0,
        note_colored_spendable_before=0,
        measured_prior_send_fee_sat=395,
        measured_redemption_minimum_sat=1_395,
        recommended_redemption_vanilla_sat=2_000,
    )


def test_live_plan_is_exact_transport_free_and_no_broadcast():
    payload = redacted_plan(_plan())

    assert payload["network"] == "mutinynet"
    assert payload["amount_base_units"] == 5_000_000
    assert payload["transport_endpoints"] == ()
    assert payload["confirmation_required"] == "CONFIRM plan-1"
    assert payload["status"] == "PLANNED"
    assert "mnemonic" not in json.dumps(payload).lower()


def test_note_settings_isolate_note_wallet_from_sender(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    settings = _note_settings(sender, paths, fingerprint="note-fingerprint")

    assert settings.role is BillRole.B
    assert settings.data_dir == paths.wallet_dir
    assert settings.data_dir != sender.data_dir
    assert settings.peer_data_dir == sender.data_dir
    assert settings.mnemonic_file == paths.mnemonic_file
    assert settings.electrum_url == "https://mutinynet.com/api"


def test_issuing_dock_funds_note_btc_reserve_with_separate_confirmation(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    funding = redacted_plan(_plan())
    _atomic_json(paths.plan_file, funding)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AWAITING_FUNDING",
            "master_fingerprint": "note-fingerprint",
        },
    )

    tx = SimpleNamespace(
        txid="a" * 64,
        confirmation_time=None,
    )

    class SourceWallet:
        def __init__(self):
            self.sent = 0

        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=10_000),
                colored=SimpleNamespace(spendable=1_000),
            )

        def list_unspents(self, *_args):
            return []

        def send_btc(self, _online, address, amount, fee_rate, skip_sync):
            assert (address, amount, fee_rate, skip_sync) == (
                "tb1ptest",
                2_000,
                1,
                False,
            )
            assert json.loads(paths.funding_btc_topup_plan_file.read_text())[
                "status"
            ] == "CONFIRMATION_CONSUMED"
            self.sent += 1
            return tx.txid

        def list_transactions(self, *_args):
            return [tx] if self.sent else []

    class Backend:
        def __init__(self, wallet):
            self._wallet = wallet
            self._online = object()

        def refresh(self):
            return {}

        def close(self):
            pass

    source = Backend(SourceWallet())
    plan, manifest = prepare_live_funding_btc_topup_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_100,
        id_factory=lambda: "topup-plan-1",
        sender_backend_opener=lambda _settings: source,
    )
    assert plan.topup_amount_sat == 2_000
    assert manifest["state"] == "BTC_TOPUP_PLANNED"

    with pytest.raises(LiveNoteConfirmationRequired):
        execute_live_funding_btc_topup_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
            plan_id=plan.plan_id,
            confirmation_phrase="yes",
            clock=lambda: 1_200,
            sender_backend_opener=lambda _settings: source,
        )
    assert source._wallet.sent == 0

    result = execute_live_funding_btc_topup_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        plan_id=plan.plan_id,
        confirmation_phrase=plan.confirmation_phrase,
        clock=lambda: 1_200,
        sender_backend_opener=lambda _settings: source,
    )
    assert result["txid"] == "a" * 64
    assert source._wallet.sent == 1
    with pytest.raises(LiveNoteConfirmationRequired):
        execute_live_funding_btc_topup_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
            plan_id=plan.plan_id,
            confirmation_phrase=plan.confirmation_phrase,
            clock=lambda: 1_225,
            sender_backend_opener=lambda _settings: source,
        )
    assert source._wallet.sent == 1

    class NoteWallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

        def list_unspents(self, *_args):
            return []

    note = Backend(NoteWallet())
    waiting = refresh_live_funding_btc_topup(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_250,
        sender_backend_opener=lambda _settings: source,
        note_backend_opener=lambda _settings: note,
    )
    assert waiting["confirmed"] is False

    tx.confirmation_time = SimpleNamespace(height=123, timestamp=1_300)
    confirmed = refresh_live_funding_btc_topup(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_350,
        sender_backend_opener=lambda _settings: source,
        note_backend_opener=lambda _settings: note,
    )
    assert confirmed["confirmed"] is True
    assert confirmed["note_vanilla_spendable_sat"] == 2_000
    assert json.loads(paths.manifest_file.read_text())["state"] == (
        "AWAITING_FUNDING"
    )


def test_either_dock_profile_may_issue_or_redeem(tmp_path):
    _validate_sender(_destination(tmp_path))
    _validate_destination(_sender(tmp_path))


def test_ambiguous_btc_topup_can_only_replan_after_unchanged_wallet_proof(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    _atomic_json(paths.plan_file, redacted_plan(_plan()))
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AWAITING_FUNDING",
            "master_fingerprint": "note-fingerprint",
        },
    )

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=10_000),
                colored=SimpleNamespace(spendable=1_000),
            )

        def list_unspents(self, *_args):
            return []

        def send_btc(self, *_args):
            raise RuntimeError("indexer connection reset")

    class EmptyNoteWallet(Wallet):
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=0),
                colored=SimpleNamespace(spendable=0),
            )

    class Backend:
        def __init__(self, wallet):
            self._wallet = wallet
            self._online = object()

        def refresh(self):
            return {}

        def close(self):
            pass

    source = Backend(Wallet())
    note = Backend(EmptyNoteWallet())
    plan, _manifest = prepare_live_funding_btc_topup_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_100,
        id_factory=lambda: "ambiguous-topup",
        sender_backend_opener=lambda _settings: source,
    )
    with pytest.raises(LiveNoteError, match="ambiguous"):
        execute_live_funding_btc_topup_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
            plan_id=plan.plan_id,
            confirmation_phrase=plan.confirmation_phrase,
            clock=lambda: 1_200,
            sender_backend_opener=lambda _settings: source,
        )

    result = reconcile_live_funding_btc_topup_prebroadcast_rejection(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_300,
        sender_backend_opener=lambda _settings: source,
        note_backend_opener=lambda _settings: note,
    )

    assert result["status"] == "PRE_BROADCAST_REJECTED"
    assert result["broadcast"] is False
    assert not paths.funding_btc_topup_plan_file.exists()
    archive = paths.funding_btc_topup_plan_file.parent / (
        "funding-btc-topup-ambiguous-topup.json"
    )
    assert json.loads(archive.read_text())["status"] == "PRE_BROADCAST_REJECTED"
    assert json.loads(paths.manifest_file.read_text())["state"] == "AWAITING_FUNDING"

    fresh, manifest = prepare_live_funding_btc_topup_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_400,
        id_factory=lambda: "fresh-topup",
        sender_backend_opener=lambda _settings: source,
    )
    assert fresh.plan_id == "fresh-topup"
    assert manifest["state"] == "BTC_TOPUP_PLANNED"


def test_private_and_atomic_records_are_mode_0600(tmp_path):
    private = tmp_path / "secret" / "mnemonic"
    record = tmp_path / "plans" / "funding.json"
    _write_private(private, b"never print this")
    _atomic_json(record, {"status": "PLANNED"})

    assert private.stat().st_mode & 0o777 == 0o600
    assert record.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        _write_private(private, b"overwrite")


def test_failed_unbroadcast_note_can_be_archived_but_funded_note_cannot(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-failed")
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-failed",
            "state": "PREPARATION_RECOVERY_REQUIRED",
            "issuer_master_fingerprint": sender.master_fingerprint,
        },
    )

    result = abandon_live_unbroadcast_note(
        sender,
        vault_root=paths.root,
        note_id="note-failed",
        clock=lambda: 1_234,
    )

    assert result == {
        "phase": "live-note-abandoned",
        "note_id": "note-failed",
        "state": "ABANDONED",
        "abandoned_at": 1_234,
        "broadcast": False,
        "preserved": True,
    }
    manifest = json.loads(paths.manifest_file.read_text())
    assert manifest["state"] == "ABANDONED"
    assert manifest["broadcast"] is False

    manifest["state"] = "ACTIVE"
    _atomic_json(paths.manifest_file, manifest)
    with pytest.raises(LiveNoteError, match="never-planned"):
        abandon_live_unbroadcast_note(
            sender,
            vault_root=paths.root,
            note_id="note-failed",
        )


def test_load_plan_rejects_wrong_network(tmp_path):
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    payload = redacted_plan(_plan())
    payload["network"] = "bitcoin"
    _atomic_json(paths.plan_file, payload)

    with pytest.raises(LiveNoteError, match="version or network"):
        load_funding_plan(paths.root, "note-1")


def test_resume_refuses_note_with_existing_plan(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "PREPARATION_RECOVERY_REQUIRED",
        },
    )
    _atomic_json(paths.plan_file, redacted_plan(_plan()))

    with pytest.raises(LiveNoteError, match="already exists"):
        resume_live_funding_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
        )


def test_live_confirmation_mismatch_cannot_open_or_consume_wallet(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    plan = redacted_plan(_plan())
    _atomic_json(paths.plan_file, plan)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AWAITING_FUNDING",
        },
    )

    def unexpected_open(_settings):
        raise AssertionError("wallet must not open before exact confirmation")

    with pytest.raises(LiveNoteConfirmationRequired, match="exact confirmation"):
        execute_live_funding_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
            plan_id="plan-1",
            confirmation_phrase="yes",
            clock=lambda: 1_100,
            sender_backend_opener=unexpected_open,
            note_backend_opener=unexpected_open,
        )
    assert json.loads(paths.plan_file.read_text())["status"] == "PLANNED"


def test_funding_allocation_rejection_is_recorded_as_prebroadcast(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    invoice = "rgb-invoice"
    plan = redacted_plan(_plan())
    plan.update(
        {
            "recipient_invoice_sha256": hashlib.sha256(invoice.encode()).hexdigest(),
            "note_vanilla_spendable_before": 2_000,
        }
    )
    _atomic_json(paths.plan_file, plan)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AWAITING_FUNDING",
        },
    )
    _write_private(paths.invoice_file, invoice.encode())
    parsed = SimpleNamespace(
        recipient_id="recipient",
        asset_id="rgb:usdt",
        transport_endpoints=(),
        unknown_query_params={},
    )

    class InsufficientAllocationSlots(Exception):
        pass

    class Backend:
        def parse_invoice(self, _invoice):
            return parsed

        def spendable_balance(self, _asset):
            return 50_000_000

        def send_donation(self, **_kwargs):
            raise InsufficientAllocationSlots()

        def close(self):
            pass

    with pytest.raises(LiveNoteError, match="free RGB allocation slot"):
        execute_live_funding_plan(
            sender,
            vault_root=paths.root,
            note_id="note-1",
            plan_id="plan-1",
            confirmation_phrase="CONFIRM plan-1",
            clock=lambda: 1_100,
            sender_backend_opener=lambda _settings: Backend(),
        )

    assert json.loads(paths.plan_file.read_text())["status"] == (
        "PRE_BROADCAST_REJECTED"
    )
    assert json.loads(paths.manifest_file.read_text())["state"] == (
        "PRE_BROADCAST_REJECTED"
    )


def test_ambiguous_old_funding_reconciles_and_creates_separate_capacity_plan(
    tmp_path,
):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    funding = redacted_plan(_plan())
    funding.update(
        {
            "status": "AMBIGUOUS",
            "consumed_at": 1_100,
            "last_error_type": "InsufficientAllocationSlots",
            "last_error": "",
        }
    )
    _atomic_json(paths.plan_file, funding)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AMBIGUOUS",
        },
    )
    occupied = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True),
        rgb_allocations=[SimpleNamespace()],
    )
    vanilla = SimpleNamespace(
        utxo=SimpleNamespace(colorable=False, exists=True),
        rgb_allocations=[],
    )

    class Wallet:
        def get_btc_balance(self, online, skip_sync):
            assert online == "sender-online"
            assert skip_sync is False
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=20_000),
                colored=SimpleNamespace(spendable=1_000),
            )

        def list_unspents(self, online, settled_only, skip_sync):
            assert (online, settled_only, skip_sync) == (
                "sender-online",
                False,
                False,
            )
            return [occupied, vanilla]

        def list_transfers(self, _asset):
            return [
                SimpleNamespace(
                    kind=SimpleNamespace(name="SEND"), recipient_id="older-recipient"
                )
            ]

    class Backend:
        def __init__(self):
            self._wallet = Wallet()
            self._online = "sender-online"

        def refresh(self):
            return {}

        def spendable_balance(self, _asset):
            return 50_000_000

        def close(self):
            pass

    plan, manifest = prepare_live_funding_capacity_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_200,
        id_factory=lambda: "funding-capacity-1",
        sender_backend_opener=lambda _settings: Backend(),
    )

    assert plan.operation == "create-funding-source-colorable-utxo"
    assert plan.plan_id == "funding-capacity-1"
    assert plan.utxo_size_sat == 1_000
    assert manifest["state"] == "FUNDING_CAPACITY_PLANNED"
    assert json.loads(paths.plan_file.read_text())["status"] == (
        "PRE_BROADCAST_REJECTED"
    )
    assert json.loads(paths.funding_capacity_plan_file.read_text())["status"] == (
        "PLANNED"
    )


def test_expired_funding_capacity_plan_is_superseded_without_broadcast(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    funding = redacted_plan(_plan())
    funding["status"] = "PRE_BROADCAST_REJECTED"
    _atomic_json(paths.plan_file, funding)
    expired = LiveCapacityPlan(
        version=LIVE_NOTE_VERSION,
        plan_id="expired-capacity",
        network="mutinynet",
        operation="create-funding-source-colorable-utxo",
        note_id="note-1",
        planned_timestamp=1_000,
        source_wallet="dock-wallet:fingerprint-a",
        vanilla_spendable_before=20_000,
        colored_spendable_before=1_000,
        colorable_utxo_count_before=1,
        free_colorable_utxo_count_before=0,
        utxo_count=1,
        utxo_size_sat=8_000,
        fee_rate_sat_vb=2,
        expiration_timestamp=1_100,
    )
    _atomic_json(
        paths.funding_capacity_plan_file,
        {**redacted_plan(expired), "status": "PLANNED"},
    )
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "FUNDING_CAPACITY_PLANNED",
        },
    )
    occupied = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True),
        rgb_allocations=[SimpleNamespace()],
    )
    vanilla = SimpleNamespace(
        utxo=SimpleNamespace(colorable=False, exists=True),
        rgb_allocations=[],
    )

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=20_000),
                colored=SimpleNamespace(spendable=1_000),
            )

        def list_unspents(self, *_args):
            return [occupied, vanilla]

    class Backend:
        _wallet = Wallet()
        _online = "sender-online"

        def refresh(self):
            return {}

        def close(self):
            pass

    plan, manifest = prepare_live_funding_capacity_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_200,
        id_factory=lambda: "fresh-capacity",
        sender_backend_opener=lambda _settings: Backend(),
    )

    assert plan.plan_id == "fresh-capacity"
    assert manifest["state"] == "FUNDING_CAPACITY_PLANNED"
    assert json.loads(paths.funding_capacity_plan_file.read_text())["status"] == (
        "PLANNED"
    )
    archived = paths.funding_capacity_plan_file.parent / (
        "funding-capacity-expired-capacity.json"
    )
    assert json.loads(archived.read_text())["status"] == "SUPERSEDED"


def test_funding_capacity_has_its_own_confirmation_and_settlement_refresh(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    capacity = LiveCapacityPlan(
        version=LIVE_NOTE_VERSION,
        plan_id="funding-capacity-1",
        network="mutinynet",
        operation="create-funding-source-colorable-utxo",
        note_id="note-1",
        planned_timestamp=1_200,
        source_wallet="dock-wallet:fingerprint-a",
        vanilla_spendable_before=20_000,
        colored_spendable_before=1_000,
        colorable_utxo_count_before=1,
        free_colorable_utxo_count_before=0,
        utxo_count=1,
        utxo_size_sat=8_000,
        fee_rate_sat_vb=2,
        expiration_timestamp=2_100,
    )
    _atomic_json(
        paths.funding_capacity_plan_file,
        {**redacted_plan(capacity), "status": "PLANNED"},
    )
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "FUNDING_CAPACITY_PLANNED",
        },
    )
    occupied = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True),
        rgb_allocations=[SimpleNamespace()],
    )
    vanilla = SimpleNamespace(
        utxo=SimpleNamespace(colorable=False, exists=True),
        rgb_allocations=[],
    )
    free = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True),
        rgb_allocations=[],
    )
    before_tx = SimpleNamespace(txid="before")
    capacity_tx = SimpleNamespace(
        txid="capacity-txid",
        transaction_type=SimpleNamespace(name="CREATE_UTXOS"),
        received=8_000,
        sent=8_300,
        fee=300,
        confirmation_time=None,
    )

    class Wallet:
        def __init__(self):
            self.created = False
            self.confirmed = False

        def get_btc_balance(self, online, skip_sync):
            assert online == "sender-online"
            assert skip_sync is False
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=11_700 if self.created else 20_000),
                colored=SimpleNamespace(spendable=9_000 if self.created else 1_000),
            )

        def list_unspents(self, _online, _settled_only, _skip_sync):
            return [occupied, vanilla, free] if self.confirmed else [occupied, vanilla]

        def list_transactions(self, _online, _skip_sync):
            return [before_tx, capacity_tx] if self.created else [before_tx]

        def create_utxos(self, *_args):
            self.created = True
            return 1

    class Backend:
        def __init__(self):
            self._wallet = Wallet()
            self._online = "sender-online"

        def refresh(self):
            return {}

        def close(self):
            pass

    backend = Backend()
    result = execute_live_funding_capacity_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        plan_id="funding-capacity-1",
        confirmation_phrase="CONFIRM funding-capacity-1",
        clock=lambda: 1_300,
        sender_backend_opener=lambda _settings: backend,
    )
    assert result["txid"] == "capacity-txid"
    assert json.loads(paths.funding_capacity_plan_file.read_text())["status"] == (
        "BROADCAST_RECORDED"
    )

    capacity_tx.confirmation_time = SimpleNamespace(height=123, timestamp=1_350)
    backend._wallet.confirmed = True
    refreshed = refresh_live_funding_capacity(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_400,
        sender_backend_opener=lambda _settings: backend,
    )
    assert refreshed["confirmed"] is True
    assert refreshed["free_colorable_utxo_count"] == 1
    assert json.loads(paths.manifest_file.read_text())["state"] == (
        "FUNDING_CAPACITY_READY"
    )


def test_funding_renewal_syncs_existing_note_wallet_before_preflight(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    previous = redacted_plan(_plan())
    _atomic_json(paths.plan_file, previous)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "AWAITING_FUNDING",
            "created_at": 1_000,
            "asset_id": "rgb:usdt",
            "amount_base_units": 5_000_000,
            "master_fingerprint": "note-fingerprint",
        },
    )
    _write_private(paths.invoice_file, b"rgb-invoice")
    parsed = SimpleNamespace(
        recipient_id="recipient",
        asset_id="rgb:usdt",
        expiration_timestamp=10_000,
        transport_endpoints=(),
        unknown_query_params={},
    )

    class NoteWallet:
        def get_btc_balance(self, online, skip_sync):
            assert online == "note-online"
            assert skip_sync is False
            return SimpleNamespace(
                vanilla=SimpleNamespace(
                    settled=104_000, future=104_000, spendable=104_000
                ),
                colored=SimpleNamespace(settled=0, future=0, spendable=0),
            )

        def get_address(self):
            return "tb1pcurrent"

        def list_unspents(self, online, settled_only, skip_sync):
            assert (online, settled_only, skip_sync) == (
                "note-online",
                False,
                False,
            )
            return [SimpleNamespace()]

    class NoteBackend:
        def __init__(self):
            self._wallet = NoteWallet()
            self._online = "note-online"

        def parse_invoice(self, invoice):
            assert invoice == "rgb-invoice"
            return parsed

        def close(self):
            pass

    class SenderBackend:
        def parse_invoice(self, invoice):
            assert invoice == "rgb-invoice"
            return parsed

        def spendable_balance(self, asset_id):
            assert asset_id == "rgb:usdt"
            return 55_000_000

        def close(self):
            pass

    plan, manifest = renew_live_funding_plan(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 1_100,
        id_factory=lambda: "plan-2",
        note_backend_opener=lambda _settings: NoteBackend(),
        sender_backend_opener=lambda _settings: SenderBackend(),
    )

    assert plan.note_vanilla_spendable_before == 104_000
    assert plan.note_btc_address == "tb1pcurrent"
    assert manifest["state"] == "AWAITING_FUNDING"
    archived = paths.plan_file.parent / "funding-plan-1.json"
    assert json.loads(archived.read_text())["status"] == "SUPERSEDED"
    assert json.loads(paths.plan_file.read_text())["plan_id"] == "plan-2"


def test_refresh_activates_note_only_with_exact_settled_balance(tmp_path):
    sender = _sender(tmp_path)
    paths = LiveNotePaths.create(tmp_path / ".note-vault", "note-1")
    plan = redacted_plan(_plan())
    plan.update(
        {
            "status": "FUNDING_IMPORTED",
            "broadcast": {"txid": "funding-txid"},
        }
    )
    _atomic_json(paths.plan_file, plan)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": "note-1",
            "state": "FUNDING_IMPORTED",
            "master_fingerprint": "note-fingerprint",
        },
    )

    class Wallet:
        def get_asset_balance(self, asset_id):
            assert asset_id == "rgb:usdt"
            return SimpleNamespace(
                settled=5_000_000,
                future=5_000_000,
                spendable=5_000_000,
            )

        def list_transfers(self, asset_id):
            assert asset_id == "rgb:usdt"
            return [
                SimpleNamespace(
                    batch_transfer_idx=1,
                        kind=SimpleNamespace(name="RECEIVE_WITNESS"),
                        status=SimpleNamespace(name="SETTLED"),
                    txid="funding-txid",
                )
            ]

    class Backend:
        def __init__(self):
            self._wallet = Wallet()
            self.closed = False

        def refresh(self):
            return {1: "settled"}

        def close(self):
            self.closed = True

    backend = Backend()
    result = refresh_live_funding(
        sender,
        vault_root=paths.root,
        note_id="note-1",
        clock=lambda: 3_000,
        note_backend_opener=lambda _settings: backend,
    )

    assert backend.closed
    assert result["settled"] is True
    assert result["state"] == "ACTIVE"
    assert result["balance"] == {
        "settled": 5_000_000,
        "future": 5_000_000,
        "spendable": 5_000_000,
    }
    assert json.loads(paths.plan_file.read_text())["status"] == "FUNDING_SETTLED"
    assert json.loads(paths.manifest_file.read_text())["state"] == "ACTIVE"


def test_prepare_capsule_is_secret_redacted_and_idempotent(tmp_path):
    # Profile B may issue; the private issuer binding, not the A/B label, owns it.
    sender = _destination(tmp_path)
    note_id = "758186be-791d-4c8c-92f5-a8c4194ac856"
    paths = LiveNotePaths.create(tmp_path / ".note-vault", note_id)
    txid = "aa" * 32
    consignment_sha256 = "bb" * 32
    plan = redacted_plan(_plan())
    plan.update(
        {
            "note_id": note_id,
            "status": "FUNDING_SETTLED",
            "broadcast": {
                "txid": txid,
                "consignment_sha256": consignment_sha256,
            },
        }
    )
    balance = {
        "settled": 5_000_000,
        "future": 5_000_000,
        "spendable": 5_000_000,
    }
    _atomic_json(paths.plan_file, plan)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": note_id,
            "state": "ACTIVE",
            "issuer_master_fingerprint": "fingerprint-b",
            "funding_settlement": {
                "txid": txid,
                "transfer_status": "settled",
                "balance": balance,
            },
        },
    )
    secret = bytes.fromhex("11" * 32)
    salt = bytes.fromhex("22" * 32)

    first = prepare_live_bearer_capsule(
        sender,
        vault_root=paths.root,
        note_id=note_id,
        clock=lambda: 3_000,
        secret_factory=lambda size: secret if size == 32 else b"",
        salt_factory=lambda size: salt if size == 32 else b"",
    )

    assert first["created"] is True
    assert first["note_state"] == "ACTIVE"
    assert first["claim_secret_redacted"] is True
    assert paths.capsule_file.stat().st_mode & 0o777 == 0o600
    assert decode_bearer_capsule(paths.capsule_file.read_bytes()).claim_secret == secret
    manifest_text = paths.manifest_file.read_text()
    assert secret.hex() not in manifest_text
    assert b"mnemonic" not in paths.capsule_file.read_bytes()

    def unexpected_secret(_size):
        raise AssertionError("an existing capsule must not mint a second claim secret")

    second = prepare_live_bearer_capsule(
        sender,
        vault_root=paths.root,
        note_id=note_id,
        secret_factory=unexpected_secret,
        salt_factory=unexpected_secret,
    )
    assert second["created"] is False
    assert second["artifact_sha256"] == first["artifact_sha256"]

    with pytest.raises(LiveNoteError, match="different funding dock wallet"):
        prepare_live_bearer_capsule(
            _sender(tmp_path),
            vault_root=paths.root,
            note_id=note_id,
        )


def _active_capsule(tmp_path: Path) -> tuple[LiveNotePaths, dict[str, object], bytes]:
    sender = _sender(tmp_path)
    note_id = "758186be-791d-4c8c-92f5-a8c4194ac856"
    paths = LiveNotePaths.create(tmp_path / ".note-vault", note_id)
    funding = redacted_plan(_plan())
    funding.update(
        {
            "note_id": note_id,
            "status": "FUNDING_SETTLED",
            "broadcast": {
                "txid": "aa" * 32,
                "consignment_sha256": "bb" * 32,
            },
        }
    )
    _atomic_json(paths.plan_file, funding)
    _atomic_json(
        paths.manifest_file,
        {
            "version": LIVE_NOTE_VERSION,
            "network": "mutinynet",
            "note_id": note_id,
            "state": "ACTIVE",
            "master_fingerprint": "note-fingerprint",
            "funding_settlement": {
                "txid": "aa" * 32,
                "transfer_status": "settled",
                "balance": {
                    "settled": 5_000_000,
                    "future": 5_000_000,
                    "spendable": 5_000_000,
                },
            },
        },
    )
    evidence = prepare_live_bearer_capsule(
        sender,
        vault_root=paths.root,
        note_id=note_id,
        clock=lambda: 3_000,
        secret_factory=lambda size: bytes.fromhex("11" * size),
        salt_factory=lambda size: bytes.fromhex("22" * size),
    )
    return paths, evidence, paths.capsule_file.read_bytes()


def test_prepare_live_redemption_plan_is_exact_idempotent_and_no_broadcast(tmp_path):
    paths, capsule_evidence, encoded = _active_capsule(tmp_path)
    received = tmp_path / "received" / "bearer.rgbn"
    _write_private(received, encoded)
    destination = _destination(tmp_path)

    parsed = SimpleNamespace(
        recipient_id="wallet-b-recipient",
        asset_id="rgb:usdt",
        expiration_timestamp=10_000,
        transport_endpoints=(),
        unknown_query_params={},
    )

    class DestinationBackend:
        def __init__(self):
            self.closed = False

        def witness_receive(self, asset_id, expiration, min_confirmations):
            assert asset_id == "rgb:usdt"
            assert expiration == 90_400
            assert min_confirmations == 1
            return SimpleNamespace(invoice="rgb-invoice-b")

        def parse_invoice(self, invoice):
            assert invoice == "rgb-invoice-b"
            return parsed

        def close(self):
            self.closed = True

    class NoteWallet:
        def get_btc_balance(self, _online, _skip_sync):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class NoteBackend:
        def __init__(self):
            self._wallet = NoteWallet()
            self.closed = False

        def refresh(self):
            return {}

        def parse_invoice(self, invoice):
            assert invoice == "rgb-invoice-b"
            return parsed

        def spendable_balance(self, asset_id):
            assert asset_id == "rgb:usdt"
            return 5_000_000

        def close(self):
            self.closed = True

    destination_backend = DestinationBackend()
    note_backend = NoteBackend()
    plan, manifest = prepare_live_redemption_plan(
        destination,
        vault_root=paths.root,
        capsule_path=received,
        expected_capsule_sha256=str(capsule_evidence["artifact_sha256"]),
        clock=lambda: 4_000,
        id_factory=lambda: "redemption-plan-1",
        destination_backend_opener=lambda _settings: destination_backend,
        note_backend_opener=lambda _settings: note_backend,
    )

    assert isinstance(plan, LiveRedemptionPlan)
    assert plan.plan_id == "redemption-plan-1"
    assert plan.amount_base_units == 5_000_000
    assert plan.capsule_sha256 == capsule_evidence["artifact_sha256"]
    assert plan.transport_endpoints == ()
    assert plan.confirmation_phrase == "CONFIRM redemption-plan-1"
    assert manifest["state"] == "CLAIM_PLANNED"
    assert destination_backend.closed and note_backend.closed
    assert paths.redemption_invoice_file.stat().st_mode & 0o777 == 0o600
    assert paths.redemption_plan_file.stat().st_mode & 0o777 == 0o600
    plan_text = paths.redemption_plan_file.read_text()
    assert bytes.fromhex("11" * 32).hex() not in plan_text
    assert json.loads(plan_text)["status"] == "PLANNED"

    def unexpected_open(_settings):
        raise AssertionError("idempotent plan lookup must not reopen either wallet")

    same, _manifest = prepare_live_redemption_plan(
        destination,
        vault_root=paths.root,
        capsule_path=received,
        expected_capsule_sha256=str(capsule_evidence["artifact_sha256"]),
        destination_backend_opener=unexpected_open,
        note_backend_opener=unexpected_open,
    )
    assert same == plan


def test_prepare_live_redemption_rejects_wrong_digest_before_wallet_open(tmp_path):
    paths, _capsule_evidence, encoded = _active_capsule(tmp_path)
    received = tmp_path / "received" / "bearer.rgbn"
    _write_private(received, encoded)

    def unexpected_open(_settings):
        raise AssertionError("wrong digest must fail before opening a wallet")

    with pytest.raises(LiveNoteError, match="SHA-256 does not match"):
        prepare_live_redemption_plan(
            _destination(tmp_path),
            vault_root=paths.root,
            capsule_path=received,
            expected_capsule_sha256="00" * 32,
            destination_backend_opener=unexpected_open,
            note_backend_opener=unexpected_open,
        )


def _prepared_redemption(tmp_path: Path):
    paths, capsule_evidence, encoded = _active_capsule(tmp_path)
    received = tmp_path / "received" / "bearer.rgbn"
    _write_private(received, encoded)
    parsed = SimpleNamespace(
        recipient_id="wallet-b-recipient",
        asset_id="rgb:usdt",
        expiration_timestamp=10_000,
        transport_endpoints=(),
        unknown_query_params={},
    )

    class DestinationOffline:
        def witness_receive(self, *_args):
            return SimpleNamespace(invoice="rgb-invoice-b")

        def parse_invoice(self, _invoice):
            return parsed

        def close(self):
            pass

    class NoteWallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class NoteOffline:
        def __init__(self):
            self._wallet = NoteWallet()

        def refresh(self):
            return {}

        def parse_invoice(self, _invoice):
            return parsed

        def spendable_balance(self, _asset_id):
            return 5_000_000

        def close(self):
            pass

    plan, _manifest = prepare_live_redemption_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        capsule_path=received,
        expected_capsule_sha256=str(capsule_evidence["artifact_sha256"]),
        clock=lambda: 4_000,
        id_factory=lambda: "redemption-plan-1",
        destination_backend_opener=lambda _settings: DestinationOffline(),
        note_backend_opener=lambda _settings: NoteOffline(),
    )
    return paths, received, capsule_evidence, parsed, plan


def test_redemption_confirmation_is_one_shot_and_journaled_before_send(tmp_path):
    paths, _received, _evidence, parsed, plan = _prepared_redemption(tmp_path)
    consignment = tmp_path / "rgb-lib-consignment"
    consignment.write_bytes(b"exact-redemption-consignment")
    calls = {"send": 0, "import": 0}

    class NoteWallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class NoteBackend:
        def __init__(self):
            self._wallet = NoteWallet()

        def parse_invoice(self, _invoice):
            return parsed

        def refresh(self):
            return {}

        def spendable_balance(self, _asset_id):
            return 5_000_000

        def send_donation(self, **kwargs):
            calls["send"] += 1
            assert kwargs["amount"] == 5_000_000
            assert json.loads(paths.redemption_plan_file.read_text())["status"] == (
                "CONFIRMATION_CONSUMED"
            )
            assert json.loads(paths.manifest_file.read_text())["state"] == "CLAIMING"
            events = [
                json.loads(line)
                for line in paths.redemption_journal_file.read_text().splitlines()
            ]
            assert events[-1]["event"] == "CONFIRMATION_CONSUMED"
            return SimpleNamespace(
                txid="redemption-txid",
                batch_transfer_idx=9,
                consignment_path=consignment,
            )

        def close(self):
            pass

    class DestinationBackend:
        def import_consignment(self, path):
            calls["import"] += 1
            assert path == paths.redemption_consignment_file
            return {3: "waiting_confirmations"}

        def close(self):
            pass

    result = execute_live_redemption_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        plan_id=plan.plan_id,
        confirmation_phrase=plan.confirmation_phrase,
        clock=lambda: 4_100,
        note_backend_opener=lambda _settings: NoteBackend(),
        destination_backend_opener=lambda _settings: DestinationBackend(),
    )

    assert result["txid"] == "redemption-txid"
    assert result["replayed"] is False
    assert calls == {"send": 1, "import": 1}
    assert paths.redemption_consignment_file.stat().st_mode & 0o777 == 0o600
    durable = json.loads(paths.redemption_plan_file.read_text())
    manifest = json.loads(paths.manifest_file.read_text())
    assert durable["status"] == "REDEMPTION_IMPORTED"
    assert manifest["claim_secret"]["state"] == "SPENT"
    journal = paths.redemption_journal_file.read_text()
    assert "BROADCAST_RECORDED" in journal
    assert "REDEMPTION_IMPORTED" in journal
    assert bytes.fromhex("11" * 32).hex() not in journal

    def unexpected_open(_settings):
        raise AssertionError("completed redemption replay must not reopen a wallet")

    replay = execute_live_redemption_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        plan_id=plan.plan_id,
        confirmation_phrase=plan.confirmation_phrase,
        note_backend_opener=unexpected_open,
        destination_backend_opener=unexpected_open,
    )
    assert replay["replayed"] is True
    assert calls["send"] == 1


def _mark_redemption_imported(paths: LiveNotePaths, *, txid: str = "redemption-txid"):
    plan = json.loads(paths.redemption_plan_file.read_text())
    plan.update(
        {
            "status": "REDEMPTION_IMPORTED",
            "broadcast": {"txid": txid},
            "import_statuses": {"3": "waiting_confirmations"},
        }
    )
    manifest = json.loads(paths.manifest_file.read_text())
    manifest.update(
        {
            "state": "REDEMPTION_IMPORTED",
            "import_statuses": {"3": "waiting_confirmations"},
        }
    )
    manifest["claim_secret"]["state"] = "SPENT"
    _atomic_json(paths.redemption_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)


def _settlement_backend(*, status: str, balance: tuple[int, int, int], copies=1):
    transfer = SimpleNamespace(
        txid="redemption-txid",
        kind=SimpleNamespace(name="RECEIVE_WITNESS"),
        status=SimpleNamespace(name=status.upper()),
        requested_assignment=SimpleNamespace(amount=0),
        assignments=[SimpleNamespace(amount=5_000_000)],
        batch_transfer_idx=9,
    )

    class Wallet:
        def get_asset_balance(self, _asset):
            return SimpleNamespace(
                settled=balance[0], future=balance[1], spendable=balance[2]
            )

        def list_transfers(self, _asset):
            return [transfer] * copies

    class Backend:
        def __init__(self):
            self._wallet = Wallet()

        def refresh(self):
            return {3: status.lower()}

        def close(self):
            pass

    return Backend()


def test_redemption_settlement_records_exact_five_usdt_delta(tmp_path):
    paths, _received, _evidence, _parsed, plan = _prepared_redemption(tmp_path)
    _mark_redemption_imported(paths)

    waiting = refresh_live_redemption_settlement(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        clock=lambda: 4_200,
        destination_backend_opener=lambda _settings: _settlement_backend(
            status="waiting_confirmations",
            balance=(45_000_000, 50_000_000, 45_000_000),
        ),
    )
    assert waiting["settled"] is False
    assert waiting["state"] == "REDEMPTION_IMPORTED"
    assert waiting["destination_balance_before"] == 45_000_000
    assert waiting["destination_balance_after"] == 50_000_000

    settled = refresh_live_redemption_settlement(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        clock=lambda: 4_300,
        destination_backend_opener=lambda _settings: _settlement_backend(
            status="settled",
            balance=(50_000_000, 50_000_000, 50_000_000),
        ),
    )
    assert settled["settled"] is True
    assert settled["state"] == "REDEEMED"
    durable = json.loads(paths.redemption_plan_file.read_text())
    manifest = json.loads(paths.manifest_file.read_text())
    assert durable["status"] == "REDEEMED"
    assert durable["settlement"]["txid"] == "redemption-txid"
    assert manifest["state"] == "REDEEMED"
    assert manifest["claim_secret"]["state"] == "SPENT"
    assert json.loads(paths.redemption_journal_file.read_text().splitlines()[-1])[
        "event"
    ] == "REDEEMED"

    def unexpected_open(_settings):
        raise AssertionError("settled redemption replay must not reopen Wallet B")

    replay = refresh_live_redemption_settlement(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        destination_backend_opener=unexpected_open,
    )
    assert replay["settled"] is True
    assert replay["replayed"] is True


@pytest.mark.parametrize("copies", [0, 2])
def test_redemption_settlement_requires_one_exact_transfer(tmp_path, copies):
    paths, _received, _evidence, _parsed, plan = _prepared_redemption(tmp_path)
    _mark_redemption_imported(paths)

    with pytest.raises(LiveNoteError, match="exactly one matching"):
        refresh_live_redemption_settlement(
            _destination(tmp_path),
            vault_root=paths.root,
            note_id=plan.note_id,
            destination_backend_opener=lambda _settings: _settlement_backend(
                status="waiting_confirmations",
                balance=(45_000_000, 50_000_000, 45_000_000),
                copies=copies,
            ),
        )


def test_ambiguous_redemption_is_durable_and_never_resent(tmp_path):
    paths, _received, _evidence, parsed, plan = _prepared_redemption(tmp_path)
    calls = {"send": 0}

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class FailingNote:
        def __init__(self):
            self._wallet = Wallet()

        def parse_invoice(self, _invoice):
            return parsed

        def refresh(self):
            return {}

        def spendable_balance(self, _asset):
            return 5_000_000

        def send_donation(self, **_kwargs):
            calls["send"] += 1
            raise TimeoutError("unknown network outcome")

        def close(self):
            pass

    with pytest.raises(LiveNoteError, match="ambiguous"):
        execute_live_redemption_plan(
            _destination(tmp_path),
            vault_root=paths.root,
            note_id=plan.note_id,
            plan_id=plan.plan_id,
            confirmation_phrase=plan.confirmation_phrase,
            clock=lambda: 4_100,
            note_backend_opener=lambda _settings: FailingNote(),
        )
    assert json.loads(paths.redemption_plan_file.read_text())["status"] == "AMBIGUOUS"

    recovery = recover_live_redemption(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        destination_backend_opener=lambda _settings: pytest.fail(
            "ambiguous recovery must not import or send"
        ),
    )
    assert recovery["status"] == "AMBIGUOUS"
    assert "Do not resend" in recovery["instruction"]
    assert calls["send"] == 1


def test_redemption_import_failure_recovers_without_resend(tmp_path):
    paths, _received, _evidence, parsed, plan = _prepared_redemption(tmp_path)
    consignment = tmp_path / "rgb-lib-consignment"
    consignment.write_bytes(b"retained-consignment")
    calls = {"send": 0, "import": 0}

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class NoteBackend:
        def __init__(self):
            self._wallet = Wallet()

        def parse_invoice(self, _invoice):
            return parsed

        def refresh(self):
            return {}

        def spendable_balance(self, _asset):
            return 5_000_000

        def send_donation(self, **_kwargs):
            calls["send"] += 1
            return SimpleNamespace(
                txid="redemption-txid",
                batch_transfer_idx=9,
                consignment_path=consignment,
            )

        def close(self):
            pass

    class ImportBackend:
        def __init__(self, fail):
            self.fail = fail

        def import_consignment(self, _path):
            calls["import"] += 1
            if self.fail:
                raise TimeoutError("indexer unavailable")
            return {3: "waiting_confirmations"}

        def close(self):
            pass

    with pytest.raises(LiveNoteError, match="import needs recovery"):
        execute_live_redemption_plan(
            _destination(tmp_path),
            vault_root=paths.root,
            note_id=plan.note_id,
            plan_id=plan.plan_id,
            confirmation_phrase=plan.confirmation_phrase,
            clock=lambda: 4_100,
            note_backend_opener=lambda _settings: NoteBackend(),
            destination_backend_opener=lambda _settings: ImportBackend(True),
        )
    assert json.loads(paths.redemption_plan_file.read_text())["status"] == (
        "IMPORT_RECOVERY_REQUIRED"
    )

    recovered = recover_live_redemption(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=plan.note_id,
        clock=lambda: 4_200,
        destination_backend_opener=lambda _settings: ImportBackend(False),
    )
    assert recovered["phase"] == "live-redemption-imported"
    assert calls == {"send": 1, "import": 2}


def test_expired_redemption_is_archived_not_extended(tmp_path):
    paths, received, evidence, parsed, plan = _prepared_redemption(tmp_path)

    class DestinationOffline:
        def parse_invoice(self, _invoice):
            return parsed

        def close(self):
            pass

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=2_000),
                colored=SimpleNamespace(spendable=0),
            )

    class NoteBackend:
        def __init__(self):
            self._wallet = Wallet()

        def refresh(self):
            return {}

        def parse_invoice(self, _invoice):
            return parsed

        def spendable_balance(self, _asset):
            return 5_000_000

        def close(self):
            pass

    renewed, manifest = renew_live_redemption_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        capsule_path=received,
        expected_capsule_sha256=str(evidence["artifact_sha256"]),
        clock=lambda: 5_000,
        id_factory=lambda: "redemption-plan-2",
        destination_backend_opener=lambda _settings: DestinationOffline(),
        note_backend_opener=lambda _settings: NoteBackend(),
    )

    archive = paths.redemption_plan_file.parent / f"redemption-{plan.plan_id}.json"
    assert json.loads(archive.read_text())["status"] == "SUPERSEDED"
    assert renewed.plan_id == "redemption-plan-2"
    assert renewed.expiration_timestamp == 5_900
    assert manifest["state"] == "CLAIM_PLANNED"


def _allocation_backend(parsed=None):
    occupied = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True),
        rgb_allocations=[SimpleNamespace()],
    )
    vanilla = SimpleNamespace(
        utxo=SimpleNamespace(colorable=False, exists=True),
        rgb_allocations=[],
    )

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=20_000),
                colored=SimpleNamespace(spendable=1_000),
            )

        def list_unspents(self, *_args):
            return [occupied, vanilla]

        def list_transfers(self, _asset):
            return [SimpleNamespace(kind=SimpleNamespace(name="RECEIVE_WITNESS"))]

    class Backend:
        def __init__(self):
            self._wallet = Wallet()
            self._online = object()

        def refresh(self):
            return {}

        def spendable_balance(self, _asset):
            return 5_000_000

        def parse_invoice(self, _invoice):
            return parsed

        def close(self):
            pass

    return Backend()


def test_allocation_rejection_reconciles_and_prepares_separate_capacity_plan(tmp_path):
    paths, _received, _evidence, parsed, redemption = _prepared_redemption(tmp_path)
    plan_payload = json.loads(paths.redemption_plan_file.read_text())
    plan_payload.update(
        {
            "status": "AMBIGUOUS",
            "last_error_type": "InsufficientAllocationSlots",
            "last_error": "",
        }
    )
    manifest = json.loads(paths.manifest_file.read_text())
    manifest["state"] = "AMBIGUOUS"
    manifest["claim_secret"].update(
        {"state": "RESERVED", "redemption_plan_id": redemption.plan_id}
    )
    _atomic_json(paths.redemption_plan_file, plan_payload)
    _atomic_json(paths.manifest_file, manifest)

    reconciled = reconcile_live_redemption_prebroadcast_rejection(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=redemption.note_id,
        clock=lambda: 4_200,
        note_backend_opener=lambda _settings: _allocation_backend(parsed),
    )
    assert reconciled["status"] == "PRE_BROADCAST_REJECTED"
    assert reconciled["free_colorable_utxo_count"] == 0
    assert json.loads(paths.manifest_file.read_text())["claim_secret"]["state"] == (
        "READY"
    )

    capacity, manifest = prepare_live_redemption_capacity_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=redemption.note_id,
        clock=lambda: 4_300,
        id_factory=lambda: "capacity-plan-1",
        note_backend_opener=lambda _settings: _allocation_backend(parsed),
    )
    assert capacity.plan_id == "capacity-plan-1"
    assert capacity.utxo_count == 1
    assert capacity.utxo_size_sat == 1_000
    assert capacity.vanilla_spendable_before == 20_000
    assert manifest["state"] == "CAPACITY_PLANNED"
    assert json.loads(paths.redemption_capacity_plan_file.read_text())["status"] == (
        "PLANNED"
    )


def test_capacity_confirmation_is_consumed_before_single_broadcast(tmp_path):
    paths, _received, _evidence, parsed, redemption = _prepared_redemption(tmp_path)
    red = json.loads(paths.redemption_plan_file.read_text())
    red.update(
        {"status": "PRE_BROADCAST_REJECTED", "last_error_type": "InsufficientAllocationSlots"}
    )
    manifest = json.loads(paths.manifest_file.read_text())
    manifest["state"] = "PRE_BROADCAST_REJECTED"
    _atomic_json(paths.redemption_plan_file, red)
    _atomic_json(paths.manifest_file, manifest)
    capacity, _manifest = prepare_live_redemption_capacity_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=redemption.note_id,
        clock=lambda: 4_300,
        id_factory=lambda: "capacity-plan-1",
        note_backend_opener=lambda _settings: _allocation_backend(parsed),
    )
    calls = {"create": 0}

    backend = _allocation_backend(parsed)
    before_tx = SimpleNamespace(
        txid="before", transaction_type=SimpleNamespace(name="CREATE_UTXOS")
    )
    after_tx = SimpleNamespace(
        txid="capacity-txid",
        transaction_type=SimpleNamespace(name="CREATE_UTXOS"),
        received=8_000,
        sent=8_300,
        fee=300,
    )
    backend._wallet.list_transactions = lambda *_args: (
        [before_tx] if calls["create"] == 0 else [before_tx, after_tx]
    )

    def create_utxos(*_args):
        calls["create"] += 1
        assert json.loads(paths.redemption_capacity_plan_file.read_text())[
            "status"
        ] == "CONFIRMATION_CONSUMED"
        return 1

    backend._wallet.create_utxos = create_utxos
    result = execute_live_redemption_capacity_plan(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=redemption.note_id,
        plan_id=capacity.plan_id,
        confirmation_phrase=capacity.confirmation_phrase,
        clock=lambda: 4_400,
        note_backend_opener=lambda _settings: backend,
    )
    assert result["txid"] == "capacity-txid"
    assert calls["create"] == 1
    assert json.loads(paths.redemption_capacity_plan_file.read_text())["status"] == (
        "BROADCAST_RECORDED"
    )


def test_capacity_refresh_requires_confirmation_and_free_colorable_utxo(tmp_path):
    paths, _received, _evidence, _parsed, redemption = _prepared_redemption(tmp_path)
    red = json.loads(paths.redemption_plan_file.read_text())
    red["status"] = "PRE_BROADCAST_REJECTED"
    manifest = json.loads(paths.manifest_file.read_text())
    manifest["state"] = "CAPACITY_BROADCAST_RECORDED"
    _atomic_json(paths.redemption_plan_file, red)
    _atomic_json(paths.manifest_file, manifest)
    _atomic_json(
        paths.redemption_capacity_plan_file,
        {
            "version": LIVE_NOTE_VERSION,
            "plan_id": "capacity-plan-1",
            "network": "mutinynet",
            "operation": "create-redemption-colorable-utxo",
            "note_id": redemption.note_id,
            "planned_timestamp": 4_300,
            "source_wallet": "disposable-note",
            "vanilla_spendable_before": 20_000,
            "colored_spendable_before": 1_000,
            "colorable_utxo_count_before": 1,
            "free_colorable_utxo_count_before": 0,
            "utxo_count": 1,
            "utxo_size_sat": 8_000,
            "fee_rate_sat_vb": 2,
            "expiration_timestamp": 5_200,
            "confirmation_required": "CONFIRM capacity-plan-1",
            "status": "BROADCAST_RECORDED",
            "broadcast": {"txid": "capacity-txid"},
        },
    )
    occupied = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True), rgb_allocations=[object()]
    )
    free = SimpleNamespace(
        utxo=SimpleNamespace(colorable=True, exists=True), rgb_allocations=[]
    )

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=11_691),
                colored=SimpleNamespace(spendable=9_000),
            )

        def list_unspents(self, *_args):
            return [occupied, free]

        def list_transactions(self, *_args):
            return [
                SimpleNamespace(
                    txid="capacity-txid",
                    confirmation_time=SimpleNamespace(height=321, timestamp=4_500),
                )
            ]

    class Backend:
        def __init__(self):
            self._wallet = Wallet()
            self._online = object()

        def refresh(self):
            return {}

        def close(self):
            pass

    result = refresh_live_redemption_capacity(
        _destination(tmp_path),
        vault_root=paths.root,
        note_id=redemption.note_id,
        clock=lambda: 4_600,
        note_backend_opener=lambda _settings: Backend(),
    )
    assert result["confirmed"] is True
    assert result["free_colorable_utxo_count"] == 1
    assert result["block_height"] == 321
    assert json.loads(paths.manifest_file.read_text())["state"] == "CAPACITY_READY"


def test_confirmed_capacity_archives_rejected_plan_and_replans(tmp_path):
    paths, received, evidence, parsed, redemption = _prepared_redemption(tmp_path)
    red = json.loads(paths.redemption_plan_file.read_text())
    red["status"] = "PRE_BROADCAST_REJECTED"
    manifest = json.loads(paths.manifest_file.read_text())
    manifest["state"] = "CAPACITY_READY"
    _atomic_json(paths.redemption_plan_file, red)
    _atomic_json(paths.manifest_file, manifest)
    _atomic_json(
        paths.redemption_capacity_plan_file,
        {
            "version": LIVE_NOTE_VERSION,
            "plan_id": "capacity-plan-1",
            "network": "mutinynet",
            "operation": "create-redemption-colorable-utxo",
            "note_id": redemption.note_id,
            "planned_timestamp": 4_300,
            "source_wallet": "disposable-note",
            "vanilla_spendable_before": 20_000,
            "colored_spendable_before": 1_000,
            "colorable_utxo_count_before": 1,
            "free_colorable_utxo_count_before": 0,
            "utxo_count": 1,
            "utxo_size_sat": 8_000,
            "fee_rate_sat_vb": 2,
            "expiration_timestamp": 5_200,
            "confirmation_required": "CONFIRM capacity-plan-1",
            "status": "CAPACITY_CONFIRMED",
            "confirmation": {"txid": "capacity-txid"},
        },
    )

    class DestinationOffline:
        def parse_invoice(self, _invoice):
            return parsed

        def close(self):
            pass

    class Wallet:
        def get_btc_balance(self, *_args):
            return SimpleNamespace(
                vanilla=SimpleNamespace(spendable=11_691),
                colored=SimpleNamespace(spendable=9_000),
            )

    class NoteBackend:
        def __init__(self):
            self._wallet = Wallet()

        def refresh(self):
            return {}

        def parse_invoice(self, _invoice):
            return parsed

        def spendable_balance(self, _asset):
            return 5_000_000

        def close(self):
            pass

    new, new_manifest = prepare_live_redemption_after_capacity(
        _destination(tmp_path),
        vault_root=paths.root,
        capsule_path=received,
        expected_capsule_sha256=str(evidence["artifact_sha256"]),
        clock=lambda: 4_700,
        id_factory=lambda: "redemption-plan-2",
        destination_backend_opener=lambda _settings: DestinationOffline(),
        note_backend_opener=lambda _settings: NoteBackend(),
    )
    archive = paths.redemption_plan_file.parent / f"redemption-{redemption.plan_id}.json"
    assert json.loads(archive.read_text())["status"] == (
        "SUPERSEDED_AFTER_CAPACITY"
    )
    assert new.plan_id == "redemption-plan-2"
    assert new_manifest["state"] == "CLAIM_PLANNED"
