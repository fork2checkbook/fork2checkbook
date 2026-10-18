"""Fail-closed configuration for a single bill wallet process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .sat_policy import MIN_FEE_RATE_SAT_VB, MIN_WITNESS_AMOUNT_SAT


class SettingsError(ValueError):
    """The wallet process configuration is unsafe or incomplete."""


class BillRole(str, Enum):
    A = "A"
    B = "B"

    @classmethod
    def parse(cls, value: str) -> BillRole:
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise SettingsError("BILL_ROLE must be A or B") from exc


def _resolved(value: str, *, name: str) -> Path:
    if not value.strip():
        raise SettingsError(f"{name} must not be empty")
    return Path(value).expanduser().resolve()


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _validate_mutinynet_indexer(url: str) -> str:
    parsed = urlparse(url)
    electrum_hosts = {"electrum.mutinynet.com"}
    esplora_hosts = {
        "esplora.signet.kaleidoswap.com",
        "mutinynet.com",
    }
    is_electrum = parsed.scheme in {"ssl", "tcp"} and parsed.hostname in electrum_hosts
    is_esplora = parsed.scheme == "https" and parsed.hostname in esplora_hosts
    if not (is_electrum or is_esplora):
        raise SettingsError(
            "ELECTRUM_URL must target the Mutinynet Electrum or Esplora service"
        )
    if is_electrum and parsed.port is None:
        raise SettingsError("ELECTRUM_URL must include an explicit port")
    return url


@dataclass(frozen=True)
class RgbSettings:
    """Configuration for exactly one live rgb-lib wallet."""

    role: BillRole
    data_dir: Path
    peer_data_dir: Path
    inbox_dir: Path
    electrum_url: str
    wallet_label: str = ""
    master_fingerprint: str | None = None
    mnemonic_file: Path | None = None
    min_confirmations: int = 1
    fee_rate: int = MIN_FEE_RATE_SAT_VB
    witness_amount_sat: int = MIN_WITNESS_AMOUNT_SAT
    max_consignment_bytes: int = 640 * 1024

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_credentials: bool = False,
    ) -> RgbSettings:
        env = os.environ if environ is None else environ
        role = BillRole.parse(env.get("BILL_ROLE", ""))
        data_a = _resolved(env.get("RGB_DATA_DIR_A", ""), name="RGB_DATA_DIR_A")
        data_b = _resolved(env.get("RGB_DATA_DIR_B", ""), name="RGB_DATA_DIR_B")
        if _paths_overlap(data_a, data_b):
            raise SettingsError("Bill A and Bill B data directories must not overlap")
        if env.get("RGB_OOB_CONSIGNMENT") != "1":
            raise SettingsError("RGB_OOB_CONSIGNMENT=1 is required")

        active = data_a if role is BillRole.A else data_b
        peer = data_b if role is BillRole.A else data_a
        inbox_name = f"RGB_OOB_INBOX_{role.value}"
        default_inbox = f".rgb-oob/bill-{role.value.lower()}"
        inbox = _resolved(env.get(inbox_name, default_inbox), name=inbox_name)
        if _paths_overlap(inbox, active) or _paths_overlap(inbox, peer):
            raise SettingsError("OOB inbox must be outside both wallet data directories")

        fingerprint_name = f"RGB_MASTER_FINGERPRINT_{role.value}"
        mnemonic_name = f"RGB_MNEMONIC_FILE_{role.value}"
        label_name = f"RGB_WALLET_LABEL_{role.value}"
        wallet_label = env.get(label_name, "").strip() or f"Wallet {role.value}"
        if len(wallet_label) > 80 or not wallet_label.isprintable():
            raise SettingsError(
                f"{label_name} must be printable and at most 80 characters"
            )
        fingerprint = env.get(fingerprint_name) or None
        mnemonic_raw = env.get(mnemonic_name) or None
        mnemonic_file = (
            _resolved(mnemonic_raw, name=mnemonic_name) if mnemonic_raw else None
        )
        if require_credentials and (not fingerprint or mnemonic_file is None):
            raise SettingsError(
                f"{fingerprint_name} and {mnemonic_name} are required for the live wallet"
            )

        try:
            min_confirmations = int(env.get("RGB_MIN_CONFIRMATIONS", "1"))
            fee_rate = int(env.get("RGB_FEE_RATE", str(MIN_FEE_RATE_SAT_VB)))
            witness_amount_sat = int(
                env.get("RGB_WITNESS_AMOUNT_SAT", str(MIN_WITNESS_AMOUNT_SAT))
            )
            max_consignment_bytes = int(
                env.get("RGB_MAX_CONSIGNMENT_BYTES", str(640 * 1024))
            )
        except ValueError as exc:
            raise SettingsError("RGB numeric settings must be integers") from exc
        if min_confirmations < 1:
            raise SettingsError("RGB_MIN_CONFIRMATIONS must be at least 1")
        if fee_rate < MIN_FEE_RATE_SAT_VB:
            raise SettingsError(
                f"RGB_FEE_RATE must be at least {MIN_FEE_RATE_SAT_VB} sat/vB"
            )
        if witness_amount_sat < MIN_WITNESS_AMOUNT_SAT:
            raise SettingsError(
                "RGB_WITNESS_AMOUNT_SAT must be at least "
                f"{MIN_WITNESS_AMOUNT_SAT} sats"
            )
        if max_consignment_bytes < 1:
            raise SettingsError("RGB_MAX_CONSIGNMENT_BYTES must be positive")

        return cls(
            role=role,
            data_dir=active,
            peer_data_dir=peer,
            inbox_dir=inbox,
            electrum_url=_validate_mutinynet_indexer(
                env.get(
                    "ELECTRUM_URL",
                    "https://esplora.signet.kaleidoswap.com",
                )
            ),
            wallet_label=wallet_label,
            master_fingerprint=fingerprint,
            mnemonic_file=mnemonic_file,
            min_confirmations=min_confirmations,
            fee_rate=fee_rate,
            witness_amount_sat=witness_amount_sat,
            max_consignment_bytes=max_consignment_bytes,
        )

    def read_mnemonic(self) -> str:
        """Read the active bill's mnemonic without retaining or logging the path contents."""
        if self.mnemonic_file is None:
            raise SettingsError("active bill mnemonic file is not configured")
        try:
            mode = self.mnemonic_file.stat().st_mode & 0o777
            if mode & 0o077:
                raise SettingsError("mnemonic file permissions must be 0600 or stricter")
            mnemonic = self.mnemonic_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SettingsError("unable to read active bill mnemonic file") from exc
        if not mnemonic:
            raise SettingsError("active bill mnemonic file is empty")
        return mnemonic
