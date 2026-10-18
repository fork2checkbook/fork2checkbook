"""Isolated live rgb-lib backend for the M1 disposable-note experiment.

This module provisions one ignored wallet directory per note and prepares a
durable, immutable local-wallet -> note funding plan. Preparing a plan never
broadcasts and does not name the eventual redemption wallet. Executing it
consumes the exact confirmation on disk before the rgb-lib send boundary and
never retries an ambiguous send.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import secrets
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

from .bearer_capsule import (
    CAPSULE_ISSUER_AUTHENTICATION,
    CAPSULE_NETWORK,
    CLAIM_HASH_SCHEME,
    CLAIM_SALT_BYTES,
    CLAIM_SECRET_BYTES,
    BearerCapsule,
    BearerCapsuleError,
    claim_secret_hash,
    decode_bearer_capsule,
    encode_bearer_capsule,
    redacted_capsule,
)
from .config import BillRole, RgbSettings
from .native import RgbLibBackend
from .note_spike import (
    NETWORK,
    NOTE_AMOUNT_BASE_UNITS,
    NOTE_DENOMINATION_CENTS,
)
from .sat_policy import (
    MIN_COLORABLE_UTXO_SAT,
    minimum_capacity_fee_sat,
    minimum_note_vanilla_sat,
    minimum_redemption_vanilla_sat,
    minimum_rgb_send_fee_sat,
)


LIVE_NOTE_VERSION = 1
DEFAULT_VAULT_KEY_ID = "local-demo-vault-v1"


class LiveNoteError(RuntimeError):
    """The live disposable-note boundary rejected an unsafe operation."""


class LiveNoteConfirmationRequired(LiveNoteError):
    """The persisted live plan lacks its exact fresh confirmation."""


@dataclass(frozen=True)
class LiveNotePaths:
    root: Path
    note_dir: Path
    wallet_dir: Path
    mnemonic_file: Path
    manifest_file: Path
    invoice_file: Path
    plan_file: Path
    consignment_file: Path
    capsule_file: Path
    redemption_invoice_file: Path
    redemption_plan_file: Path
    redemption_consignment_file: Path
    redemption_journal_file: Path
    funding_btc_topup_plan_file: Path
    funding_capacity_plan_file: Path
    redemption_capacity_plan_file: Path

    @classmethod
    def create(cls, root: Path, note_id: str) -> LiveNotePaths:
        note_dir = root.resolve() / "notes" / note_id
        return cls(
            root=root.resolve(),
            note_dir=note_dir,
            wallet_dir=note_dir / "wallet",
            mnemonic_file=note_dir / "secret" / "mnemonic",
            manifest_file=note_dir / "manifest.json",
            invoice_file=note_dir / "artifacts" / "funding.rgb-invoice",
            plan_file=note_dir / "plans" / "funding.json",
            consignment_file=note_dir / "artifacts" / "funding.rgbc",
            capsule_file=note_dir / "artifacts" / "bearer-note.rgbn",
            redemption_invoice_file=note_dir / "artifacts" / "redemption.rgb-invoice",
            redemption_plan_file=note_dir / "plans" / "redemption.json",
            redemption_consignment_file=note_dir / "artifacts" / "redemption.rgbc",
            redemption_journal_file=note_dir / "journal" / "redemption.jsonl",
            funding_btc_topup_plan_file=(
                note_dir / "plans" / "funding-btc-topup.json"
            ),
            funding_capacity_plan_file=(
                note_dir / "plans" / "funding-capacity.json"
            ),
            redemption_capacity_plan_file=(
                note_dir / "plans" / "redemption-capacity.json"
            ),
        )


@dataclass(frozen=True)
class LiveFundingPlan:
    version: int
    plan_id: str
    network: str
    operation: str
    note_id: str
    planned_timestamp: int
    source_wallet: str
    asset_id: str
    amount_base_units: int
    denomination_cents: int
    source_spendable_before: int
    recipient_id: str
    recipient_invoice_sha256: str
    witness_amount_sat: int
    fee_rate_sat_vb: int
    min_confirmations: int
    expiration_timestamp: int
    donation: bool
    transport_endpoints: tuple[str, ...]
    note_btc_address: str
    note_vanilla_spendable_before: int
    note_colored_spendable_before: int
    measured_prior_send_fee_sat: int
    measured_redemption_minimum_sat: int
    recommended_redemption_vanilla_sat: int

    @property
    def confirmation_phrase(self) -> str:
        return f"CONFIRM {self.plan_id}"


@dataclass(frozen=True)
class LiveRedemptionPlan:
    version: int
    plan_id: str
    network: str
    operation: str
    note_id: str
    planned_timestamp: int
    source_wallet: str
    destination_wallet: str
    destination_master_fingerprint: str
    asset_id: str
    amount_base_units: int
    denomination_cents: int
    source_spendable_before: int
    recipient_id: str
    recipient_invoice_sha256: str
    capsule_sha256: str
    witness_amount_sat: int
    fee_rate_sat_vb: int
    min_confirmations: int
    expiration_timestamp: int
    donation: bool
    transport_endpoints: tuple[str, ...]
    note_vanilla_spendable_before: int
    note_colored_spendable_before: int

    @property
    def confirmation_phrase(self) -> str:
        return f"CONFIRM {self.plan_id}"


@dataclass(frozen=True)
class LiveCapacityPlan:
    version: int
    plan_id: str
    network: str
    operation: str
    note_id: str
    planned_timestamp: int
    source_wallet: str
    vanilla_spendable_before: int
    colored_spendable_before: int
    colorable_utxo_count_before: int
    free_colorable_utxo_count_before: int
    utxo_count: int
    utxo_size_sat: int
    fee_rate_sat_vb: int
    expiration_timestamp: int

    @property
    def confirmation_phrase(self) -> str:
        return f"CONFIRM {self.plan_id}"


@dataclass(frozen=True)
class LiveBtcTopupPlan:
    version: int
    plan_id: str
    network: str
    operation: str
    note_id: str
    planned_timestamp: int
    source_wallet: str
    destination_address: str
    topup_amount_sat: int
    note_vanilla_spendable_before: int
    target_note_vanilla_sat: int
    source_vanilla_spendable_before: int
    source_colored_spendable_before: int
    fee_rate_sat_vb: int
    expiration_timestamp: int

    @property
    def confirmation_phrase(self) -> str:
        return f"CONFIRM {self.plan_id}"


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _append_private_jsonl(path: Path, value: Mapping[str, object]) -> None:
    """Append one fsynced, secret-free recovery event with private permissions."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveNoteError(f"cannot read durable note record: {path.name}") from exc
    if not isinstance(value, dict):
        raise LiveNoteError(f"durable note record is not an object: {path.name}")
    return value


def _balance(value: object) -> dict[str, int]:
    return {
        "settled": int(getattr(value, "settled")),
        "future": int(getattr(value, "future")),
        "spendable": int(getattr(value, "spendable")),
    }


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.lower()
    return str(value).split(".")[-1].lower()


def _note_settings(
    sender: RgbSettings,
    paths: LiveNotePaths,
    *,
    fingerprint: str,
) -> RgbSettings:
    return RgbSettings(
        role=BillRole.B,
        data_dir=paths.wallet_dir,
        peer_data_dir=sender.data_dir,
        inbox_dir=paths.note_dir / "inbox",
        electrum_url=sender.electrum_url,
        master_fingerprint=fingerprint,
        mnemonic_file=paths.mnemonic_file,
        min_confirmations=sender.min_confirmations,
        fee_rate=sender.fee_rate,
        witness_amount_sat=sender.witness_amount_sat,
        max_consignment_bytes=sender.max_consignment_bytes,
    )


def _provision_wallet(
    paths: LiveNotePaths,
    *,
    rgb_lib: ModuleType,
) -> str:
    if paths.note_dir.exists():
        raise LiveNoteError("refusing to overwrite an existing disposable note")
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(paths.root, 0o700)
    paths.note_dir.mkdir(mode=0o700, parents=True)
    paths.wallet_dir.mkdir(mode=0o700)

    keys = rgb_lib.generate_keys(
        rgb_lib.BitcoinNetwork.SIGNET_CUSTOM,
        rgb_lib.WitnessVersion.TAPROOT,
    )
    try:
        wallet_data = rgb_lib.WalletData(
            data_dir=str(paths.wallet_dir),
            bitcoin_network=rgb_lib.BitcoinNetwork.SIGNET_CUSTOM,
            database_type=rgb_lib.DatabaseType.SQLITE,
            max_allocations_per_utxo=5,
            supported_schemas=[
                rgb_lib.AssetSchema.NIA,
                rgb_lib.AssetSchema.CFA,
                rgb_lib.AssetSchema.IFA,
                rgb_lib.AssetSchema.UDA,
            ],
        )
        singlesig_keys = rgb_lib.SinglesigKeys(
            account_xpub_vanilla=keys.account_xpub_vanilla,
            account_xpub_colored=keys.account_xpub_colored,
            vanilla_keychain=None,
            master_fingerprint=keys.master_fingerprint,
            mnemonic=keys.mnemonic,
            witness_version=rgb_lib.WitnessVersion.TAPROOT,
        )
        wallet = rgb_lib.Wallet(wallet_data, singlesig_keys)
        if wallet.get_wallet_data().bitcoin_network != rgb_lib.BitcoinNetwork.SIGNET_CUSTOM:
            raise LiveNoteError("created note wallet is not Mutinynet/custom signet")
        _write_private(paths.mnemonic_file, (keys.mnemonic + "\n").encode("utf-8"))
        return str(keys.master_fingerprint)
    except Exception:
        shutil.rmtree(paths.note_dir)
        raise
    finally:
        if "wallet" in locals():
            del wallet
        del keys


def _validate_sender(sender: RgbSettings) -> None:
    if sender.master_fingerprint is None or sender.mnemonic_file is None:
        raise LiveNoteError("funding dock wallet credentials are not configured")


def _plan_payload(
    plan: LiveFundingPlan
    | LiveRedemptionPlan
    | LiveCapacityPlan
    | LiveBtcTopupPlan,
    *,
    status: str,
) -> dict[str, object]:
    value = asdict(plan)
    value.update(
        {
            "confirmation_required": plan.confirmation_phrase,
            "status": status,
        }
    )
    return value


def prepare_live_funding_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    asset_id: str,
    amount_base_units: int = NOTE_AMOUNT_BASE_UNITS,
    invoice_lifetime_seconds: int = 86_400,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    note_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.load_offline,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    rgb_lib: ModuleType | None = None,
) -> tuple[LiveFundingPlan, dict[str, object]]:
    """Provision N and prepare, but never execute, its real funding plan."""

    _validate_sender(sender)
    if not asset_id:
        raise LiveNoteError("asset_id is required")
    if amount_base_units != NOTE_AMOUNT_BASE_UNITS:
        raise LiveNoteError("the M1 note amount must be exactly 5,000,000 base units")
    if invoice_lifetime_seconds < 900 or plan_lifetime_seconds < 60:
        raise LiveNoteError("invoice or plan lifetime is too short")

    note_id = id_factory()
    paths = LiveNotePaths.create(vault_root, note_id)
    native = rgb_lib or importlib.import_module("rgb_lib")
    fingerprint = _provision_wallet(paths, rgb_lib=native)
    created_at = int(clock())
    manifest: dict[str, object] = {
        "version": LIVE_NOTE_VERSION,
        "network": NETWORK,
        "note_id": note_id,
        "state": "PREPARING",
        "created_at": created_at,
        "asset_id": asset_id,
        "amount_base_units": amount_base_units,
        "denomination_cents": NOTE_DENOMINATION_CENTS,
        "master_fingerprint": fingerprint,
        "issuer_master_fingerprint": str(sender.master_fingerprint),
        "issuer_wallet_label": sender.wallet_label or f"Wallet {sender.role.value}",
        "wallet_dir": str(paths.wallet_dir),
        "mnemonic_file": str(paths.mnemonic_file),
    }
    _atomic_json(paths.manifest_file, manifest)

    try:
        return _finish_live_funding_plan(
            sender,
            paths=paths,
            manifest=manifest,
            invoice_lifetime_seconds=invoice_lifetime_seconds,
            plan_lifetime_seconds=plan_lifetime_seconds,
            clock=clock,
            plan_id=id_factory(),
            note_backend_opener=note_backend_opener,
            sender_backend_opener=sender_backend_opener,
        )
    except Exception as exc:
        manifest.update(
            {
                "state": "PREPARATION_RECOVERY_REQUIRED",
                "last_error_type": type(exc).__name__,
                "last_error": str(exc),
            }
        )
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError(
            f"note {note_id} was preserved after preparation failed; "
            "resume this note instead of creating another"
        ) from exc


