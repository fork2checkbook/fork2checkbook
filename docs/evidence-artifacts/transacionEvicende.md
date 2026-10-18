# Historical transaction evidence artifact

Snapshot date: 2026-10-18 CEST
Network: Mutinynet/custom signet only
Verification tip: `3515070`
Transactions checked: 16
Result: 16 confirmed, 0 unconfirmed, 0 missing

This append-oriented artifact lists every transaction used as public evidence
by fork2checkbook at the snapshot date. Each transaction was checked using the
live Mutinynet Esplora status endpoint. Every response returned
`confirmed: true`; its reported height, hash, and block time are preserved
below.

“USDT” means the project's valueless Mutinynet RGB asset. Rows marked
**Bitcoin support** are fee-reserve or colorable-UTXO transactions required by
the RGB workflow; they do not move additional RGB value. The offline BLE
handoff creates no blockchain transaction and is evidenced separately by
matching capsule hashes and physical device logs in
[`../LIVE-EVIDENCE.md`](../LIVE-EVIDENCE.md).

## Chronological transaction table

| # | Block time (CEST) | Run | Layer | Verifiable result | Transaction | Confirmed block | Status |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-10 20:30:48 | Initial setup | RGB allocation | Original 100-USDT faucet allocation | [`921614229c1420fa75bf4f2f5aeb3a925b705a1b4a99f4797b5218fc121c3f85`](https://mutinynet.com/tx/921614229c1420fa75bf4f2f5aeb3a925b705a1b4a99f4797b5218fc121c3f85) | [`3495147`](https://mutinynet.com/block/00000172b83200ec242c4e93ef137c159b7692910ddcecb181fc9a56eaa087f7) · `00000172b83200ec242c4e93ef137c159b7692910ddcecb181fc9a56eaa087f7` | Confirmed |
| 2 | 2026-10-10 23:39:31 | Direct fallback | RGB transfer | Wallet A → Wallet B · 20 USDT | [`72b6d753eaf788032cdd72ebd9e0c831380fc4e4c97ef5d66f538e62c5e460de`](https://mutinynet.com/tx/72b6d753eaf788032cdd72ebd9e0c831380fc4e4c97ef5d66f538e62c5e460de) | [`3495492`](https://mutinynet.com/block/000001db272b83d74f2026179ddc9f384ae69b1cc8c41eedcb3266f5affaef37) · `000001db272b83d74f2026179ddc9f384ae69b1cc8c41eedcb3266f5affaef37` | Confirmed |
| 3 | 2026-10-12 06:22:37 | Direct fallback | RGB transfer | Wallet A → Wallet B · 20 USDT | [`97885ddffc2305aabf1574d78be8cf102d0afb1f104529c817fc4dcff541a1fb`](https://mutinynet.com/tx/97885ddffc2305aabf1574d78be8cf102d0afb1f104529c817fc4dcff541a1fb) | [`3498861`](https://mutinynet.com/block/000000e18655033b073d12891ebbd8cd5a5edaa0ae0cd0ce4f1e0eee4be7acb3) · `000000e18655033b073d12891ebbd8cd5a5edaa0ae0cd0ce4f1e0eee4be7acb3` | Confirmed |
| 4 | 2026-10-15 00:44:01 | Direct fallback | RGB transfer | Wallet A → Wallet B · 5 USDT | [`109d2d24c572d0b74fbd50c1dd2911b3030f0fc963f6018916397d9891b650e3`](https://mutinynet.com/tx/109d2d24c572d0b74fbd50c1dd2911b3030f0fc963f6018916397d9891b650e3) | [`3506146`](https://mutinynet.com/block/00000025a21619df2b16140f098ca7c7eda52d27612db3f894e48817de25cac3) · `00000025a21619df2b16140f098ca7c7eda52d27612db3f894e48817de25cac3` | Confirmed |
| 5 | 2026-10-15 13:36:28 | Direct fallback | RGB transfer | Wallet A → Wallet B · 5 USDT | [`1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f`](https://mutinynet.com/tx/1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f) | [`3507561`](https://mutinynet.com/block/00000267aae6de709933eefb15e71db53b3209dc4cdc6aa14fe6009053ece7bd) · `00000267aae6de709933eefb15e71db53b3209dc4cdc6aa14fe6009053ece7bd` | Confirmed |
| 6 | 2026-10-15 19:15:27 | First bearer | RGB funding | Wallet A → note N · 5 USDT | [`b325cbe40c282e661c40ff8555cb6cb0fc9867a32855feb8b17d7756c9a83963`](https://mutinynet.com/tx/b325cbe40c282e661c40ff8555cb6cb0fc9867a32855feb8b17d7756c9a83963) | [`3508184`](https://mutinynet.com/block/00000138a4bbdc898a41a5294c7de13a3910d43f545b135913d8391a297d29d7) · `00000138a4bbdc898a41a5294c7de13a3910d43f545b135913d8391a297d29d7` | Confirmed |
| 7 | 2026-10-15 22:17:35 | First bearer | Bitcoin support | Redemption capacity · 8,000-sat colorable UTXO | [`089a8231b4c8916d9b1aac3cfd445b11dc9ec87583f15cd8eeec072b4f0734bb`](https://mutinynet.com/tx/089a8231b4c8916d9b1aac3cfd445b11dc9ec87583f15cd8eeec072b4f0734bb) | [`3508518`](https://mutinynet.com/block/0000011d13a0fc54ec3dd9af00a7c3090c8156f575273bbc5ec6e89d3df4e82c) · `0000011d13a0fc54ec3dd9af00a7c3090c8156f575273bbc5ec6e89d3df4e82c` | Confirmed |
| 8 | 2026-10-15 22:24:53 | First bearer | RGB redemption | Note N → Wallet B · 5 USDT | [`dfe138b0983f6238103d2e5c57bf0848fdd5b7fd733162299fb4c6e5e9f427bf`](https://mutinynet.com/tx/dfe138b0983f6238103d2e5c57bf0848fdd5b7fd733162299fb4c6e5e9f427bf) | [`3508531`](https://mutinynet.com/block/000001bfebff2f852eb7f379b8beff1ea02a13cbcfb7297d89f844cbc0035b64) · `000001bfebff2f852eb7f379b8beff1ea02a13cbcfb7297d89f844cbc0035b64` | Confirmed |
| 9 | 2026-10-17 19:49:24 | Reciprocal bearer | Bitcoin support | Disposable-note funding capacity | [`b3427bbbe07dcf25c27d7871f2ecc1a8db9dff8cd0a818d831b455b28980294b`](https://mutinynet.com/tx/b3427bbbe07dcf25c27d7871f2ecc1a8db9dff8cd0a818d831b455b28980294b) | [`3513524`](https://mutinynet.com/block/000002ed03f3d55e06eeff1575cdcba06ee5752c51f59c7b7f4abde05f0af345) · `000002ed03f3d55e06eeff1575cdcba06ee5752c51f59c7b7f4abde05f0af345` | Confirmed |
| 10 | 2026-10-17 19:51:27 | Reciprocal bearer | RGB funding | Wallet B → note N · 5 USDT | [`4e5c6c9401a0a1b7d778b61215ea70cb81a42619a528a597cdef27e51d70494d`](https://mutinynet.com/tx/4e5c6c9401a0a1b7d778b61215ea70cb81a42619a528a597cdef27e51d70494d) | [`3513528`](https://mutinynet.com/block/0000032f3a2d0e05804c620dfce59f142ccca3bc13db54be67e88a02bfc52bca) · `0000032f3a2d0e05804c620dfce59f142ccca3bc13db54be67e88a02bfc52bca` | Confirmed |
| 11 | 2026-10-17 20:38:47 | Reciprocal bearer | Bitcoin support | Redemption capacity · free colorable UTXO | [`3d9dfb341d129c9debf56e0b36f6a266fa4bdcfaceb2818b5c34a442850e6557`](https://mutinynet.com/tx/3d9dfb341d129c9debf56e0b36f6a266fa4bdcfaceb2818b5c34a442850e6557) | [`3513614`](https://mutinynet.com/block/0000017c13102bf29f6e57fb0d1e59073c4635e74d4e0593cceb7e1d4b1430d6) · `0000017c13102bf29f6e57fb0d1e59073c4635e74d4e0593cceb7e1d4b1430d6` | Confirmed |
| 12 | 2026-10-17 20:43:42 | Reciprocal bearer | RGB redemption | Note N → Wallet A · 5 USDT | [`83fa1a8b1eb61c6bbd7509d723f9cd02a28b5f848017c8dbbe6f1eee0d2506c9`](https://mutinynet.com/tx/83fa1a8b1eb61c6bbd7509d723f9cd02a28b5f848017c8dbbe6f1eee0d2506c9) | [`3513623`](https://mutinynet.com/block/0000023b27dd48bbbf96be7bd5bea33d311d2473f0787b0900fcb834b0604740) · `0000023b27dd48bbbf96be7bd5bea33d311d2473f0787b0900fcb834b0604740` | Confirmed |
| 13 | 2026-10-18 01:46:56 | Watch bearer | Bitcoin support | Issuing-wallet fee reserve | [`9d26e9f5130fae0dc1373e8e21f1456566147d84df3ff1e0ccc071f7c717aac4`](https://mutinynet.com/tx/9d26e9f5130fae0dc1373e8e21f1456566147d84df3ff1e0ccc071f7c717aac4) | [`3514180`](https://mutinynet.com/block/000000d7607340f006ca6b5bdf227501cbb2731ca06b5d416b51c973d1c88abd) · `000000d7607340f006ca6b5bdf227501cbb2731ca06b5d416b51c973d1c88abd` | Confirmed |
| 14 | 2026-10-18 01:47:43 | Watch bearer | RGB funding | Wallet B → note N · 5 USDT | [`8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f`](https://mutinynet.com/tx/8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f) | [`3514181`](https://mutinynet.com/block/00000323d0857eb1046fccde9ab527e190f7c300aa5b81b490aa077b4be0aba3) · `00000323d0857eb1046fccde9ab527e190f7c300aa5b81b490aa077b4be0aba3` | Confirmed |
| 15 | 2026-10-18 02:06:06 | Watch bearer | Bitcoin support | Redemption capacity · 1,000-sat colorable UTXO | [`8301d3893e1c1f6a7697304fd7c65365c527284dc8f9443bf5cbb7904b952b02`](https://mutinynet.com/tx/8301d3893e1c1f6a7697304fd7c65365c527284dc8f9443bf5cbb7904b952b02) | [`3514215`](https://mutinynet.com/block/00000334fd79074ee7cfbe87760b61a3ea08169ed6e72272702935b802d92c1f) · `00000334fd79074ee7cfbe87760b61a3ea08169ed6e72272702935b802d92c1f` | Confirmed |
| 16 | 2026-10-18 02:14:59 | Watch bearer | RGB redemption | Note N → Wallet A · 5 USDT | [`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`](https://mutinynet.com/tx/65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5) | [`3514231`](https://mutinynet.com/block/000000efaced4f87d49164f55c205c569b6d188a7ba01279146ac7671b2f04c6) · `000000efaced4f87d49164f55c205c569b6d188a7ba01279146ac7671b2f04c6` | Confirmed |

## Verification method

For each transaction, the snapshot used the public Esplora endpoint:

```bash
TXID='<full transaction id from the table>'
curl -fsS "https://mutinynet.com/api/tx/${TXID}/status" |
  python3 -m json.tool
```

A matching proof has all of the following:

1. `confirmed` is `true`;
2. `block_height` equals the table's decimal height;
3. `block_hash` equals the full hash displayed beside that height;
4. `block_time` converts to the recorded CEST time.

The current chain tip can be checked independently:

```bash
curl -fsS https://mutinynet.com/api/blocks/tip/height
```

Future confirmations increase with the tip, so this artifact records immutable
inclusion data rather than a confirmation count that immediately becomes
stale. New accepted transactions should be appended with a new verification
snapshot; historical rows should not be rewritten.
