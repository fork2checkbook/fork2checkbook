"""Minimum-safe Mutinynet satoshi policy for the controlled bearer-note shape.

The demo deliberately uses fresh, single-input disposable-note wallets.  These
constants are based on the largest accepted transaction virtual sizes observed
for that shape and on rgb-lib's 1,000-sat default colorable output.  Persisted
plans retain their own values; this module controls only newly prepared plans.
"""

from __future__ import annotations


MIN_FEE_RATE_SAT_VB = 1
MIN_COLORABLE_UTXO_SAT = 1_000
MIN_WITNESS_AMOUNT_SAT = 1_000

# Accepted demo transactions measured at 2 sat/vB.  Rounding upward preserves
# a deterministic minimum when the configured fee rate is reduced to 1 sat/vB.
CAPACITY_TX_VBYTES = 213
RGB_SEND_TX_VBYTES = 198


def _positive(value: int, *, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def minimum_rgb_send_fee_sat(fee_rate_sat_vb: int) -> int:
    """Return the measured minimum fee for the controlled RGB send shape."""

    return RGB_SEND_TX_VBYTES * _positive(
        fee_rate_sat_vb, name="fee rate"
    )


def minimum_capacity_fee_sat(fee_rate_sat_vb: int) -> int:
    """Return the measured minimum fee for one capacity transaction."""

    return CAPACITY_TX_VBYTES * _positive(
        fee_rate_sat_vb, name="fee rate"
    )


def minimum_redemption_vanilla_sat(
    witness_amount_sat: int,
    fee_rate_sat_vb: int,
) -> int:
    """Return sats that must remain after creating redemption capacity."""

    if witness_amount_sat < MIN_WITNESS_AMOUNT_SAT:
        raise ValueError(
            f"witness amount must be at least {MIN_WITNESS_AMOUNT_SAT} sats"
        )
    return witness_amount_sat + minimum_rgb_send_fee_sat(fee_rate_sat_vb)


def minimum_note_vanilla_sat(
    witness_amount_sat: int,
    fee_rate_sat_vb: int,
    *,
    colorable_utxo_sat: int = MIN_COLORABLE_UTXO_SAT,
) -> int:
    """Return the exact initial reserve for capacity plus one RGB redemption."""

    if colorable_utxo_sat < MIN_COLORABLE_UTXO_SAT:
        raise ValueError(
            f"colorable UTXO must be at least {MIN_COLORABLE_UTXO_SAT} sats"
        )
    fee_rate = _positive(fee_rate_sat_vb, name="fee rate")
    return (
        colorable_utxo_sat
        + minimum_capacity_fee_sat(fee_rate)
        + minimum_redemption_vanilla_sat(witness_amount_sat, fee_rate)
    )
