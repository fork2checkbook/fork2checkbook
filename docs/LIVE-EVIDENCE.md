# Live evidence ledger

Updated: 2026-10-18 CEST
Network: Mutinynet/custom signet only

This is the canonical public index of real RGB and physical-transport evidence
for the project. It contains no mnemonic, claim secret, private key, capsule
bytes, or wallet datadir. Historical test records remain in their dedicated
documents; when summary copy disagrees with this ledger, this ledger and the
wallet journals are authoritative.

## Offline bearer-note vertical slice

The selected demo makes exactly two RGB state transitions. Between them, the
ESP32 bills relay a bearer capability over BLE; that relay is not itself an
RGB transition.

| Stage | Accepted evidence |
| --- | --- |
| Disposable note | note ID `758186be-791d-4c8c-92f5-a8c4194ac856`; isolated ignored wallet N |
| Wallet A → note N | txid [`b325cbe40c282e661c40ff8555cb6cb0fc9867a32855feb8b17d7756c9a83963`](https://mutinynet.com/tx/b325cbe40c282e661c40ff8555cb6cb0fc9867a32855feb8b17d7756c9a83963); exactly 5,000,000 units; funding consignment 14,347 bytes; SHA-256 `dc44a8cf9754c5469e55d38de7e49fc61b01ba28279975d615660f81cd2f03a9` |
| Funding settlement | note transfer `receive_witness`, batch `1`, `settled`; block `3508184`; note settled/future/spendable all 5,000,000 |
| Bearer capsule | canonical v1; 603 bytes; SHA-256 `a38775213ac972dae871906e37b09750e25e4fe71a2d28a9bd4bd3fca4615144`; no mnemonic or wallet backup |
| Offline physical relay | WEMOS → TTGO; kind `0x04`; session `0x3de6f5e7`; 4 BLE frames; TTGO restored the same artifact after reboot |
| Destination extraction | 603 bytes; SHA-256 exactly matched the source capsule before parsing or planning |
| Redemption capacity | txid [`089a8231b4c8916d9b1aac3cfd445b11dc9ec87583f15cd8eeec072b4f0734bb`](https://mutinynet.com/tx/089a8231b4c8916d9b1aac3cfd445b11dc9ec87583f15cd8eeec072b4f0734bb); one 8,000-sat free colorable UTXO created by its own confirmation gate |
| Note N → Wallet B | plan `95c33df6-d74a-496d-9867-aab341c6be87`; txid [`dfe138b0983f6238103d2e5c57bf0848fdd5b7fd733162299fb4c6e5e9f427bf`](https://mutinynet.com/tx/dfe138b0983f6238103d2e5c57bf0848fdd5b7fd733162299fb4c6e5e9f427bf); consignment 15,057 bytes; SHA-256 `7f3915724cffed5aee99ad93a10b97bd2b895595ca4c473783672a6d13fad704` |
| Wallet B settlement | exact txid match; batch `10`; `receive_witness`; one 5,000,000-unit assignment; balance 50,000,000 → 55,000,000; settled/future/spendable all 55,000,000 |
| Chain check | redemption included at block `3508531`, block time 2026-10-15 22:24:53 CEST; 11 confirmations at verification tip `3508541` |
| Durable terminal state | plan `REDEEMED`; manifest `REDEEMED`; claim `SPENT`; journal order `BROADCAST_RECORDED` → `REDEMPTION_IMPORTED` → `REDEEMED` |

The final settlement refresh is idempotent: replay returned the recorded result
without reopening Wallet B or creating another send. The private plan,
manifest, and journal files were mode `0600` at acceptance.

## Reciprocal web-dock bearer cycle

The second accepted run exercised the same bearer design in the opposite
direction: Wallet B issued the note, the bills relayed it without a dock or
network between them, and Wallet A was selected only at redemption. This run
also exercised the guarded web/QVAC preparation path and the dock-funded
Bitcoin-capacity steps introduced for the demo UX.

| Stage | Accepted evidence |
| --- | --- |
| Disposable note | note ID `b4a03f79-9f32-4717-8f55-49e0f2130b70`; isolated ignored wallet N |
| Funding capacity | txid [`b3427bbbe07dcf25c27d7871f2ecc1a8db9dff8cd0a818d831b455b28980294b`](https://mutinynet.com/tx/b3427bbbe07dcf25c27d7871f2ecc1a8db9dff8cd0a818d831b455b28980294b); immutable plan `416460b1-1f77-4496-82cb-3fa8110a4f06`; `CAPACITY_CONFIRMED` |
| Wallet B → note N | plan `6dd3424a-a886-484c-ad8a-179a7ddb717f`; txid [`4e5c6c9401a0a1b7d778b61215ea70cb81a42619a528a597cdef27e51d70494d`](https://mutinynet.com/tx/4e5c6c9401a0a1b7d778b61215ea70cb81a42619a528a597cdef27e51d70494d); exactly 5,000,000 units; funding consignment 13,823 bytes; SHA-256 `3da2347962ab4ece5dd967b2a1a77ee7136353c20af7f8fa22cf456a371aaaea` |
| Funding settlement | note transfer `receive_witness`, batch `1`, `settled`; note settled/future/spendable all 5,000,000 |
| Bearer capsule | canonical v1; 603 bytes; SHA-256 `64144586afa3e97964059a27edb56d5b03fe76df677d51770e03e57abcc3517f` |
| Offline physical relay | TTGO → WEMOS; kind `0x04`; session `0x2704e0f2`; 4 BLE frames; WEMOS restored the same 603-byte artifact after reboot |
| Destination extraction | Dock A extracted the capsule and matched SHA-256 exactly before parsing or planning; WEMOS cleared only after the final ACK |
| Redemption capacity | txid [`3d9dfb341d129c9debf56e0b36f6a266fa4bdcfaceb2818b5c34a442850e6557`](https://mutinynet.com/tx/3d9dfb341d129c9debf56e0b36f6a266fa4bdcfaceb2818b5c34a442850e6557); immutable plan `683706d6-1781-4000-9dcd-46a6bf425756`; `CAPACITY_CONFIRMED` |
| Note N → Wallet A | plan `1cdf2221-a670-4915-939d-f0c09c4ea2fc`; txid [`83fa1a8b1eb61c6bbd7509d723f9cd02a28b5f848017c8dbbe6f1eee0d2506c9`](https://mutinynet.com/tx/83fa1a8b1eb61c6bbd7509d723f9cd02a28b5f848017c8dbbe6f1eee0d2506c9); redemption consignment 14,533 bytes; SHA-256 `2b1878607c085bc46ea7dd602c0da80e16bca691845a2b316da78093aadc2a89` |
| Wallet A settlement | exact txid match; batch `10`; `receive_witness`; one 5,000,000-unit assignment; balance 45,000,000 → 50,000,000; settled/future/spendable all 50,000,000 |
| Durable terminal state | funding `FUNDING_SETTLED`; redemption plan `REDEEMED`; manifest `REDEEMED`; claim `SPENT` |

The browser's temporary `$0` startup display was not the authoritative
pre-redemption balance. The refreshed rgb-lib wallet journal proved the actual
Wallet A transition was 45,000,000 → 50,000,000 units.

## Watch-mediated bearer cycle

The third accepted run added the Waveshare ESP32-S3 touchscreen watch as an
offline intermediary. Wallet B issued the note without choosing a recipient,
TTGO passed it to the watch, the watch passed it to WEMOS, and only then did
Wallet A become the redemption wallet.

| Stage | Accepted evidence |
| --- | --- |
| Disposable note | note ID `feb839fb-17a9-48b2-a378-f6a89b4f6eef`; isolated ignored wallet N |
| Bitcoin fee reserve | txid [`9d26e9f5130fae0dc1373e8e21f1456566147d84df3ff1e0ccc071f7c717aac4`](https://mutinynet.com/tx/9d26e9f5130fae0dc1373e8e21f1456566147d84df3ff1e0ccc071f7c717aac4); separately confirmed top-up |
| Wallet B → note N | txid [`8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f`](https://mutinynet.com/tx/8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f); exactly 5,000,000 units |
| Bearer capsule | canonical v1; 603 bytes; SHA-256 `159805d2dea99c3dd2601e3dcf07d0dfd6b4c6a400df1970f8b405710d5f310c` |
| Offline physical relay | TTGO → Waveshare watch → WEMOS; kind `0x04`; session `0x220902c8`; four BLE frames on each live send; each sender deleted only after final ACK |
| Destination extraction | Dock A extracted 603 bytes from WEMOS and matched the capsule SHA-256 byte-for-byte before parsing; WEMOS then became empty |
| Safe pre-broadcast rejection | first redemption plan `f26ec6a9-9f11-459e-887a-61e90837f896` failed with `InsufficientAllocationSlots`; the journal proved no broadcast and the consumed plan was not reused |
| Redemption capacity | plan `6c60404f-5c3f-4f30-b76c-95a4943e1ef0`; txid [`8301d3893e1c1f6a7697304fd7c65365c527284dc8f9443bf5cbb7904b952b02`](https://mutinynet.com/tx/8301d3893e1c1f6a7697304fd7c65365c527284dc8f9443bf5cbb7904b952b02); one 1,000-sat free colorable UTXO; 309-sat fee; confirmed in block `3514215` |
| Reserve recovery | the automatic 2,000-sat reserve left only 691 vanilla sats after capacity creation. The operator added valueless Mutinynet sats; the fresh redemption plan then recorded 12,691 vanilla sats. This is a known demo sizing gap, not an automatic recovery claim |
| Note N → Wallet A | fresh plan `1810b78e-8dd0-43de-be80-b6ba9e178a49`; txid [`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`](https://mutinynet.com/tx/65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5); consignment 15,150 bytes; SHA-256 `8a0a4de0eba577a4c40d19520246c3307669dcd10d0dc91a2d77041d313dfd12` |
| Wallet A settlement | exact txid match; batch `11`; `receive_witness`; one 5,000,000-unit assignment; balance 50,000,000 → 55,000,000; settled/future/spendable all 55,000,000 |
| Durable terminal state | plan `REDEEMED`; manifest `REDEEMED`; claim `SPENT`; no recovery send; TTGO, watch, and WEMOS finished empty |

The watch used the same inventory-v3 and bearer kind `0x04` contract as the
two original bills. Its touchscreen armed a one-shot receive window; wallet
keys, QVAC, rgb-lib, confirmations, and Internet access remained on the
laptop docks.

## Accepted direct-mode fallback

The original human-first invoice/consignment path remains a working fallback.
It creates Wallet B's invoice first, relays that invoice to the sender bill,
broadcasts once after exact confirmation, then relays the exact consignment
back to Wallet B.

| Date | Amount | Txid | Result |
| --- | ---: | --- | --- |
| 2026-10-11 | 20 USDT | [`97885ddffc2305aabf1574d78be8cf102d0afb1f104529c817fc4dcff541a1fb`](https://mutinynet.com/tx/97885ddffc2305aabf1574d78be8cf102d0afb1f104529c817fc4dcff541a1fb) | 12,496-byte consignment crossed BLE in 70 frames; Wallet A 80→60, Wallet B 20→40; settled |
| 2026-10-15 | 5 USDT | [`1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f`](https://mutinynet.com/tx/1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f) | browser-driven inventory-v2 flow; both wallets reached 50 USDT; settled |

Additional historical settled rows are listed in
[`spikes/M0-BASELINE.md`](spikes/M0-BASELINE.md). They are regression history,
not separate claims about the bearer-note architecture.

## What this proves

- real RGB funding and redemption using rgb-lib on Mutinynet;
- empty RGB transport endpoints and exact out-of-band consignments;
- byte-exact BLE transport and reboot persistence on two original ESP32
  profiles, plus a live two-hop relay through the Waveshare watch;
- an unknown-at-issuance final wallet chosen only at redemption;
- either reference dock wallet can issue or redeem the complete bearer value;
- local QVAC can map the primary preparation request into the guarded web flow;
- one-use confirmation, durable recovery records, and no automatic resend;
- exact destination assignment and settled-balance verification.

## What this does not prove

- Mainnet safety or real-dollar value;
- trustless custody: the note vault retains each disposable wallet key;
- offline double-spend prevention: a copied capsule is first-redemption-wins;
- offline issuer authentication: capsule v1 has integrity hashing but no
  device-verifiable signature;
- a completed fixed adversarial QVAC intent evaluation or WDK integration;
- production web authentication, secure-element resistance, or network-level
  anonymity;
- automatic reserve sizing sufficient for both capacity creation and the
  final RGB redemption without an operator-added Mutinynet top-up.

## Local validation at release

- Python: `174 passed`;
- native firmware protocol and watch policies: `25 passed`;
- WEMOS `esp32-bill`, TTGO `ttgo-lora32-oled-test`, and Waveshare
  `waveshare-s3-bill` production builds: passed;
- `git diff --check`: passed;
- public evidence intentionally excludes ignored `.note-vault/` contents.