def abandon_live_unbroadcast_note(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    reason: str = "operator-test-reset",
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:
    """Close a failed note shell only when durable evidence proves no send began."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if manifest.get("version") != LIVE_NOTE_VERSION or manifest.get("network") != NETWORK:
        raise LiveNoteError("preserved note version or network is invalid")
    if manifest.get("note_id") != note_id:
        raise LiveNoteError("preserved note ID mismatch")
    if str(manifest.get("issuer_master_fingerprint") or "").lower() != str(
        sender.master_fingerprint or ""
    ).lower():
        raise LiveNoteError("note does not belong to the selected issuing wallet")
    if manifest.get("state") not in {
        "PREPARING",
        "PREPARATION_RECOVERY_REQUIRED",
    }:
        raise LiveNoteError("only a never-planned preparation failure may be abandoned")
    if paths.plan_file.exists():
        raise LiveNoteError("note has a funding plan and cannot be abandoned")
    if paths.consignment_file.exists() or paths.capsule_file.exists():
        raise LiveNoteError("note has funded artifacts and cannot be abandoned")
    if manifest.get("funding") or manifest.get("funding_txid"):
        raise LiveNoteError("note has broadcast evidence and cannot be abandoned")
    if not reason.strip() or len(reason.strip()) > 120:
        raise LiveNoteError("abandon reason must be 1 to 120 characters")

    abandoned_at = int(clock())
    manifest.update(
        {
            "state": "ABANDONED",
            "abandoned_at": abandoned_at,
            "abandon_reason": reason.strip(),
            "broadcast": False,
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-note-abandoned",
        "note_id": note_id,
        "state": "ABANDONED",
        "abandoned_at": abandoned_at,
        "broadcast": False,
        "preserved": True,
    }


def resume_live_funding_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    invoice_lifetime_seconds: int = 86_400,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    note_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.load_offline,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveFundingPlan, dict[str, object]]:
    """Resume only the same preserved PREPARING note after a read/network fault."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if manifest.get("version") != LIVE_NOTE_VERSION or manifest.get("network") != NETWORK:
        raise LiveNoteError("preserved note version or network is invalid")
    if manifest.get("note_id") != note_id:
        raise LiveNoteError("preserved note ID mismatch")
    if manifest.get("state") not in {
        "PREPARING",
        "PREPARATION_RECOVERY_REQUIRED",
    }:
        raise LiveNoteError("note is not in a resumable preparation state")
    if paths.plan_file.exists():
        raise LiveNoteError("a durable funding plan already exists; do not replace it")
    manifest.update({"state": "PREPARING"})
    manifest.pop("last_error", None)
    manifest.pop("last_error_type", None)
    _atomic_json(paths.manifest_file, manifest)

    try:
        return _finish_live_funding_plan(
            sender,
            paths=paths,
            manifest=manifest,
            invoice_lifetime_seconds=invoice_lifetime_seconds,
            plan_lifetime_seconds=plan_lifetime_seconds,
            clock=clock,
            plan_id=id_factory(),
            note_backend_opener=note_backend_opener,
            sender_backend_opener=sender_backend_opener,
        )
    except Exception as exc:
        manifest.update(
            {
                "state": "PREPARATION_RECOVERY_REQUIRED",
                "last_error_type": type(exc).__name__,
                "last_error": str(exc),
            }
        )
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError(
            f"note {note_id} remains preserved and safe to resume; no broadcast occurred"
        ) from exc


def renew_live_funding_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    note_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.open,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveFundingPlan, dict[str, object]]:
    """Archive one unspent plan and bind a fresh plan to current balances."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if manifest.get("version") != LIVE_NOTE_VERSION or manifest.get("network") != NETWORK:
        raise LiveNoteError("preserved note version or network is invalid")
    if manifest.get("note_id") != note_id:
        raise LiveNoteError("preserved note ID mismatch")
    if manifest.get("state") != "AWAITING_FUNDING":
        raise LiveNoteError("only an awaiting-funding note may renew its plan")
    previous = _read_json(paths.plan_file)
    if previous.get("status") != "PLANNED":
        raise LiveNoteError("only an unconsumed planned transfer may be superseded")
    previous_plan_id = str(previous.get("plan_id", ""))
    if not previous_plan_id:
        raise LiveNoteError("previous funding plan has no plan ID")
    archive = paths.plan_file.parent / f"funding-{previous_plan_id}.json"
    if archive.exists():
        raise LiveNoteError("previous funding plan archive already exists")

    os.replace(paths.plan_file, archive)
    previous.update(
        {
            "status": "SUPERSEDED",
            "superseded_at": int(clock()),
        }
    )
    _atomic_json(archive, previous)
    manifest.update(
        {
            "state": "PLAN_RENEWAL_IN_PROGRESS",
            "superseded_funding_plan_id": previous_plan_id,
            "superseded_funding_plan_path": str(archive),
        }
    )
    _atomic_json(paths.manifest_file, manifest)

    try:
        return _finish_live_funding_plan(
            sender,
            paths=paths,
            manifest=manifest,
            invoice_lifetime_seconds=86_400,
            plan_lifetime_seconds=plan_lifetime_seconds,
            clock=clock,
            plan_id=id_factory(),
            note_backend_opener=note_backend_opener,
            sender_backend_opener=sender_backend_opener,
        )
    except Exception as exc:
        manifest.update(
            {
                "state": "PREPARATION_RECOVERY_REQUIRED",
                "last_error_type": type(exc).__name__,
                "last_error": str(exc),
            }
        )
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError(
            f"note {note_id} plan renewal failed safely; no broadcast occurred"
        ) from exc


def _finish_live_funding_plan(
    sender: RgbSettings,
    *,
    paths: LiveNotePaths,
    manifest: dict[str, object],
    invoice_lifetime_seconds: int,
    plan_lifetime_seconds: int,
    clock: Callable[[], float],
    plan_id: str,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend],
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend],
) -> tuple[LiveFundingPlan, dict[str, object]]:
    asset_id = str(manifest["asset_id"])
    amount_base_units = int(manifest["amount_base_units"])
    fingerprint = str(manifest["master_fingerprint"])
    note_id = str(manifest["note_id"])
    created_at = int(manifest["created_at"])
    planned_at = int(clock())

    note_settings = _note_settings(sender, paths, fingerprint=fingerprint)
    note_backend = note_backend_opener(note_settings)
    try:
        if paths.invoice_file.exists():
            invoice = paths.invoice_file.read_text(encoding="utf-8")
        else:
            receive = note_backend.witness_receive(
                None,
                created_at + invoice_lifetime_seconds,
                sender.min_confirmations,
            )
            invoice = receive.invoice
        parsed = note_backend.parse_invoice(invoice)
        if parsed.asset_id not in {None, asset_id}:
            raise LiveNoteError("note invoice is bound to a different asset")
        if parsed.transport_endpoints:
            raise LiveNoteError("note invoice unexpectedly contains a transport endpoint")
        if parsed.unknown_query_params:
            raise LiveNoteError("note invoice contains unsupported query parameters")
        if parsed.expiration_timestamp is None:
            raise LiveNoteError("note invoice has no expiration")
        # Initial preparation may deliberately use an offline note wallet, but
        # renewal is the point where external BTC preflight funding must be
        # discovered.  An online backend must therefore sync instead of
        # repeatedly deriving a new address from stale local state.
        online = getattr(note_backend, "_online", None)
        skip_sync = online is None
        btc = note_backend._wallet.get_btc_balance(online, skip_sync)
        note_btc = {
            "address": note_backend._wallet.get_address(),
            "vanilla": _balance(btc.vanilla),
            "colored": _balance(btc.colored),
            "unspent_count": len(
                note_backend._wallet.list_unspents(online, False, skip_sync)
            ),
        }
    finally:
        note_backend.close()

    if not paths.invoice_file.exists():
        _write_private(paths.invoice_file, invoice.encode("utf-8"))
    invoice_sha256 = hashlib.sha256(invoice.encode("utf-8")).hexdigest()

    sender_backend = sender_backend_opener(sender)
    try:
        sender_parsed = sender_backend.parse_invoice(invoice)
        if sender_parsed.recipient_id != parsed.recipient_id:
            raise LiveNoteError("funding dock wallet parsed a different invoice recipient")
        if sender_parsed.asset_id not in {None, asset_id}:
            raise LiveNoteError("funding dock wallet parsed an invoice for a different asset")
        if sender_parsed.transport_endpoints or sender_parsed.unknown_query_params:
            raise LiveNoteError(
                "funding dock wallet rejected the invoice as non-canonical OOB"
            )
        source_balance = sender_backend.spendable_balance(asset_id)
    finally:
        sender_backend.close()
    if source_balance < amount_base_units:
        raise LiveNoteError("funding dock wallet lacks the exact 5 USDT note amount")

    measured_send_fee_sat = minimum_rgb_send_fee_sat(sender.fee_rate)
    measured_redemption_minimum_sat = minimum_redemption_vanilla_sat(
        sender.witness_amount_sat,
        sender.fee_rate,
    )
    recommended_note_vanilla_sat = minimum_note_vanilla_sat(
        sender.witness_amount_sat,
        sender.fee_rate,
    )
    plan = LiveFundingPlan(
        version=LIVE_NOTE_VERSION,
        plan_id=plan_id,
        network=NETWORK,
        operation="fund-disposable-note",
        note_id=note_id,
        planned_timestamp=planned_at,
        source_wallet=f"dock-wallet:{sender.master_fingerprint}",
        asset_id=asset_id,
        amount_base_units=amount_base_units,
        denomination_cents=NOTE_DENOMINATION_CENTS,
        source_spendable_before=source_balance,
        recipient_id=parsed.recipient_id,
        recipient_invoice_sha256=invoice_sha256,
        witness_amount_sat=sender.witness_amount_sat,
        fee_rate_sat_vb=sender.fee_rate,
        min_confirmations=sender.min_confirmations,
        expiration_timestamp=min(
            int(sender_parsed.expiration_timestamp or 0),
            planned_at + plan_lifetime_seconds,
        ),
        donation=True,
        transport_endpoints=(),
        note_btc_address=str(note_btc["address"]),
        note_vanilla_spendable_before=int(note_btc["vanilla"]["spendable"]),
        note_colored_spendable_before=int(note_btc["colored"]["spendable"]),
        measured_prior_send_fee_sat=measured_send_fee_sat,
        measured_redemption_minimum_sat=measured_redemption_minimum_sat,
        recommended_redemption_vanilla_sat=recommended_note_vanilla_sat,
    )
    if plan.expiration_timestamp <= planned_at:
        raise LiveNoteError("prepared funding plan is already expired")
    _atomic_json(paths.plan_file, _plan_payload(plan, status="PLANNED"))
    manifest.update(
        {
            "state": "AWAITING_FUNDING",
            "invoice_path": str(paths.invoice_file),
            "invoice_sha256": invoice_sha256,
            "invoice_expiration_timestamp": int(sender_parsed.expiration_timestamp or 0),
            "funding_plan_id": plan.plan_id,
            "funding_plan_path": str(paths.plan_file),
            "note_btc": note_btc,
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    return plan, manifest


def load_funding_plan(vault_root: Path, note_id: str) -> tuple[LiveNotePaths, dict[str, Any]]:
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.plan_file)
    if plan.get("version") != LIVE_NOTE_VERSION or plan.get("network") != NETWORK:
        raise LiveNoteError("funding plan version or network is invalid")
    if plan.get("note_id") != note_id:
        raise LiveNoteError("funding plan note ID mismatch")
    return paths, plan


def execute_live_funding_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_id: str,
    confirmation_phrase: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Consume one plan, send once, retain proof, and import into N."""

    _validate_sender(sender)
    paths, plan = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if manifest.get("state") != "AWAITING_FUNDING":
        raise LiveNoteError("note is not awaiting its first funding transfer")
    if plan.get("plan_id") != plan_id or plan.get("status") != "PLANNED":
        raise LiveNoteConfirmationRequired("unknown, superseded, or consumed plan")
    expected = f"CONFIRM {plan_id}"
    if confirmation_phrase != expected:
        raise LiveNoteConfirmationRequired("exact confirmation phrase is required")
    now = int(clock())
    if int(plan.get("expiration_timestamp", 0)) <= now:
        raise LiveNoteConfirmationRequired("funding plan expired before confirmation")
    if int(plan.get("amount_base_units", 0)) != NOTE_AMOUNT_BASE_UNITS:
        raise LiveNoteError("funding plan amount is not exactly 5 USDT")
    if plan.get("transport_endpoints") not in ([], ()) or not plan.get("donation"):
        raise LiveNoteError("funding plan is not transport-free donation mode")
    if (
        int(plan.get("note_vanilla_spendable_before", 0))
        < int(plan.get("recommended_redemption_vanilla_sat", 0))
    ):
        raise LiveNoteError("note BTC preflight is not ready")

    try:
        invoice = paths.invoice_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveNoteError("cannot read the exact planned invoice") from exc
    if hashlib.sha256(invoice.encode("utf-8")).hexdigest() != plan.get(
        "recipient_invoice_sha256"
    ):
        raise LiveNoteError("planned invoice digest changed")

    sender_backend = sender_backend_opener(sender)
    try:
        parsed = sender_backend.parse_invoice(invoice)
        if parsed.recipient_id != plan.get("recipient_id"):
            raise LiveNoteError("planned invoice recipient changed")
        if parsed.asset_id not in {None, plan.get("asset_id")}:
            raise LiveNoteError("planned invoice asset changed")
        if parsed.transport_endpoints or parsed.unknown_query_params:
            raise LiveNoteError("planned invoice is no longer canonical OOB")
        balance = sender_backend.spendable_balance(str(plan["asset_id"]))
        if balance != int(plan["source_spendable_before"]):
            raise LiveNoteConfirmationRequired(
                "funding dock wallet balance changed; supersede and review a new plan"
            )

        # From here onward, a failure is ambiguous. Persist consumption before
        # crossing the rgb-lib send boundary and never retry this plan.
        plan.update({"status": "CONFIRMATION_CONSUMED", "consumed_at": now})
        _atomic_json(paths.plan_file, plan)
        manifest.update(
            {
                "state": "FUNDING_BROADCAST_IN_PROGRESS",
                "funding_confirmation_consumed_at": now,
            }
        )
        _atomic_json(paths.manifest_file, manifest)
        try:
            result = sender_backend.send_donation(
                asset_id=str(plan["asset_id"]),
                recipient_id=str(plan["recipient_id"]),
                amount=int(plan["amount_base_units"]),
                witness_amount_sat=int(plan["witness_amount_sat"]),
                fee_rate=int(plan["fee_rate_sat_vb"]),
                min_confirmations=int(plan["min_confirmations"]),
                expiration_timestamp=int(plan["expiration_timestamp"]),
            )
        except Exception as exc:
            prebroadcast = type(exc).__name__ == "InsufficientAllocationSlots"
            failure_status = "PRE_BROADCAST_REJECTED" if prebroadcast else "AMBIGUOUS"
            plan.update(
                {
                    "status": failure_status,
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            manifest.update(
                {
                    "state": failure_status,
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            _atomic_json(paths.plan_file, plan)
            _atomic_json(paths.manifest_file, manifest)
            if prebroadcast:
                raise LiveNoteError(
                    "funding needs a free RGB allocation slot; the rejected plan "
                    "was consumed before any send"
                ) from exc
            raise LiveNoteError(
                "funding send outcome is ambiguous; recovery must not resend"
            ) from exc
    finally:
        sender_backend.close()

    try:
        consignment = result.consignment_path.read_bytes()
    except OSError as exc:
        raise LiveNoteError(
            "broadcast returned but exact consignment cannot be retained; do not resend"
        ) from exc
    if not consignment or len(consignment) > sender.max_consignment_bytes:
        raise LiveNoteError("broadcast consignment is empty or exceeds the size limit")
    digest = hashlib.sha256(consignment).hexdigest()
    _write_private(paths.consignment_file, consignment)
    broadcast = {
        "txid": result.txid,
        "batch_transfer_idx": result.batch_transfer_idx,
        "consignment_path": str(paths.consignment_file),
        "consignment_bytes": len(consignment),
        "consignment_sha256": digest,
    }
    plan.update({"status": "BROADCAST_RECORDED", "broadcast": broadcast})
    manifest.update({"state": "BROADCAST_RECORDED", "funding": broadcast})
    _atomic_json(paths.plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)

    note_settings = _note_settings(
        sender,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    try:
        note_backend = note_backend_opener(note_settings)
        try:
            updated = note_backend.import_consignment(paths.consignment_file)
        finally:
            note_backend.close()
    except Exception as exc:
        plan.update(
            {
                "status": "IMPORT_RECOVERY_REQUIRED",
                "import_error_type": type(exc).__name__,
                "import_error": str(exc),
            }
        )
        manifest.update(
            {
                "state": "IMPORT_RECOVERY_REQUIRED",
                "import_error_type": type(exc).__name__,
                "import_error": str(exc),
            }
        )
        _atomic_json(paths.plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError(
            "funding broadcast is recorded but import needs recovery; do not resend"
        ) from exc

    statuses = {str(key): value for key, value in updated.items()}
    plan.update({"status": "FUNDING_IMPORTED", "import_statuses": statuses})
    manifest.update({"state": "FUNDING_IMPORTED", "import_statuses": statuses})
    _atomic_json(paths.plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-funding-imported",
        "broadcast": True,
        "note_id": note_id,
        "plan_id": plan_id,
        **broadcast,
        "import_statuses": statuses,
    }


def refresh_live_funding(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Refresh note N and durably activate it only after exact settlement proof."""

    _validate_sender(sender)
    paths, plan = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if manifest.get("state") not in {"FUNDING_IMPORTED", "ACTIVE"}:
        raise LiveNoteError("note funding is not ready for settlement refresh")
    if plan.get("status") not in {"FUNDING_IMPORTED", "FUNDING_SETTLED"}:
        raise LiveNoteError("funding plan is not ready for settlement refresh")
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("funding broadcast evidence is missing")

    note_settings = _note_settings(
        sender,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    backend = note_backend_opener(note_settings)
    try:
        updated = backend.refresh()
        asset_id = str(plan["asset_id"])
        balance = _balance(backend._wallet.get_asset_balance(asset_id))
        matches = [
            transfer
            for transfer in backend._wallet.list_transfers(asset_id)
            if getattr(transfer, "txid", None) == broadcast["txid"]
        ]
    finally:
        backend.close()

    if len(matches) != 1:
        raise LiveNoteError("note does not contain exactly one matching funding transfer")
    transfer = matches[0]
    transfer_kind = _enum_name(getattr(transfer, "kind"))
    transfer_status = _enum_name(getattr(transfer, "status"))
    if transfer_kind != "receive_witness":
        raise LiveNoteError("matching note funding transfer has the wrong kind")

    expected = int(plan["amount_base_units"])
    settled = transfer_status == "settled" and all(
        balance[field] == expected for field in ("settled", "future", "spendable")
    )
    evidence = {
        "verified_at": int(clock()),
        "txid": str(broadcast["txid"]),
        "batch_transfer_idx": int(getattr(transfer, "batch_transfer_idx")),
        "transfer_kind": transfer_kind,
        "transfer_status": transfer_status,
        "asset_id": str(plan["asset_id"]),
        "amount_base_units": expected,
        "balance": balance,
        "updated_transfers": {str(key): value for key, value in updated.items()},
    }
    plan["last_settlement_refresh"] = evidence
    manifest["last_settlement_refresh"] = evidence
    if settled:
        plan.update({"status": "FUNDING_SETTLED", "settlement": evidence})
        manifest.update({"state": "ACTIVE", "funding_settlement": evidence})
    _atomic_json(paths.plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-funding-settlement-refresh",
        "note_id": note_id,
        "state": manifest["state"],
        "settled": settled,
        **evidence,
    }


def _validate_capsule_binding(
    capsule: BearerCapsule,
    plan: Mapping[str, Any],
    *,
    vault_key_id: str,
) -> None:
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict):
        raise LiveNoteError("funding broadcast evidence is missing")
    expected = {
        "network": CAPSULE_NETWORK,
        "vault_key_id": vault_key_id,
        "note_id": str(plan["note_id"]),
        "asset_id": str(plan["asset_id"]),
        "amount_base_units": int(plan["amount_base_units"]),
        "denomination_cents": int(plan["denomination_cents"]),
        "funding_txid": str(broadcast["txid"]),
        "funding_consignment_sha256": str(broadcast["consignment_sha256"]),
        "issuer_authentication": CAPSULE_ISSUER_AUTHENTICATION,
    }
    for field, value in expected.items():
        if getattr(capsule, field) != value:
            raise LiveNoteError(f"existing bearer capsule has a mismatched {field}")


def prepare_live_bearer_capsule(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    vault_key_id: str = DEFAULT_VAULT_KEY_ID,
    clock: Callable[[], float] = time.time,
    secret_factory: Callable[[int], bytes] = secrets.token_bytes,
    salt_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> dict[str, object]:
    """Create one recoverable bearer capsule without opening any wallet."""

    _validate_sender(sender)
    paths, plan = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    issuer_fingerprint = manifest.get("issuer_master_fingerprint")
    if issuer_fingerprint is not None:
        if not hmac.compare_digest(
            str(issuer_fingerprint).lower(), str(sender.master_fingerprint).lower()
        ):
            raise LiveNoteError(
                "active note belongs to a different funding dock wallet"
            )
    elif not (
        sender.role is BillRole.A and plan.get("source_wallet") == "wallet-a"
    ):
        raise LiveNoteError(
            "legacy active note cannot be attributed to this funding dock wallet"
        )
    if manifest.get("state") != "ACTIVE" or plan.get("status") != "FUNDING_SETTLED":
        raise LiveNoteError("only an exactly settled active note can become a capsule")
    settlement = manifest.get("funding_settlement")
    broadcast = plan.get("broadcast")
    if not isinstance(settlement, dict) or not isinstance(broadcast, dict):
        raise LiveNoteError("settlement or broadcast evidence is missing")
    expected_amount = int(plan["amount_base_units"])
    balance = settlement.get("balance")
    if (
        settlement.get("txid") != broadcast.get("txid")
        or settlement.get("transfer_status") != "settled"
        or not isinstance(balance, dict)
        or any(
            int(balance.get(field, -1)) != expected_amount
            for field in ("settled", "future", "spendable")
        )
    ):
        raise LiveNoteError("durable settlement evidence does not match the funded note")

    def load_existing() -> tuple[BearerCapsule, bytes, dict[str, object]]:
        try:
            encoded = paths.capsule_file.read_bytes()
            capsule = decode_bearer_capsule(encoded)
        except (OSError, BearerCapsuleError) as exc:
            raise LiveNoteError("existing bearer capsule is unreadable or invalid") from exc
        _validate_capsule_binding(capsule, plan, vault_key_id=vault_key_id)
        claim_record = manifest.get("claim_secret")
        if not isinstance(claim_record, dict):
            raise LiveNoteError("bearer capsule claim registry is missing")
        try:
            salt = bytes.fromhex(str(claim_record["salt_hex"]))
            recorded_hash = str(claim_record["hash_sha256"])
        except (KeyError, ValueError) as exc:
            raise LiveNoteError("bearer capsule claim registry is invalid") from exc
        calculated_hash = claim_secret_hash(capsule.claim_secret, salt)
        if claim_record.get("hash_scheme") != CLAIM_HASH_SCHEME or not hmac.compare_digest(
            calculated_hash, recorded_hash
        ):
            raise LiveNoteError("bearer capsule claim secret does not match its registry")
        evidence = redacted_capsule(capsule, encoded)
        recorded_capsule = manifest.get("capsule")
        if isinstance(recorded_capsule, dict) and (
            recorded_capsule.get("artifact_sha256") != evidence["artifact_sha256"]
            or recorded_capsule.get("artifact_bytes") != evidence["artifact_bytes"]
        ):
            raise LiveNoteError("bearer capsule metadata does not match its artifact")
        return capsule, encoded, evidence

    if paths.capsule_file.exists():
        _capsule, _encoded, evidence = load_existing()
        if not isinstance(manifest.get("capsule"), dict):
            manifest["capsule"] = {
                **evidence,
                "path": str(paths.capsule_file),
                "state": "READY",
            }
            manifest["claim_secret"]["state"] = "READY"
            _atomic_json(paths.manifest_file, manifest)
        return {
            "phase": "live-bearer-capsule-ready",
            "created": False,
            "note_state": manifest["state"],
            "capsule_path": str(paths.capsule_file),
            **evidence,
        }
    if isinstance(manifest.get("capsule"), dict):
        raise LiveNoteError("registered bearer capsule artifact is missing")

    claim_secret = secret_factory(CLAIM_SECRET_BYTES)
    claim_salt = salt_factory(CLAIM_SALT_BYTES)
    if len(claim_secret) != CLAIM_SECRET_BYTES or len(claim_salt) != CLAIM_SALT_BYTES:
        raise LiveNoteError("secret factory returned an invalid bearer capability")
    claim_hash = claim_secret_hash(claim_secret, claim_salt)
    manifest["claim_secret"] = {
        "state": "PREPARING",
        "hash_scheme": CLAIM_HASH_SCHEME,
        "salt_hex": claim_salt.hex(),
        "hash_sha256": claim_hash,
    }
    _atomic_json(paths.manifest_file, manifest)

    capsule = BearerCapsule(
        network=CAPSULE_NETWORK,
        vault_key_id=vault_key_id,
        note_id=note_id,
        claim_secret=claim_secret,
        asset_id=str(plan["asset_id"]),
        amount_base_units=expected_amount,
        denomination_cents=int(plan["denomination_cents"]),
        funding_txid=str(broadcast["txid"]),
        funding_consignment_sha256=str(broadcast["consignment_sha256"]),
        issued_timestamp=int(clock()),
    )
    try:
        encoded = encode_bearer_capsule(capsule)
        _write_private(paths.capsule_file, encoded)
    except (BearerCapsuleError, OSError) as exc:
        raise LiveNoteError("unable to create the bearer capsule artifact") from exc
    evidence = redacted_capsule(capsule, encoded)
    manifest["claim_secret"]["state"] = "READY"
    manifest["capsule"] = {
        **evidence,
        "path": str(paths.capsule_file),
        "state": "READY",
    }
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-bearer-capsule-ready",
        "created": True,
        "note_state": manifest["state"],
        "capsule_path": str(paths.capsule_file),
        **evidence,
    }


def _validate_destination(destination: RgbSettings) -> None:
    if destination.master_fingerprint is None or destination.mnemonic_file is None:
        raise LiveNoteError("redemption dock wallet credentials are not configured")


def _redemption_plan_from_payload(payload: Mapping[str, Any]) -> LiveRedemptionPlan:
    try:
        values = {field.name: payload[field.name] for field in fields(LiveRedemptionPlan)}
        values["transport_endpoints"] = tuple(values["transport_endpoints"])
        return LiveRedemptionPlan(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveNoteError("durable redemption plan is invalid") from exc


def prepare_live_redemption_plan(
    destination: RgbSettings,
    *,
    vault_root: Path,
    capsule_path: Path,
    expected_capsule_sha256: str,
    invoice_lifetime_seconds: int = 86_400,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.load_offline,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveRedemptionPlan, dict[str, object]]:
    """Verify one bearer capability and persist a no-broadcast destination plan."""

    _validate_destination(destination)
    if invoice_lifetime_seconds < 900 or plan_lifetime_seconds < 60:
        raise LiveNoteError("invoice or plan lifetime is too short")
    if len(expected_capsule_sha256) != 64:
        raise LiveNoteError("expected capsule SHA-256 is invalid")
    try:
        int(expected_capsule_sha256, 16)
        if capsule_path.stat().st_mode & 0o077:
            raise LiveNoteError("received bearer capsule permissions must be 0600 or stricter")
        encoded = capsule_path.read_bytes()
    except OSError as exc:
        raise LiveNoteError("cannot read the received bearer capsule") from exc
    artifact_sha256 = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(artifact_sha256, expected_capsule_sha256.lower()):
        raise LiveNoteError("received bearer capsule SHA-256 does not match")
    try:
        capsule = decode_bearer_capsule(encoded)
    except BearerCapsuleError as exc:
        raise LiveNoteError("received bearer capsule is invalid") from exc

    paths, funding_plan = load_funding_plan(vault_root, capsule.note_id)
    manifest = _read_json(paths.manifest_file)
    _validate_capsule_binding(
        capsule,
        funding_plan,
        vault_key_id=DEFAULT_VAULT_KEY_ID,
    )
    recorded_capsule = manifest.get("capsule")
    if not isinstance(recorded_capsule, dict) or recorded_capsule.get(
        "artifact_sha256"
    ) != artifact_sha256:
        raise LiveNoteError("received bearer capsule does not match the vault record")
    claim = manifest.get("claim_secret")
    if not isinstance(claim, dict) or claim.get("state") != "READY":
        raise LiveNoteError("bearer claim registry is not ready")
    if claim.get("hash_scheme") != CLAIM_HASH_SCHEME:
        raise LiveNoteError("bearer claim registry hash scheme is invalid")
    try:
        salt = bytes.fromhex(str(claim["salt_hex"]))
        recorded_hash = str(claim["hash_sha256"])
    except (KeyError, ValueError) as exc:
        raise LiveNoteError("bearer claim registry is invalid") from exc
    if len(salt) != CLAIM_SALT_BYTES or not hmac.compare_digest(
        claim_secret_hash(capsule.claim_secret, salt), recorded_hash
    ):
        raise LiveNoteError("bearer capability is not authorized by this vault")

    if paths.redemption_plan_file.exists():
        existing = _read_json(paths.redemption_plan_file)
        plan = _redemption_plan_from_payload(existing)
        if (
            existing.get("status") != "PLANNED"
            or plan.network != NETWORK
            or plan.note_id != capsule.note_id
            or plan.capsule_sha256 != artifact_sha256
            or plan.destination_master_fingerprint != destination.master_fingerprint
        ):
            raise LiveNoteError("an incompatible or consumed redemption plan already exists")
        return plan, manifest

    if manifest.get("state") != "ACTIVE" or funding_plan.get("status") != "FUNDING_SETTLED":
        raise LiveNoteError("bearer note is not active and settled")

    planned_at = int(clock())
    destination_backend = destination_backend_opener(destination)
    try:
        if paths.redemption_invoice_file.exists():
            invoice = paths.redemption_invoice_file.read_text(encoding="utf-8")
        else:
            receive = destination_backend.witness_receive(
                capsule.asset_id,
                planned_at + invoice_lifetime_seconds,
                destination.min_confirmations,
            )
            invoice = receive.invoice
        parsed = destination_backend.parse_invoice(invoice)
        if parsed.asset_id != capsule.asset_id:
            raise LiveNoteError("redemption dock invoice is bound to a different asset")
        if parsed.transport_endpoints or parsed.unknown_query_params:
            raise LiveNoteError(
                "redemption dock invoice is not canonical transport-free OOB"
            )
        if parsed.expiration_timestamp is None:
            raise LiveNoteError("redemption dock invoice has no expiration")
    finally:
        destination_backend.close()
    if not paths.redemption_invoice_file.exists():
        _write_private(paths.redemption_invoice_file, invoice.encode("utf-8"))
    invoice_sha256 = hashlib.sha256(invoice.encode("utf-8")).hexdigest()

    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    note_backend = note_backend_opener(note_settings)
    try:
        note_backend.refresh()
        note_parsed = note_backend.parse_invoice(invoice)
        if note_parsed.recipient_id != parsed.recipient_id:
            raise LiveNoteError(
                "note wallet parsed a different redemption-dock recipient"
            )
        if note_parsed.asset_id != capsule.asset_id:
            raise LiveNoteError("note wallet parsed a different invoice asset")
        if note_parsed.transport_endpoints or note_parsed.unknown_query_params:
            raise LiveNoteError("note wallet rejected the transport-free invoice")
        source_balance = note_backend.spendable_balance(capsule.asset_id)
        btc = note_backend._wallet.get_btc_balance(None, True)
        vanilla_spendable = int(getattr(btc.vanilla, "spendable"))
        colored_spendable = int(getattr(btc.colored, "spendable"))
    finally:
        note_backend.close()
    if source_balance != capsule.amount_base_units:
        raise LiveNoteError("note wallet no longer holds the exact bearer amount")
    redemption_minimum_sat = minimum_redemption_vanilla_sat(
        destination.witness_amount_sat,
        destination.fee_rate,
    )
    if vanilla_spendable < redemption_minimum_sat:
        raise LiveNoteError("note wallet BTC preflight is no longer ready")

    plan = LiveRedemptionPlan(
        version=LIVE_NOTE_VERSION,
        plan_id=id_factory(),
        network=NETWORK,
        operation="redeem-disposable-note",
        note_id=capsule.note_id,
        planned_timestamp=planned_at,
        source_wallet="disposable-note",
        destination_wallet=f"dock-wallet:{destination.master_fingerprint}",
        destination_master_fingerprint=str(destination.master_fingerprint),
        asset_id=capsule.asset_id,
        amount_base_units=capsule.amount_base_units,
        denomination_cents=capsule.denomination_cents,
        source_spendable_before=source_balance,
        recipient_id=parsed.recipient_id,
        recipient_invoice_sha256=invoice_sha256,
        capsule_sha256=artifact_sha256,
        witness_amount_sat=destination.witness_amount_sat,
        fee_rate_sat_vb=destination.fee_rate,
        min_confirmations=destination.min_confirmations,
        expiration_timestamp=min(
            int(parsed.expiration_timestamp),
            planned_at + plan_lifetime_seconds,
        ),
        donation=True,
        transport_endpoints=(),
        note_vanilla_spendable_before=vanilla_spendable,
        note_colored_spendable_before=colored_spendable,
    )
    if plan.expiration_timestamp <= planned_at:
        raise LiveNoteError("prepared redemption plan is already expired")
    _atomic_json(paths.redemption_plan_file, _plan_payload(plan, status="PLANNED"))
    manifest.update(
        {
            "state": "CLAIM_PLANNED",
            "redemption_plan_id": plan.plan_id,
            "redemption_plan_path": str(paths.redemption_plan_file),
            "redemption_invoice_path": str(paths.redemption_invoice_file),
            "redemption_invoice_sha256": invoice_sha256,
            "redemption_capsule_sha256": artifact_sha256,
            "redemption_destination_master_fingerprint": str(
                destination.master_fingerprint
            ),
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "PLAN_READY",
            "timestamp": planned_at,
            "note_id": plan.note_id,
            "plan_id": plan.plan_id,
            "status": "PLANNED",
            "invoice_sha256": invoice_sha256,
            "capsule_sha256": artifact_sha256,
        },
    )
    return plan, manifest


def load_redemption_plan(
    vault_root: Path, note_id: str
) -> tuple[LiveNotePaths, dict[str, Any]]:
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.redemption_plan_file)
    if plan.get("version") != LIVE_NOTE_VERSION or plan.get("network") != NETWORK:
        raise LiveNoteError("redemption plan version or network is invalid")
    if plan.get("note_id") != note_id:
        raise LiveNoteError("redemption plan note ID mismatch")
    _redemption_plan_from_payload(plan)
    return paths, plan


def _redemption_result(
    *,
    note_id: str,
    plan: Mapping[str, Any],
    replayed: bool,
) -> dict[str, object]:
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("redemption broadcast evidence is missing")
    return {
        "phase": "live-redemption-imported",
        "broadcast": True,
        "replayed": replayed,
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        **broadcast,
        "import_statuses": dict(plan.get("import_statuses", {})),
    }


def execute_live_redemption_plan(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_id: str,
    confirmation_phrase: str,
    clock: Callable[[], float] = time.time,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.open,
) -> dict[str, object]:
    """Consume one redemption confirmation and cross the send boundary once."""

    _validate_destination(destination)
    paths, plan = load_redemption_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    status = str(plan.get("status", ""))
    if status == "REDEMPTION_IMPORTED":
        return _redemption_result(note_id=note_id, plan=plan, replayed=True)
    if status != "PLANNED" or manifest.get("state") != "CLAIM_PLANNED":
        raise LiveNoteError(
            "redemption plan is consumed or recovery-blocked; inspect recovery without resending"
        )
    if plan.get("plan_id") != plan_id:
        raise LiveNoteConfirmationRequired("unknown or superseded redemption plan")
    expected = f"CONFIRM {plan_id}"
    if confirmation_phrase != expected:
        raise LiveNoteConfirmationRequired("exact confirmation phrase is required")
    now = int(clock())
    if int(plan.get("expiration_timestamp", 0)) <= now:
        raise LiveNoteConfirmationRequired(
            "redemption plan expired; supersede it with a fresh immutable plan"
        )
    if int(plan.get("amount_base_units", 0)) != NOTE_AMOUNT_BASE_UNITS:
        raise LiveNoteError("redemption plan amount is not exactly 5 USDT")
    if plan.get("transport_endpoints") not in ([], ()) or not plan.get("donation"):
        raise LiveNoteError("redemption plan is not transport-free donation mode")
    if str(plan.get("destination_master_fingerprint")) != str(
        destination.master_fingerprint
    ):
        raise LiveNoteError("redemption plan belongs to a different dock wallet")

    try:
        invoice = paths.redemption_invoice_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveNoteError("cannot read the exact redemption invoice") from exc
    if hashlib.sha256(invoice.encode("utf-8")).hexdigest() != plan.get(
        "recipient_invoice_sha256"
    ):
        raise LiveNoteError("redemption invoice digest changed")

    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    note_backend = note_backend_opener(note_settings)
    try:
        # The note wallet process lock serializes the final check with the send.
        paths, plan = load_redemption_plan(vault_root, note_id)
        manifest = _read_json(paths.manifest_file)
        if plan.get("status") != "PLANNED" or manifest.get("state") != "CLAIM_PLANNED":
            raise LiveNoteError("redemption plan changed while acquiring the note wallet")
        if plan.get("plan_id") != plan_id:
            raise LiveNoteConfirmationRequired("redemption plan was superseded")
        now = int(clock())
        if int(plan.get("expiration_timestamp", 0)) <= now:
            raise LiveNoteConfirmationRequired(
                "redemption plan expired; supersede it with a fresh immutable plan"
            )
        parsed = note_backend.parse_invoice(invoice)
        if parsed.recipient_id != plan.get("recipient_id"):
            raise LiveNoteError("redemption invoice recipient changed")
        if parsed.asset_id != plan.get("asset_id"):
            raise LiveNoteError("redemption invoice asset changed")
        if parsed.transport_endpoints or parsed.unknown_query_params:
            raise LiveNoteError("redemption invoice is no longer canonical OOB")
        note_backend.refresh()
        balance = note_backend.spendable_balance(str(plan["asset_id"]))
        if balance != int(plan["source_spendable_before"]):
            raise LiveNoteConfirmationRequired(
                "note balance changed; do not consume this redemption plan"
            )
        btc = note_backend._wallet.get_btc_balance(None, True)
        redemption_minimum_sat = minimum_redemption_vanilla_sat(
            int(plan["witness_amount_sat"]),
            int(plan["fee_rate_sat_vb"]),
        )
        if int(getattr(btc.vanilla, "spendable")) < redemption_minimum_sat:
            raise LiveNoteError("note wallet BTC preflight is no longer ready")
        now = int(clock())
        if int(plan.get("expiration_timestamp", 0)) <= now:
            raise LiveNoteConfirmationRequired(
                "redemption plan expired during final validation; supersede it"
            )

        # Persist consumption before the only irreversible rgb-lib call.
        plan.update({"status": "CONFIRMATION_CONSUMED", "consumed_at": now})
        manifest.update(
            {
                "state": "CLAIMING",
                "redemption_confirmation_consumed_at": now,
            }
        )
        claim = manifest.get("claim_secret")
        if not isinstance(claim, dict) or claim.get("state") != "READY":
            raise LiveNoteError("bearer claim registry is not ready for consumption")
        claim.update(
            {
                "state": "RESERVED",
                "redemption_plan_id": plan_id,
                "reserved_at": now,
                "destination_invoice_sha256": str(
                    plan["recipient_invoice_sha256"]
                ),
            }
        )
        _atomic_json(paths.redemption_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        _append_private_jsonl(
            paths.redemption_journal_file,
            {
                "event": "CONFIRMATION_CONSUMED",
                "timestamp": now,
                "note_id": note_id,
                "plan_id": plan_id,
                "status": "CONFIRMATION_CONSUMED",
            },
        )
        try:
            result = note_backend.send_donation(
                asset_id=str(plan["asset_id"]),
                recipient_id=str(plan["recipient_id"]),
                amount=int(plan["amount_base_units"]),
                witness_amount_sat=int(plan["witness_amount_sat"]),
                fee_rate=int(plan["fee_rate_sat_vb"]),
                min_confirmations=int(plan["min_confirmations"]),
                expiration_timestamp=int(plan["expiration_timestamp"]),
            )
        except Exception as exc:
            failed_at = int(clock())
            prebroadcast = type(exc).__name__ == "InsufficientAllocationSlots"
            failure_status = "PRE_BROADCAST_REJECTED" if prebroadcast else "AMBIGUOUS"
            plan.update(
                {
                    "status": failure_status,
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            manifest.update(
                {
                    "state": failure_status,
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            if prebroadcast:
                claim.update({"state": "READY"})
                claim.pop("redemption_plan_id", None)
                claim.pop("reserved_at", None)
                claim.pop("destination_invoice_sha256", None)
            _atomic_json(paths.redemption_plan_file, plan)
            _atomic_json(paths.manifest_file, manifest)
            _append_private_jsonl(
                paths.redemption_journal_file,
                {
                    "event": failure_status,
                    "timestamp": failed_at,
                    "note_id": note_id,
                    "plan_id": plan_id,
                    "status": failure_status,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if prebroadcast:
                raise LiveNoteError(
                    "redemption was rejected before broadcast: one free colorable UTXO is required"
                ) from exc
            raise LiveNoteError(
                "redemption send outcome is ambiguous; recovery must not resend"
            ) from exc
    finally:
        note_backend.close()

    try:
        consignment = result.consignment_path.read_bytes()
    except OSError as exc:
        raise LiveNoteError(
            "redemption broadcast returned but its consignment cannot be retained; do not resend"
        ) from exc
    if not consignment or len(consignment) > destination.max_consignment_bytes:
        raise LiveNoteError("redemption consignment is empty or exceeds the size limit")
    digest = hashlib.sha256(consignment).hexdigest()
    _write_private(paths.redemption_consignment_file, consignment)
    broadcast = {
        "txid": result.txid,
        "batch_transfer_idx": result.batch_transfer_idx,
        "consignment_path": str(paths.redemption_consignment_file),
        "consignment_bytes": len(consignment),
        "consignment_sha256": digest,
    }
    recorded_at = int(clock())
    plan.update({"status": "BROADCAST_RECORDED", "broadcast": broadcast})
    manifest.update({"state": "BROADCAST_RECORDED", "redemption": broadcast})
    _atomic_json(paths.redemption_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "BROADCAST_RECORDED",
            "timestamp": recorded_at,
            "note_id": note_id,
            "plan_id": plan_id,
            "status": "BROADCAST_RECORDED",
            **broadcast,
        },
    )
    return _import_recorded_redemption(
        destination,
        paths=paths,
        plan=plan,
        manifest=manifest,
        clock=clock,
        destination_backend_opener=destination_backend_opener,
    )


def _import_recorded_redemption(
    destination: RgbSettings,
    *,
    paths: LiveNotePaths,
    plan: dict[str, Any],
    manifest: dict[str, Any],
    clock: Callable[[], float],
    destination_backend_opener: Callable[[RgbSettings], RgbLibBackend],
) -> dict[str, object]:
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("redemption broadcast evidence is missing")
    try:
        consignment = paths.redemption_consignment_file.read_bytes()
    except OSError as exc:
        raise LiveNoteError("recorded redemption consignment is unavailable") from exc
    size_changed = len(consignment) != int(broadcast.get("consignment_bytes", -1))
    digest_changed = not hmac.compare_digest(
        hashlib.sha256(consignment).hexdigest(),
        str(broadcast.get("consignment_sha256", "")),
    )
    if size_changed or digest_changed:
        raise LiveNoteError("recorded redemption consignment failed integrity checks")
    try:
        destination_backend = destination_backend_opener(destination)
        try:
            updated = destination_backend.import_consignment(
                paths.redemption_consignment_file
            )
        finally:
            destination_backend.close()
    except Exception as exc:
        failed_at = int(clock())
        plan.update(
            {
                "status": "IMPORT_RECOVERY_REQUIRED",
                "import_error_type": type(exc).__name__,
                "import_error": str(exc),
            }
        )
        manifest.update(
            {
                "state": "IMPORT_RECOVERY_REQUIRED",
                "import_error_type": type(exc).__name__,
                "import_error": str(exc),
            }
        )
        _atomic_json(paths.redemption_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        _append_private_jsonl(
            paths.redemption_journal_file,
            {
                "event": "IMPORT_RECOVERY_REQUIRED",
                "timestamp": failed_at,
                "note_id": str(plan["note_id"]),
                "plan_id": str(plan["plan_id"]),
                "status": "IMPORT_RECOVERY_REQUIRED",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise LiveNoteError(
            "redemption broadcast is recorded but destination import needs recovery; do not resend"
        ) from exc

    statuses = {str(key): value for key, value in updated.items()}
    imported_at = int(clock())
    plan.update({"status": "REDEMPTION_IMPORTED", "import_statuses": statuses})
    plan.pop("import_error", None)
    plan.pop("import_error_type", None)
    manifest.update({"state": "REDEMPTION_IMPORTED", "import_statuses": statuses})
    manifest.pop("import_error", None)
    manifest.pop("import_error_type", None)
    claim = manifest.get("claim_secret")
    if isinstance(claim, dict):
        claim.update({"state": "SPENT", "spent_at": imported_at})
    _atomic_json(paths.redemption_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "REDEMPTION_IMPORTED",
            "timestamp": imported_at,
            "note_id": str(plan["note_id"]),
            "plan_id": str(plan["plan_id"]),
            "status": "REDEMPTION_IMPORTED",
            "txid": str(broadcast["txid"]),
            "import_statuses": statuses,
        },
    )
    return _redemption_result(
        note_id=str(plan["note_id"]), plan=plan, replayed=False
    )


def refresh_live_redemption_settlement(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.open,
) -> dict[str, object]:
    """Refresh the destination wallet after exact settlement proof."""

    _validate_destination(destination)
    paths, plan = load_redemption_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    status = str(plan.get("status", ""))
    if status == "REDEEMED":
        settlement = plan.get("settlement")
        if manifest.get("state") != "REDEEMED" or not isinstance(
            settlement, dict
        ):
            raise LiveNoteError("redeemed state is missing durable settlement evidence")
        claim = manifest.get("claim_secret")
        if not isinstance(claim, dict) or claim.get("state") != "SPENT":
            raise LiveNoteError("redeemed state has an invalid bearer claim state")
        return {
            "phase": "live-redemption-settlement-refresh",
            "note_id": note_id,
            "state": "REDEEMED",
            "settled": True,
            "replayed": True,
            **settlement,
        }
    if status != "REDEMPTION_IMPORTED" or manifest.get("state") != (
        "REDEMPTION_IMPORTED"
    ):
        raise LiveNoteError("redemption is not ready for settlement refresh")
    claim = manifest.get("claim_secret")
    if not isinstance(claim, dict) or claim.get("state") != "SPENT":
        raise LiveNoteError("imported redemption has an invalid bearer claim state")
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("redemption broadcast evidence is missing")

    txid = str(broadcast["txid"])
    asset_id = str(plan["asset_id"])
    expected = int(plan["amount_base_units"])
    if expected != NOTE_AMOUNT_BASE_UNITS:
        raise LiveNoteError("redemption settlement amount is not exactly 5 USDT")

    backend = destination_backend_opener(destination)
    try:
        updated = backend.refresh()
        balance = _balance(backend._wallet.get_asset_balance(asset_id))
        matches = [
            transfer
            for transfer in backend._wallet.list_transfers(asset_id)
            if getattr(transfer, "txid", None) == txid
        ]
    finally:
        backend.close()

    if len(matches) != 1:
        raise LiveNoteError(
            "destination wallet does not contain exactly one matching redemption transfer"
        )
    transfer = matches[0]
    transfer_kind = _enum_name(getattr(transfer, "kind"))
    transfer_status = _enum_name(getattr(transfer, "status"))
    if transfer_kind != "receive_witness":
        raise LiveNoteError("matching destination redemption transfer has the wrong kind")
    requested_assignment = getattr(transfer, "requested_assignment", None)
    requested_amount = getattr(requested_assignment, "amount", None)
    if requested_amount is not None and int(requested_amount) not in {0, expected}:
        raise LiveNoteError(
            "matching destination redemption transfer has an invalid requested amount"
        )
    assignment_amounts = [
        int(amount)
        for assignment in (getattr(transfer, "assignments", ()) or ())
        if (amount := getattr(assignment, "amount", None)) is not None
    ]
    if assignment_amounts != [expected]:
        raise LiveNoteError(
            "matching destination redemption transfer has the wrong settled assignment"
        )
    if transfer_status not in {"waiting_confirmations", "settled"}:
        raise LiveNoteError(
            "matching destination redemption transfer is not pending or settled"
        )

    watch = plan.get("settlement_watch")
    if watch is None:
        if transfer_status == "waiting_confirmations":
            destination_balance_before = balance["settled"]
            destination_balance_after = destination_balance_before + expected
            if balance["future"] != destination_balance_after:
                raise LiveNoteError(
                    "destination pending balance does not equal baseline plus 5 USDT"
                )
        else:
            if balance["settled"] < expected:
                raise LiveNoteError("destination settled balance is below 5 USDT")
            destination_balance_after = balance["settled"]
            destination_balance_before = destination_balance_after - expected
        watch = {
            "txid": txid,
            "asset_id": asset_id,
            "amount_base_units": expected,
            "destination_balance_before": destination_balance_before,
            "destination_balance_after": destination_balance_after,
        }
        plan["settlement_watch"] = watch
        manifest["redemption_settlement_watch"] = watch
    elif not isinstance(watch, dict):
        raise LiveNoteError("redemption settlement watch is invalid")

    expected_watch = {
        "txid": txid,
        "asset_id": asset_id,
        "amount_base_units": expected,
    }
    for field, value in expected_watch.items():
        if watch.get(field) != value:
            raise LiveNoteError(f"redemption settlement watch has a mismatched {field}")
    try:
        destination_balance_before = int(watch["destination_balance_before"])
        destination_balance_after = int(watch["destination_balance_after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveNoteError("redemption settlement watch balances are invalid") from exc
    if destination_balance_after - destination_balance_before != expected:
        raise LiveNoteError("redemption settlement watch delta is not exactly 5 USDT")

    settled = transfer_status == "settled" and all(
        balance[field] == destination_balance_after
        for field in ("settled", "future", "spendable")
    )
    evidence = {
        "verified_at": int(clock()),
        "txid": txid,
        "batch_transfer_idx": int(getattr(transfer, "batch_transfer_idx")),
        "transfer_kind": transfer_kind,
        "transfer_status": transfer_status,
        "asset_id": asset_id,
        "amount_base_units": expected,
        "assignment_amounts": assignment_amounts,
        "destination_balance_before": destination_balance_before,
        "destination_balance_after": destination_balance_after,
        "balance": balance,
        "updated_transfers": {
            str(key): _enum_name(value) for key, value in updated.items()
        },
    }
    plan["last_settlement_refresh"] = evidence
    manifest["last_redemption_settlement_refresh"] = evidence
    if settled:
        plan.update({"status": "REDEEMED", "settlement": evidence})
        manifest.update({"state": "REDEEMED", "redemption_settlement": evidence})
    _atomic_json(paths.redemption_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    if settled:
        _append_private_jsonl(
            paths.redemption_journal_file,
            {
                "event": "REDEEMED",
                "timestamp": evidence["verified_at"],
                "note_id": note_id,
                "plan_id": str(plan["plan_id"]),
                "status": "REDEEMED",
                "txid": txid,
                "amount_base_units": expected,
                "destination_balance_before": destination_balance_before,
                "destination_balance_after": destination_balance_after,
                "transfer_kind": transfer_kind,
                "transfer_status": transfer_status,
            },
        )
    return {
        "phase": "live-redemption-settlement-refresh",
        "note_id": note_id,
        "state": manifest["state"],
        "settled": settled,
        "replayed": False,
        **evidence,
    }


def recover_live_redemption(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.open,
) -> dict[str, object]:
    """Recover only a recorded destination import; never repeat the RGB send."""

    _validate_destination(destination)
    paths, plan = load_redemption_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    status = str(plan.get("status", ""))
    if status == "REDEMPTION_IMPORTED":
        return _redemption_result(note_id=note_id, plan=plan, replayed=True)
    if status not in {"BROADCAST_RECORDED", "IMPORT_RECOVERY_REQUIRED"}:
        return {
            "phase": "live-redemption-recovery-blocked",
            "broadcast": status not in {"PLANNED", "SUPERSEDED"},
            "note_id": note_id,
            "plan_id": str(plan.get("plan_id", "")),
            "status": status,
            "instruction": (
                "Do not resend. Manual chain/wallet reconciliation is required."
                if status in {"CONFIRMATION_CONSUMED", "AMBIGUOUS"}
                else "No recovery action is available for this plan state."
            ),
        }
    return _import_recorded_redemption(
        destination,
        paths=paths,
        plan=plan,
        manifest=manifest,
        clock=clock,
        destination_backend_opener=destination_backend_opener,
    )


def _capacity_snapshot(backend: RgbLibBackend) -> dict[str, int]:
    online = getattr(backend, "_online", None)
    skip_sync = online is None
    btc = backend._wallet.get_btc_balance(online, skip_sync)
    unspents = backend._wallet.list_unspents(online, False, skip_sync)
    colorable = []
    free = []
    for local in unspents:
        utxo = getattr(local, "utxo")
        if bool(getattr(utxo, "colorable", False)) and bool(
            getattr(utxo, "exists", True)
        ):
            colorable.append(local)
            if not (getattr(local, "rgb_allocations", ()) or ()):
                free.append(local)
    return {
        "vanilla_spendable": int(getattr(btc.vanilla, "spendable")),
        "colored_spendable": int(getattr(btc.colored, "spendable")),
        "colorable_utxo_count": len(colorable),
        "free_colorable_utxo_count": len(free),
    }


def reconcile_live_redemption_prebroadcast_rejection(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Prove the pinned rgb-lib allocation error occurred before send_end."""

    _validate_destination(destination)
    paths, plan = load_redemption_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if plan.get("status") == "PRE_BROADCAST_REJECTED":
        return {
            "phase": "live-redemption-prebroadcast-reconciled",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "status": "PRE_BROADCAST_REJECTED",
            "replayed": True,
        }
    if (
        plan.get("status") != "AMBIGUOUS"
        or manifest.get("state") != "AMBIGUOUS"
        or plan.get("last_error_type") != "InsufficientAllocationSlots"
        or plan.get("broadcast")
    ):
        raise LiveNoteError("ambiguous redemption is not a proven pre-broadcast rejection")
    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    backend = note_backend_opener(note_settings)
    try:
        backend.refresh()
        balance = backend.spendable_balance(str(plan["asset_id"]))
        transfers = backend._wallet.list_transfers(str(plan["asset_id"]))
        sends = [
            transfer
            for transfer in transfers
            if _enum_name(getattr(transfer, "kind", "")) == "send"
        ]
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if balance != int(plan["source_spendable_before"]) or sends:
        raise LiveNoteError(
            "wallet evidence cannot prove that the rejected redemption stayed pre-broadcast"
        )
    reconciled_at = int(clock())
    plan.update({"status": "PRE_BROADCAST_REJECTED", "reconciled_at": reconciled_at})
    manifest.update({"state": "PRE_BROADCAST_REJECTED"})
    claim = manifest.get("claim_secret")
    if not isinstance(claim, dict) or claim.get("state") != "RESERVED":
        raise LiveNoteError("claim reservation is not in the expected recovery state")
    claim.update({"state": "READY"})
    claim.pop("redemption_plan_id", None)
    claim.pop("reserved_at", None)
    claim.pop("destination_invoice_sha256", None)
    _atomic_json(paths.redemption_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "PRE_BROADCAST_REJECTED",
            "timestamp": reconciled_at,
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "status": "PRE_BROADCAST_REJECTED",
            "error_type": "InsufficientAllocationSlots",
            "source_spendable": balance,
            **capacity,
        },
    )
    return {
        "phase": "live-redemption-prebroadcast-reconciled",
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        "status": "PRE_BROADCAST_REJECTED",
        "replayed": False,
        "source_spendable": balance,
        **capacity,
    }


def _capacity_plan_from_payload(payload: Mapping[str, Any]) -> LiveCapacityPlan:
    try:
        values = {field.name: payload[field.name] for field in fields(LiveCapacityPlan)}
        return LiveCapacityPlan(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveNoteError("durable capacity plan is invalid") from exc


def _btc_topup_plan_from_payload(payload: Mapping[str, Any]) -> LiveBtcTopupPlan:
    try:
        values = {field.name: payload[field.name] for field in fields(LiveBtcTopupPlan)}
        return LiveBtcTopupPlan(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveNoteError("durable note BTC top-up plan is invalid") from exc


def prepare_live_funding_btc_topup_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveBtcTopupPlan, dict[str, object]]:
    """Plan a dock-funded Bitcoin top-up for one disposable note.

    This replaces the interactive faucet step. It is still a separate Bitcoin
    spend with its own exact confirmation and never authorizes the later RGB
    funding send.
    """

    _validate_sender(sender)
    if plan_lifetime_seconds < 60:
        raise LiveNoteError("note BTC top-up plan lifetime is too short")
    paths, funding = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)

    if paths.funding_btc_topup_plan_file.exists():
        existing = _read_json(paths.funding_btc_topup_plan_file)
        plan = _btc_topup_plan_from_payload(existing)
        if existing.get("status") != "PLANNED":
            raise LiveNoteError("note BTC top-up plan is already consumed")
        if manifest.get("state") != "BTC_TOPUP_PLANNED":
            raise LiveNoteError("note BTC top-up state does not match its plan")
        if int(existing.get("expiration_timestamp", 0)) > int(clock()):
            return plan, manifest
        previous_id = str(existing["plan_id"])
        archive = (
            paths.funding_btc_topup_plan_file.parent
            / f"funding-btc-topup-{previous_id}.json"
        )
        if archive.exists():
            raise LiveNoteError("expired note BTC top-up archive already exists")
        os.replace(paths.funding_btc_topup_plan_file, archive)
        existing.update({"status": "SUPERSEDED", "superseded_at": int(clock())})
        _atomic_json(archive, existing)
        manifest.update(
            {
                "state": "AWAITING_FUNDING",
                "superseded_funding_btc_topup_plan_id": previous_id,
                "superseded_funding_btc_topup_plan_path": str(archive),
            }
        )
        _atomic_json(paths.manifest_file, manifest)

    if manifest.get("state") != "AWAITING_FUNDING":
        raise LiveNoteError("note is not ready for automatic BTC top-up")
    if funding.get("status") != "PLANNED":
        raise LiveNoteError("RGB funding plan is not safely awaiting confirmation")
    current_note_sat = int(funding.get("note_vanilla_spendable_before", 0))
    target_note_sat = int(funding.get("recommended_redemption_vanilla_sat", 0))
    if target_note_sat < 1:
        raise LiveNoteError("note BTC safety target is invalid")
    if current_note_sat >= target_note_sat:
        raise LiveNoteError("note already has its required Bitcoin fee reserve")
    destination = str(funding.get("note_btc_address") or "")
    if not destination:
        raise LiveNoteError("note Bitcoin destination is missing")

    backend = sender_backend_opener(sender)
    try:
        backend.refresh()
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    topup_amount = target_note_sat - current_note_sat
    if capacity["vanilla_spendable"] < topup_amount + 1_000:
        raise LiveNoteError(
            "issuing dock wallet lacks enough vanilla Mutinynet sats to fund "
            "the note fee reserve"
        )

    planned_at = int(clock())
    plan = LiveBtcTopupPlan(
        version=LIVE_NOTE_VERSION,
        plan_id=id_factory(),
        network=NETWORK,
        operation="fund-note-bitcoin-reserve",
        note_id=note_id,
        planned_timestamp=planned_at,
        source_wallet=f"dock-wallet:{sender.master_fingerprint}",
        destination_address=destination,
        topup_amount_sat=topup_amount,
        note_vanilla_spendable_before=current_note_sat,
        target_note_vanilla_sat=target_note_sat,
        source_vanilla_spendable_before=capacity["vanilla_spendable"],
        source_colored_spendable_before=capacity["colored_spendable"],
        fee_rate_sat_vb=sender.fee_rate,
        expiration_timestamp=planned_at + plan_lifetime_seconds,
    )
    _atomic_json(
        paths.funding_btc_topup_plan_file,
        _plan_payload(plan, status="PLANNED"),
    )
    manifest.update(
        {
            "state": "BTC_TOPUP_PLANNED",
            "funding_btc_topup_plan_id": plan.plan_id,
            "funding_btc_topup_plan_path": str(paths.funding_btc_topup_plan_file),
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    return plan, manifest


def execute_live_funding_btc_topup_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_id: str,
    confirmation_phrase: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Send the note's Bitcoin fee reserve once after exact confirmation."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.funding_btc_topup_plan_file)
    _btc_topup_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("plan_id") != plan_id or plan.get("status") != "PLANNED":
        raise LiveNoteConfirmationRequired("unknown or consumed note BTC top-up plan")
    if manifest.get("state") != "BTC_TOPUP_PLANNED":
        raise LiveNoteError("note is not awaiting its Bitcoin reserve top-up")
    if confirmation_phrase != f"CONFIRM {plan_id}":
        raise LiveNoteConfirmationRequired("exact confirmation phrase is required")
    if int(plan.get("expiration_timestamp", 0)) <= int(clock()):
        raise LiveNoteConfirmationRequired("note BTC top-up plan expired")

    backend = sender_backend_opener(sender)
    try:
        backend.refresh()
        capacity = _capacity_snapshot(backend)
        if capacity["vanilla_spendable"] != int(
            plan["source_vanilla_spendable_before"]
        ):
            raise LiveNoteConfirmationRequired(
                "issuing dock BTC balance changed; review a new top-up plan"
            )
        if capacity["colored_spendable"] != int(
            plan["source_colored_spendable_before"]
        ):
            raise LiveNoteConfirmationRequired(
                "issuing dock colored BTC balance changed; review a new top-up plan"
            )

        now = int(clock())
        plan.update({"status": "CONFIRMATION_CONSUMED", "consumed_at": now})
        manifest.update(
            {
                "state": "BTC_TOPUP_BROADCAST_IN_PROGRESS",
                "funding_btc_topup_confirmation_consumed_at": now,
            }
        )
        _atomic_json(paths.funding_btc_topup_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        try:
            txid = backend._wallet.send_btc(
                backend._online,
                str(plan["destination_address"]),
                int(plan["topup_amount_sat"]),
                int(plan["fee_rate_sat_vb"]),
                False,
            )
        except Exception as exc:
            plan.update(
                {
                    "status": "AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            manifest.update(
                {
                    "state": "BTC_TOPUP_AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            _atomic_json(paths.funding_btc_topup_plan_file, plan)
            _atomic_json(paths.manifest_file, manifest)
            raise LiveNoteError(
                "note BTC top-up outcome is ambiguous; do not resend"
            ) from exc
    finally:
        backend.close()

    txid = str(txid)
    try:
        valid_txid = len(txid) == 64 and int(txid, 16) >= 0
    except ValueError:
        valid_txid = False
    if not valid_txid:
        plan.update(
            {
                "status": "AMBIGUOUS",
                "last_error_type": "InvalidTxid",
                "last_error": "send_btc returned an invalid transaction ID",
            }
        )
        manifest.update(
            {
                "state": "BTC_TOPUP_AMBIGUOUS",
                "last_error_type": "InvalidTxid",
                "last_error": "send_btc returned an invalid transaction ID",
            }
        )
        _atomic_json(paths.funding_btc_topup_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError("note BTC top-up returned invalid evidence; do not resend")

    broadcast = {
        "txid": txid,
        "destination_address": str(plan["destination_address"]),
        "topup_amount_sat": int(plan["topup_amount_sat"]),
    }
    plan.update({"status": "BROADCAST_RECORDED", "broadcast": broadcast})
    manifest.update(
        {"state": "BTC_TOPUP_BROADCAST_RECORDED", "funding_btc_topup": broadcast}
    )
    _atomic_json(paths.funding_btc_topup_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-note-btc-topup-broadcast",
        "broadcast": True,
        "note_id": note_id,
        "plan_id": plan_id,
        **broadcast,
        "warning": "wait for one Mutinynet confirmation, then check the note",
    }


def refresh_live_funding_btc_topup(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Verify the exact top-up tx and the note's confirmed Bitcoin reserve."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.funding_btc_topup_plan_file)
    _btc_topup_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("status") == "TOPUP_CONFIRMED":
        return {
            "phase": "live-note-btc-topup-ready",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "confirmed": True,
            "replayed": True,
            **dict(plan.get("confirmation", {})),
        }
    if (
        plan.get("status") != "BROADCAST_RECORDED"
        or manifest.get("state") != "BTC_TOPUP_BROADCAST_RECORDED"
    ):
        raise LiveNoteError("note BTC top-up is not ready for confirmation refresh")
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("note BTC top-up broadcast evidence is missing")

    sender_backend = sender_backend_opener(sender)
    try:
        sender_backend.refresh()
        matches = [
            tx
            for tx in sender_backend._wallet.list_transactions(
                sender_backend._online, False
            )
            if tx.txid == broadcast["txid"]
        ]
    finally:
        sender_backend.close()
    if len(matches) > 1:
        raise LiveNoteError("note BTC top-up transaction evidence is not unique")
    if not matches or getattr(matches[0], "confirmation_time", None) is None:
        return {
            "phase": "live-note-btc-topup-waiting",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "txid": str(broadcast["txid"]),
            "confirmed": False,
        }
    note_settings = _note_settings(
        sender,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    note_backend = note_backend_opener(note_settings)
    try:
        note_backend.refresh()
        note_capacity = _capacity_snapshot(note_backend)
    finally:
        note_backend.close()
    if note_capacity["vanilla_spendable"] < int(plan["target_note_vanilla_sat"]):
        raise LiveNoteError(
            "top-up transaction confirmed but the note fee reserve is not spendable"
        )

    confirmation_time = matches[0].confirmation_time
    confirmation = {
        "txid": str(broadcast["txid"]),
        "block_height": int(confirmation_time.height),
        "block_timestamp": int(confirmation_time.timestamp),
        "verified_at": int(clock()),
        "note_vanilla_spendable_sat": note_capacity["vanilla_spendable"],
    }
    plan.update({"status": "TOPUP_CONFIRMED", "confirmation": confirmation})
    manifest.update(
        {
            "state": "AWAITING_FUNDING",
            "funding_btc_topup_confirmation": confirmation,
        }
    )
    _atomic_json(paths.funding_btc_topup_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-note-btc-topup-ready",
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        "confirmed": True,
        "replayed": False,
        **confirmation,
    }


def reconcile_live_funding_btc_topup_prebroadcast_rejection(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Prove an ambiguous note top-up left both wallets unchanged.

    ``send_btc`` may surface an indexer transport error before returning a txid.
    The confirmation is already consumed, so recovery must never retry that
    plan.  A fresh plan is allowed only when the issuing wallet still has the
    exact pre-send inventory and the disposable note wallet has no Bitcoin
    inventory at all.
    """

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.funding_btc_topup_plan_file)
    _btc_topup_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if (
        plan.get("status") != "AMBIGUOUS"
        or manifest.get("state") != "BTC_TOPUP_AMBIGUOUS"
        or plan.get("broadcast")
    ):
        raise LiveNoteError(
            "ambiguous note BTC top-up is not ready for pre-broadcast reconciliation"
        )

    sender_backend = sender_backend_opener(sender)
    try:
        sender_backend.refresh()
        sender_capacity = _capacity_snapshot(sender_backend)
    finally:
        sender_backend.close()
    if (
        sender_capacity["vanilla_spendable"]
        != int(plan["source_vanilla_spendable_before"])
        or sender_capacity["colored_spendable"]
        != int(plan["source_colored_spendable_before"])
    ):
        raise LiveNoteError(
            "issuing wallet inventory changed; the ambiguous top-up cannot be retried"
        )

    note_settings = _note_settings(
        sender,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    note_backend = note_backend_opener(note_settings)
    try:
        note_backend.refresh()
        note_capacity = _capacity_snapshot(note_backend)
    finally:
        note_backend.close()
    if any(note_capacity.values()):
        raise LiveNoteError(
            "disposable note Bitcoin inventory changed; the ambiguous top-up "
            "cannot be retried"
        )

    reconciled_at = int(clock())
    previous_id = str(plan["plan_id"])
    archive = (
        paths.funding_btc_topup_plan_file.parent
        / f"funding-btc-topup-{previous_id}.json"
    )
    if archive.exists():
        raise LiveNoteError("reconciled note BTC top-up archive already exists")
    os.replace(paths.funding_btc_topup_plan_file, archive)
    plan.update(
        {
            "status": "PRE_BROADCAST_REJECTED",
            "reconciled_at": reconciled_at,
        }
    )
    _atomic_json(archive, plan)
    manifest.update(
        {
            "state": "AWAITING_FUNDING",
            "reconciled_funding_btc_topup_plan_id": previous_id,
            "reconciled_funding_btc_topup_plan_path": str(archive),
        }
    )
    manifest.pop("funding_btc_topup_plan_id", None)
    manifest.pop("funding_btc_topup_plan_path", None)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-note-btc-topup-prebroadcast-reconciled",
        "note_id": note_id,
        "plan_id": previous_id,
        "status": "PRE_BROADCAST_REJECTED",
        "broadcast": False,
        "replayed": False,
        "sender_capacity": sender_capacity,
        "note_capacity": note_capacity,
    }


def reconcile_live_funding_prebroadcast_rejection(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Prove an old allocation-slot failure did not create a funding send.

    Funding plans created before allocation-slot handling was added were
    conservatively marked AMBIGUOUS.  The pinned rgb-lib exception is known to
    occur before ``send_end``; we additionally require the exact source RGB
    balance and absence of a send to the note's unique recipient before
    allowing a separate capacity plan.
    """

    _validate_sender(sender)
    paths, plan = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if plan.get("status") == "PRE_BROADCAST_REJECTED":
        return {
            "phase": "live-funding-prebroadcast-reconciled",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "status": "PRE_BROADCAST_REJECTED",
            "replayed": True,
        }
    if (
        plan.get("status") != "AMBIGUOUS"
        or manifest.get("state") != "AMBIGUOUS"
        or plan.get("last_error_type") != "InsufficientAllocationSlots"
        or plan.get("broadcast")
    ):
        raise LiveNoteError("ambiguous funding is not a proven pre-broadcast rejection")

    backend = sender_backend_opener(sender)
    try:
        backend.refresh()
        asset_id = str(plan["asset_id"])
        balance = backend.spendable_balance(asset_id)
        recipient_id = str(plan["recipient_id"])
        sends_to_recipient = [
            transfer
            for transfer in backend._wallet.list_transfers(asset_id)
            if _enum_name(getattr(transfer, "kind", "")) == "send"
            and str(getattr(transfer, "recipient_id", "")) == recipient_id
        ]
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if balance != int(plan["source_spendable_before"]) or sends_to_recipient:
        raise LiveNoteError(
            "wallet evidence cannot prove that the rejected funding stayed pre-broadcast"
        )

    reconciled_at = int(clock())
    plan.update({"status": "PRE_BROADCAST_REJECTED", "reconciled_at": reconciled_at})
    manifest.update({"state": "PRE_BROADCAST_REJECTED"})
    _atomic_json(paths.plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-funding-prebroadcast-reconciled",
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        "status": "PRE_BROADCAST_REJECTED",
        "replayed": False,
        "source_spendable": balance,
        **capacity,
    }


def prepare_live_funding_capacity_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    utxo_size_sat: int = MIN_COLORABLE_UTXO_SAT,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveCapacityPlan, dict[str, object]]:
    """Prepare a separately confirmed source-wallet RGB capacity transaction."""

    _validate_sender(sender)
    if utxo_size_sat < MIN_COLORABLE_UTXO_SAT or plan_lifetime_seconds < 60:
        raise LiveNoteError("capacity UTXO size or plan lifetime is too small")
    now = int(clock())
    paths, funding = load_funding_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if funding.get("status") == "AMBIGUOUS":
        reconcile_live_funding_prebroadcast_rejection(
            sender,
            vault_root=vault_root,
            note_id=note_id,
            clock=clock,
            sender_backend_opener=sender_backend_opener,
        )
        funding = _read_json(paths.plan_file)
        manifest = _read_json(paths.manifest_file)
    if funding.get("status") != "PRE_BROADCAST_REJECTED":
        raise LiveNoteError("funding is not ready for source-capacity preparation")
    if paths.funding_capacity_plan_file.exists():
        existing = _read_json(paths.funding_capacity_plan_file)
        plan = _capacity_plan_from_payload(existing)
        if existing.get("status") != "PLANNED":
            raise LiveNoteError("funding-capacity plan is already consumed")
        if manifest.get("state") != "FUNDING_CAPACITY_PLANNED":
            raise LiveNoteError("funding-capacity plan and manifest state disagree")
        if int(existing.get("expiration_timestamp", 0)) > now:
            return plan, manifest

        archive = (
            paths.funding_capacity_plan_file.parent
            / f"funding-capacity-{plan.plan_id}.json"
        )
        if archive.exists():
            raise LiveNoteError("expired funding-capacity plan archive already exists")
        os.replace(paths.funding_capacity_plan_file, archive)
        existing.update({"status": "SUPERSEDED", "superseded_at": now})
        _atomic_json(archive, existing)
        manifest.update(
            {
                "state": "PRE_BROADCAST_REJECTED",
                "superseded_funding_capacity_plan_id": plan.plan_id,
                "superseded_funding_capacity_plan_path": str(archive),
            }
        )
        manifest.pop("funding_capacity_plan_id", None)
        manifest.pop("funding_capacity_plan_path", None)
        _atomic_json(paths.manifest_file, manifest)
    elif manifest.get("state") != "PRE_BROADCAST_REJECTED":
        raise LiveNoteError("funding is not ready for source-capacity preparation")

    backend = sender_backend_opener(sender)
    try:
        backend.refresh()
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if capacity["free_colorable_utxo_count"] != 0:
        raise LiveNoteError(
            "source wallet reports free colorable capacity; manual reconciliation is required"
        )
    capacity_minimum_sat = utxo_size_sat + minimum_capacity_fee_sat(sender.fee_rate)
    if capacity["vanilla_spendable"] < capacity_minimum_sat:
        raise LiveNoteError("source wallet lacks enough vanilla sats for RGB capacity")

    planned_at = int(clock())
    plan = LiveCapacityPlan(
        version=LIVE_NOTE_VERSION,
        plan_id=id_factory(),
        network=NETWORK,
        operation="create-funding-source-colorable-utxo",
        note_id=note_id,
        planned_timestamp=planned_at,
        source_wallet=f"dock-wallet:{sender.master_fingerprint}",
        vanilla_spendable_before=capacity["vanilla_spendable"],
        colored_spendable_before=capacity["colored_spendable"],
        colorable_utxo_count_before=capacity["colorable_utxo_count"],
        free_colorable_utxo_count_before=capacity["free_colorable_utxo_count"],
        utxo_count=1,
        utxo_size_sat=utxo_size_sat,
        fee_rate_sat_vb=sender.fee_rate,
        expiration_timestamp=planned_at + plan_lifetime_seconds,
    )
    _atomic_json(
        paths.funding_capacity_plan_file,
        _plan_payload(plan, status="PLANNED"),
    )
    manifest.update(
        {
            "state": "FUNDING_CAPACITY_PLANNED",
            "funding_capacity_plan_id": plan.plan_id,
            "funding_capacity_plan_path": str(paths.funding_capacity_plan_file),
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    return plan, manifest


def execute_live_funding_capacity_plan(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_id: str,
    confirmation_phrase: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Create one source-wallet colorable UTXO after its own confirmation."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.funding_capacity_plan_file)
    _capacity_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("plan_id") != plan_id or plan.get("status") != "PLANNED":
        raise LiveNoteConfirmationRequired("unknown or consumed funding-capacity plan")
    if manifest.get("state") != "FUNDING_CAPACITY_PLANNED":
        raise LiveNoteError("funding is not awaiting its capacity transaction")
    if confirmation_phrase != f"CONFIRM {plan_id}":
        raise LiveNoteConfirmationRequired("exact confirmation phrase is required")
    if int(plan.get("expiration_timestamp", 0)) <= int(clock()):
        raise LiveNoteConfirmationRequired("funding-capacity plan expired")

    backend = sender_backend_opener(sender)
    try:
        plan = _read_json(paths.funding_capacity_plan_file)
        manifest = _read_json(paths.manifest_file)
        now = int(clock())
        if plan.get("status") != "PLANNED" or manifest.get("state") != (
            "FUNDING_CAPACITY_PLANNED"
        ):
            raise LiveNoteError("funding-capacity plan changed during validation")
        if int(plan.get("expiration_timestamp", 0)) <= now:
            raise LiveNoteConfirmationRequired("funding-capacity plan expired")
        backend.refresh()
        capacity = _capacity_snapshot(backend)
        if capacity["vanilla_spendable"] != int(plan["vanilla_spendable_before"]):
            raise LiveNoteConfirmationRequired(
                "source BTC balance changed; review a new capacity plan"
            )
        if capacity["free_colorable_utxo_count"] != int(
            plan["free_colorable_utxo_count_before"]
        ):
            raise LiveNoteConfirmationRequired(
                "source RGB capacity changed; review a new capacity plan"
            )
        before = {
            tx.txid for tx in backend._wallet.list_transactions(backend._online, False)
        }
        plan.update({"status": "CONFIRMATION_CONSUMED", "consumed_at": now})
        manifest.update(
            {
                "state": "FUNDING_CAPACITY_BROADCAST_IN_PROGRESS",
                "funding_capacity_confirmation_consumed_at": now,
            }
        )
        _atomic_json(paths.funding_capacity_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        try:
            created = backend._wallet.create_utxos(
                backend._online,
                False,
                int(plan["utxo_count"]),
                int(plan["utxo_size_sat"]),
                int(plan["fee_rate_sat_vb"]),
                False,
            )
        except Exception as exc:
            plan.update(
                {
                    "status": "AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            manifest.update(
                {
                    "state": "FUNDING_CAPACITY_AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            _atomic_json(paths.funding_capacity_plan_file, plan)
            _atomic_json(paths.manifest_file, manifest)
            raise LiveNoteError(
                "funding-capacity transaction outcome is ambiguous; do not retry"
            ) from exc
        after = [
            tx
            for tx in backend._wallet.list_transactions(backend._online, False)
            if tx.txid not in before
        ]
    finally:
        backend.close()
    if int(created) != int(plan["utxo_count"]) or len(after) != 1:
        plan.update(
            {
                "status": "AMBIGUOUS",
                "last_error_type": "UnexpectedCapacityEvidence",
                "last_error": "create_utxos returned without one exact transaction",
            }
        )
        manifest.update(
            {
                "state": "FUNDING_CAPACITY_AMBIGUOUS",
                "last_error_type": "UnexpectedCapacityEvidence",
                "last_error": "create_utxos returned without one exact transaction",
            }
        )
        _atomic_json(paths.funding_capacity_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        raise LiveNoteError(
            "funding-capacity broadcast returned unexpected evidence; do not retry"
        )
    tx = after[0]
    broadcast = {
        "txid": tx.txid,
        "created_colorable_utxos": int(created),
        "transaction_type": _enum_name(tx.transaction_type),
        "received_sat": int(tx.received),
        "sent_sat": int(tx.sent),
        "fee_sat": int(tx.fee),
    }
    plan.update({"status": "BROADCAST_RECORDED", "broadcast": broadcast})
    manifest.update(
        {"state": "FUNDING_CAPACITY_BROADCAST_RECORDED", "funding_capacity": broadcast}
    )
    _atomic_json(paths.funding_capacity_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-funding-capacity-broadcast",
        "broadcast": True,
        "note_id": note_id,
        "plan_id": plan_id,
        **broadcast,
        "warning": "wait for one Mutinynet confirmation, then check the note",
    }


def refresh_live_funding_capacity(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Confirm the source capacity transaction and its one free RGB UTXO."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.funding_capacity_plan_file)
    _capacity_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("status") == "CAPACITY_CONFIRMED":
        return {
            "phase": "live-funding-capacity-ready",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "confirmed": True,
            "replayed": True,
            **dict(plan.get("confirmation", {})),
        }
    if (
        plan.get("status") != "BROADCAST_RECORDED"
        or manifest.get("state") != "FUNDING_CAPACITY_BROADCAST_RECORDED"
    ):
        raise LiveNoteError("funding-capacity transaction is not ready for refresh")
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("funding-capacity broadcast evidence is missing")

    backend = sender_backend_opener(sender)
    try:
        backend.refresh()
        matches = [
            tx
            for tx in backend._wallet.list_transactions(backend._online, False)
            if tx.txid == broadcast["txid"]
        ]
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if len(matches) != 1:
        raise LiveNoteError("funding-capacity transaction is missing from source wallet")
    tx = matches[0]
    confirmation_time = getattr(tx, "confirmation_time", None)
    if confirmation_time is None:
        return {
            "phase": "live-funding-capacity-waiting",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "txid": str(broadcast["txid"]),
            "confirmed": False,
            **capacity,
        }
    if capacity["free_colorable_utxo_count"] < 1:
        raise LiveNoteError(
            "funding-capacity transaction confirmed but no free colorable UTXO is visible"
        )
    confirmation = {
        "txid": str(broadcast["txid"]),
        "block_height": int(confirmation_time.height),
        "block_timestamp": int(confirmation_time.timestamp),
        "verified_at": int(clock()),
        **capacity,
    }
    plan.update({"status": "CAPACITY_CONFIRMED", "confirmation": confirmation})
    manifest.update(
        {"state": "FUNDING_CAPACITY_READY", "funding_capacity_confirmation": confirmation}
    )
    _atomic_json(paths.funding_capacity_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    return {
        "phase": "live-funding-capacity-ready",
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        "confirmed": True,
        "replayed": False,
        **confirmation,
    }


def prepare_live_funding_after_capacity(
    sender: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
    sender_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveFundingPlan, dict[str, object]]:
    """Archive the rejected funding plan after capacity and prepare a fresh one."""

    _validate_sender(sender)
    paths = LiveNotePaths.create(vault_root, note_id)
    capacity = _read_json(paths.funding_capacity_plan_file)
    manifest = _read_json(paths.manifest_file)
    if (
        capacity.get("status") != "CAPACITY_CONFIRMED"
        or manifest.get("state") != "FUNDING_CAPACITY_READY"
    ):
        raise LiveNoteError("confirmed source capacity is required before re-planning")
    previous = _read_json(paths.plan_file)
    if previous.get("status") != "PRE_BROADCAST_REJECTED":
        raise LiveNoteError("only the proven rejected funding plan may be superseded")
    previous_id = str(previous.get("plan_id", ""))
    archive = paths.plan_file.parent / f"funding-{previous_id}.json"
    if archive.exists():
        raise LiveNoteError("rejected funding plan archive already exists")
    os.replace(paths.plan_file, archive)
    previous.update(
        {"status": "SUPERSEDED_AFTER_CAPACITY", "superseded_at": int(clock())}
    )
    _atomic_json(archive, previous)
    manifest.update(
        {
            "state": "PLAN_RENEWAL_IN_PROGRESS",
            "superseded_funding_plan_id": previous_id,
            "superseded_funding_plan_path": str(archive),
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    return _finish_live_funding_plan(
        sender,
        paths=paths,
        manifest=manifest,
        invoice_lifetime_seconds=86_400,
        plan_lifetime_seconds=plan_lifetime_seconds,
        clock=clock,
        plan_id=id_factory(),
        note_backend_opener=note_backend_opener,
        sender_backend_opener=sender_backend_opener,
    )


def prepare_live_redemption_capacity_plan(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    utxo_size_sat: int = MIN_COLORABLE_UTXO_SAT,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveCapacityPlan, dict[str, object]]:
    """Prepare a separately confirmed Bitcoin transaction for RGB change capacity."""

    _validate_destination(destination)
    if utxo_size_sat < MIN_COLORABLE_UTXO_SAT or plan_lifetime_seconds < 60:
        raise LiveNoteError("capacity UTXO size or plan lifetime is too small")
    paths, redemption = load_redemption_plan(vault_root, note_id)
    manifest = _read_json(paths.manifest_file)
    if (
        redemption.get("status") != "PRE_BROADCAST_REJECTED"
        or manifest.get("state") != "PRE_BROADCAST_REJECTED"
    ):
        raise LiveNoteError("redemption is not ready for capacity preparation")
    if paths.redemption_capacity_plan_file.exists():
        existing = _read_json(paths.redemption_capacity_plan_file)
        plan = _capacity_plan_from_payload(existing)
        if existing.get("status") != "PLANNED":
            raise LiveNoteError("redemption-capacity plan is already consumed")
        return plan, manifest
    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    backend = note_backend_opener(note_settings)
    try:
        backend.refresh()
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if capacity["free_colorable_utxo_count"] != 0:
        raise LiveNoteError("note already has free colorable capacity")
    capacity_minimum_sat = utxo_size_sat + minimum_capacity_fee_sat(
        destination.fee_rate
    )
    if capacity["vanilla_spendable"] < capacity_minimum_sat:
        raise LiveNoteError("note lacks enough vanilla sats for RGB change capacity")
    planned_at = int(clock())
    plan = LiveCapacityPlan(
        version=LIVE_NOTE_VERSION,
        plan_id=id_factory(),
        network=NETWORK,
        operation="create-redemption-colorable-utxo",
        note_id=note_id,
        planned_timestamp=planned_at,
        source_wallet="disposable-note",
        vanilla_spendable_before=capacity["vanilla_spendable"],
        colored_spendable_before=capacity["colored_spendable"],
        colorable_utxo_count_before=capacity["colorable_utxo_count"],
        free_colorable_utxo_count_before=capacity["free_colorable_utxo_count"],
        utxo_count=1,
        utxo_size_sat=utxo_size_sat,
        fee_rate_sat_vb=destination.fee_rate,
        expiration_timestamp=planned_at + plan_lifetime_seconds,
    )
    _atomic_json(
        paths.redemption_capacity_plan_file,
        _plan_payload(plan, status="PLANNED"),
    )
    manifest.update(
        {
            "state": "CAPACITY_PLANNED",
            "redemption_capacity_plan_id": plan.plan_id,
            "redemption_capacity_plan_path": str(
                paths.redemption_capacity_plan_file
            ),
        }
    )
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "CAPACITY_PLAN_READY",
            "timestamp": planned_at,
            "note_id": note_id,
            "plan_id": plan.plan_id,
            "status": "PLANNED",
            "utxo_count": 1,
            "utxo_size_sat": utxo_size_sat,
            "fee_rate_sat_vb": destination.fee_rate,
        },
    )
    return plan, manifest


def execute_live_redemption_capacity_plan(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    plan_id: str,
    confirmation_phrase: str,
    clock: Callable[[], float] = time.time,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Create one colorable UTXO once; this is a separate Bitcoin broadcast."""

    _validate_destination(destination)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.redemption_capacity_plan_file)
    _capacity_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("plan_id") != plan_id or plan.get("status") != "PLANNED":
        raise LiveNoteConfirmationRequired("unknown or consumed capacity plan")
    if manifest.get("state") != "CAPACITY_PLANNED":
        raise LiveNoteError("note is not awaiting its capacity transaction")
    if confirmation_phrase != f"CONFIRM {plan_id}":
        raise LiveNoteConfirmationRequired("exact confirmation phrase is required")
    if int(plan.get("expiration_timestamp", 0)) <= int(clock()):
        raise LiveNoteConfirmationRequired("capacity plan expired before confirmation")
    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    backend = note_backend_opener(note_settings)
    try:
        plan = _read_json(paths.redemption_capacity_plan_file)
        manifest = _read_json(paths.manifest_file)
        now = int(clock())
        if plan.get("status") != "PLANNED" or manifest.get("state") != "CAPACITY_PLANNED":
            raise LiveNoteError("capacity plan changed while acquiring the note wallet")
        if plan.get("plan_id") != plan_id:
            raise LiveNoteConfirmationRequired("capacity plan was superseded")
        if int(plan.get("expiration_timestamp", 0)) <= now:
            raise LiveNoteConfirmationRequired("capacity plan expired during validation")
        backend.refresh()
        capacity = _capacity_snapshot(backend)
        if capacity["vanilla_spendable"] != int(plan["vanilla_spendable_before"]):
            raise LiveNoteConfirmationRequired(
                "note BTC balance changed; prepare a new capacity plan"
            )
        if capacity["free_colorable_utxo_count"] != int(
            plan["free_colorable_utxo_count_before"]
        ):
            raise LiveNoteConfirmationRequired(
                "note colorable capacity changed; prepare a new capacity plan"
            )
        before = {
            tx.txid
            for tx in backend._wallet.list_transactions(backend._online, False)
        }
        plan.update({"status": "CONFIRMATION_CONSUMED", "consumed_at": now})
        manifest.update(
            {
                "state": "CAPACITY_BROADCAST_IN_PROGRESS",
                "capacity_confirmation_consumed_at": now,
            }
        )
        _atomic_json(paths.redemption_capacity_plan_file, plan)
        _atomic_json(paths.manifest_file, manifest)
        _append_private_jsonl(
            paths.redemption_journal_file,
            {
                "event": "CAPACITY_CONFIRMATION_CONSUMED",
                "timestamp": now,
                "note_id": note_id,
                "plan_id": plan_id,
                "status": "CONFIRMATION_CONSUMED",
            },
        )
        try:
            created = backend._wallet.create_utxos(
                backend._online,
                False,
                int(plan["utxo_count"]),
                int(plan["utxo_size_sat"]),
                int(plan["fee_rate_sat_vb"]),
                False,
            )
        except Exception as exc:
            failed_at = int(clock())
            plan.update(
                {
                    "status": "AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            manifest.update(
                {
                    "state": "CAPACITY_AMBIGUOUS",
                    "last_error_type": type(exc).__name__,
                    "last_error": str(exc),
                }
            )
            _atomic_json(paths.redemption_capacity_plan_file, plan)
            _atomic_json(paths.manifest_file, manifest)
            _append_private_jsonl(
                paths.redemption_journal_file,
                {
                    "event": "CAPACITY_AMBIGUOUS",
                    "timestamp": failed_at,
                    "note_id": note_id,
                    "plan_id": plan_id,
                    "status": "AMBIGUOUS",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise LiveNoteError(
                "capacity transaction outcome is ambiguous; do not retry"
            ) from exc
        after = [
            tx
            for tx in backend._wallet.list_transactions(backend._online, False)
            if tx.txid not in before
        ]
    finally:
        backend.close()
    if int(created) != int(plan["utxo_count"]) or len(after) != 1:
        raise LiveNoteError(
            "capacity broadcast returned unexpected evidence; do not retry"
        )
    tx = after[0]
    broadcast = {
        "txid": tx.txid,
        "created_colorable_utxos": int(created),
        "transaction_type": _enum_name(tx.transaction_type),
        "received_sat": int(tx.received),
        "sent_sat": int(tx.sent),
        "fee_sat": int(tx.fee),
    }
    recorded_at = int(clock())
    plan.update({"status": "BROADCAST_RECORDED", "broadcast": broadcast})
    manifest.update(
        {"state": "CAPACITY_BROADCAST_RECORDED", "capacity": broadcast}
    )
    _atomic_json(paths.redemption_capacity_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "CAPACITY_BROADCAST_RECORDED",
            "timestamp": recorded_at,
            "note_id": note_id,
            "plan_id": plan_id,
            "status": "BROADCAST_RECORDED",
            **broadcast,
        },
    )
    return {
        "phase": "live-redemption-capacity-broadcast",
        "broadcast": True,
        "note_id": note_id,
        "plan_id": plan_id,
        **broadcast,
        "warning": "wait for Mutinynet confirmation before preparing redemption",
    }


def refresh_live_redemption_capacity(
    destination: RgbSettings,
    *,
    vault_root: Path,
    note_id: str,
    clock: Callable[[], float] = time.time,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> dict[str, object]:
    """Accept capacity only after its exact tx confirms and a free UTXO exists."""

    _validate_destination(destination)
    paths = LiveNotePaths.create(vault_root, note_id)
    plan = _read_json(paths.redemption_capacity_plan_file)
    _capacity_plan_from_payload(plan)
    manifest = _read_json(paths.manifest_file)
    if plan.get("status") == "CAPACITY_CONFIRMED":
        return {
            "phase": "live-redemption-capacity-ready",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "confirmed": True,
            "replayed": True,
            **dict(plan.get("confirmation", {})),
        }
    if (
        plan.get("status") != "BROADCAST_RECORDED"
        or manifest.get("state") != "CAPACITY_BROADCAST_RECORDED"
    ):
        raise LiveNoteError("capacity transaction is not ready for confirmation refresh")
    broadcast = plan.get("broadcast")
    if not isinstance(broadcast, dict) or not broadcast.get("txid"):
        raise LiveNoteError("capacity broadcast evidence is missing")
    note_settings = _note_settings(
        destination,
        paths,
        fingerprint=str(manifest["master_fingerprint"]),
    )
    backend = note_backend_opener(note_settings)
    try:
        backend.refresh()
        matches = [
            tx
            for tx in backend._wallet.list_transactions(backend._online, False)
            if tx.txid == broadcast["txid"]
        ]
        capacity = _capacity_snapshot(backend)
    finally:
        backend.close()
    if len(matches) != 1:
        raise LiveNoteError("capacity transaction is missing from the note wallet")
    tx = matches[0]
    confirmation_time = getattr(tx, "confirmation_time", None)
    if confirmation_time is None:
        return {
            "phase": "live-redemption-capacity-waiting",
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "txid": str(broadcast["txid"]),
            "confirmed": False,
            **capacity,
        }
    if capacity["free_colorable_utxo_count"] < 1:
        raise LiveNoteError(
            "capacity transaction confirmed but no free colorable UTXO is visible"
        )
    confirmed_at = int(clock())
    confirmation = {
        "txid": str(broadcast["txid"]),
        "block_height": int(confirmation_time.height),
        "block_timestamp": int(confirmation_time.timestamp),
        "verified_at": confirmed_at,
        **capacity,
    }
    plan.update({"status": "CAPACITY_CONFIRMED", "confirmation": confirmation})
    manifest.update({"state": "CAPACITY_READY", "capacity_confirmation": confirmation})
    _atomic_json(paths.redemption_capacity_plan_file, plan)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "CAPACITY_CONFIRMED",
            "timestamp": confirmed_at,
            "note_id": note_id,
            "plan_id": str(plan["plan_id"]),
            "status": "CAPACITY_CONFIRMED",
            **confirmation,
        },
    )
    return {
        "phase": "live-redemption-capacity-ready",
        "note_id": note_id,
        "plan_id": str(plan["plan_id"]),
        "confirmed": True,
        "replayed": False,
        **confirmation,
    }


def prepare_live_redemption_after_capacity(
    destination: RgbSettings,
    *,
    vault_root: Path,
    capsule_path: Path,
    expected_capsule_sha256: str,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.load_offline,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveRedemptionPlan, dict[str, object]]:
    """Archive the rejected plan only after capacity is confirmed, then re-plan."""

    _validate_destination(destination)
    try:
        capsule = decode_bearer_capsule(capsule_path.read_bytes())
    except (OSError, BearerCapsuleError) as exc:
        raise LiveNoteError("cannot read the received bearer capsule") from exc
    paths = LiveNotePaths.create(vault_root, capsule.note_id)
    capacity_plan = _read_json(paths.redemption_capacity_plan_file)
    manifest = _read_json(paths.manifest_file)
    if (
        capacity_plan.get("status") != "CAPACITY_CONFIRMED"
        or manifest.get("state") != "CAPACITY_READY"
    ):
        raise LiveNoteError("confirmed redemption capacity is required before re-planning")
    previous = _read_json(paths.redemption_plan_file)
    if previous.get("status") != "PRE_BROADCAST_REJECTED":
        raise LiveNoteError("only the proven rejected redemption may be superseded")
    previous_id = str(previous.get("plan_id", ""))
    archive = paths.redemption_plan_file.parent / f"redemption-{previous_id}.json"
    if archive.exists():
        raise LiveNoteError("rejected redemption plan archive already exists")
    os.replace(paths.redemption_plan_file, archive)
    superseded_at = int(clock())
    previous.update(
        {"status": "SUPERSEDED_AFTER_CAPACITY", "superseded_at": superseded_at}
    )
    _atomic_json(archive, previous)
    manifest.update(
        {
            "state": "ACTIVE",
            "superseded_redemption_plan_id": previous_id,
            "superseded_redemption_plan_path": str(archive),
        }
    )
    for key in tuple(manifest):
        if key.startswith("redemption_"):
            manifest.pop(key, None)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "REDEMPTION_SUPERSEDED_AFTER_CAPACITY",
            "timestamp": superseded_at,
            "note_id": capsule.note_id,
            "plan_id": previous_id,
            "status": "SUPERSEDED_AFTER_CAPACITY",
            "archive_path": str(archive),
        },
    )
    return prepare_live_redemption_plan(
        destination,
        vault_root=vault_root,
        capsule_path=capsule_path,
        expected_capsule_sha256=expected_capsule_sha256,
        plan_lifetime_seconds=plan_lifetime_seconds,
        clock=clock,
        id_factory=id_factory,
        destination_backend_opener=destination_backend_opener,
        note_backend_opener=note_backend_opener,
    )


def renew_live_redemption_plan(
    destination: RgbSettings,
    *,
    vault_root: Path,
    capsule_path: Path,
    expected_capsule_sha256: str,
    plan_lifetime_seconds: int = 900,
    clock: Callable[[], float] = time.time,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    destination_backend_opener: Callable[
        [RgbSettings], RgbLibBackend
    ] = RgbLibBackend.load_offline,
    note_backend_opener: Callable[[RgbSettings], RgbLibBackend] = RgbLibBackend.open,
) -> tuple[LiveRedemptionPlan, dict[str, object]]:
    """Archive only an expired, unconsumed plan and create a new immutable plan."""

    _validate_destination(destination)
    try:
        capsule = decode_bearer_capsule(capsule_path.read_bytes())
    except (OSError, BearerCapsuleError) as exc:
        raise LiveNoteError("cannot read the received bearer capsule") from exc
    paths, previous = load_redemption_plan(vault_root, capsule.note_id)
    manifest = _read_json(paths.manifest_file)
    now = int(clock())
    if previous.get("status") != "PLANNED" or manifest.get("state") != "CLAIM_PLANNED":
        raise LiveNoteError("only an unconsumed planned redemption may be superseded")
    if int(previous.get("expiration_timestamp", 0)) > now:
        raise LiveNoteError("redemption plan is still fresh and must not be superseded")
    previous_id = str(previous.get("plan_id", ""))
    if not previous_id:
        raise LiveNoteError("previous redemption plan has no plan ID")
    archive = paths.redemption_plan_file.parent / f"redemption-{previous_id}.json"
    if archive.exists():
        raise LiveNoteError("previous redemption plan archive already exists")
    os.replace(paths.redemption_plan_file, archive)
    previous.update({"status": "SUPERSEDED", "superseded_at": now})
    _atomic_json(archive, previous)
    manifest.update(
        {
            "state": "ACTIVE",
            "superseded_redemption_plan_id": previous_id,
            "superseded_redemption_plan_path": str(archive),
        }
    )
    for key in tuple(manifest):
        if key.startswith("redemption_"):
            manifest.pop(key, None)
    _atomic_json(paths.manifest_file, manifest)
    _append_private_jsonl(
        paths.redemption_journal_file,
        {
            "event": "PLAN_SUPERSEDED",
            "timestamp": now,
            "note_id": capsule.note_id,
            "plan_id": previous_id,
            "status": "SUPERSEDED",
            "archive_path": str(archive),
        },
    )
    return prepare_live_redemption_plan(
        destination,
        vault_root=vault_root,
        capsule_path=capsule_path,
        expected_capsule_sha256=expected_capsule_sha256,
        plan_lifetime_seconds=plan_lifetime_seconds,
        clock=clock,
        id_factory=id_factory,
        destination_backend_opener=destination_backend_opener,
        note_backend_opener=note_backend_opener,
    )


def redacted_plan(
    plan: LiveFundingPlan
    | LiveRedemptionPlan
    | LiveCapacityPlan
    | LiveBtcTopupPlan,
) -> dict[str, object]:
    """Return the complete public plan without local secret-bearing paths."""

    return _plan_payload(plan, status="PLANNED")
