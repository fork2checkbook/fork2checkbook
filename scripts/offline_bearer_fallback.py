"""Operator fallback for the accepted offline bearer-note demonstration.

This runner talks to the two already-running web docks. It never opens either
role wallet directly, never invents a confirmation phrase, and never retries a
send. The note must already be ACTIVE and settled before ``run`` is used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_ISSUER_URL = "http://127.0.0.1:8789"
DEFAULT_REDEEMER_URL = "http://127.0.0.1:8788"
BEARER_KIND = 4
DENOMINATION_CENTS = 500


class DemoError(RuntimeError):
    """The fallback stopped at a safe, explainable boundary."""


class ApiFailure(DemoError):
    """One dock returned a structured HTTP failure."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.payload = payload
        message = str(payload.get("error") or payload.get("reply") or "dock request failed")
        super().__init__(f"HTTP {status}: {message}")


class DockApi:
    """Minimal JSON client for one already-running dock process."""

    def __init__(self, base_url: str, *, timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DemoError(f"cannot reach dock {self.base_url}: {exc}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DemoError(
                f"dock {self.base_url} returned non-JSON HTTP {status}"
            ) from exc
        if not isinstance(decoded, dict):
            raise DemoError(f"dock {self.base_url} returned a non-object response")
        if status >= 400 or not decoded.get("ok", False):
            raise ApiFailure(status, decoded)
        return decoded

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload or {})


@dataclass(frozen=True)
class HardwareState:
    dock_url: str
    address: str
    identity: str
    status: str
    denomination: int
    direction: str
    kind: int | None
    size: int
    denomination_cents: int

    @property
    def empty(self) -> bool:
        return (
            self.status == "idle"
            and self.denomination == 0
            and self.direction == "empty"
            and self.kind is None
            and self.size == 0
            and self.denomination_cents == 0
        )

    @property
    def bearer_held(self) -> bool:
        return (
            self.direction in {"bearer-held", "to-peer"}
            and self.kind == BEARER_KIND
            and self.denomination == DENOMINATION_CENTS
            and self.denomination_cents == DENOMINATION_CENTS
            and self.size > 0
        )

    @property
    def blocked(self) -> bool:
        return self.direction == "blocked"

    def public(self) -> dict[str, object]:
        return {
            "address": self.address,
            "identity": self.identity,
            "status": self.status,
            "denomination": self.denomination,
            "direction": self.direction,
            "kind": self.kind,
            "size": self.size,
            "denomination_cents": self.denomination_cents,
        }


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise DemoError(f"bill returned invalid {field}")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DemoError(f"bill returned invalid {field}") from exc


def read_hardware(api: DockApi) -> HardwareState:
    result = api.post("/api/device/connect")
    connection = result.get("connection")
    if not isinstance(connection, dict) or not connection.get("connected"):
        raise DemoError(f"dock {api.base_url} did not verify its registered bill")
    artifact = connection.get("artifact")
    if not isinstance(artifact, dict):
        raise DemoError(
            f"dock {api.base_url} bill does not expose inventory v3; do not continue"
        )
    kind_value = artifact.get("kind")
    kind = None if kind_value is None else _integer(kind_value, "artifact kind")
    return HardwareState(
        dock_url=api.base_url,
        address=str(connection.get("address") or ""),
        identity=str(connection.get("identity") or ""),
        status=str(connection.get("status") or ""),
        denomination=_integer(connection.get("denomination"), "denomination"),
        direction=str(artifact.get("direction") or ""),
        kind=kind,
        size=_integer(artifact.get("size"), "artifact size"),
        denomination_cents=_integer(
            artifact.get("denomination_cents"), "artifact denomination"
        ),
    )


def describe(state: HardwareState) -> str:
    kind = "empty" if state.kind is None else str(state.kind)
    return (
        f"{state.identity} {state.address} status={state.status} "
        f"denomination={state.denomination} "
        f"artifact={state.direction}:{kind} bytes={state.size}"
    )


