"""Web-safe orchestration for the accepted offline bearer-note lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from dock.chunk_protocol import FrameKind
from web.rgb.bearer_capsule import BearerCapsuleError, decode_bearer_capsule
from web.rgb.config import BillRole, RgbSettings
from web.rgb.note_live import (
    LiveNoteConfirmationRequired,
    LiveNoteError,
    LiveNotePaths,
    execute_live_funding_btc_topup_plan,
    execute_live_funding_capacity_plan,
    execute_live_funding_plan,
    execute_live_redemption_plan,
    prepare_live_funding_plan,
    prepare_live_funding_btc_topup_plan,
    prepare_live_funding_after_capacity,
    prepare_live_funding_capacity_plan,
    prepare_live_bearer_capsule,
    prepare_live_redemption_plan,
    recover_live_redemption,
    reconcile_live_funding_btc_topup_prebroadcast_rejection,
    redacted_plan,
    refresh_live_funding,
    refresh_live_funding_btc_topup,
    refresh_live_funding_capacity,
    refresh_live_redemption_settlement,
    renew_live_funding_plan,
    renew_live_redemption_plan,
)
from web.rgb_web import RgbWebService


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT_ROOT = ROOT / ".note-vault"


class BearerWebError(RuntimeError):
    """A bearer operation stopped at a known spend/recovery boundary."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        broadcast: bool | None,
        recovery_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.broadcast = broadcast
        self.recovery_action = recovery_action


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BearerWebError(
            f"cannot read durable bearer record {path.name}",
            retryable=False,
            broadcast=False,
        ) from exc
    if not isinstance(value, dict):
        raise BearerWebError(
            f"durable bearer record {path.name} is invalid",
            retryable=False,
            broadcast=False,
        )
    return value


