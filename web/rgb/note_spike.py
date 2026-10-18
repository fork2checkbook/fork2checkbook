"""Disposable-note RGB round-trip spike with an injectable backend.

The fake backend in this module is deliberately synthetic: it cannot open a
wallet, access BLE, or broadcast.  It exists to freeze the M1 ordering and
confirmation contract before a live rgb-lib backend is introduced.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from .sat_policy import MIN_FEE_RATE_SAT_VB


NETWORK = "mutinynet"
NOTE_AMOUNT_BASE_UNITS = 5_000_000
NOTE_DENOMINATION_CENTS = 500


class NoteSpikeError(RuntimeError):
    """The disposable-note spike rejected an unsafe state transition."""


class NoteConfirmationRequired(NoteSpikeError):
    """A fake transition did not receive its exact one-use confirmation."""


class NoteState(str, Enum):
    PREPARING = "preparing"
    AWAITING_FUNDING = "awaiting-funding"
    ACTIVE = "active"
    CLAIM_PLANNED = "claim-planned"
    AMBIGUOUS = "ambiguous"
    REDEEMED = "redeemed"


class PlanKind(str, Enum):
    FUNDING = "funding"
    REDEMPTION = "redemption"


@dataclass(frozen=True)
class NoteInvoice:
    invoice: str
    recipient_id: str
    asset_id: str
    amount: int
    expiration_timestamp: int
    transport_endpoints: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.invoice.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NoteTransferPlan:
    plan_id: str
    kind: PlanKind
    network: str
    note_id: str
    source_wallet_id: str
    recipient_wallet_id: str
    recipient_id: str
    recipient_invoice_sha256: str
    asset_id: str
    amount: int
    denomination_cents: int
    fee_rate_sat_vb: int
    expiration_timestamp: int

    @property
    def confirmation_phrase(self) -> str:
        return f"CONFIRM {self.plan_id}"


@dataclass(frozen=True)
class FakeTransitionResult:
    txid: str
    consignment: bytes
    consignment_sha256: str


@dataclass
class _PlanRecord:
    plan: NoteTransferPlan
    consumed: bool = False
    result: FakeTransitionResult | None = None


class DisposableWalletBackend(Protocol):
    """Minimum wallet surface required by the M1 experiment."""

    def create_wallet(self, wallet_id: str) -> None: ...

    def create_invoice(
        self,
        *,
        wallet_id: str,
        asset_id: str,
        amount: int,
        expiration_timestamp: int,
    ) -> NoteInvoice: ...

    def spendable_balance(self, wallet_id: str, asset_id: str) -> int: ...

    def send_oob(
        self,
        *,
        source_wallet_id: str,
        invoice: NoteInvoice,
        amount: int,
        fee_rate_sat_vb: int,
    ) -> FakeTransitionResult: ...

    def import_oob(
        self, *, wallet_id: str, consignment: bytes
    ) -> FakeTransitionResult: ...


class FakeDisposableWalletBackend:
    """Deterministic, network-free wallet model for the first M1 slice."""

    def __init__(self, *, asset_id: str) -> None:
        self.asset_id = asset_id
        self.wallets: dict[str, int] = {
            "wallet-a": 50_000_000,
            "wallet-b": 50_000_000,
        }
        self.send_calls: list[dict[str, object]] = []
        self.import_calls: list[dict[str, object]] = []
        self._pending: dict[str, tuple[str, int, FakeTransitionResult]] = {}

    def create_wallet(self, wallet_id: str) -> None:
        if wallet_id in self.wallets:
            raise NoteSpikeError("wallet already exists")
        self.wallets[wallet_id] = 0

    def create_invoice(
        self,
        *,
        wallet_id: str,
        asset_id: str,
        amount: int,
        expiration_timestamp: int,
    ) -> NoteInvoice:
        self._require_wallet(wallet_id)
        if asset_id != self.asset_id or amount <= 0:
            raise NoteSpikeError("fake invoice asset and amount must be exact")
        recipient_id = f"fake-recipient:{wallet_id}"
        invoice = (
            f"rgb-fake:{NETWORK}:{recipient_id}:{asset_id}:{amount}:"
            f"{expiration_timestamp}"
        )
        return NoteInvoice(
            invoice=invoice,
            recipient_id=recipient_id,
            asset_id=asset_id,
            amount=amount,
            expiration_timestamp=expiration_timestamp,
        )

    def spendable_balance(self, wallet_id: str, asset_id: str) -> int:
        self._require_wallet(wallet_id)
        if asset_id != self.asset_id:
            raise NoteSpikeError("unknown fake asset")
        return self.wallets[wallet_id]

    def send_oob(
        self,
        *,
        source_wallet_id: str,
        invoice: NoteInvoice,
        amount: int,
        fee_rate_sat_vb: int,
    ) -> FakeTransitionResult:
        self._require_wallet(source_wallet_id)
        if invoice.transport_endpoints:
            raise NoteSpikeError("physical OOB invoice must have no transport endpoint")
        if invoice.asset_id != self.asset_id or amount != invoice.amount:
            raise NoteSpikeError("fake send does not match the invoice")
        if self.wallets[source_wallet_id] < amount:
            raise NoteSpikeError("fake wallet has insufficient spendable balance")

        sequence = len(self.send_calls) + 1
        body = json.dumps(
            {
                "amount": amount,
                "asset_id": invoice.asset_id,
                "destination": invoice.recipient_id,
                "fee_rate_sat_vb": fee_rate_sat_vb,
                "network": NETWORK,
                "sequence": sequence,
                "source": source_wallet_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        consignment = b"FAKE-RGB-OOB-V1\x00" + body
        txid = hashlib.sha256(b"fake-tx-v1\x00" + body).hexdigest()
        digest = hashlib.sha256(consignment).hexdigest()
        result = FakeTransitionResult(
            txid=txid,
            consignment=consignment,
            consignment_sha256=digest,
        )
        self.wallets[source_wallet_id] -= amount
        destination = invoice.recipient_id.removeprefix("fake-recipient:")
        self._require_wallet(destination)
        self._pending[digest] = (destination, amount, result)
        self.send_calls.append(
            {
                "source_wallet_id": source_wallet_id,
                "destination_wallet_id": destination,
                "amount": amount,
                "transport_endpoints": invoice.transport_endpoints,
                "txid": txid,
            }
        )
        return result

    def import_oob(
        self, *, wallet_id: str, consignment: bytes
    ) -> FakeTransitionResult:
        self._require_wallet(wallet_id)
        digest = hashlib.sha256(consignment).hexdigest()
        pending = self._pending.pop(digest, None)
        if pending is None:
            raise NoteSpikeError("unknown or already imported fake consignment")
        destination, amount, result = pending
        if destination != wallet_id:
            raise NoteSpikeError("fake consignment belongs to another wallet")
        self.wallets[wallet_id] += amount
        self.import_calls.append(
            {
                "wallet_id": wallet_id,
                "amount": amount,
                "consignment_sha256": digest,
                "txid": result.txid,
            }
        )
        return result

    def _require_wallet(self, wallet_id: str) -> None:
        if wallet_id not in self.wallets:
            raise NoteSpikeError("unknown fake wallet")


class DisposableNoteSpike:
    """M1 state ordering around one disposable note wallet."""

    def __init__(
        self,
        backend: DisposableWalletBackend,
        *,
        asset_id: str,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        if not asset_id:
            raise NoteSpikeError("asset_id is required")
        self.backend = backend
        self.asset_id = asset_id
        self.clock = clock
        self.id_factory = id_factory
        self.note_id = id_factory()
        self.note_wallet_id = f"note:{self.note_id}"
        self.state = NoteState.PREPARING
        self.note_invoice: NoteInvoice | None = None
        self.final_invoice: NoteInvoice | None = None
        self._plans: dict[str, _PlanRecord] = {}
        self.funding_result: FakeTransitionResult | None = None
        self.redemption_result: FakeTransitionResult | None = None

    def prepare_note(self) -> NoteInvoice:
        self._require_state(NoteState.PREPARING)
        self.backend.create_wallet(self.note_wallet_id)
        self.note_invoice = self.backend.create_invoice(
            wallet_id=self.note_wallet_id,
            asset_id=self.asset_id,
            amount=NOTE_AMOUNT_BASE_UNITS,
            expiration_timestamp=int(self.clock()) + 900,
        )
        self._validate_invoice(self.note_invoice)
        self.state = NoteState.AWAITING_FUNDING
        return self.note_invoice

    def plan_funding(self) -> NoteTransferPlan:
        self._require_state(NoteState.AWAITING_FUNDING)
        if self.note_invoice is None:
            raise NoteSpikeError("note invoice is missing")
        return self._new_plan(
            kind=PlanKind.FUNDING,
            source_wallet_id="wallet-a",
            recipient_wallet_id=self.note_wallet_id,
            invoice=self.note_invoice,
        )

    def create_final_invoice(self) -> NoteInvoice:
        self._require_state(NoteState.ACTIVE)
        self.final_invoice = self.backend.create_invoice(
            wallet_id="wallet-b",
            asset_id=self.asset_id,
            amount=NOTE_AMOUNT_BASE_UNITS,
            expiration_timestamp=int(self.clock()) + 900,
        )
        self._validate_invoice(self.final_invoice)
        return self.final_invoice

    def plan_redemption(self) -> NoteTransferPlan:
        self._require_state(NoteState.ACTIVE)
        if self.final_invoice is None:
            raise NoteSpikeError("final recipient invoice must be created after activation")
        plan = self._new_plan(
            kind=PlanKind.REDEMPTION,
            source_wallet_id=self.note_wallet_id,
            recipient_wallet_id="wallet-b",
            invoice=self.final_invoice,
        )
        self.state = NoteState.CLAIM_PLANNED
        return plan

    def execute_confirmed(
        self, plan_id: str, confirmation_phrase: str
    ) -> FakeTransitionResult:
        record = self._plans.get(plan_id)
        if record is None or record.consumed:
            raise NoteConfirmationRequired("unknown or already-consumed plan")
        plan = record.plan
        if confirmation_phrase != plan.confirmation_phrase:
            raise NoteConfirmationRequired("exact confirmation phrase is required")
        if plan.expiration_timestamp <= int(self.clock()):
            record.consumed = True
            raise NoteConfirmationRequired("plan expired before confirmation")
        expected_state = (
            NoteState.AWAITING_FUNDING
            if plan.kind is PlanKind.FUNDING
            else NoteState.CLAIM_PLANNED
        )
        self._require_state(expected_state)

        # Consume before the backend boundary. An exception is ambiguous and
        # cannot be retried under the same plan.
        record.consumed = True
        invoice = self.note_invoice if plan.kind is PlanKind.FUNDING else self.final_invoice
        if invoice is None:
            raise NoteSpikeError("planned invoice is missing")
        try:
            result = self.backend.send_oob(
                source_wallet_id=plan.source_wallet_id,
                invoice=invoice,
                amount=plan.amount,
                fee_rate_sat_vb=plan.fee_rate_sat_vb,
            )
            imported = self.backend.import_oob(
                wallet_id=plan.recipient_wallet_id,
                consignment=result.consignment,
            )
        except Exception:
            self.state = NoteState.AMBIGUOUS
            raise
        if imported != result:
            self.state = NoteState.AMBIGUOUS
            raise NoteSpikeError("fake OOB import did not preserve exact result")
        record.result = result
        if plan.kind is PlanKind.FUNDING:
            self.funding_result = result
            if (
                self.backend.spendable_balance(self.note_wallet_id, self.asset_id)
                != NOTE_AMOUNT_BASE_UNITS
            ):
                raise NoteSpikeError("note wallet did not receive the exact amount")
            self.state = NoteState.ACTIVE
        else:
            self.redemption_result = result
            if self.backend.spendable_balance(self.note_wallet_id, self.asset_id) != 0:
                raise NoteSpikeError("note wallet was not emptied by redemption")
            self.state = NoteState.REDEEMED
        return result

    def replay(self) -> dict[str, object]:
        self._require_state(NoteState.REDEEMED)
        if self.redemption_result is None:
            raise NoteSpikeError("redemption result is missing")
        return {
            "status": "BOUNCED",
            "note_id": self.note_id,
            "winning_txid": self.redemption_result.txid,
            "new_send": False,
        }

    def _new_plan(
        self,
        *,
        kind: PlanKind,
        source_wallet_id: str,
        recipient_wallet_id: str,
        invoice: NoteInvoice,
    ) -> NoteTransferPlan:
        if self.backend.spendable_balance(source_wallet_id, self.asset_id) < invoice.amount:
            raise NoteSpikeError("source wallet lacks the exact note amount")
        plan = NoteTransferPlan(
            plan_id=self.id_factory(),
            kind=kind,
            network=NETWORK,
            note_id=self.note_id,
            source_wallet_id=source_wallet_id,
            recipient_wallet_id=recipient_wallet_id,
            recipient_id=invoice.recipient_id,
            recipient_invoice_sha256=invoice.sha256,
            asset_id=self.asset_id,
            amount=NOTE_AMOUNT_BASE_UNITS,
            denomination_cents=NOTE_DENOMINATION_CENTS,
            fee_rate_sat_vb=MIN_FEE_RATE_SAT_VB,
            expiration_timestamp=min(
                invoice.expiration_timestamp,
                int(self.clock()) + 900,
            ),
        )
        self._plans[plan.plan_id] = _PlanRecord(plan=plan)
        return plan

    def _validate_invoice(self, invoice: NoteInvoice) -> None:
        if invoice.transport_endpoints:
            raise NoteSpikeError("note invoices must have empty transport endpoints")
        if invoice.asset_id != self.asset_id:
            raise NoteSpikeError("note invoice asset mismatch")
        if invoice.amount != NOTE_AMOUNT_BASE_UNITS:
            raise NoteSpikeError("note invoice must request exactly 5 USDT")

    def _require_state(self, expected: NoteState) -> None:
        if self.state is not expected:
            raise NoteSpikeError(
                f"operation requires {expected.value}; current state is {self.state.value}"
            )