def require_clean(state: HardwareState, label: str) -> None:
    if state.blocked:
        raise DemoError(f"{label} is recovery-blocked: {describe(state)}")
    if not state.empty:
        raise DemoError(f"{label} is not clean: {describe(state)}")


def verify_docks(
    issuer: DockApi, redeemer: DockApi, *, require_empty: bool
) -> tuple[HardwareState, HardwareState]:
    if issuer.base_url == redeemer.base_url:
        raise DemoError("issuer and redeemer dock URLs must be different")
    for label, api in (("issuer", issuer), ("redeemer", redeemer)):
        health = api.get("/api/health")
        if health.get("mode") != "live-rgb-tap":
            raise DemoError(f"{label} dock is not in live-rgb-tap mode")
    issuer_state = read_hardware(issuer)
    redeemer_state = read_hardware(redeemer)
    if not issuer_state.address or issuer_state.address == redeemer_state.address:
        raise DemoError("the two docks must have different registered physical bills")
    if require_empty:
        require_clean(issuer_state, "issuer bill")
        require_clean(redeemer_state, "redeemer bill")
    return issuer_state, redeemer_state


def wait_for_offline_tap(
    issuer: DockApi,
    redeemer: DockApi,
    *,
    timeout: float,
    quiet_window: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    emit: Callable[[str], None] = print,
) -> tuple[HardwareState, HardwareState]:
    """Wait in radio-quiet windows, then verify the all-or-nothing handoff."""

    deadline = monotonic() + timeout
    attempt = 0
    while monotonic() < deadline:
        attempt += 1
        remaining = max(0.0, deadline - monotonic())
        window = min(quiet_window, remaining)
        emit(
            f"Tap window {attempt}: press the EMPTY receiving bill once near the "
            f"bill showing NOTE, then release. Waiting {window:.0f}s without laptop BLE…"
        )
        sleep(window)
        source = read_hardware(issuer)
        destination = read_hardware(redeemer)
        emit(f"  source:      {describe(source)}")
        emit(f"  destination: {describe(destination)}")
        if source.empty and destination.bearer_held:
            return source, destination
        if source.blocked or destination.blocked:
            raise DemoError("a bill entered recovery-blocked state; do not tap again")
        if source.empty and destination.empty:
            raise DemoError(
                "both bills report empty after loading; stop and inspect serial logs"
            )
        if not (
            (source.bearer_held and destination.empty)
            or (source.bearer_held and destination.bearer_held)
        ):
            raise DemoError("unexpected inventory transition; do not discard either bill")
    raise DemoError("timed out waiting for the offline tap; no artifact was discarded")


def exact_confirmation(
    pending: dict[str, Any], *, input_fn: Callable[[str], str] = input
) -> tuple[str, str]:
    plan_id = str(pending.get("id") or "")
    phrase = str(pending.get("confirmation_phrase") or "")
    if not plan_id or phrase != f"CONFIRM {plan_id}":
        raise DemoError("dock returned an invalid exact-confirmation boundary")
    print("\nImmutable redemption plan")
    print(f"  plan:   {plan_id}")
    print(f"  action: {pending.get('action')}")
    print(f"  amount: ${_integer(pending.get('cents'), 'pending cents') / 100:.2f}")
    print(f"  phrase: {phrase}")
    typed = input_fn("Type the exact phrase to broadcast once, or Ctrl+C to stop: ").strip()
    if typed != phrase:
        raise DemoError("confirmation phrase did not match; nothing was submitted")
    return plan_id, typed


def wait_for_settlement(
    redeemer: DockApi,
    note_id: str,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = monotonic() + timeout
    attempt = 0
    last_error = ""
    while monotonic() < deadline:
        attempt += 1
        try:
            result = redeemer.post("/api/bearer/refresh", {"note_id": note_id})
        except ApiFailure as exc:
            if not exc.payload.get("retryable"):
                raise
            last_error = str(exc.payload.get("error") or exc)
            print(f"Settlement check {attempt}: retryable read error: {last_error}")
        else:
            if result.get("settled"):
                return result
            txid = str(result.get("txid") or "pending")
            print(f"Settlement check {attempt}: {txid} is still waiting")
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(interval, remaining))
    detail = f" Last read error: {last_error}" if last_error else ""
    raise DemoError(
        "settlement is still pending; it is safe to rerun Refresh settlement later."
        + detail
    )


