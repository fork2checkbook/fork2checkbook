"""Local web dock for the accepted isolated-wallet RGB-over-BLE flow."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Callable
from functools import wraps
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ParamSpec, TypeVar
from urllib.parse import urlparse

from web.rgb.config import BillRole
from dock.chunk_protocol import FrameKind
from web.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
    RegisteredBillGateway,
)
from web.bearer_web import BearerWebError, BearerWebService
from web.qvac_intent import QvacIntentError, QvacIntentService
from web.rgb_web import RgbWebError, RgbWebService

STATIC = Path(__file__).resolve().parent / "static"
HOST = os.environ.get("DOCK_HTTP_HOST", "127.0.0.1")
PORT = int(os.environ.get("DOCK_HTTP_PORT", "8788"))
ROLE = os.environ.get("BILL_ROLE", "A").upper()
MODE = "live-rgb-tap"

_state_lock = threading.RLock()
_confirm_lock = threading.Lock()
_workflow_lock = threading.RLock()
_rgb_service: RgbWebService | None = None
_bearer_service: BearerWebService | None = None
_device_registry: DeviceRegistry | None = None
_qvac_service: QvacIntentService | None = None

P = ParamSpec("P")
R = TypeVar("R")

_state: dict[str, Any] = {
    "role": ROLE,
    "identity": "bill-unprovisioned",
    "status": "starting",
    "cents": 0,
    "pending": None,
    "receipts": [],
    "recovery": None,
    "mode": MODE,
    "settlement": "not-broadcast",
    "asset_id": os.environ.get("RGB_ASSET_ID") or None,
    "asset_precision": os.environ.get("RGB_ASSET_PRECISION") or None,
    "expected_receive_cents": None,
    "bearer": None,
    "wallet": None,
}


def _serialized_workflow(function: Callable[P, R]) -> Callable[P, R]:
    """Keep device registration and transfer state changes in one order."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with _workflow_lock:
            return function(*args, **kwargs)

    return wrapped


def _bill() -> dict[str, Any]:
    device = _device_registry.snapshot() if _device_registry is not None else None
    with _state_lock:
        pending = _state["pending"]
        recovery = _state["recovery"]
        return {
            "role": _state["role"],
            "identity": _state["identity"],
            "status": _state["status"],
            "cents": _state["cents"],
            "pending": dict(pending) if pending is not None else None,
            "receipts": [dict(receipt) for receipt in _state["receipts"]],
            "recovery": dict(recovery) if recovery is not None else None,
            "mode": _state["mode"],
            "settlement": _state["settlement"],
            "asset_id": _state["asset_id"],
            "asset_precision": _state["asset_precision"],
            "device": device,
            "bearer": dict(_state["bearer"]) if _state["bearer"] else None,
            "wallet": dict(_state["wallet"]) if _state["wallet"] else None,
        }


def _public_wallet_identity(service: RgbWebService) -> dict[str, Any]:
    """Describe the open wallet without exposing paths or credentials."""

    role = service.role.value
    settings = service.settings
    return {
        "label": settings.wallet_label or f"Wallet {role}",
        "role": role,
        "network": "Mutinynet",
        "master_fingerprint": settings.master_fingerprint,
        "asset_id": service.asset_id,
        "purpose": "Issues or redeems whole-value bearer notes",
        "bearer_target": (
            "Unknown until redemption; issuance funds a fresh disposable "
            "note wallet"
        ),
        "profile_setting": f"RGB_DATA_DIR_{role}",
        "restart_required_to_switch": True,
    }


def _service(selected: RgbWebService | None) -> RgbWebService:
    service = selected or _rgb_service
    if service is None:
        raise RgbWebError(
            "live RGB wallet service is not initialized",
            retryable=True,
            broadcast=False,
        )
    return service


def _bearer(selected: BearerWebService | None) -> BearerWebService:
    service = selected or _bearer_service
    if service is None:
        raise BearerWebError(
            "bearer-note web service is not initialized",
            retryable=True,
            broadcast=False,
        )
    return service


def _qvac(selected: QvacIntentService | None) -> QvacIntentService:
    service = selected or _qvac_service
    if service is None:
        raise QvacIntentError("local QVAC intent service is not initialized")
    return service


def _error(exc: Exception, *, retryable: bool = True) -> dict[str, Any]:
    if isinstance(exc, BearerWebError):
        return {
            "ok": False,
            "error": str(exc),
            "retryable": exc.retryable,
            "broadcast": exc.broadcast,
            "recovery_action": exc.recovery_action,
            "mode": MODE,
        }
    if isinstance(exc, RgbWebError):
        return {
            "ok": False,
            "error": str(exc),
            "retryable": exc.retryable,
            "broadcast": exc.broadcast,
            "recovery_path": (
                str(exc.recovery_path) if exc.recovery_path is not None else None
            ),
            "mode": MODE,
        }
    return {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "retryable": retryable,
        "broadcast": False,
        "mode": MODE,
    }


def _registry(selected: DeviceRegistry | None = None) -> DeviceRegistry:
    registry = selected or _device_registry
    if registry is None:
        raise DeviceRegistryError("physical bill registry is not initialized")
    return registry


def _device_error(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "retryable": (
            exc.retryable if isinstance(exc, DeviceRegistryError) else True
        ),
        "broadcast": False,
    }


def handle_device_scan(
    timeout: float, registry: DeviceRegistry | None = None
) -> dict[str, Any]:
    try:
        selected = _registry(registry)
        devices = selected.scan(timeout)
    except Exception as exc:
        return _device_error(exc)
    return {
        "ok": True,
        "devices": devices,
        "count": len(devices),
        "device": selected.snapshot(),
        "broadcast": False,
    }


def handle_device_connect(
    address: str | None, registry: DeviceRegistry | None = None
) -> dict[str, Any]:
    try:
        selected = _registry(registry)
        connection = selected.connect(address or None)
    except Exception as exc:
        return _device_error(exc)
    if connection["address"] == selected.active_address():
        with _state_lock:
            _state["identity"] = connection["identity"]
    return {
        "ok": True,
        "connection": connection,
        "device": selected.snapshot(),
        "broadcast": False,
    }


@_serialized_workflow
def handle_device_register(
    address: str, registry: DeviceRegistry | None = None
) -> dict[str, Any]:
    def registration_allowed() -> bool:
        with _state_lock:
            return (
                _state["pending"] is None
                and _state["recovery"] is None
                and _state["status"] in {"idle", "settled"}
            )

    try:
        selected = _registry(registry)
        snapshot = selected.register(address, guard=registration_allowed)
    except Exception as exc:
        return _device_error(exc)
    with _state_lock:
        _state["identity"] = snapshot["device"]["identity"]
    return {
        "ok": True,
        "reply": "Physical bill registered. Future BLE operations will reconnect automatically.",
        "device": snapshot,
        "broadcast": False,
    }


def _requested_cents(text: str) -> int:
    match = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", text)
    return 500 if match is None else int(round(float(match.group(1)) * 100))


def _require_five(cents: int) -> dict[str, Any] | None:
    if cents == 500:
        return None
    return {
        "ok": False,
        "reply": "The cash-bill web flow currently supports exactly $5.00.",
        "pending": None,
        "retryable": False,
        "broadcast": False,
    }


