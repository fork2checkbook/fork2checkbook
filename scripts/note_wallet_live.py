"""Operator CLI for the M1 live disposable rgb-lib wallet gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from web.rgb.config import RgbSettings
from web.rgb.note_live import (
    abandon_live_unbroadcast_note,
    execute_live_redemption_plan,
    execute_live_redemption_capacity_plan,
    execute_live_funding_plan,
    LiveNoteError,
    prepare_live_bearer_capsule,
    prepare_live_funding_plan,
    prepare_live_redemption_plan,
    prepare_live_redemption_capacity_plan,
    prepare_live_redemption_after_capacity,
    recover_live_redemption,
    reconcile_live_redemption_prebroadcast_rejection,
    redacted_plan,
    refresh_live_funding,
    refresh_live_redemption_settlement,
    refresh_live_redemption_capacity,
    renew_live_funding_plan,
    renew_live_redemption_plan,
    resume_live_funding_plan,
)
from web.rgb.sat_policy import MIN_COLORABLE_UTXO_SAT


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT_ROOT = ROOT / ".note-vault"


def _prepare(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    asset_id = args.asset_id or os.environ.get("RGB_ASSET_ID", "")
    plan, manifest = prepare_live_funding_plan(
        settings,
        vault_root=args.vault_root,
        asset_id=asset_id,
        invoice_lifetime_seconds=args.invoice_lifetime_seconds,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-funding-plan-ready",
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "wallet_created": True,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "funding_gate": (
            "READY_FOR_CONFIRMATION"
            if plan.note_vanilla_spendable_before
            >= plan.recommended_redemption_vanilla_sat
            else "BTC_PREFLIGHT_REQUIRED"
        ),
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "redemption_btc_preflight": {
            "address": plan.note_btc_address,
            "vanilla_spendable_sat": plan.note_vanilla_spendable_before,
            "colored_spendable_sat": plan.note_colored_spendable_before,
            "measured_minimum_sat": plan.measured_redemption_minimum_sat,
            "recommended_vanilla_target_sat": plan.recommended_redemption_vanilla_sat,
            "ready": (
                plan.note_vanilla_spendable_before
                >= plan.recommended_redemption_vanilla_sat
            ),
        },
        "warning": (
            "No transaction was broadcast. The exact confirmation phrase is displayed "
            "for review only and must be supplied in a separate operation."
        ),
        "secret_handling": "mnemonic stored mode 0600 under ignored .note-vault",
    }


def _resume(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = resume_live_funding_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        invoice_lifetime_seconds=args.invoice_lifetime_seconds,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-funding-plan-ready",
        "resumed": True,
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "wallet_created": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "funding_gate": (
            "READY_FOR_CONFIRMATION"
            if plan.note_vanilla_spendable_before
            >= plan.recommended_redemption_vanilla_sat
            else "BTC_PREFLIGHT_REQUIRED"
        ),
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "redemption_btc_preflight": {
            "address": plan.note_btc_address,
            "vanilla_spendable_sat": plan.note_vanilla_spendable_before,
            "colored_spendable_sat": plan.note_colored_spendable_before,
            "measured_minimum_sat": plan.measured_redemption_minimum_sat,
            "recommended_vanilla_target_sat": plan.recommended_redemption_vanilla_sat,
            "ready": (
                plan.note_vanilla_spendable_before
                >= plan.recommended_redemption_vanilla_sat
            ),
        },
        "warning": "No transaction was broadcast; review the exact plan separately.",
        "secret_handling": "existing private note wallet reused; no new keys generated",
    }


def _abandon(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    expected = f"ABANDON {args.note_id}"
    entered = input(f"Type exactly '{expected}' to archive this unbroadcast note: ")
    if entered != expected:
        raise LiveNoteError("exact abandon phrase is required")
    return abandon_live_unbroadcast_note(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        reason=args.reason,
    )


def _renew(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = renew_live_funding_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    ready = (
        plan.note_vanilla_spendable_before
        >= plan.recommended_redemption_vanilla_sat
    )
    if not ready:
        raise LiveNoteError("renewed plan did not pass the note BTC preflight")
    return {
        "phase": "live-funding-plan-ready",
        "renewed": True,
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "wallet_created": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "funding_gate": "READY_FOR_CONFIRMATION",
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "redemption_btc_preflight": {
            "address": plan.note_btc_address,
            "vanilla_spendable_sat": plan.note_vanilla_spendable_before,
            "colored_spendable_sat": plan.note_colored_spendable_before,
            "measured_minimum_sat": plan.measured_redemption_minimum_sat,
            "recommended_vanilla_target_sat": plan.recommended_redemption_vanilla_sat,
            "ready": True,
        },
        "warning": "No transaction was broadcast; exact confirmation is required.",
        "secret_handling": "existing private note wallet reused; no new keys generated",
    }


def _confirm(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    expected = f"CONFIRM {args.plan_id}"
    entered = input(f"Type exactly '{expected}' to broadcast on Mutinynet: ")
    return execute_live_funding_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        plan_id=args.plan_id,
        confirmation_phrase=entered,
    )


def _refresh_funding(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return refresh_live_funding(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
    )


def _prepare_capsule(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return prepare_live_bearer_capsule(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        vault_key_id=args.vault_key_id,
    )


def _prepare_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = prepare_live_redemption_plan(
        settings,
        vault_root=args.vault_root,
        capsule_path=args.capsule,
        expected_capsule_sha256=args.expected_sha256,
        invoice_lifetime_seconds=args.invoice_lifetime_seconds,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-redemption-plan-ready",
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "capsule_sha256": plan.capsule_sha256,
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "warning": (
            "No transaction was broadcast. Review this immutable plan and supply its "
            "exact confirmation phrase only in a separate redemption operation."
        ),
    }


def _confirm_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    expected = f"CONFIRM {args.plan_id}"
    entered = input(f"Type exactly '{expected}' to redeem on Mutinynet: ")
    return execute_live_redemption_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        plan_id=args.plan_id,
        confirmation_phrase=entered,
    )


def _recover_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return recover_live_redemption(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
    )


def _refresh_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return refresh_live_redemption_settlement(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
    )


def _renew_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = renew_live_redemption_plan(
        settings,
        vault_root=args.vault_root,
        capsule_path=args.capsule,
        expected_capsule_sha256=args.expected_sha256,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-redemption-plan-ready",
        "renewed": True,
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "capsule_sha256": plan.capsule_sha256,
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "warning": (
            "The expired plan was archived. No transaction was broadcast; only the "
            "new exact confirmation phrase can authorize redemption."
        ),
    }


def _reconcile_redemption(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return reconcile_live_redemption_prebroadcast_rejection(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
    )


def _prepare_capacity(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = prepare_live_redemption_capacity_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        utxo_size_sat=args.utxo_size_sat,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-redemption-capacity-plan-ready",
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "plan": redacted_plan(plan),
        "warning": (
            "No transaction was broadcast. This separate confirmation creates one "
            "colorable Bitcoin UTXO required by rgb-lib for redemption."
        ),
    }


def _confirm_capacity(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    expected = f"CONFIRM {args.plan_id}"
    entered = input(f"Type exactly '{expected}' to create capacity on Mutinynet: ")
    return execute_live_redemption_capacity_plan(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
        plan_id=args.plan_id,
        confirmation_phrase=entered,
    )


def _refresh_capacity(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    return refresh_live_redemption_capacity(
        settings,
        vault_root=args.vault_root,
        note_id=args.note_id,
    )


def _prepare_redemption_after_capacity(args: argparse.Namespace) -> dict[str, object]:
    settings = RgbSettings.from_env(require_credentials=True)
    plan, manifest = prepare_live_redemption_after_capacity(
        settings,
        vault_root=args.vault_root,
        capsule_path=args.capsule,
        expected_capsule_sha256=args.expected_sha256,
        plan_lifetime_seconds=args.plan_lifetime_seconds,
    )
    return {
        "phase": "live-redemption-plan-ready",
        "after_capacity": True,
        "network": "Mutinynet/custom-signet",
        "broadcast": False,
        "note_id": plan.note_id,
        "note_state": manifest["state"],
        "capsule_sha256": plan.capsule_sha256,
        "invoice_sha256": plan.recipient_invoice_sha256,
        "plan": redacted_plan(plan),
        "warning": (
            "Capacity is confirmed and the rejected plan was archived. No RGB "
            "transaction was broadcast; exact fresh confirmation is required."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="M1 live disposable-wallet plan; Mutinynet only"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare-funding-plan",
        help="create N, create its OOB invoice, and persist a no-broadcast plan",
    )
    prepare.add_argument("--asset-id")
    prepare.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    prepare.add_argument("--invoice-lifetime-seconds", type=int, default=86_400)
    prepare.add_argument("--plan-lifetime-seconds", type=int, default=900)
    resume = subparsers.add_parser(
        "resume-funding-plan",
        help="resume one preserved note after a read-only/indexer failure",
    )
    resume.add_argument("--note-id", required=True)
    resume.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    resume.add_argument("--invoice-lifetime-seconds", type=int, default=86_400)
    resume.add_argument("--plan-lifetime-seconds", type=int, default=900)
    abandon = subparsers.add_parser(
        "abandon-unbroadcast-note",
        help="archive a failed note shell only when no funding plan or send exists",
    )
    abandon.add_argument("--note-id", required=True)
    abandon.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    abandon.add_argument("--reason", default="operator-test-reset")
    renew = subparsers.add_parser(
        "renew-funding-plan",
        help="archive an unconsumed plan and bind a fresh plan to current balances",
    )
    renew.add_argument("--note-id", required=True)
    renew.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    renew.add_argument("--plan-lifetime-seconds", type=int, default=900)
    confirm = subparsers.add_parser(
        "confirm-funding",
        help="consume one exact confirmation and execute the saved plan once",
    )
    confirm.add_argument("--note-id", required=True)
    confirm.add_argument("--plan-id", required=True)
    confirm.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    refresh = subparsers.add_parser(
        "refresh-funding",
        help="refresh note N and activate it only after exact settlement proof",
    )
    refresh.add_argument("--note-id", required=True)
    refresh.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    capsule = subparsers.add_parser(
        "prepare-capsule",
        help="create one redacted-evidence bearer capsule from an active note",
    )
    capsule.add_argument("--note-id", required=True)
    capsule.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    capsule.add_argument("--vault-key-id", default="local-demo-vault-v1")
    redeem = subparsers.add_parser(
        "prepare-redemption-plan",
        help="verify a received bearer capsule and persist a no-broadcast destination-wallet plan",
    )
    redeem.add_argument("--capsule", type=Path, required=True)
    redeem.add_argument("--expected-sha256", required=True)
    redeem.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    redeem.add_argument("--invoice-lifetime-seconds", type=int, default=86_400)
    redeem.add_argument("--plan-lifetime-seconds", type=int, default=900)
    confirm_redeem = subparsers.add_parser(
        "confirm-redemption",
        help="consume one exact redemption confirmation and execute it once",
    )
    confirm_redeem.add_argument("--note-id", required=True)
    confirm_redeem.add_argument("--plan-id", required=True)
    confirm_redeem.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    recover_redeem = subparsers.add_parser(
        "recover-redemption",
        help="resume only a recorded destination-wallet import; never resend",
    )
    recover_redeem.add_argument("--note-id", required=True)
    recover_redeem.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    refresh_redeem = subparsers.add_parser(
        "refresh-redemption",
        help="refresh the destination wallet and close only an exactly settled redemption",
    )
    refresh_redeem.add_argument("--note-id", required=True)
    refresh_redeem.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    renew_redeem = subparsers.add_parser(
        "renew-redemption-plan",
        help="archive an expired unconsumed plan and prepare a fresh immutable plan",
    )
    renew_redeem.add_argument("--capsule", type=Path, required=True)
    renew_redeem.add_argument("--expected-sha256", required=True)
    renew_redeem.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    renew_redeem.add_argument("--plan-lifetime-seconds", type=int, default=900)
    reconcile_redeem = subparsers.add_parser(
        "reconcile-redemption",
        help="prove a known allocation-slot failure happened before broadcast",
    )
    reconcile_redeem.add_argument("--note-id", required=True)
    reconcile_redeem.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    prepare_capacity = subparsers.add_parser(
        "prepare-redemption-capacity",
        help="prepare a no-broadcast plan for one required colorable UTXO",
    )
    prepare_capacity.add_argument("--note-id", required=True)
    prepare_capacity.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    prepare_capacity.add_argument(
        "--utxo-size-sat", type=int, default=MIN_COLORABLE_UTXO_SAT
    )
    prepare_capacity.add_argument("--plan-lifetime-seconds", type=int, default=900)
    confirm_capacity = subparsers.add_parser(
        "confirm-redemption-capacity",
        help="consume one capacity confirmation and create one colorable UTXO",
    )
    confirm_capacity.add_argument("--note-id", required=True)
    confirm_capacity.add_argument("--plan-id", required=True)
    confirm_capacity.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    refresh_capacity = subparsers.add_parser(
        "refresh-redemption-capacity",
        help="verify the capacity tx confirmation and one free colorable UTXO",
    )
    refresh_capacity.add_argument("--note-id", required=True)
    refresh_capacity.add_argument(
        "--vault-root", type=Path, default=DEFAULT_VAULT_ROOT
    )
    replan = subparsers.add_parser(
        "prepare-redemption-after-capacity",
        help="archive the rejected redemption and prepare a fresh immutable plan",
    )
    replan.add_argument("--capsule", type=Path, required=True)
    replan.add_argument("--expected-sha256", required=True)
    replan.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    replan.add_argument("--plan-lifetime-seconds", type=int, default=900)
    args = parser.parse_args()

    try:
        if args.command == "prepare-funding-plan":
            result = _prepare(args)
        elif args.command == "resume-funding-plan":
            result = _resume(args)
        elif args.command == "abandon-unbroadcast-note":
            result = _abandon(args)
        elif args.command == "renew-funding-plan":
            result = _renew(args)
        elif args.command == "confirm-funding":
            result = _confirm(args)
        elif args.command == "refresh-funding":
            result = _refresh_funding(args)
        elif args.command == "prepare-capsule":
            result = _prepare_capsule(args)
        elif args.command == "prepare-redemption-plan":
            result = _prepare_redemption(args)
        elif args.command == "confirm-redemption":
            result = _confirm_redemption(args)
        elif args.command == "recover-redemption":
            result = _recover_redemption(args)
        elif args.command == "refresh-redemption":
            result = _refresh_redemption(args)
        elif args.command == "renew-redemption-plan":
            result = _renew_redemption(args)
        elif args.command == "reconcile-redemption":
            result = _reconcile_redemption(args)
        elif args.command == "prepare-redemption-capacity":
            result = _prepare_capacity(args)
        elif args.command == "confirm-redemption-capacity":
            result = _confirm_capacity(args)
        elif args.command == "refresh-redemption-capacity":
            result = _refresh_capacity(args)
        elif args.command == "prepare-redemption-after-capacity":
            result = _prepare_redemption_after_capacity(args)
        else:  # pragma: no cover - argparse owns this boundary
            raise LiveNoteError("unknown live note command")
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(
            f"live note command failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