def write_evidence(directory: Path, evidence: dict[str, Any]) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"offline-bearer-{stamp}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def run_check(args: argparse.Namespace) -> int:
    issuer = DockApi(args.issuer_url, timeout=args.http_timeout)
    redeemer = DockApi(args.redeemer_url, timeout=args.http_timeout)
    source, destination = verify_docks(issuer, redeemer, require_empty=True)
    print("Both registered bills are clean.")
    print(f"  issuer:   {describe(source)}")
    print(f"  redeemer: {describe(destination)}")
    return 0


def run_demo(args: argparse.Namespace) -> int:
    issuer = DockApi(args.issuer_url, timeout=args.http_timeout)
    redeemer = DockApi(args.redeemer_url, timeout=args.http_timeout)
    source_before, destination_before = verify_docks(
        issuer, redeemer, require_empty=True
    )
    evidence: dict[str, Any] = {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "issuer_url": issuer.base_url,
        "redeemer_url": redeemer.base_url,
        "source_before": source_before.public(),
        "destination_before": destination_before.public(),
        "broadcasts": [],
    }

    print("Loading one already-settled $5 bearer note onto the issuer bill…")
    loaded = issuer.post("/api/bearer/load", {"note_id": args.note_id or ""})
    receipt = loaded.get("receipt")
    if not isinstance(receipt, dict):
        raise DemoError("load completed without a durable bearer receipt")
    note_id = str(receipt.get("note_id") or "")
    capsule_sha256 = str(receipt.get("artifact_sha256") or "")
    if not note_id or len(capsule_sha256) != 64:
        raise DemoError("load receipt is missing note ID or capsule SHA-256")
    evidence.update(
        {
            "note_id": note_id,
            "capsule_sha256": capsule_sha256,
            "capsule_bytes": receipt.get("artifact_bytes"),
            "load_session_id": receipt.get("ble_session_id"),
        }
    )
    source_loaded = read_hardware(issuer)
    destination_empty = read_hardware(redeemer)
    if not source_loaded.bearer_held:
        raise DemoError(f"issuer did not retain the loaded note: {describe(source_loaded)}")
    require_clean(destination_empty, "redeemer bill")
    print(f"Loaded capsule SHA-256: {capsule_sha256}")

    issuer.post("/api/bearer/pass")
    source_after_tap, destination_after_tap = wait_for_offline_tap(
        issuer,
        redeemer,
        timeout=args.tap_timeout,
        quiet_window=args.tap_window,
    )
    evidence["source_after_tap"] = source_after_tap.public()
    evidence["destination_after_tap"] = destination_after_tap.public()
    print("Offline handoff verified: source EMPTY, destination NOTE.")

    print("Extracting the exact capsule and preparing the destination plan…")
    redemption = redeemer.post("/api/bearer/redeem")
    pending = redemption.get("pending")
    extract_receipt = redemption.get("receipt")
    if not isinstance(pending, dict) or not isinstance(extract_receipt, dict):
        raise DemoError("redeemer did not return an immutable confirmation plan")
    extracted_sha256 = str(extract_receipt.get("capsule_sha256") or "")
    if extracted_sha256 != capsule_sha256:
        raise DemoError("extracted capsule SHA-256 does not match the loaded capsule")
    evidence["extraction"] = {
        "capsule_sha256": extracted_sha256,
        "capsule_bytes": extract_receipt.get("capsule_bytes"),
        "ble_session_id": extract_receipt.get("ble_session_id"),
    }

    plan_id, phrase = exact_confirmation(pending)
    try:
        confirmed = redeemer.post(
            "/api/confirm", {"id": plan_id, "phrase": phrase}
        )
    except ApiFailure as exc:
        recovery = exc.payload.get("recovery")
        if (
            isinstance(recovery, dict)
            and recovery.get("endpoint") == "/api/bearer/recover"
            and recovery.get("retry_send") is False
            and exc.payload.get("broadcast") is True
        ):
            print("Broadcast was recorded; running import-only recovery (never resend)…")
            confirmed = redeemer.post(
                "/api/bearer/recover", {"note_id": note_id}
            )
        else:
            raise DemoError(
                f"redemption stopped safely: {exc}. Do not submit the phrase again."
            ) from exc
    confirmation_receipt = confirmed.get("receipt")
    if isinstance(confirmation_receipt, dict):
        txid = str(confirmation_receipt.get("txid") or "")
        consignment_sha256 = str(
            confirmation_receipt.get("consignment_sha256") or ""
        )
    else:
        bill = confirmed.get("bill")
        bearer = bill.get("bearer") if isinstance(bill, dict) else None
        txid = str(bearer.get("txid") or "") if isinstance(bearer, dict) else ""
        consignment_sha256 = ""
    if txid:
        evidence["broadcasts"].append({"kind": "redemption", "txid": txid})
    evidence["redemption"] = {
        "plan_id": plan_id,
        "txid": txid,
        "consignment_sha256": consignment_sha256,
    }
    print(f"Redemption recorded: {txid or 'txid retained by dock'}")

    settled = wait_for_settlement(
        redeemer,
        note_id,
        timeout=args.settlement_timeout,
        interval=args.settlement_interval,
    )
    evidence["settlement"] = {
        key: settled.get(key)
        for key in (
            "settled",
            "txid",
            "transfer_kind",
            "transfer_status",
            "assignment_amounts",
            "destination_balance_before",
            "destination_balance_after",
        )
        if key in settled
    }

    source_final, destination_final = verify_docks(
        issuer, redeemer, require_empty=True
    )
    evidence["source_final"] = source_final.public()
    evidence["destination_final"] = destination_final.public()
    evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
    evidence["result"] = "REDEEMED"
    evidence_path = write_evidence(args.evidence_dir, evidence)
    print("\nFallback demo complete: note REDEEMED and both bills EMPTY.")
    print(f"Redacted evidence: {evidence_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive the accepted pre-funded offline bearer demo through two "
            "already-running web docks"
        )
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--issuer-url",
        default=os.environ.get("DEMO_ISSUER_URL", DEFAULT_ISSUER_URL),
        help="dock holding the ACTIVE note (default: Wallet B on :8789)",
    )
    common.add_argument(
        "--redeemer-url",
        default=os.environ.get("DEMO_REDEEMER_URL", DEFAULT_REDEEMER_URL),
        help="previously unknown destination dock (default: Wallet A on :8788)",
    )
    common.add_argument("--http-timeout", type=float, default=90.0)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check", parents=[common], help="verify both registered bills are empty"
    )
    check.set_defaults(handler=run_check)
    run = subparsers.add_parser(
        "run", parents=[common], help="run one pre-funded whole-value demo"
    )
    run.add_argument(
        "--note-id",
        default="",
        help="ACTIVE settled note; omit only when the issuer owns exactly one",
    )
    run.add_argument("--tap-timeout", type=float, default=120.0)
    run.add_argument(
        "--tap-window",
        type=float,
        default=12.0,
        help="radio-quiet seconds allowed for each physical tap attempt",
    )
    run.add_argument("--settlement-timeout", type=float, default=900.0)
    run.add_argument("--settlement-interval", type=float, default=20.0)
    run.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path(".note-vault/demo-runs"),
        help="ignored directory for redacted mode-0600 run evidence",
    )
    run.set_defaults(handler=run_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for field in (
        "http_timeout",
        "tap_timeout",
        "tap_window",
        "settlement_timeout",
        "settlement_interval",
    ):
        if hasattr(args, field) and getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("\nStopped by operator; no confirmation is retried.", file=sys.stderr)
        return 130
    except DemoError as exc:
        print(f"fallback demo stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