def _inspect_registered_bill(
    registry: DeviceRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _registry(registry)
    connection = selected.connect()
    artifact = connection.get("artifact")
    if artifact is None:
        raise DeviceRegistryError(
            "bill firmware does not expose artifact inventory; flash the new protocol firmware",
            retryable=False,
        )
    return connection, artifact


def _blocked_inventory_error(artifact: dict[str, Any]) -> RgbWebError:
    kind = artifact.get("kind")
    label = "artifact"
    if kind == int(FrameKind.INVOICE):
        label = "invoice"
    elif kind == int(FrameKind.CONSIGNMENT):
        label = "consignment"
    elif kind == int(FrameKind.BEARER_NOTE):
        label = "bearer note"
    return RgbWebError(
        f"Bill storage recovery is required: a retained {label} could not be handed to the tap service. "
        "Do not create or send another artifact. Use the explicit chunk-discard recovery, then retry the retained dock action.",
        retryable=False,
        broadcast=False,
    )


@_serialized_workflow
def handle_send_usdt(
    cents: int = 500,
    service: RgbWebService | None = None,
    registry: DeviceRegistry | None = None,
) -> dict[str, Any]:
    """Inspect Bill A, then read exactly one invoice and prepare confirmation."""
    rejected = _require_five(cents)
    if rejected is not None:
        return rejected
    try:
        selected = _service(service)
        if selected.role is not BillRole.A:
            raise RgbWebError(
                "this dock is the receiver; use Receive USDT",
                retryable=False,
                broadcast=False,
            )
        _connection, artifact = _inspect_registered_bill(registry)
        if artifact["direction"] == "blocked":
            raise _blocked_inventory_error(artifact)
        if artifact["direction"] != "to-dock" or artifact["kind"] != int(
            FrameKind.INVOICE
        ):
            raise RgbWebError(
                "Bill A has no received invoice. On Bill A press once near Bill B, then try Send USDT again.",
                retryable=True,
                broadcast=False,
            )
        if artifact["denomination_cents"] != cents:
            raise RgbWebError(
                "the invoice amount on Bill A does not match $5.00",
                retryable=False,
                broadcast=False,
            )
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        result["pending"] = None
        return result
    return handle_plan(cents, selected)


@_serialized_workflow
def handle_receive_usdt(
    cents: int = 500,
    service: RgbWebService | None = None,
    registry: DeviceRegistry | None = None,
) -> dict[str, Any]:
    """Inspect Bill B, then either stage its invoice or import its proof."""
    rejected = _require_five(cents)
    if rejected is not None:
        return rejected
    try:
        selected = _service(service)
        if selected.role is not BillRole.B:
            raise RgbWebError(
                "this dock is the sender; use Send USDT",
                retryable=False,
                broadcast=False,
            )
        _connection, artifact = _inspect_registered_bill(registry)
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        result["pending"] = None
        return result

    direction = artifact["direction"]
    kind = artifact["kind"]
    if direction == "blocked":
        result = _error(_blocked_inventory_error(artifact))
        result["reply"] = result["error"]
        result["pending"] = None
        return result
    if direction == "empty":
        return handle_stage_invoice(cents, selected)
    if direction == "to-dock" and kind == int(FrameKind.CONSIGNMENT):
        return handle_import(selected)
    if direction == "to-peer" and kind == int(FrameKind.INVOICE):
        return {
            "ok": True,
            "reply": "Invoice is loaded on Bill B. Press Bill A once near Bill B, then use Send USDT on Dock A.",
            "pending": None,
            "broadcast": False,
            "bill": _bill(),
        }
    if direction == "to-peer" and kind == int(FrameKind.CONSIGNMENT):
        return {
            "ok": True,
            "reply": "Consignment is loaded on Bill B. Press the empty receiving bill once near it.",
            "pending": None,
            "broadcast": True,
            "bill": _bill(),
        }
    result = _error(
        RgbWebError(
            "Bill B contains an unexpected artifact; do not discard it automatically",
            retryable=False,
            broadcast=False,
        )
    )
    result["reply"] = result["error"]
    result["pending"] = None
    return result


@_serialized_workflow
def handle_stage_invoice(
    cents: int, service: RgbWebService | None = None
) -> dict[str, Any]:
    rejected = _require_five(cents)
    if rejected is not None:
        return rejected
    try:
        outcome = _service(service).stage_invoice(cents)
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        result["pending"] = None
        return result

    receipt = {
        "action": "invoice-stage",
        "cents": cents,
        "source": MODE,
        "settlement": "not-broadcast",
        "batch_transfer_idx": outcome.batch_transfer_idx,
        "expiration_timestamp": outcome.expiration_timestamp,
        "artifact_path": str(outcome.artifact.path),
        "artifact_bytes": outcome.artifact.size,
        "artifact_sha256": outcome.artifact.sha256,
        "transport_endpoints": [],
    }
    if outcome.staged is None:
        receipt.update(
            {
                "status": "invoice-stage-recovery-required",
                "error": outcome.error,
            }
        )
        recovery = {
            "action": "retry-invoice-stage",
            "artifact_path": str(outcome.artifact.path),
            "artifact_sha256": outcome.artifact.sha256,
            "broadcast": False,
        }
        with _state_lock:
            _state["status"] = "invoice-stage-recovery-required"
            _state["recovery"] = recovery
            _state["receipts"].insert(0, receipt)
        return {
            "ok": False,
            "reply": "Invoice retained, but BLE staging failed. Retry this step.",
            "pending": None,
            "retryable": True,
            "broadcast": False,
            "recovery": recovery,
            "receipt": receipt,
        }

    receipt.update(
        {
            "status": "invoice-staged",
            "ble_session_id": f"0x{outcome.staged.session_id:08x}",
        }
    )
    with _state_lock:
        _state["status"] = "invoice-staged"
        _state["settlement"] = "not-broadcast"
        _state["recovery"] = None
        _state["expected_receive_cents"] = cents
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "reply": "Real transport-free invoice staged. Tap Bill A once.",
        "pending": None,
        "broadcast": False,
        "receipt": receipt,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_plan(cents: int, service: RgbWebService | None = None) -> dict[str, Any]:
    rejected = _require_five(cents)
    if rejected is not None:
        return rejected
    try:
        selected = _service(service)
        prepared = selected.plan_transfer(cents)
        balance_cents = selected.cents_from_base_units(
            prepared.plan.balance_before
        )
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        result["pending"] = None
        return result

    plan = prepared.plan
    pending = {
        "id": plan.plan_id,
        "action": "rgb-send",
        "cents": cents,
        "ticker": "USDT",
        "asset_id": plan.asset_id,
        "recipient_id": plan.recipient_id,
        "amount_base_units": plan.amount,
        "balance_before_base_units": plan.balance_before,
        "balance_before_cents": balance_cents,
        "witness_amount_sat": plan.witness_amount_sat,
        "fee_rate_sat_vb": plan.fee_rate,
        "min_confirmations": plan.min_confirmations,
        "expiration_timestamp": plan.expiration_timestamp,
        "invoice_path": str(prepared.artifact.path),
        "invoice_sha256": prepared.artifact.sha256,
        "invoice_session_id": (
            f"0x{prepared.received_session_id:08x}"
            if prepared.received_session_id is not None
            else None
        ),
        "confirmation_phrase": f"CONFIRM {plan.plan_id}",
        "donation": True,
        "transport_endpoints": [],
        "note": (
            f"Send ${cents / 100:.2f} USDT on Mutinynet to the tapped invoice. "
            "Review the immutable plan and type its exact confirmation phrase."
        ),
    }
    with _state_lock:
        _state["pending"] = pending
        _state["status"] = "confirmation-required"
        _state["settlement"] = "not-broadcast"
        _state["recovery"] = None
    return {
        "ok": True,
        "reply": pending["note"],
        "pending": pending,
        "broadcast": False,
        "bill": _bill(),
    }


def _bearer_pending(outcome: dict[str, object]) -> dict[str, Any]:
    plan = dict(outcome["plan"])  # type: ignore[arg-type]
    pending = {
        "id": str(plan["plan_id"]),
        "action": "bearer-redemption",
        "note_id": str(plan["note_id"]),
        "cents": int(plan["denomination_cents"]),
        "ticker": "USDT",
        "asset_id": str(plan["asset_id"]),
        "amount_base_units": int(plan["amount_base_units"]),
        "recipient_id": str(plan["recipient_id"]),
        "witness_amount_sat": int(plan["witness_amount_sat"]),
        "fee_rate_sat_vb": int(plan["fee_rate_sat_vb"]),
        "min_confirmations": int(plan["min_confirmations"]),
        "expiration_timestamp": int(plan["expiration_timestamp"]),
        "invoice_sha256": str(plan["recipient_invoice_sha256"]),
        "capsule_sha256": str(plan["capsule_sha256"]),
        "confirmation_phrase": f"CONFIRM {plan['plan_id']}",
        "donation": True,
        "transport_endpoints": [],
        "note": (
            "Redeem this exact offline bearer note into this dock's wallet on "
            "Mutinynet. Verify the wallet fingerprint, review the immutable "
            "plan, and type its exact confirmation phrase."
        ),
    }
    return pending


def _bearer_funding_pending(
    outcome: dict[str, object], wallet: RgbWebService
) -> dict[str, Any]:
    plan = dict(outcome["plan"])  # type: ignore[arg-type]
    return {
        "id": str(plan["plan_id"]),
        "action": "bearer-funding",
        "note_id": str(plan["note_id"]),
        "cents": int(plan["denomination_cents"]),
        "ticker": "USDT",
        "asset_id": str(plan["asset_id"]),
        "amount_base_units": int(plan["amount_base_units"]),
        "balance_before_cents": wallet.cents_from_base_units(
            int(plan["source_spendable_before"])
        ),
        "recipient_id": str(plan["recipient_id"]),
        "witness_amount_sat": int(plan["witness_amount_sat"]),
        "fee_rate_sat_vb": int(plan["fee_rate_sat_vb"]),
        "min_confirmations": int(plan["min_confirmations"]),
        "expiration_timestamp": int(plan["expiration_timestamp"]),
        "invoice_sha256": str(plan["recipient_invoice_sha256"]),
        "confirmation_phrase": f"CONFIRM {plan['plan_id']}",
        "donation": True,
        "transport_endpoints": [],
        "note": (
            "Fund this fresh disposable bearer note with exactly $5.00 USDT on "
            "Mutinynet. The eventual redemption wallet is still unknown. Review "
            "the immutable plan and type its exact confirmation phrase."
        ),
    }


def _bearer_funding_capacity_pending(outcome: dict[str, object]) -> dict[str, Any]:
    plan = dict(outcome["plan"])  # type: ignore[arg-type]
    return {
        "id": str(plan["plan_id"]),
        "action": "bearer-funding-capacity",
        "note_id": str(plan["note_id"]),
        "utxo_count": int(plan["utxo_count"]),
        "utxo_size_sat": int(plan["utxo_size_sat"]),
        "vanilla_spendable_before": int(plan["vanilla_spendable_before"]),
        "free_colorable_utxo_count_before": int(
            plan["free_colorable_utxo_count_before"]
        ),
        "fee_rate_sat_vb": int(plan["fee_rate_sat_vb"]),
        "expiration_timestamp": int(plan["expiration_timestamp"]),
        "confirmation_phrase": f"CONFIRM {plan['plan_id']}",
        "note": (
            "The rejected RGB send created no transfer. Create one "
            f"{int(plan['utxo_size_sat']):,}-sat "
            "colorable UTXO in this dock wallet, using a separate Bitcoin "
            "transaction and confirmation. This does not fund the note yet."
        ),
    }


def _bearer_btc_topup_pending(outcome: dict[str, object]) -> dict[str, Any]:
    plan = dict(outcome["plan"])  # type: ignore[arg-type]
    return {
        "id": str(plan["plan_id"]),
        "action": "bearer-note-btc-topup",
        "note_id": str(plan["note_id"]),
        "destination_address": str(plan["destination_address"]),
        "topup_amount_sat": int(plan["topup_amount_sat"]),
        "target_note_vanilla_sat": int(plan["target_note_vanilla_sat"]),
        "note_vanilla_spendable_before": int(
            plan["note_vanilla_spendable_before"]
        ),
        "source_vanilla_spendable_before": int(
            plan["source_vanilla_spendable_before"]
        ),
        "fee_rate_sat_vb": int(plan["fee_rate_sat_vb"]),
        "expiration_timestamp": int(plan["expiration_timestamp"]),
        "confirmation_phrase": f"CONFIRM {plan['plan_id']}",
        "note": (
            "Fund this disposable note's Bitcoin fee reserve from the issuing "
            "dock wallet on Mutinynet. This replaces the faucet step and does "
            "not fund the $5 RGB value yet. Review the immutable plan and type "
            "its exact confirmation phrase."
        ),
    }


@_serialized_workflow
def handle_bearer_prepare(
    *,
    check_existing: bool = False,
    service: BearerWebService | None = None,
    wallet_service: RgbWebService | None = None,
) -> dict[str, Any]:
    """Prepare or refresh one note, but never confirm or touch bill storage."""

    try:
        selected = _bearer(service)
        wallet = _service(wallet_service)
        if check_existing:
            outcome = selected.inspect_funding()
        else:
            outcome = selected.prepare_funding()
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        return result

    if outcome is None:
        bill = _bill()
        return {
            "ok": True,
            "reply": (
                f"No unfinished note belongs to this wallet. It shows "
                f"${bill['cents'] / 100:.2f} and state {bill['status']}."
            ),
            "pending": None,
            "broadcast": False,
            "bill": bill,
        }

    selected_note = str(outcome["note_id"])
    if bool(outcome.get("settled")) or outcome.get("note_state") == "ACTIVE":
        with _state_lock:
            _state["pending"] = None
            _state["status"] = "note-ready-to-load"
            _state["settlement"] = "funded-note-active"
            _state["bearer"] = {
                "step": 1,
                "phase": "ready-to-load",
                "offline": False,
                "note_id": selected_note,
            }
        return {
            "ok": True,
            "reply": "The $5 note is settled. Click Load note to place it on the empty bill.",
            "pending": None,
            "broadcast": False,
            "qvac_action": "LOAD_NOTE",
            "bill": _bill(),
        }

    if outcome.get("phase") == "bearer-funding-capacity-plan-ready":
        pending = _bearer_funding_capacity_pending(outcome)
        with _state_lock:
            _state["pending"] = pending
            _state["status"] = "note-funding-capacity-confirmation-required"
            _state["settlement"] = "not-broadcast"
            _state["recovery"] = None
            _state["bearer"] = {
                "step": 0,
                "phase": "funding-capacity-plan-ready",
                "offline": False,
                "note_id": selected_note,
            }
        return {
            "ok": True,
            "reply": pending["note"],
            "pending": pending,
            "broadcast": False,
            "qvac_action": "INSPECT_NOTE",
            "bill": _bill(),
        }

    if outcome.get("phase") == "bearer-note-btc-topup-plan-ready":
        pending = _bearer_btc_topup_pending(outcome)
        with _state_lock:
            _state["pending"] = pending
            _state["status"] = "note-btc-topup-confirmation-required"
            _state["settlement"] = "not-broadcast"
            _state["recovery"] = None
            _state["bearer"] = {
                "step": 0,
                "phase": "btc-topup-plan-ready",
                "offline": False,
                "note_id": selected_note,
            }
        return {
            "ok": True,
            "reply": pending["note"],
            "pending": pending,
            "broadcast": False,
            "qvac_action": "LOAD_NOTE",
            "bill": _bill(),
        }

    if outcome.get("phase") == "live-note-btc-topup-waiting":
        txid = str(outcome.get("txid") or "")
        with _state_lock:
            _state["pending"] = None
            _state["status"] = "note-btc-topup-waiting"
            _state["settlement"] = "waiting-confirmations"
            _state["recovery"] = None
            _state["bearer"] = {
                "step": 0,
                "phase": "btc-topup-waiting",
                "offline": False,
                "note_id": selected_note,
                "txid": txid,
            }
        return {
            "ok": True,
            "reply": (
                "The issuing dock funded the note's Bitcoin fee reserve once. "
                "Wait for one Mutinynet confirmation, then ask QVAC ‘check my "
                "note’. The $5 RGB funding send has not occurred."
            ),
            "pending": None,
            "broadcast": True,
            "txid": txid,
            "qvac_action": "INSPECT_NOTE",
            "bill": _bill(),
        }

    if outcome.get("phase") == "live-funding-capacity-waiting":
        txid = str(outcome.get("txid") or "")
        with _state_lock:
            _state["pending"] = None
            _state["status"] = "note-funding-capacity-waiting"
            _state["settlement"] = "waiting-confirmations"
            _state["recovery"] = None
            _state["bearer"] = {
                "step": 0,
                "phase": "funding-capacity-waiting",
                "offline": False,
                "note_id": selected_note,
                "txid": txid,
            }
        return {
            "ok": True,
            "reply": (
                "The source-wallet capacity transaction is recorded but is not "
                "confirmed yet. Wait for one Mutinynet confirmation, then ask "
                "QVAC ‘check my note’. The RGB funding send has not occurred."
            ),
            "pending": None,
            "broadcast": True,
            "txid": txid,
            "qvac_action": "INSPECT_NOTE",
            "bill": _bill(),
        }

    preflight = dict(outcome.get("redemption_btc_preflight") or {})
    if outcome.get("funding_gate") != "READY_FOR_CONFIRMATION":
        with _state_lock:
            _state["pending"] = None
            _state["status"] = "note-btc-topup-recovery-required"
            _state["settlement"] = "not-broadcast"
            _state["bearer"] = {
                "step": 0,
                "phase": "btc-topup-recovery-required",
                "offline": False,
                "note_id": selected_note,
            }
        return {
            "ok": False,
            "reply": (
                "The note still lacks its Bitcoin fee reserve, but no automatic "
                "top-up plan was produced. Use Recover; do not fund an unverified "
                "address or attempt the RGB send."
            ),
            "pending": None,
            "preflight": preflight,
            "broadcast": False,
            "retryable": False,
            "qvac_action": "INSPECT_NOTE",
            "bill": _bill(),
        }

    pending = _bearer_funding_pending(outcome, wallet)
    with _state_lock:
        _state["pending"] = pending
        _state["status"] = "note-funding-confirmation-required"
        _state["settlement"] = "not-broadcast"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 1,
            "phase": "funding-plan-ready",
            "offline": False,
            "note_id": selected_note,
        }
    return {
        "ok": True,
        "reply": pending["note"],
        "pending": pending,
        "broadcast": False,
        "qvac_action": "LOAD_NOTE",
        "bill": _bill(),
    }


