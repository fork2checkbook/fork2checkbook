from types import SimpleNamespace

import pytest

from scripts.rgb_wallet import _create_utxos
from web.rgb.config import BillRole, RgbSettings


class FakeWallet:
    def __init__(self):
        self.calls = []
        self.transactions = [
            SimpleNamespace(
                txid="funding",
                transaction_type=SimpleNamespace(name="INCOMING"),
                received=10_000,
                sent=0,
                fee=100,
            )
        ]

    def list_transactions(self, _online, _skip_sync):
        return self.transactions

    def create_utxos(self, *args):
        self.calls.append(args)
        self.transactions = [
            SimpleNamespace(
                txid="split",
                transaction_type=SimpleNamespace(name="CREATE_UTXOS"),
                received=0,
                sent=8_000,
                fee=300,
            ),
            *self.transactions,
        ]
        return 1


def settings(tmp_path):
    return RgbSettings(
        role=BillRole.A,
        data_dir=tmp_path / "bill-a",
        peer_data_dir=tmp_path / "bill-b",
        inbox_dir=tmp_path / "inbox-a",
        electrum_url="https://mutinynet.com/api",
    )


def test_create_utxos_requires_exact_confirmation(monkeypatch, tmp_path):
    wallet = FakeWallet()
    backend = SimpleNamespace(_wallet=wallet, _online="online")
    monkeypatch.setattr("builtins.input", lambda _prompt: "CANCEL")

    with pytest.raises(RuntimeError, match="nothing was sent"):
        _create_utxos(backend, settings(tmp_path), num=1, size_sat=8_000)

    assert wallet.calls == []


def test_create_utxos_broadcasts_only_after_matching_plan(monkeypatch, tmp_path):
    wallet = FakeWallet()
    backend = SimpleNamespace(_wallet=wallet, _online="online")

    def confirm(prompt):
        return prompt.split("'")[1]

    monkeypatch.setattr("builtins.input", confirm)
    result = _create_utxos(backend, settings(tmp_path), num=1, size_sat=8_000)

    assert wallet.calls == [("online", False, 1, 8_000, 1, False)]
    assert result["created_colorable_utxos"] == 1
    assert result["transactions"][0]["txid"] == "split"