class BearerWebService:
    """Compose one web-owned RGB wallet with the durable note vault and BLE."""

    def __init__(
        self,
        wallet: RgbWebService,
        *,
        vault_root: Path = DEFAULT_VAULT_ROOT,
        selected_note_id: str | None = None,
    ) -> None:
        self.wallet = wallet
        self.role = wallet.role
        self.vault_root = vault_root.expanduser().resolve()
        self.selected_note_id = selected_note_id.strip() if selected_note_id else None
        data_dir = wallet.settings.data_dir
        if (
            self.vault_root == data_dir
            or self.vault_root in data_dir.parents
            or data_dir in self.vault_root.parents
        ):
            raise BearerWebError(
                "note vault must not contain the active RGB wallet",
                retryable=False,
                broadcast=False,
            )
        self.vault_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.vault_root, 0o700)

    @classmethod
    def from_env(cls, wallet: RgbWebService) -> BearerWebService:
        return cls(
            wallet,
            vault_root=Path(os.environ.get("NOTE_VAULT_ROOT", DEFAULT_VAULT_ROOT)),
            selected_note_id=os.environ.get("BEARER_NOTE_ID"),
        )

    def _owned_open_funding_notes(self) -> list[tuple[str, dict[str, Any]]]:
        """Return this issuer's non-terminal notes without opening a wallet."""

        notes = self.vault_root / "notes"
        if not notes.exists():
            return []
        fingerprint = str(self.wallet.settings.master_fingerprint).lower()
        found: list[tuple[str, dict[str, Any]]] = []
        for manifest_path in sorted(notes.glob("*/manifest.json")):
            manifest = _read_object(manifest_path)
            note_id = manifest.get("note_id")
            if not isinstance(note_id, str) or note_id != manifest_path.parent.name:
                continue
            issuer = str(manifest.get("issuer_master_fingerprint") or "").lower()
            if issuer != fingerprint:
                continue
            if manifest.get("state") not in {"REDEEMED", "BOUNCED", "ABANDONED"}:
                found.append((note_id, manifest))
        return found

    @staticmethod
    def _funding_outcome(
        plan: object,
        manifest: dict[str, object],
        *,
        resumed: bool = False,
        renewed: bool = False,
    ) -> dict[str, object]:
        public = dict(plan) if isinstance(plan, dict) else redacted_plan(plan)  # type: ignore[arg-type]
        ready = int(public["note_vanilla_spendable_before"]) >= int(
            public["recommended_redemption_vanilla_sat"]
        )
        return {
            "phase": "bearer-funding-plan-ready",
            "note_id": str(public["note_id"]),
            "note_state": str(manifest["state"]),
            "funding_gate": (
                "READY_FOR_CONFIRMATION" if ready else "BTC_PREFLIGHT_REQUIRED"
            ),
            "redemption_btc_preflight": {
                "address": str(public["note_btc_address"]),
                "vanilla_spendable_sat": int(
                    public["note_vanilla_spendable_before"]
                ),
                "recommended_vanilla_target_sat": int(
                    public["recommended_redemption_vanilla_sat"]
                ),
                "ready": ready,
            },
            "resumed": resumed,
            "renewed": renewed,
            "plan": public,
            "broadcast": False,
        }

    def prepare_funding(self) -> dict[str, object]:
        """Create one fresh disposable note and stop before confirmation."""

        open_notes = self._owned_open_funding_notes()
        if len(open_notes) > 1:
            raise BearerWebError(
                "more than one unfinished note belongs to this wallet; recover it "
                "before preparing another",
                retryable=False,
                broadcast=False,
                recovery_action="inspect-note-funding",
            )
        if open_notes:
            note_id, manifest = open_notes[0]
            state = str(manifest.get("state", ""))
            if state == "ACTIVE":
                return {
                    "phase": "bearer-funding-settled",
                    "note_id": note_id,
                    "note_state": state,
                    "settled": True,
                    "broadcast": False,
                }
            if state == "FUNDING_IMPORTED":
                return self.refresh_funding(note_id)
            if state == "AWAITING_FUNDING":
                plan = _read_object(
                    LiveNotePaths.create(self.vault_root, note_id).plan_file
                )
                outcome = self._funding_outcome(plan, manifest)
                if outcome["funding_gate"] != "READY_FOR_CONFIRMATION":
                    return self.prepare_btc_topup(note_id)
                return outcome
            if state == "BTC_TOPUP_PLANNED":
                return self.prepare_btc_topup(note_id)
            if state == "BTC_TOPUP_BROADCAST_RECORDED":
                topup = self.refresh_btc_topup(note_id)
                if not bool(topup.get("confirmed")):
                    return topup
                return self.renew_funding(note_id)
            raise BearerWebError(
                f"note {note_id} is retained in state {state}; recover that exact "
                "note instead of creating another",
                retryable=False,
                broadcast=None,
                recovery_action="inspect-note-funding",
            )

        def prepare(settings: RgbSettings, opener: object) -> tuple[object, dict[str, object]]:
            return prepare_live_funding_plan(
                settings,
                vault_root=self.vault_root,
                asset_id=self.wallet.asset_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(prepare)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=True,
                broadcast=False,
                recovery_action="inspect-note-funding",
            ) from exc
        outcome = self._funding_outcome(plan, manifest)
        if outcome["funding_gate"] != "READY_FOR_CONFIRMATION":
            return self.prepare_btc_topup(str(outcome["note_id"]))
        return outcome

    def inspect_funding(self) -> dict[str, object] | None:
        """Rediscover and refresh this issuer's one durable unfinished note."""

        open_notes = self._owned_open_funding_notes()
        if not open_notes:
            return None
        if len(open_notes) > 1:
            raise BearerWebError(
                "more than one unfinished note belongs to this wallet; manual "
                "reconciliation is required",
                retryable=False,
                broadcast=None,
                recovery_action="inspect-note-funding",
            )
        note_id, manifest = open_notes[0]
        state = str(manifest.get("state", ""))
        if state == "AWAITING_FUNDING":
            plan = _read_object(
                LiveNotePaths.create(self.vault_root, note_id).plan_file
            )
            outcome = self._funding_outcome(plan, manifest)
            if outcome["funding_gate"] != "READY_FOR_CONFIRMATION":
                return self.prepare_btc_topup(note_id)
            return self.renew_funding(note_id)
        if state == "BTC_TOPUP_PLANNED":
            return self.prepare_btc_topup(note_id)
        if state == "BTC_TOPUP_BROADCAST_RECORDED":
            topup = self.refresh_btc_topup(note_id)
            if not bool(topup.get("confirmed")):
                return topup
            return self.renew_funding(note_id)
        if state == "BTC_TOPUP_AMBIGUOUS":
            self.reconcile_btc_topup(note_id)
            return self.prepare_btc_topup(note_id)
        if state in {"FUNDING_IMPORTED", "ACTIVE"}:
            return self.refresh_funding(note_id)
        if state in {
            "AMBIGUOUS",
            "PRE_BROADCAST_REJECTED",
            "FUNDING_CAPACITY_PLANNED",
        }:
            return self.prepare_funding_capacity(note_id)
        if state == "FUNDING_CAPACITY_BROADCAST_RECORDED":
            capacity = self.refresh_funding_capacity(note_id)
            if not bool(capacity.get("confirmed")):
                return capacity
            return self.prepare_funding_after_capacity(note_id)
        if state == "FUNDING_CAPACITY_READY":
            return self.prepare_funding_after_capacity(note_id)
        raise BearerWebError(
            f"note {note_id} is retained in state {state}; do not create or send "
            "another note",
            retryable=False,
            broadcast=None,
            recovery_action="inspect-note-funding",
        )

    @staticmethod
    def _btc_topup_outcome(
        plan: object, manifest: dict[str, object]
    ) -> dict[str, object]:
        public = redacted_plan(plan)  # type: ignore[arg-type]
        return {
            "phase": "bearer-note-btc-topup-plan-ready",
            "note_id": str(public["note_id"]),
            "note_state": str(manifest["state"]),
            "plan": public,
            "broadcast": False,
        }

    def prepare_btc_topup(self, note_id: str) -> dict[str, object]:
        """Prepare the issuer-funded note fee reserve without using a faucet."""

        def prepare(
            settings: RgbSettings, opener: object
        ) -> tuple[object, dict[str, object]]:
            return prepare_live_funding_btc_topup_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(prepare)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=False,
                recovery_action="inspect-note-funding",
            ) from exc
        return self._btc_topup_outcome(plan, manifest)

    def execute_btc_topup(
        self, *, note_id: str, plan_id: str, confirmation_phrase: str
    ) -> dict[str, object]:
        """Consume one exact Bitcoin top-up confirmation and broadcast once."""

        def execute(settings: RgbSettings, opener: object) -> dict[str, object]:
            return execute_live_funding_btc_topup_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                plan_id=plan_id,
                confirmation_phrase=confirmation_phrase,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(execute)
        except LiveNoteConfirmationRequired as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=False
            ) from exc
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=None,
                recovery_action="inspect-note-funding",
            ) from exc

    def refresh_btc_topup(self, note_id: str) -> dict[str, object]:
        """Verify the recorded top-up and note reserve before RGB planning."""

        def refresh(settings: RgbSettings, opener: object) -> dict[str, object]:
            return refresh_live_funding_btc_topup(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(refresh)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=True,
                broadcast=True,
                recovery_action="inspect-note-funding",
            ) from exc

    def reconcile_btc_topup(self, note_id: str) -> dict[str, object]:
        """Prove an ambiguous top-up stayed pre-broadcast before replanning."""

        def reconcile(settings: RgbSettings, opener: object) -> dict[str, object]:
            return reconcile_live_funding_btc_topup_prebroadcast_rejection(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(reconcile)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=None,
                recovery_action="inspect-note-funding",
            ) from exc

    def renew_funding(self, note_id: str) -> dict[str, object]:
        """Refresh BTC preflight by superseding only an unconsumed plan."""

        def renew(settings: RgbSettings, opener: object) -> tuple[object, dict[str, object]]:
            return renew_live_funding_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(renew)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc), retryable=False, broadcast=False
            ) from exc
        return self._funding_outcome(plan, manifest, renewed=True)

    @staticmethod
    def _funding_capacity_outcome(
        plan: object, manifest: dict[str, object]
    ) -> dict[str, object]:
        public = redacted_plan(plan)  # type: ignore[arg-type]
        return {
            "phase": "bearer-funding-capacity-plan-ready",
            "note_id": str(public["note_id"]),
            "note_state": str(manifest["state"]),
            "plan": public,
            "broadcast": False,
        }

    def prepare_funding_capacity(self, note_id: str) -> dict[str, object]:
        """Reconcile allocation rejection and plan one source capacity UTXO."""

        def prepare(
            settings: RgbSettings, opener: object
        ) -> tuple[object, dict[str, object]]:
            return prepare_live_funding_capacity_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(prepare)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=False,
                recovery_action="inspect-note-funding",
            ) from exc
        return self._funding_capacity_outcome(plan, manifest)

    def execute_funding_capacity(
        self, *, note_id: str, plan_id: str, confirmation_phrase: str
    ) -> dict[str, object]:
        """Consume one exact capacity confirmation and broadcast at most once."""

        def execute(settings: RgbSettings, opener: object) -> dict[str, object]:
            return execute_live_funding_capacity_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                plan_id=plan_id,
                confirmation_phrase=confirmation_phrase,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(execute)
        except LiveNoteConfirmationRequired as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=False
            ) from exc
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=None,
                recovery_action="inspect-note-funding",
            ) from exc

    def refresh_funding_capacity(self, note_id: str) -> dict[str, object]:
        def refresh(settings: RgbSettings, opener: object) -> dict[str, object]:
            return refresh_live_funding_capacity(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(refresh)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=True,
                broadcast=True,
                recovery_action="inspect-note-funding",
            ) from exc

    def prepare_funding_after_capacity(self, note_id: str) -> dict[str, object]:
        def prepare(
            settings: RgbSettings, opener: object
        ) -> tuple[object, dict[str, object]]:
            return prepare_live_funding_after_capacity(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(prepare)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=False,
                recovery_action="inspect-note-funding",
            ) from exc
        return self._funding_outcome(plan, manifest, renewed=True)

    def execute_funding(
        self, *, note_id: str, plan_id: str, confirmation_phrase: str
    ) -> dict[str, object]:
        """Consume one exact plan and execute at most one RGB funding send."""

        def execute(settings: RgbSettings, opener: object) -> dict[str, object]:
            return execute_live_funding_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                plan_id=plan_id,
                confirmation_phrase=confirmation_phrase,
                sender_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(execute)
        except LiveNoteConfirmationRequired as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=False
            ) from exc
        except LiveNoteError as exc:
            status = "unknown"
            broadcast: bool | None = None
            try:
                plan = _read_object(
                    LiveNotePaths.create(self.vault_root, note_id).plan_file
                )
                status = str(plan.get("status", "unknown"))
                if status in {"PLANNED", "SUPERSEDED", "PRE_BROADCAST_REJECTED"}:
                    broadcast = False
                elif status in {
                    "BROADCAST_RECORDED",
                    "IMPORT_RECOVERY_REQUIRED",
                    "FUNDING_IMPORTED",
                    "FUNDING_SETTLED",
                }:
                    broadcast = True
            except BearerWebError:
                pass
            raise BearerWebError(
                f"{exc} (durable status: {status})",
                retryable=False,
                broadcast=broadcast,
                recovery_action="inspect-note-funding",
            ) from exc

    def refresh_funding(self, note_id: str) -> dict[str, object]:
        try:
            return refresh_live_funding(
                self.wallet.settings,
                vault_root=self.vault_root,
                note_id=note_id,
            )
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=True
            ) from exc

    def _eligible_note_ids(self) -> list[str]:
        notes = self.vault_root / "notes"
        if not notes.exists():
            return []
        eligible: list[str] = []
        for manifest_path in sorted(notes.glob("*/manifest.json")):
            manifest = _read_object(manifest_path)
            note_id = manifest.get("note_id")
            if not isinstance(note_id, str) or note_id != manifest_path.parent.name:
                continue
            issuer = manifest.get("issuer_master_fingerprint")
            owned = (
                issuer is not None
                and str(issuer).lower()
                == str(self.wallet.settings.master_fingerprint).lower()
            )
            if issuer is None and self.role is BillRole.A:
                try:
                    legacy_plan = _read_object(
                        LiveNotePaths.create(self.vault_root, note_id).plan_file
                    )
                    owned = legacy_plan.get("source_wallet") == "wallet-a"
                except BearerWebError:
                    owned = False
            if manifest.get("state") == "ACTIVE" and owned:
                eligible.append(note_id)
        return eligible

    def select_load_note(self, requested_note_id: str | None = None) -> str:
        note_id = (requested_note_id or self.selected_note_id or "").strip()
        eligible = self._eligible_note_ids()
        if note_id:
            if note_id not in eligible:
                raise BearerWebError(
                    "selected bearer note is not an active, settled $5 note",
                    retryable=False,
                    broadcast=False,
                )
            return note_id
        if len(eligible) != 1:
            detail = "no" if not eligible else "more than one"
            raise BearerWebError(
                f"{detail} active bearer note is available; set BEARER_NOTE_ID explicitly",
                retryable=False,
                broadcast=False,
            )
        return eligible[0]

    def load_note(self, requested_note_id: str | None = None) -> dict[str, object]:
        note_id = self.select_load_note(requested_note_id)
        evidence = prepare_live_bearer_capsule(
            self.wallet.settings,
            vault_root=self.vault_root,
            note_id=note_id,
        )
        capsule_path = Path(str(evidence["capsule_path"]))
        try:
            session_id = self.wallet.flow.gateway.stage(
                FrameKind.BEARER_NOTE,
                capsule_path,
                int(evidence["denomination_cents"]),
            )
        except Exception as exc:
            raise BearerWebError(
                "The funded capsule is retained in the vault, but loading the bill failed: "
                f"{type(exc).__name__}: {exc}",
                retryable=True,
                broadcast=False,
                recovery_action="retry-bearer-load",
            ) from exc
        return {
            "phase": "bearer-loaded",
            "note_id": note_id,
            "denomination_cents": int(evidence["denomination_cents"]),
            "artifact_bytes": int(evidence["artifact_bytes"]),
            "artifact_sha256": str(evidence["artifact_sha256"]),
            "ble_session_id": f"0x{session_id:08x}",
            "broadcast": False,
        }

    def pass_note(self) -> dict[str, object]:
        return {
            "phase": "offline-pass-ready",
            "broadcast": False,
            "offline": True,
            "instruction": (
                "Take both bills away from the docks. Press the empty receiving bill "
                "once near the bill holding NOTE, then release. No internet is used."
            ),
        }

    def _incoming_directory(self) -> Path:
        directory = self.vault_root / "incoming"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        return directory

    def _persist_received_capsule(self) -> tuple[Path, str, int, int]:
        directory = self._incoming_directory()
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=".bearer-",
            suffix=".part",
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            session_id, reported_size = self.wallet.flow.gateway.receive(
                FrameKind.BEARER_NOTE,
                temporary,
            )
            os.chmod(temporary, 0o600)
            payload = temporary.read_bytes()
            if not payload or len(payload) != reported_size:
                raise BearerWebError(
                    "received bearer capsule size did not match BLE metadata",
                    retryable=False,
                    broadcast=False,
                )
            decode_bearer_capsule(payload)
            digest = hashlib.sha256(payload).hexdigest()
            destination = directory / f"{digest}.rgbn"
            if destination.exists():
                if destination.read_bytes() != payload:
                    raise BearerWebError(
                        "content-addressed bearer capsule was modified",
                        retryable=False,
                        broadcast=False,
                    )
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
                os.chmod(destination, 0o600)
            return destination, digest, len(payload), session_id
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _pending_incoming(self) -> tuple[Path, str, int]:
        candidates: list[tuple[Path, str, int]] = []
        for path in sorted(self._incoming_directory().glob("*.rgbn")):
            try:
                payload = path.read_bytes()
                capsule = decode_bearer_capsule(payload)
                manifest = _read_object(
                    LiveNotePaths.create(self.vault_root, capsule.note_id).manifest_file
                )
            except (OSError, BearerCapsuleError, BearerWebError, LiveNoteError):
                continue
            if manifest.get("state") != "REDEEMED":
                digest = hashlib.sha256(payload).hexdigest()
                if path.stem == digest:
                    candidates.append((path, digest, len(payload)))
        if len(candidates) != 1:
            detail = "no" if not candidates else "more than one"
            raise BearerWebError(
                f"{detail} unredeemed extracted capsule is available for recovery",
                retryable=False,
                broadcast=False,
            )
        return candidates[0]

    def prepare_redemption(self, *, receive_from_bill: bool) -> dict[str, object]:
        session_id: int | None = None
        if receive_from_bill:
            capsule_path, digest, size, session_id = self._persist_received_capsule()
        else:
            capsule_path, digest, size = self._pending_incoming()

        def prepare(
            settings: RgbSettings,
            opener: object,
        ) -> tuple[object, dict[str, object]]:
            return prepare_live_redemption_plan(
                settings,
                vault_root=self.vault_root,
                capsule_path=capsule_path,
                expected_capsule_sha256=digest,
                destination_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(prepare)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=True,
                broadcast=False,
                recovery_action="retry-bearer-plan",
            ) from exc
        public_plan = redacted_plan(plan)  # type: ignore[arg-type]
        return {
            "phase": "bearer-redemption-plan-ready",
            "note_id": str(public_plan["note_id"]),
            "note_state": str(manifest["state"]),
            "capsule_path": str(capsule_path),
            "capsule_sha256": digest,
            "capsule_bytes": size,
            "ble_session_id": (
                f"0x{session_id:08x}" if session_id is not None else None
            ),
            "plan": public_plan,
            "broadcast": False,
        }

    def renew_redemption(self, note_id: str) -> dict[str, object]:
        capsule_path, digest, size = self._pending_incoming()
        capsule = decode_bearer_capsule(capsule_path.read_bytes())
        if capsule.note_id != note_id:
            raise BearerWebError(
                "pending capsule does not match the expired plan",
                retryable=False,
                broadcast=False,
            )

        def renew(settings: RgbSettings, opener: object) -> tuple[object, dict[str, object]]:
            return renew_live_redemption_plan(
                settings,
                vault_root=self.vault_root,
                capsule_path=capsule_path,
                expected_capsule_sha256=digest,
                destination_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            plan, manifest = self.wallet.with_borrowed_backend(renew)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc), retryable=False, broadcast=False
            ) from exc
        public_plan = redacted_plan(plan)  # type: ignore[arg-type]
        return {
            "phase": "bearer-redemption-plan-ready",
            "renewed": True,
            "note_id": note_id,
            "note_state": str(manifest["state"]),
            "capsule_path": str(capsule_path),
            "capsule_sha256": digest,
            "capsule_bytes": size,
            "plan": public_plan,
            "broadcast": False,
        }

    def execute_redemption(
        self, *, note_id: str, plan_id: str, confirmation_phrase: str
    ) -> dict[str, object]:
        def execute(settings: RgbSettings, opener: object) -> dict[str, object]:
            return execute_live_redemption_plan(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                plan_id=plan_id,
                confirmation_phrase=confirmation_phrase,
                destination_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(execute)
        except LiveNoteConfirmationRequired as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=False
            ) from exc
        except LiveNoteError as exc:
            status = "unknown"
            broadcast: bool | None = None
            try:
                plan = _read_object(
                    LiveNotePaths.create(self.vault_root, note_id).redemption_plan_file
                )
                status = str(plan.get("status", "unknown"))
                if status in {"PLANNED", "PRE_BROADCAST_REJECTED"}:
                    broadcast = False
                elif status in {
                    "BROADCAST_RECORDED",
                    "IMPORT_RECOVERY_REQUIRED",
                    "REDEMPTION_IMPORTED",
                    "REDEEMED",
                }:
                    broadcast = True
            except BearerWebError:
                pass
            raise BearerWebError(
                f"{exc} (durable status: {status})",
                retryable=False,
                broadcast=broadcast,
                recovery_action="recover-bearer-redemption",
            ) from exc

    def recover_redemption(self, note_id: str) -> dict[str, object]:
        paths = LiveNotePaths.create(self.vault_root, note_id)
        plan = _read_object(paths.redemption_plan_file)
        if plan.get("status") == "PLANNED":
            if int(plan.get("expiration_timestamp", 0)) <= int(time.time()):
                return self.renew_redemption(note_id)
            return {
                "phase": "live-redemption-recovery-blocked",
                "broadcast": False,
                "note_id": note_id,
                "plan_id": str(plan.get("plan_id", "")),
                "status": "PLANNED",
                "instruction": (
                    "The immutable plan is still fresh. Confirm that exact plan; "
                    "it cannot be silently replaced."
                ),
            }

        def recover(settings: RgbSettings, opener: object) -> dict[str, object]:
            return recover_live_redemption(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                destination_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(recover)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc),
                retryable=False,
                broadcast=None,
                recovery_action="manual-bearer-reconciliation",
            ) from exc

    def refresh_redemption(self, note_id: str) -> dict[str, object]:
        def refresh(settings: RgbSettings, opener: object) -> dict[str, object]:
            return refresh_live_redemption_settlement(
                settings,
                vault_root=self.vault_root,
                note_id=note_id,
                destination_backend_opener=opener,  # type: ignore[arg-type]
            )

        try:
            return self.wallet.with_borrowed_backend(refresh)
        except LiveNoteError as exc:
            raise BearerWebError(
                str(exc), retryable=True, broadcast=True
            ) from exc