@_serialized_workflow
def handle_bearer_load(
    note_id: str | None = None,
    service: BearerWebService | None = None,
    registry: DeviceRegistry | None = None,
) -> dict[str, Any]:
    """Load one already-funded capsule without creating or spending another note."""

    try:
        selected = _bearer(service)
        _connection, artifact = _inspect_registered_bill(registry)
        if artifact["direction"] == "blocked":
            raise _blocked_inventory_error(artifact)
        if artifact["direction"] != "empty":
            raise BearerWebError(
                "The registered bill is not empty. Pass or redeem its retained "
                "artifact before loading another note.",
                retryable=False,
                broadcast=False,
            )
        bearer_state = _bill().get("bearer") or {}
        selected_note_id = note_id or str(bearer_state.get("note_id") or "") or None
        if bearer_state.get("phase") in {"funding-imported", "waiting-confirmations"}:
            if selected_note_id is None:
                raise BearerWebError(
                    "the funding settlement has no selected note",
                    retryable=False,
                    broadcast=True,
                )
            funding = selected.refresh_funding(selected_note_id)
            if not bool(funding.get("settled")):
                with _state_lock:
                    _state["status"] = "note-funding-waiting-confirmations"
                    _state["settlement"] = "waiting-confirmations"
                    _state["bearer"] = {
                        "step": 1,
                        "phase": "waiting-confirmations",
                        "offline": False,
                        "note_id": selected_note_id,
                        "txid": funding.get("txid"),
                    }
                return {
                    "ok": True,
                    "reply": (
                        "The funding transaction is recorded but not settled yet. "
                        "Wait for one Mutinynet confirmation, then click Load note again."
                    ),
                    "broadcast": False,
                    "settled": False,
                    "bill": _bill(),
                }
            with _state_lock:
                _state["status"] = "note-ready-to-load"
                _state["settlement"] = "funded-note-active"
                _state["bearer"] = {
                    "step": 1,
                    "phase": "ready-to-load",
                    "offline": False,
                    "note_id": selected_note_id,
                    "txid": funding.get("txid"),
                }
        outcome = selected.load_note(selected_note_id)
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        if result.get("recovery_action") == "retry-bearer-load":
            with _state_lock:
                _state["status"] = "bearer-load-recovery-required"
                _state["recovery"] = {
                    "action": "retry-bearer-load",
                    "endpoint": "/api/bearer/load",
                    "note_id": note_id,
                    "retry_send": False,
                }
        return result
    receipt = {"action": "bearer-load", "source": MODE, **outcome}
    with _state_lock:
        _state["status"] = "bearer-loaded"
        _state["settlement"] = "funded-note-active"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 2,
            "phase": "loaded",
            "offline": True,
            "note_id": outcome["note_id"],
            "artifact_sha256": outcome["artifact_sha256"],
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "reply": "Funded $5 bearer note loaded. Take the bill offline and use Pass note.",
        "broadcast": False,
        "receipt": receipt,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_bearer_pass(
    service: BearerWebService | None = None,
    registry: DeviceRegistry | None = None,
) -> dict[str, Any]:
    """Verify a held capsule and display the dock-free physical handoff step."""

    try:
        selected = _bearer(service)
        _connection, artifact = _inspect_registered_bill(registry)
        if artifact["direction"] == "blocked":
            raise _blocked_inventory_error(artifact)
        if artifact["kind"] != int(FrameKind.BEARER_NOTE) or artifact[
            "direction"
        ] not in {"to-peer", "bearer-held"}:
            raise BearerWebError(
                "This bill does not hold a transferable bearer note.",
                retryable=False,
                broadcast=False,
            )
        if artifact["denomination_cents"] != 500:
            raise BearerWebError(
                "The held bearer note is not the fixed $5 demo denomination.",
                retryable=False,
                broadcast=False,
            )
        outcome = selected.pass_note()
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        return result
    with _state_lock:
        _state["status"] = "offline-pass-ready"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 3,
            "phase": "offline-pass",
            "offline": True,
            "note_id": (_state["bearer"] or {}).get("note_id"),
        }
    return {
        "ok": True,
        "reply": outcome["instruction"],
        **outcome,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_bearer_redeem(
    service: BearerWebService | None = None,
    registry: DeviceRegistry | None = None,
) -> dict[str, Any]:
    """Extract one capsule and prepare, but never auto-confirm, this dock's plan."""

    try:
        selected = _bearer(service)
        _connection, artifact = _inspect_registered_bill(registry)
        if artifact["direction"] == "blocked":
            raise _blocked_inventory_error(artifact)
        receive_from_bill = artifact["direction"] == "bearer-held" and artifact[
            "kind"
        ] == int(FrameKind.BEARER_NOTE)
        if receive_from_bill and artifact["denomination_cents"] != 500:
            raise BearerWebError(
                "The held bearer note is not the fixed $5 demo denomination.",
                retryable=False,
                broadcast=False,
            )
        if not receive_from_bill and artifact["direction"] != "empty":
            raise BearerWebError(
                "The dock expected a held bearer note or an empty bill with a "
                "retained extraction recovery.",
                retryable=False,
                broadcast=False,
            )
        outcome = selected.prepare_redemption(receive_from_bill=receive_from_bill)
        pending = _bearer_pending(outcome)
    except Exception as exc:
        result = _error(exc)
        result["reply"] = result["error"]
        result["pending"] = None
        if result.get("recovery_action") == "retry-bearer-plan":
            with _state_lock:
                _state["status"] = "bearer-plan-recovery-required"
                _state["recovery"] = {
                    "action": "retry-bearer-plan",
                    "endpoint": "/api/bearer/redeem",
                    "retry_send": False,
                }
        return result
    receipt = {
        "action": "bearer-extract-and-plan",
        "source": MODE,
        "note_id": outcome["note_id"],
        "capsule_sha256": outcome["capsule_sha256"],
        "capsule_bytes": outcome["capsule_bytes"],
        "ble_session_id": outcome["ble_session_id"],
        "plan_id": pending["id"],
        "broadcast": False,
    }
    with _state_lock:
        _state["pending"] = pending
        _state["status"] = "bearer-confirmation-required"
        _state["settlement"] = "not-broadcast"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 5,
            "phase": "confirmation-required",
            "offline": False,
            "note_id": outcome["note_id"],
            "artifact_sha256": outcome["capsule_sha256"],
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "reply": pending["note"],
        "pending": pending,
        "broadcast": False,
        "receipt": receipt,
        "bill": _bill(),
    }


