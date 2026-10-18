import pytest

from web.rgb.sat_policy import (
    CAPACITY_TX_VBYTES,
    MIN_COLORABLE_UTXO_SAT,
    MIN_FEE_RATE_SAT_VB,
    MIN_WITNESS_AMOUNT_SAT,
    RGB_SEND_TX_VBYTES,
    minimum_capacity_fee_sat,
    minimum_note_vanilla_sat,
    minimum_redemption_vanilla_sat,
    minimum_rgb_send_fee_sat,
)


def test_default_policy_is_the_minimum_supported_demo_shape():
    assert MIN_FEE_RATE_SAT_VB == 1
    assert MIN_COLORABLE_UTXO_SAT == 1_000
    assert MIN_WITNESS_AMOUNT_SAT == 1_000
    assert CAPACITY_TX_VBYTES == 213
    assert RGB_SEND_TX_VBYTES == 198
    assert minimum_capacity_fee_sat(1) == 213
    assert minimum_rgb_send_fee_sat(1) == 198
    assert minimum_redemption_vanilla_sat(1_000, 1) == 1_198
    assert minimum_note_vanilla_sat(1_000, 1) == 2_411


def test_policy_scales_exactly_with_a_higher_configured_fee_rate():
    assert minimum_rgb_send_fee_sat(2) == 396
    assert minimum_redemption_vanilla_sat(1_000, 2) == 1_396
    assert minimum_note_vanilla_sat(1_000, 2) == 2_822


@pytest.mark.parametrize(
    ("witness", "fee", "utxo"),
    [(999, 1, 1_000), (1_000, 0, 1_000), (1_000, 1, 999)],
)
def test_policy_rejects_values_below_supported_minima(witness, fee, utxo):
    with pytest.raises(ValueError):
        minimum_note_vanilla_sat(
            witness,
            fee,
            colorable_utxo_sat=utxo,
        )
