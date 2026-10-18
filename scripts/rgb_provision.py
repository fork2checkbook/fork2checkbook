"""Provision one fresh Mutinynet rgb-lib wallet without printing its mnemonic."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

import rgb_lib


ROOT = Path(__file__).resolve().parents[1]


def _write_private(path: Path, contents: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def provision(role: str) -> tuple[Path, Path, Path]:
    role = role.upper()
    if role not in {"A", "B"}:
        raise ValueError("role must be A or B")

    slug = role.lower()
    data_dir = ROOT / "wallet-data" / f"bill-{slug}"
    mnemonic_file = ROOT / "secrets" / f"bill-{slug}.mnemonic"
    env_file = ROOT / f".env.{slug}"
    for path in (data_dir, mnemonic_file, env_file):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing path: {path}")

    data_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mnemonic_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data_dir.parent, 0o700)
    os.chmod(mnemonic_file.parent, 0o700)

    keys = rgb_lib.generate_keys(
        rgb_lib.BitcoinNetwork.SIGNET_CUSTOM,
        rgb_lib.WitnessVersion.TAPROOT,
    )
    data_dir.mkdir(mode=0o700)
    try:
        wallet_data = rgb_lib.WalletData(
            data_dir=str(data_dir),
            bitcoin_network=rgb_lib.BitcoinNetwork.SIGNET_CUSTOM,
            database_type=rgb_lib.DatabaseType.SQLITE,
            max_allocations_per_utxo=5,
            supported_schemas=[
                rgb_lib.AssetSchema.NIA,
                rgb_lib.AssetSchema.CFA,
                rgb_lib.AssetSchema.IFA,
                rgb_lib.AssetSchema.UDA,
            ],
        )
        singlesig_keys = rgb_lib.SinglesigKeys(
            account_xpub_vanilla=keys.account_xpub_vanilla,
            account_xpub_colored=keys.account_xpub_colored,
            vanilla_keychain=None,
            master_fingerprint=keys.master_fingerprint,
            mnemonic=keys.mnemonic,
            witness_version=rgb_lib.WitnessVersion.TAPROOT,
        )
        wallet = rgb_lib.Wallet(wallet_data, singlesig_keys)
        if wallet.get_wallet_data().bitcoin_network != rgb_lib.BitcoinNetwork.SIGNET_CUSTOM:
            raise RuntimeError("created wallet is not custom signet")
        _write_private(mnemonic_file, keys.mnemonic + "\n")
        env_contents = "\n".join(
            [
                f"BILL_ROLE={role}",
                f"RGB_DATA_DIR_A={ROOT / 'wallet-data' / 'bill-a'}",
                f"RGB_DATA_DIR_B={ROOT / 'wallet-data' / 'bill-b'}",
                f"RGB_MASTER_FINGERPRINT_{role}={keys.master_fingerprint}",
                f"RGB_MNEMONIC_FILE_{role}={mnemonic_file}",
                "ELECTRUM_URL=https://esplora.signet.kaleidoswap.com",
                "RGB_OOB_CONSIGNMENT=1",
                "RGB_BOOTSTRAP_PROXY=rpcs://proxy.kaleidoswap.com/json-rpc",
                f"RGB_OOB_INBOX_{role}={ROOT / '.rgb-oob' / f'bill-{slug}'}",
                "RGB_MIN_CONFIRMATIONS=1",
                "RGB_FEE_RATE=1",
                "RGB_WITNESS_AMOUNT_SAT=1000",
                "RGB_MAX_CONSIGNMENT_BYTES=655360",
                "RGB_ASSET_ID=",
                "RGB_ASSET_PRECISION=6",
                "",
            ]
        )
        _write_private(env_file, env_contents)
        del wallet
    except Exception:
        mnemonic_file.unlink(missing_ok=True)
        env_file.unlink(missing_ok=True)
        shutil.rmtree(data_dir)
        raise
    finally:
        del keys  # Python strings cannot be reliably wiped; never log the value

    return data_dir, mnemonic_file, env_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["A", "B", "a", "b"])
    args = parser.parse_args()
    try:
        data_dir, mnemonic_file, env_file = provision(args.role)
    except Exception as exc:
        print(f"provision failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    mnemonic_mode = stat.S_IMODE(mnemonic_file.stat().st_mode)
    env_mode = stat.S_IMODE(env_file.stat().st_mode)
    print(f"provisioned Bill {args.role.upper()} custom-signet wallet")
    print(f"data_dir={data_dir}")
    print(f"mnemonic_file={mnemonic_file} mode={mnemonic_mode:04o} (contents hidden)")
    print(f"env_file={env_file} mode={env_mode:04o}")


if __name__ == "__main__":
    main()