def _handle_bearer_confirm(
    pending: dict[str, Any],
    phrase: str,
    service: BearerWebService | None,
) -> dict[str, Any]:
    selected = _bearer(service)
    try:
        outcome = selected.execute_redemption(
            note_id=str(pending["note_id"]),
            plan_id=str(pending["id"]),
            confirmation_phrase=phrase,
        )
    except Exception as exc:
        result = _error(exc)
        if isinstance(exc, BearerWebError) and exc.recovery_action:
            recovery = {
                "action": exc.recovery_action,
                "endpoint": (
                    "/api/bearer/recover"
                    if exc.recovery_action == "recover-bearer-redemption"
                    else None
                ),
                "note_id": pending["note_id"],
                "retry_send": False,
                "broadcast": exc.broadcast,
            }
            with _state_lock:
                _state["pending"] = None
                _state["status"] = "bearer-redemption-recovery-required"
                _state["settlement"] = "unknown"
                _state["recovery"] = recovery
            result["recovery"] = recovery
        return result

    receipt = {
        "action": "bearer-redemption",
        "source": MODE,
        "status": "redemption-imported",
        "settlement": "waiting-confirmations",
        "retry_send": False,
        **outcome,
    }
    with _state_lock:
        _state["pending"] = None
        _state["status"] = "bearer-redemption-imported"
        _state["settlement"] = "waiting-confirmations"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 6,
            "phase": "waiting-confirmations",
            "offline": False,
            "note_id": pending["note_id"],
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "completed": True,
        "broadcast": True,
        "recovery_required": False,
        "retry_send": False,
        "receipt": receipt,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_bearer_recover(
    note_id: str | None = None,
    service: BearerWebService | None = None,
) -> dict[str, Any]:
    selected_note = note_id or str((_bill().get("bearer") or {}).get("note_id") or "")
    if not selected_note:
        return _error(
            BearerWebError(
                "no bearer note is selected for recovery",
                retryable=False,
                broadcast=False,
            )
        )
    try:
        outcome = _bearer(service).recover_redemption(selected_note)
    except Exception as exc:
        return _error(exc)
    phase = str(outcome.get("phase", ""))
    if phase == "bearer-redemption-plan-ready":
        pending = _bearer_pending(outcome)
        with _state_lock:
            _state["pending"] = pending
            _state["status"] = "bearer-confirmation-required"
            _state["settlement"] = "not-broadcast"
            _state["recovery"] = None
            _state["bearer"] = {
                "step": 5,
                "phase": "confirmation-required",
                "offline": False,
                "note_id": selected_note,
                "artifact_sha256": outcome["capsule_sha256"],
            }
        return {
            "ok": True,
            "reply": (
                "The expired plan was safely superseded. Review and confirm "
                "the new immutable plan."
            ),
            "pending": pending,
            "broadcast": False,
            "bill": _bill(),
        }
    blocked = phase.endswith("recovery-blocked")
    if blocked:
        with _state_lock:
            _state["status"] = "manual-reconciliation-required"
            _state["settlement"] = "unknown"
            _state["recovery"] = {
                "action": "manual-bearer-reconciliation",
                "note_id": selected_note,
                "retry_send": False,
            }
        return {
            "ok": False,
            "error": str(outcome.get("instruction")),
            "retryable": False,
            "broadcast": outcome.get("broadcast"),
            "retry_send": False,
            "bill": _bill(),
        }
    with _state_lock:
        _state["status"] = "bearer-redemption-imported"
        _state["settlement"] = "waiting-confirmations"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 6,
            "phase": "waiting-confirmations",
            "offline": False,
            "note_id": selected_note,
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(
            0, {"action": "bearer-redemption-recovery", "retry_send": False, **outcome}
        )
    return {"ok": True, "broadcast": True, "retry_send": False, "bill": _bill()}


