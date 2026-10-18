"""Operator CLI for one role-scoped Mutinynet wallet process."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from web.rgb.adapter import RgbBillAdapter
from web.rgb.config import BillRole, RgbSettings
from web.rgb.native import RgbLibBackend
from web.rgb.sat_policy import MIN_COLORABLE_UTXO_SAT


def _balance(value: Any) -> dict[str, int]:
    return {
        "settled": int(value.settled),
        "future": int(value.future),
        "spendable": int(value.spendable),
    }


def _asset(value: Any, schema: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": schema,
        "asset_id": value.asset_id,
        "name": value.name,
        "precision": int(value.precision),
        "balance": _balance(value.balance),
    }
    ticker = getattr(value, "ticker", None)
    if ticker is not None:
        result["ticker"] = ticker
    return result


def _status(backend: RgbLibBackend, settings: RgbSettings) -> dict[str, Any]:
    wallet = backend._wallet
    btc = wallet.get_btc_balance(backend._online, False)
    assets = wallet.list_assets([])
    asset_rows = []
    for field, schema in (("nia", "NIA"), ("cfa", "CFA"), ("ifa", "IFA"), ("uda", "UDA")):
        asset_rows.extend(
            _asset(asset, schema) for asset in (getattr(assets, field, None) or [])
        )
    return {
        "role": settings.role.value,
        "network": "Mutinynet/custom-signet",
        "indexer": settings.electrum_url,
        "address": wallet.get_address(),
        "btc": {
            "vanilla": _balance(btc.vanilla),
            "colored": _balance(btc.colored),
        },
        "assets": asset_rows,
    }


def _refresh(backend: RgbLibBackend, settings: RgbSettings) -> dict[str, Any]:
    refreshed = backend._wallet.refresh(backend._online, None, [], False)
    return {
        "role": settings.role.value,
        "network": "Mutinynet/custom-signet",
        "updated_transfers": {
            str(batch_idx): {
                "updated_status": (
                    value.updated_status.name
                    if value.updated_status is not None
                    else None
                ),
                "failure": str(value.failure) if value.failure is not None else None,
            }
            for batch_idx, value in refreshed.items()
        },
    }


def _receive(
    backend: RgbLibBackend,
    settings: RgbSettings,
    *,
    asset_id: str | None,
    expires_seconds: int,
    bootstrap: bool,
) -> dict[str, Any]:
    if bootstrap:
        if settings.role is not BillRole.A or asset_id is not None:
            raise ValueError("bootstrap receive is only generic funding for Bill A")
        bootstrap_proxy = os.environ.get(
            "RGB_BOOTSTRAP_PROXY",
            "rpcs://proxy.kaleidoswap.com/json-rpc",
        )
        receive = backend._wallet.witness_receive(
            None,
            backend._rgb.Assignment.ANY(),
            int(time.time()) + expires_seconds,
            [bootstrap_proxy],
            settings.min_confirmations,
        )
        transport_endpoints = [bootstrap_proxy]
    else:
        adapter = RgbBillAdapter(settings, backend)
        receive = adapter.create_receive_intent(
            asset_id=asset_id,
            expires_in_seconds=expires_seconds,
        )
        transport_endpoints = []
    return {
        "role": settings.role.value,
        "purpose": "bootstrap-funding" if bootstrap else "tap-receive",
        "invoice": receive.invoice,
        "recipient_id": receive.recipient_id,
        "expiration_timestamp": receive.expiration_timestamp,
        "batch_transfer_idx": receive.batch_transfer_idx,
        "transport_endpoints": transport_endpoints,
    }


def _send(
    backend: RgbLibBackend,
    settings: RgbSettings,
    *,
    asset_id: str,
    recipient_id: str,
    amount: int | None,
    expires_seconds: int,
) -> dict[str, Any]:
    adapter = RgbBillAdapter(settings, backend)
    plan = adapter.plan_transfer(
        asset_id=asset_id,
        recipient_id=recipient_id,
        amount=amount,
        expires_in_seconds=expires_seconds,
    )
    print(
        json.dumps(
            {
                "confirmation_required": True,
                "plan_id": plan.plan_id,
                "asset_id": plan.asset_id,
                "recipient_id": plan.recipient_id,
                "transfer_amount_base_units": plan.amount,
                "spendable_balance_before": plan.balance_before,
                "witness_amount_sat": plan.witness_amount_sat,
                "fee_rate_sat_vb": plan.fee_rate,
                "min_confirmations": plan.min_confirmations,
                "donation": True,
                "transport_endpoints": [],
                "warning": "confirmation will broadcast on Mutinynet",
            },
            indent=2,
        ),
        flush=True,
    )
    expected = f"CONFIRM {plan.plan_id}"
    entered = input(f"Type exactly '{expected}' to broadcast: ")
    if entered != expected:
        raise RuntimeError("confirmation did not match; nothing was sent")
    adapter.confirm_transfer(plan.plan_id)
    artifact = adapter.execute_confirmed_transfer(plan.plan_id)
    return {
        "broadcast": True,
        "txid": artifact.txid,
        "batch_transfer_idx": artifact.batch_transfer_idx,
        "consignment_path": str(artifact.path),
        "consignment_size": artifact.size,
        "consignment_sha256": artifact.sha256,
    }


def _create_utxos(
    backend: RgbLibBackend,
    settings: RgbSettings,
    *,
    num: int,
    size_sat: int,
) -> dict[str, Any]:
    if settings.role is not BillRole.A:
        raise ValueError("this preparation step is only allowed for Bill A")
    if not 1 <= num <= 255:
        raise ValueError("UTXO count must be between 1 and 255")
    if size_sat < 1:
        raise ValueError("UTXO size must be positive")
    plan_id = str(uuid.uuid4())
    print(
        json.dumps(
            {
                "confirmation_required": True,
                "plan_id": plan_id,
                "operation": "create-colorable-utxos",
                "utxo_count": num,
                "utxo_size_sat": size_sat,
                "fee_rate_sat_vb": settings.fee_rate,
                "up_to": False,
                "warning": "confirmation will broadcast a Bitcoin transaction on Mutinynet",
            },
            indent=2,
        ),
        flush=True,
    )
    expected = f"CONFIRM {plan_id}"
    entered = input(f"Type exactly '{expected}' to broadcast: ")
    if entered != expected:
        raise RuntimeError("confirmation did not match; nothing was sent")

    before_txids = {
        tx.txid for tx in backend._wallet.list_transactions(backend._online, False)
    }
    created = backend._wallet.create_utxos(
        backend._online,
        False,
        num,
        size_sat,
        settings.fee_rate,
        False,
    )
    new_transactions = []
    for tx in backend._wallet.list_transactions(backend._online, False):
        if tx.txid not in before_txids:
            new_transactions.append(
                {
                    "txid": tx.txid,
                    "type": tx.transaction_type.name,
                    "received_sat": int(tx.received),
                    "sent_sat": int(tx.sent),
                    "fee_sat": int(tx.fee),
                }
            )
    return {
        "broadcast": True,
        "created_colorable_utxos": int(created),
        "transactions": new_transactions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("refresh")
    receive = subparsers.add_parser("receive")
    receive.add_argument("--asset-id")
    receive.add_argument("--expires-seconds", type=int, default=3600)
    receive.add_argument("--bootstrap", action="store_true")
    send = subparsers.add_parser("send")
    send.add_argument("--asset-id", required=True)
    send.add_argument("--recipient-id", required=True)
    send.add_argument(
        "--amount",
        type=int,
        help="asset amount in base units; defaults to the whole spendable balance",
    )
    send.add_argument("--expires-seconds", type=int, default=900)
    create_utxos = subparsers.add_parser("create-utxos")
    create_utxos.add_argument("--num", type=int, default=1)
    create_utxos.add_argument(
        "--size-sat", type=int, default=MIN_COLORABLE_UTXO_SAT
    )
    import_cmd = subparsers.add_parser("import-consignment")
    import_cmd.add_argument("path", type=Path)
    args = parser.parse_args()

    backend: RgbLibBackend | None = None
    close_backend = False
    try:
        settings = RgbSettings.from_env(require_credentials=True)
        backend = RgbLibBackend.open(settings)
        close_backend = True
        if args.command == "status":
            result = _status(backend, settings)
        elif args.command == "refresh":
            result = _refresh(backend, settings)
        elif args.command == "receive":
            result = _receive(
                backend,
                settings,
                asset_id=args.asset_id,
                expires_seconds=args.expires_seconds,
                bootstrap=args.bootstrap,
            )
        elif args.command == "send":
            result = _send(
                backend,
                settings,
                asset_id=args.asset_id,
                recipient_id=args.recipient_id,
                amount=args.amount,
                expires_seconds=args.expires_seconds,
            )
        elif args.command == "create-utxos":
            result = _create_utxos(
                backend,
                settings,
                num=args.num,
                size_sat=args.size_sat,
            )
        else:
            adapter = RgbBillAdapter(settings, backend)
            close_backend = False
            result = {
                "updated_transfers": adapter.import_consignment(args.path.read_bytes())
            }
            adapter.close()
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(
            f"wallet command failed: {type(exc).__name__}: {exc!r}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    finally:
        if close_backend and backend is not None:
            backend.close()


if __name__ == "__main__":
    main()