@_serialized_workflow
def handle_bearer_refresh(
    note_id: str | None = None,
    service: BearerWebService | None = None,
    wallet_service: RgbWebService | None = None,
) -> dict[str, Any]:
    selected_note = note_id or str((_bill().get("bearer") or {}).get("note_id") or "")
    if not selected_note:
        return _error(
            BearerWebError(
                "no redeemed bearer note is selected for settlement refresh",
                retryable=False,
                broadcast=True,
            )
        )
    try:
        outcome = _bearer(service).refresh_redemption(selected_note)
    except Exception as exc:
        return _error(exc)
    settled = bool(outcome.get("settled"))
    with _state_lock:
        _state["status"] = "settled" if settled else "waiting-confirmations"
        _state["settlement"] = "settled" if settled else "waiting-confirmations"
        _state["cents"] = _service(wallet_service).spendable_cents()
        _state["bearer"] = {
            "step": 7 if settled else 6,
            "phase": "settled" if settled else "waiting-confirmations",
            "offline": False,
            "note_id": selected_note,
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(0, {"action": "bearer-settlement-refresh", **outcome})
    return {"ok": True, "broadcast": False, **outcome, "bill": _bill()}


def _handle_bearer_funding_confirm(
    pending: dict[str, Any],
    phrase: str,
    bearer_service: BearerWebService | None,
    wallet_service: RgbWebService | None,
) -> dict[str, Any]:
    try:
        outcome = _bearer(bearer_service).execute_funding(
            note_id=str(pending["note_id"]),
            plan_id=str(pending["id"]),
            confirmation_phrase=phrase,
        )
    except Exception as exc:
        result = _error(exc)
        if not result.get("retryable"):
            with _state_lock:
                _state["pending"] = None
                _state["status"] = "note-funding-recovery-required"
                _state["settlement"] = "unknown"
                _state["recovery"] = {
                    "action": "inspect-note-funding",
                    "note_id": pending["note_id"],
                    "retry_send": False,
                }
            result["recovery"] = _bill()["recovery"]
        return result

    receipt = {
        "action": "bearer-funding",
        "source": MODE,
        "status": "funding-imported",
        "settlement": "waiting-confirmations",
        "retry_send": False,
        **outcome,
    }
    try:
        cents = _service(wallet_service).spendable_cents()
    except Exception:
        cents = int(_state["cents"]) - int(pending["cents"])
    with _state_lock:
        _state["pending"] = None
        _state["cents"] = cents
        _state["status"] = "note-funding-imported"
        _state["settlement"] = "waiting-confirmations"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 1,
            "phase": "funding-imported",
            "offline": False,
            "note_id": pending["note_id"],
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "completed": True,
        "broadcast": True,
        "retry_send": False,
        "receipt": receipt,
        "reply": (
            "The $5 funding send was broadcast once and imported into the disposable "
            "note wallet. After one Mutinynet confirmation, click Load note."
        ),
        "bill": _bill(),
    }


def _handle_bearer_funding_capacity_confirm(
    pending: dict[str, Any],
    phrase: str,
    bearer_service: BearerWebService | None,
) -> dict[str, Any]:
    try:
        outcome = _bearer(bearer_service).execute_funding_capacity(
            note_id=str(pending["note_id"]),
            plan_id=str(pending["id"]),
            confirmation_phrase=phrase,
        )
    except Exception as exc:
        result = _error(exc)
        if not result.get("retryable"):
            with _state_lock:
                _state["pending"] = None
                _state["status"] = "note-funding-capacity-recovery-required"
                _state["settlement"] = "unknown"
                _state["recovery"] = {
                    "action": "inspect-note-funding",
                    "note_id": pending["note_id"],
                    "retry_send": False,
                }
            result["recovery"] = _bill()["recovery"]
        return result

    receipt = {
        "action": "bearer-funding-capacity",
        "source": MODE,
        "status": "capacity-broadcast-recorded",
        "settlement": "waiting-confirmations",
        "retry_send": False,
        **outcome,
    }
    with _state_lock:
        _state["pending"] = None
        _state["status"] = "note-funding-capacity-waiting"
        _state["settlement"] = "waiting-confirmations"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 0,
            "phase": "funding-capacity-waiting",
            "offline": False,
            "note_id": pending["note_id"],
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "completed": True,
        "broadcast": True,
        "retry_send": False,
        "receipt": receipt,
        "reply": (
            "The separate source-wallet capacity transaction was broadcast once. "
            "Wait for one Mutinynet confirmation, then ask QVAC ‘check my note’."
        ),
        "bill": _bill(),
    }


def _handle_bearer_btc_topup_confirm(
    pending: dict[str, Any],
    phrase: str,
    bearer_service: BearerWebService | None,
) -> dict[str, Any]:
    try:
        outcome = _bearer(bearer_service).execute_btc_topup(
            note_id=str(pending["note_id"]),
            plan_id=str(pending["id"]),
            confirmation_phrase=phrase,
        )
    except Exception as exc:
        result = _error(exc)
        if not result.get("retryable"):
            with _state_lock:
                _state["pending"] = None
                _state["status"] = "note-btc-topup-recovery-required"
                _state["settlement"] = "unknown"
                _state["recovery"] = {
                    "action": "inspect-note-funding",
                    "note_id": pending["note_id"],
                    "retry_send": False,
                }
            result["recovery"] = _bill()["recovery"]
        return result

    receipt = {
        "action": "bearer-note-btc-topup",
        "source": MODE,
        "status": "btc-topup-broadcast-recorded",
        "settlement": "waiting-confirmations",
        "retry_send": False,
        **outcome,
    }
    with _state_lock:
        _state["pending"] = None
        _state["status"] = "note-btc-topup-waiting"
        _state["settlement"] = "waiting-confirmations"
        _state["recovery"] = None
        _state["bearer"] = {
            "step": 0,
            "phase": "btc-topup-waiting",
            "offline": False,
            "note_id": pending["note_id"],
            "txid": outcome.get("txid"),
        }
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "completed": True,
        "broadcast": True,
        "retry_send": False,
        "receipt": receipt,
        "reply": (
            "The issuing dock funded the disposable note's Bitcoin fee reserve "
            "once. Wait for one Mutinynet confirmation, then ask QVAC ‘check my "
            "note’ to prepare the separate $5 RGB funding plan."
        ),
        "bill": _bill(),
    }


@_serialized_workflow
def handle_confirm(
    confirm_id: str,
    phrase: str,
    service: RgbWebService | None = None,
    bearer_service: BearerWebService | None = None,
) -> dict[str, Any]:
    with _confirm_lock:
        with _state_lock:
            pending = _state["pending"]
            if (
                pending is None
                or pending.get("id") != confirm_id
            ):
                return {
                    "ok": False,
                    "error": "no matching pending RGB transfer plan",
                    "retryable": False,
                    "broadcast": False,
                }
            pending = dict(pending)

        if pending.get("action") == "bearer-redemption":
            return _handle_bearer_confirm(pending, phrase, bearer_service)
        if pending.get("action") == "bearer-funding":
            return _handle_bearer_funding_confirm(
                pending, phrase, bearer_service, service
            )
        if pending.get("action") == "bearer-funding-capacity":
            return _handle_bearer_funding_capacity_confirm(
                pending, phrase, bearer_service
            )
        if pending.get("action") == "bearer-note-btc-topup":
            return _handle_bearer_btc_topup_confirm(
                pending, phrase, bearer_service
            )
        if pending.get("action") != "rgb-send":
            return {
                "ok": False,
                "error": "pending operation is not confirmable",
                "retryable": False,
                "broadcast": False,
            }

        try:
            selected = _service(service)
        except Exception as exc:
            return _error(exc)

        try:
            outcome = selected.confirm_and_stage(confirm_id, phrase)
        except Exception as exc:
            result = _error(exc)
            if not result["retryable"]:
                receipt = {
                    "id": confirm_id,
                    "action": "rgb-send",
                    "source": MODE,
                    "status": "send-outcome-unknown",
                    "settlement": "unknown",
                    "broadcast": result["broadcast"],
                    "error": result["error"],
                }
                with _state_lock:
                    _state["pending"] = None
                    _state["status"] = "send-outcome-unknown"
                    _state["settlement"] = "unknown"
                    _state["recovery"] = {
                        "action": "inspect-wallet-before-any-new-plan",
                        "retry_send": False,
                    }
                    _state["receipts"].insert(0, receipt)
                result["receipt"] = receipt
            return result

        artifact = outcome.consignment
        receipt = {
            "id": confirm_id,
            "action": "rgb-send",
            "cents": pending["cents"],
            "source": MODE,
            "settlement": "broadcast",
            "broadcast": True,
            "txid": artifact.txid,
            "batch_transfer_idx": artifact.batch_transfer_idx,
            "consignment_path": str(artifact.path),
            "consignment_bytes": artifact.size,
            "consignment_sha256": artifact.sha256,
            "retry_send": False,
        }
        remaining_cents = pending["balance_before_cents"] - pending["cents"]
        if outcome.staged is None:
            receipt.update(
                {
                    "status": "consignment-stage-recovery-required",
                    "error": outcome.error,
                }
            )
            recovery = {
                "action": "retry-consignment-stage",
                "endpoint": "/api/rgb/retry-stage",
                "txid": artifact.txid,
                "artifact_path": str(artifact.path),
                "artifact_sha256": artifact.sha256,
                "retry_send": False,
            }
            with _state_lock:
                _state["pending"] = None
                _state["cents"] = remaining_cents
                _state["status"] = "consignment-stage-recovery-required"
                _state["settlement"] = "broadcast"
                _state["recovery"] = recovery
                _state["receipts"].insert(0, receipt)
            return {
                "ok": True,
                "completed": False,
                "broadcast": True,
                "recovery_required": True,
                "retry_send": False,
                "receipt": receipt,
                "recovery": recovery,
                "bill": _bill(),
            }

        receipt.update(
            {
                "status": "consignment-staged",
                "ble_session_id": f"0x{outcome.staged.session_id:08x}",
            }
        )
        with _state_lock:
            _state["pending"] = None
            _state["cents"] = remaining_cents
            _state["status"] = "consignment-staged"
            _state["settlement"] = "broadcast"
            _state["recovery"] = None
            _state["receipts"].insert(0, receipt)
        return {
            "ok": True,
            "completed": True,
            "broadcast": True,
            "recovery_required": False,
            "receipt": receipt,
            "bill": _bill(),
        }


@_serialized_workflow
def handle_retry_stage(service: RgbWebService | None = None) -> dict[str, Any]:
    try:
        selected = _service(service)
        outcome = selected.retry_consignment_stage()
    except Exception as exc:
        return _error(exc)
    receipt = {
        "action": "consignment-stage-retry",
        "source": MODE,
        "settlement": "broadcast",
        "broadcast": True,
        "txid": outcome.consignment.txid,
        "consignment_path": str(outcome.consignment.path),
        "consignment_bytes": outcome.consignment.size,
        "consignment_sha256": outcome.consignment.sha256,
        "retry_send": False,
    }
    if outcome.staged is None:
        receipt.update(
            {
                "status": "consignment-stage-recovery-required",
                "error": outcome.error,
            }
        )
        return {
            "ok": False,
            "retryable": True,
            "broadcast": True,
            "retry_send": False,
            "receipt": receipt,
            "recovery": _bill()["recovery"],
        }
    receipt.update(
        {
            "status": "consignment-staged",
            "ble_session_id": f"0x{outcome.staged.session_id:08x}",
        }
    )
    with _state_lock:
        _state["status"] = "consignment-staged"
        _state["recovery"] = None
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "completed": True,
        "broadcast": True,
        "retry_send": False,
        "receipt": receipt,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_import(service: RgbWebService | None = None) -> dict[str, Any]:
    try:
        selected = _service(service)
        outcome = selected.receive_and_import()
    except Exception as exc:
        return _error(exc)
    receipt = {
        "action": "consignment-import",
        "source": MODE,
        "session_id": f"0x{outcome.received.session_id:08x}",
        "artifact_path": str(outcome.received.artifact.path),
        "artifact_bytes": outcome.received.artifact.size,
        "artifact_sha256": outcome.received.artifact.sha256,
    }
    if outcome.updated_transfers is None:
        receipt.update(
            {
                "status": "consignment-import-recovery-required",
                "settlement": "waiting-import",
                "error": outcome.error,
            }
        )
        recovery = {
            "action": "retry-consignment-import",
            "endpoint": "/api/rgb/import",
            "artifact_path": str(outcome.received.artifact.path),
            "artifact_sha256": outcome.received.artifact.sha256,
            "retry_send": False,
        }
        with _state_lock:
            _state["status"] = "consignment-import-recovery-required"
            _state["settlement"] = "waiting-import"
            _state["recovery"] = recovery
            _state["receipts"].insert(0, receipt)
        return {
            "ok": False,
            "retryable": True,
            "broadcast": True,
            "retry_send": False,
            "recovery": recovery,
            "receipt": receipt,
            "bill": _bill(),
        }

    updated = {str(key): value for key, value in outcome.updated_transfers.items()}
    statuses = set(updated.values())
    settlement = (
        "settled"
        if statuses and statuses <= {"settled"}
        else "waiting-confirmations"
    )
    receipt.update(
        {
            "status": "consignment-imported",
            "settlement": settlement,
            "updated_transfers": updated,
        }
    )
    with _state_lock:
        expected = _state["expected_receive_cents"]
        if expected is not None:
            _state["cents"] += expected
        _state["expected_receive_cents"] = None
        _state["status"] = "consignment-imported"
        _state["settlement"] = settlement
        _state["recovery"] = None
        _state["receipts"].insert(0, receipt)
    return {
        "ok": True,
        "broadcast": True,
        "receipt": receipt,
        "bill": _bill(),
    }


def handle_refresh(service: RgbWebService | None = None) -> dict[str, Any]:
    try:
        cents, updated_transfers = _service(service).refresh_settlement()
    except Exception as exc:
        return _error(exc)
    updated = {str(key): value for key, value in updated_transfers.items()}
    statuses = set(updated.values())
    with _state_lock:
        _state["cents"] = cents
        if any(status == "settled" for status in statuses):
            _state["status"] = "settled"
            _state["settlement"] = "settled"
        elif statuses:
            _state["status"] = "waiting-confirmations"
            _state["settlement"] = "waiting-confirmations"
        _state["receipts"].insert(
            0,
            {
                "action": "wallet-refresh",
                "source": MODE,
                "status": _state["status"],
                "settlement": _state["settlement"],
                "updated_transfers": updated,
                "cents": cents,
            },
        )
    return {
        "ok": True,
        "broadcast": False,
        "updated_transfers": updated,
        "bill": _bill(),
    }


@_serialized_workflow
def handle_qvac(
    text: str,
    qvac_service: QvacIntentService | None = None,
    bearer_service: BearerWebService | None = None,
    wallet_service: RgbWebService | None = None,
) -> dict[str, Any]:
    """Classify locally, then hand the typed intent to deterministic policy."""

    try:
        intent = _qvac(qvac_service).classify(text)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "reply": f"Local QVAC could not classify that request: {exc}",
            "retryable": True,
            "broadcast": False,
        }

    if intent.intent == "LOAD_NOTE":
        result = handle_bearer_prepare(
            service=bearer_service,
            wallet_service=wallet_service,
        )
    elif intent.intent == "PASS_NOTE":
        result = handle_bearer_pass(service=bearer_service)
    elif intent.intent == "REDEEM_NOTE":
        result = handle_bearer_redeem(service=bearer_service)
    elif intent.intent == "INSPECT_NOTE":
        result = handle_bearer_prepare(
            check_existing=True,
            service=bearer_service,
            wallet_service=wallet_service,
        )
    elif intent.intent == "EXPLAIN_OR_RECOVER":
        bill = _bill()
        recovery = bill.get("recovery")
        result = {
            "ok": True,
            "reply": (
                "A recorded recovery is required. Use the displayed Recover control; "
                "it will not repeat an RGB send."
                if recovery
                else "No recovery is recorded. Connect the bill and inspect its current state."
            ),
            "pending": bill["pending"],
            "broadcast": False,
            "bill": bill,
        }
    else:
        result = {
            "ok": True,
            "reply": (
                "QVAC did not map that request to a safe action. Try ‘prepare a $5 "
                "note’, ‘check my note’, ‘pass this note’, or ‘redeem this note’."
            ),
            "pending": None,
            "broadcast": False,
            "bill": _bill(),
        }
    result["qvac"] = intent.public()
    return result


def handle_chat(
    text: str, service: RgbWebService | None = None
) -> dict[str, Any]:
    lowered = text.strip().lower()
    try:
        selected = _service(service)
    except Exception as exc:
        result = _error(exc)
        return {"reply": result["error"], "pending": None, **result}

    if selected.role is BillRole.B and any(
        word in lowered for word in ("import", "consignment", "settle")
    ):
        result = handle_import(selected)
        result.setdefault(
            "reply",
            (
                "Consignment imported; waiting for Mutinynet confirmation."
                if result["ok"]
                else result.get("error", "Consignment import needs recovery.")
            ),
        )
        result.setdefault("pending", None)
        return result
    if selected.role is BillRole.A and "retry" in lowered and "stage" in lowered:
        result = handle_retry_stage(selected)
        result.setdefault(
            "reply",
            (
                "Retained consignment staged without another send."
                if result["ok"]
                else "Consignment remains retained; BLE staging still failed."
            ),
        )
        result.setdefault("pending", None)
        return result
    if any(word in lowered for word in ("bill", "print", "dollar", "receive")):
        cents = _requested_cents(lowered)
        if selected.role is BillRole.B:
            return handle_stage_invoice(cents, selected)
        return handle_plan(cents, selected)
    if any(word in lowered for word in ("status", "worth", "balance")):
        refreshed = handle_refresh(selected)
        if not refreshed["ok"]:
            refreshed.setdefault("reply", refreshed["error"])
            refreshed.setdefault("pending", None)
            return refreshed
        bill = refreshed["bill"]
        return {
            "ok": True,
            "reply": (
                f"Bill {bill['role']} shows ${bill['cents'] / 100:.2f} "
                f"({bill['status']}, {bill['settlement']})."
            ),
            "pending": None,
        }
    instruction = (
        "Say ‘prepare bill for $5’ or ‘import consignment’."
        if selected.role is BillRole.B
        else "Tap the prepared bill, then say ‘bill for $5’."
    )
    return {"ok": True, "reply": instruction, "pending": _bill()["pending"]}


@_serialized_workflow
def handle_cancel(confirm_id: str) -> dict[str, Any]:
    with _state_lock:
        pending = _state["pending"]
        if pending is None or pending.get("id") != confirm_id:
            return {
                "ok": False,
                "error": "no matching pending RGB transfer plan",
                "broadcast": False,
                "retryable": False,
            }
        if pending.get("action") in {
            "bearer-redemption",
            "bearer-funding",
            "bearer-funding-capacity",
            "bearer-note-btc-topup",
        }:
            return {
                "ok": False,
                "error": (
                    "The durable bearer plan remains reserved. "
                    "Confirm it before expiry or use Recover to supersede an expired plan."
                ),
                "broadcast": False,
                "retryable": False,
                "bill": _bill(),
            }
        _state["pending"] = None
        _state["status"] = "idle"
        _state["settlement"] = "not-broadcast"
    return {"ok": True, "broadcast": False, "bill": _bill()}


def _status_code(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 202 if result.get("recovery_required") else 200
    if result.get("retryable"):
        return 503
    return 409


def _startup_error_message(exc: Exception) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    lowered = detail.lower()
    if "429" in lowered or "too many requests" in lowered:
        return (
            "Mutinynet indexer rate limit (HTTP 429); stop both dock startup "
            "attempts, wait, then start the roles sequentially"
        )
    if "timedout" in lowered or "timed out" in lowered:
        return "Mutinynet indexer timed out; wait and retry this dock"
    return detail


class DockHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[dock] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": _rgb_service is not None and _bearer_service is not None,
                    "role": _state["role"],
                    "bind": f"{HOST}:{PORT}",
                    "mode": MODE,
                    "qvac": {
                        "ready": (
                            _qvac_service.ready
                            if _qvac_service is not None
                            else False
                        ),
                        "model": (
                            _qvac_service.model_name
                            if _qvac_service is not None
                            else None
                        ),
                        "local": True,
                    },
                    "settlement": _state["settlement"],
                    "device": (
                        _device_registry.snapshot()
                        if _device_registry is not None
                        else None
                    ),
                },
            )
            return
        if path == "/api/device":
            try:
                self._json(200, {"ok": True, "device": _registry().snapshot()})
            except Exception as exc:
                result = _device_error(exc)
                self._json(_status_code(result), result)
            return
        if path == "/api/bill":
            self._json(200, _bill())
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._json(400, {"ok": False, "error": "invalid json"})
            return
        if path == "/api/chat":
            result = handle_chat(str(body.get("text") or ""))
            self._json(_status_code(result), result)
            return
        if path == "/api/qvac":
            result = handle_qvac(str(body.get("text") or ""))
            self._json(_status_code(result), result)
            return
        if path == "/api/workflow/send":
            try:
                cents = int(body.get("cents", 500))
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "cents must be an integer"})
                return
            result = handle_send_usdt(cents)
            self._json(_status_code(result), result)
            return
        if path == "/api/workflow/receive":
            try:
                cents = int(body.get("cents", 500))
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "cents must be an integer"})
                return
            result = handle_receive_usdt(cents)
            self._json(_status_code(result), result)
            return
        if path == "/api/bearer/load":
            result = handle_bearer_load(str(body.get("note_id") or "") or None)
            self._json(_status_code(result), result)
            return
        if path == "/api/bearer/pass":
            result = handle_bearer_pass()
            self._json(_status_code(result), result)
            return
        if path == "/api/bearer/redeem":
            result = handle_bearer_redeem()
            self._json(_status_code(result), result)
            return
        if path == "/api/bearer/recover":
            result = handle_bearer_recover(str(body.get("note_id") or "") or None)
            self._json(_status_code(result), result)
            return
        if path == "/api/bearer/refresh":
            result = handle_bearer_refresh(str(body.get("note_id") or "") or None)
            self._json(_status_code(result), result)
            return
        if path == "/api/device/scan":
            try:
                timeout = float(body.get("timeout", 5))
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "timeout must be numeric"})
                return
            result = handle_device_scan(timeout)
            self._json(_status_code(result), result)
            return
        if path == "/api/device/connect":
            result = handle_device_connect(str(body.get("address") or "") or None)
            self._json(_status_code(result), result)
            return
        if path == "/api/device/register":
            result = handle_device_register(str(body.get("address") or ""))
            self._json(_status_code(result), result)
            return
        if path in {"/api/confirm", "/api/rgb/confirm"}:
            result = handle_confirm(
                str(body.get("id") or ""),
                str(body.get("phrase") or ""),
            )
            self._json(_status_code(result), result)
            return
        if path == "/api/cancel":
            result = handle_cancel(str(body.get("id") or ""))
            self._json(_status_code(result), result)
            return
        if path == "/api/rgb/invoice":
            try:
                cents = int(body.get("cents", 500))
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "cents must be an integer"})
                return
            result = handle_stage_invoice(cents)
            self._json(_status_code(result), result)
            return
        if path == "/api/rgb/plan":
            try:
                cents = int(body.get("cents", 500))
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "cents must be an integer"})
                return
            result = handle_plan(cents)
            self._json(_status_code(result), result)
            return
        if path == "/api/rgb/import":
            result = handle_import()
            self._json(_status_code(result), result)
            return
        if path == "/api/rgb/refresh":
            result = handle_refresh()
            self._json(_status_code(result), result)
            return
        if path == "/api/rgb/retry-stage":
            result = handle_retry_stage()
            self._json(_status_code(result), result)
            return
        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    global _bearer_service, _device_registry, _qvac_service, _rgb_service
    service: RgbWebService | None = None
    bearer_service: BearerWebService | None = None
    try:
        role = BillRole.parse(ROLE)
        registry = DeviceRegistry.from_env(role)
        gateway = RegisteredBillGateway(registry, timeout=registry.timeout)
        service = RgbWebService.from_env(gateway=gateway)
        bearer_service = BearerWebService.from_env(service)
        qvac_service = QvacIntentService.from_env()
        balance_cents = service.spendable_cents()
    except KeyboardInterrupt:
        if service is not None:
            service.close()
        print("bill web dock startup cancelled", file=sys.stderr)
        return
    except Exception as exc:
        if service is not None:
            service.close()
        print(
            "live RGB web dock failed to initialize: "
            f"{_startup_error_message(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    _device_registry = registry
    _rgb_service = service
    _bearer_service = bearer_service
    _qvac_service = qvac_service
    qvac_service.start_background()
    registered_device = registry.snapshot()["device"]
    with _state_lock:
        _state.update(
            {
                "role": service.role.value,
                "status": "idle",
                "cents": balance_cents,
                "mode": MODE,
                "settlement": "settled",
                "asset_id": service.asset_id,
                "asset_precision": service.asset_precision,
                "wallet": _public_wallet_identity(service),
                "identity": (
                    registered_device["identity"]
                    if registered_device is not None
                    else "bill-unregistered"
                ),
            }
        )
    try:
        server = ThreadingHTTPServer((HOST, PORT), DockHandler)
    except Exception:
        qvac_service.close()
        service.close()
        _rgb_service = None
        _bearer_service = None
        _qvac_service = None
        _device_registry = None
        raise
    print(
        f"bill web dock on http://{HOST}:{PORT}/  "
        f"role={service.role.value} mode={MODE}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("bill web dock stopped")
    finally:
        server.server_close()
        qvac_service.close()
        service.close()
        _rgb_service = None
        _bearer_service = None
        _qvac_service = None
        _device_registry = None


if __name__ == "__main__":
    main()
