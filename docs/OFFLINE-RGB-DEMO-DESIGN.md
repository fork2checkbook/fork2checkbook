# Offline RGB value demo — decision, options, and proof plan

Date: 2026-10-15

This document chooses the highest-value design that can still be proven live by
the Sunday, 2026-10-18 13:00 CEST submission deadline. It is intentionally a
Mutinynet prototype. It does not claim production custody, perfect anonymity,
or offline double-spend prevention.

## Demo claim

A current owner converts $5 of real RGB USDT into a disposable bearer note. The note
is carried by one ESP32 bill and can move Bill A -> Bill B -> Bill A -> Bill B
over BLE while the docks and Internet are unavailable. The final holder later
chooses a previously unknown RGB wallet, redeems the note, imports the exact
out-of-band consignment, and sees the balance settle on Mutinynet.

The demo makes two real RGB transfers:

```text
issuing wallet --RGB funding--> one-use note wallet N
                              |
                              +-- bearer capability relayed offline over BLE
                              |
one-use note wallet N --RGB redemption--> holder-chosen wallet
```

The middle BLE handoffs are deliberately **not** represented as RGB state
transitions. They move the capability to request the second transition. This
distinction must be visible in the UI, README, diagrams, and pitch.

## Decision: option 1, one-shot note vault

For the Sunday build, use a small trusted note-vault process. Every note gets a
fresh rgb-lib wallet, mnemonic, datadir, random note ID, and random 256-bit
claim secret. The vault retains the wallet; the bill carries a compact bearer
capsule containing the claim capability and public evidence about the funded
note.

The vault is allowed to be trusted because this is a Mutinynet hackathon
prototype, but that trust must be disclosed. The holder does not need an
account. Possession of the claim secret is sufficient to request a redemption
plan. A fresh recipient invoice is supplied only at redemption, so the final
wallet is unknown at issuance.

The dock's A/B profile is only a wallet-process and bill-registration slot.
It is not an issuer/recipient permission. New note manifests bind issuance to
the funding wallet's master fingerprint so a shared vault cannot offer another
wallet's active note for loading. That fingerprint stays in private vault
metadata and is not added to the bearer capsule.

The vault may run as a third local process for the venue demo. Stop it or gate
all access while showing the offline BLE relays. A later product would expose
the same contract over authenticated TLS and would need hardened custody,
availability, privacy, and abuse controls.

### Why this is the best demo design

- It proves real RGB at both ends without pretending ESP32 can run rgb-lib.
- The offline segment is real and visually obvious: bill-to-bill BLE only.
- The final owner can be unknown when the note is created.
- A compact capability easily fits the current 640 KiB BLE/SPIFFS limit.
- First-redemption-wins is implementable and demonstrable this weekend.
- It gives QVAC a useful job: interpret intent, explain state, and guide
  recovery without ever authorizing a spend.

### Honest limitations

- The vault can spend every active note. This is trusted custody.
- A copied capsule can be redeemed by the fastest holder. Offline double-spend
  is detected at redemption, not prevented at tap time.
- The vault observes issuance/redemption timing and the final invoice. It
  provides pseudonymity from accounts, not network-level anonymity.
- The ESP32 has no secure element. Anyone who extracts the capsule owns the
  capability.
- Redemption needs the vault and Mutinynet. Only carrying and relaying are
  offline.
- First accepted claim and first confirmed Bitcoin transaction are related but
  not identical. The state machine must report ambiguous broadcasts honestly.

## Ranked alternatives

Scores are relative to the Sunday deadline: 5 is best. Risk is reversed: 5 is
highest risk.

| Rank | Design | Buildability | RGB proof | Offline relay | Self-custody | Risk | Decision |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | One-shot trusted note vault + bearer secret | 5 | 5 | 5 | 2 | 3 | Build this |
| 2 | Encrypted full rgb-lib wallet backup carried on bill | 2 | 5 | 5 | 4 | 5 | Time-boxed research fallback only |
| 3 | Encrypted wallet blob in content-addressed storage; key on bill | 3 | 5 | 5 | 3 | 4 | Post-demo experiment |
| 4 | True RGB transition at every tap with signer/chain state on device | 1 | 5 | 3 | 5 | 5 | Not credible for Sunday |
| 5 | Blind e-cash/Cashu-style token backed by an RGB reserve | 2 | 2 | 5 | 2 | 5 | Pitch-level stretch, not core claim |

Option 2 must not become the default until a destructive restore-and-spend
spike proves it. The installed wallets currently occupy roughly 400–470 KiB
each before a backup envelope, while the bill accepts 640 KiB. More
importantly, rgb-lib warns against opening the same wallet in multiple places;
copying private wallet state onto multiple bills makes that operational hazard
the normal case.

A pre-signed transaction is not a substitute for option 1. Bitcoin and RGB
commit to transaction outputs/seals, so a safe transaction cannot leave the
final unknown recipient fully mutable after signing.

## Bearer capsule v1

The capsule is a new artifact. It is not an RGB invoice and is not an RGB
consignment. Those canonical artifacts remain byte-exact and unmodified.

| Field | Purpose |
| --- | --- |
| magic + version | Strict parser and future migration |
| network | Must equal `mutinynet` |
| vault/key ID | Selects the verifier without embedding a URL policy |
| note ID | Random 128-bit identifier, not user identity |
| claim secret | Random 256-bit bearer capability; never log it |
| asset ID + base units | Exact RGB asset and amount |
| denomination cents | Human display, cross-checked against amount |
| funding txid | Public Bitcoin evidence for the first RGB transition |
| funding consignment SHA-256 | Binds the exact RGB proof |
| issued timestamp | Audit and demo sequencing |
| capsule digest/signature | Corruption detection; authenticity if signed |

The funding consignment may be included for evidence if the measured capsule
stays comfortably below 640 KiB. Do not include a mnemonic or note-wallet
backup in the selected vault design. Do not put the claim secret in serial
logs, HTTP logs, URLs, BLE advertisements, filenames, screenshots, or Git.

Use a canonical encoding with strict length bounds and a content hash. An
Ed25519 signature from the vault is desirable if it can be added without
destabilizing the critical path. It allows a dock to authenticate a capsule
offline; firmware may still treat the payload as opaque. If signature support
is deferred, label the on-device amount as unverified until the vault accepts
the capsule.

The first live v1 capsule uses the documented fallback: a 603-byte canonical
envelope with SHA-256 integrity and `issuer_authentication=vault-online-only`.
It contains no wallet backup or RGB consignment. Its claim secret is present
only in the ignored capsule artifact, while the vault journal stores a random
salt and domain-separated hash. Ed25519 remains an explicit P1 gap; this
integrity-only capsule must not be described as offline issuer-authenticated.

## Vault state and recovery contract

Use one durable SQLite database plus one ignored wallet directory per note.
Only one process may open a note datadir at a time. Store a salted hash of the
claim secret, never the plaintext secret. The minimum state machine is:

```text
PREPARING -> AWAITING_FUNDING -> ACTIVE -> CLAIM_PLANNED
          -> CLAIMING -> BROADCAST_RECORDED -> REDEMPTION_IMPORTED -> REDEEMED
                    \-> AMBIGUOUS           \-> IMPORT_RECOVERY_REQUIRED

ACTIVE/CLAIM_PLANNED -> BOUNCED when a previously spent outpoint is detected

PRE_BROADCAST_REJECTED -> CAPACITY_PLANNED
 -> CAPACITY_BROADCAST_RECORDED -> CAPACITY_READY -> CLAIM_PLANNED

AWAITING_FUNDING -> BTC_TOPUP_PLANNED
 -> BTC_TOPUP_BROADCAST_RECORDED -> AWAITING_FUNDING
```

Required invariants:

1. One note has one exact asset, amount, note wallet, and claim-secret hash.
2. A redemption plan binds note ID, recipient invoice hash, amount, fee rate,
   expiry, and plan ID.
3. `CONFIRM <plan-id>` is fresh, exact, one-use, and consumed before send.
4. The claim is atomically reserved before calling rgb-lib.
5. Funding and redemption sends require both fee sats and a free colorable
   UTXO. If capacity is missing, its creation is a separate immutable,
   confirmation-gated Bitcoin transaction; a consumed RGB confirmation never
   authorizes it. After capacity confirms, a fresh RGB plan and confirmation
   are required.
6. When a fresh note lacks its configured fee reserve, the issuing dock prepares
   a separate immutable Bitcoin top-up plan. Exact confirmation authorizes one
   `send_btc` to the bound note address; it cannot authorize the RGB funding
   send. A confirmed top-up always causes the stale RGB plan to be superseded
   before a new RGB confirmation is shown.
   New plans compute the minimum supported reserve for the controlled
   single-input note shape. At 1 sat/vB it is 2,411 sats: one 1,000-sat
   colorable output, a measured 213-vB capacity fee, the 1,000-sat witness,
   and a measured 198-vB RGB-send fee. Higher configured fee rates scale both
   fee components. Inventory is still rechecked before every broadcast.
7. Broadcast results and exact consignment path/hash are committed before the
   API reports success.
8. An ambiguous send is never retried automatically. A pinned rgb-lib
   `InsufficientAllocationSlots` error may be reclassified as pre-broadcast
   only after the full RGB balance and absence of any send transfer are proven.
9. Repeating a request returns the recorded result or a recovery state; it
   cannot create a second send.
10. An expired, unconsumed plan is archived as `SUPERSEDED`; its expiry is never
   extended in place and its confirmation can never authorize the replacement.
11. The bill is cleared only after the final dock has durably received the
   result needed for import/recovery.
12. A copied or already redeemed capability returns `BOUNCED` with the known
   winning txid when available.
13. The chosen destination wallet reaches `REDEEMED` only after one exact `receive_witness` transfer
    matches the redemption txid, contains one 5,000,000-unit assignment, and
    its settled/future/spendable balance reflects the recorded $5 delta.

Minimum operations:

| Operation | Broadcast? | Output |
| --- | --- | --- |
| `note prepare` | No | note ID and transport-free note-wallet invoice |
| `note BTC top-up plan` | No | immutable issuer-wallet reserve plan |
| `note BTC top-up confirm` | Yes, once | recorded Mutinynet txid; no RGB send |
| `note import-funding` | No | imported transfer state and capsule plan |
| `note activate` | No | capsule after funding is accepted/settled by policy |
| `note inspect` | No | redacted state; never returns the claim secret in logs |
| `note redeem-plan` | No | immutable plan for a fresh final invoice |
| `note redeem-confirm` | Yes | txid and exact OOB consignment, or ambiguous state |
| `note recover` | No new send | re-import retained proof, or return the recorded/manual-recovery state |
| `note refresh-redemption` | No | exact chosen-wallet settlement evidence and final `REDEEMED` state |

The first demo implementation may use Python method calls rather than a public
HTTP deployment. Keep service boundaries explicit so a later HTTPS transport
does not change wallet or confirmation semantics.

## Firmware and BLE changes

Add chunk kind `0x04 = bearer note` and inventory metadata v3 direction
`bearer-held`. A received note becomes the bill's next outbound tap artifact,
which enables A -> B -> A -> B without either dock. Keep invoice/consignment
v2 decode and the accepted direct mode.

Physical bearer-v3 acceptance completed on 2026-10-15 with the 520-byte
synthetic fixture over WEMOS -> TTGO -> WEMOS -> TTGO. The TTGO restored kind
`0x04`, session `77862b2e`, size, and $5 denomination after reset. Final state
was WEMOS empty and TTGO `bearer-held`. Source SHA-256 was
`925a4bf7cdf591eea7e5b78975964aee5801417761474fe3588b385ba00bc014`;
the final synthetic multi-hop payload was not re-read through the dock.

The live 603-byte capsule subsequently passed the same physical WEMOS -> TTGO
relay and destination extraction. The extracted SHA-256 exactly matched
`a38775213ac972dae871906e37b09750e25e4fe71a2d28a9bd4bd3fca4615144`.

The sender retains its note until it receives the final ACK. The receiver
atomically promotes the complete file. After success the sender deletes its
ordinary copy, while the documentation admits that a malicious holder could
have copied it beforehand.

| State | Display |
| --- | --- |
| Empty | `EMPTY` with no amount |
| Valid stored note | `$5.00 / NOTE` |
| Armed | `TAP READY`, then return to inventory state |
| Successful receive | `NOTE RECEIVED`, then `$5.00 / NOTE` |
| Successful send | `NOTE SENT`, then `EMPTY` |
| Recovery block | `$5.00 / RECOVERY` or `CLEAR STORAGE` |

Do not advertise note bytes, note ID, amount, or claim secret. Do not add Wi-Fi
to the ESP32 path. Keep the rising-edge, one-shot GPIO12 flow and RSSI gate.

## QVAC's necessary role

QVAC stays on the dock and runs locally. It converts natural language into one
of a small typed intent set:

```text
LOAD_NOTE(amount)
PASS_NOTE
REDEEM_NOTE
INSPECT_NOTE
EXPLAIN_OR_RECOVER
UNKNOWN
```

The model receives only redacted deterministic state. It never receives a
mnemonic, claim secret, capsule bytes, or private key. A deterministic policy
layer checks the model's typed output against bill inventory, role, amount,
network, and recovery status. QVAC may prefill an action and explain why it is
allowed or blocked; it cannot call a spend or synthesize confirmation text.

Proof requires a checked-in evaluation corpus with ordinary, ambiguous, and
adversarial phrases; recorded local model/version/backend; latency; exact
intent accuracy; and examples of safe refusal. The physical buttons remain a
fallback, but the demo should use QVAC for at least one meaningful operation
and one recovery explanation.

## Evidence and remaining experiments

| Priority | Question | Current result | Next proof or fallback |
| --- | --- | --- | --- |
| P0 | Can a fresh disposable wallet receive and later send this USDT? | **Accepted live in both directions.** The latest run settled +5 USDT into previously unknown Wallet A after TTGO → watch → WEMOS relay. | Preserve direct mode as fallback. |
| P0 | How many sats/UTXOs must N hold? | **Measured and implemented.** The accepted run proved that 2,000 sats was insufficient. New one-input plans use a 1,000-sat colorable output plus measured 213-vB capacity fee, 1,000-sat witness, and measured 198-vB RGB-send fee: 2,411 sats at 1 sat/vB. | Live-accept the new minimum policy; retain exact inventory checks and allow an explicit higher fee rate if relay policy changes. |
| P0 | Can recovery avoid duplicate sends? | **Accepted in tests and live journal.** Confirmation, broadcast, import, and settlement states are durable; refresh replay performs no send. | Manual reconciliation remains for unknown send outcomes. |
| P0 | Does bearer relay persist across reboot? | **Accepted live.** The original 603-byte WEMOS → TTGO capsule restored after reboot; a later funded capsule crossed TTGO → Waveshare watch → WEMOS and extracted with exact SHA-256. | Direct mode remains fallback. |
| P0 | Is QVAC 600M reliable on five intents? | Primary live intents are integrated behind deterministic policy; fixed adversarial reliability is still open. | Run the checked-in eval; buttons plus truthful explanation if below threshold. |
| P1 | Can a dock verify a capsule offline? | Open. v1 has canonical SHA-256 integrity and online vault authentication only. | Signature spike or display `UNVERIFIED`. |
| P1 | What happens on copied-note redemption? | One-shot durable claim is implemented; a deliberate copied-capsule live bounce has not been run. | Automated no-second-send proof plus optional controlled replay. |
| P1 | What does Esplora 429/timeout do? | Read-only 429/timeouts were recovered without another send. | Keep read retry separate from ambiguous send reconciliation. |
| P2 | Can a full wallet backup fit and restore safely? | Not selected. | Separate destructive spike only; do not ship portable mode. |

## Sunday acceptance script

1. Show Wallet A/B balances and empty Bill A/B.
2. Select a pre-funded $5 note and show its funding txid.
3. Load Bill A; show `$5.00 / NOTE`.
4. Stop/gate the vault and disable the network used by the docks.
5. Tap A -> B, B -> A, A -> B. Reboot one receiver and show persistence.
6. Re-enable the vault/network. On Dock B say “redeem this five.”
7. Review amount, fee, recipient, note ID, and plan; type confirmation once.
8. Show redemption txid and exact consignment digest; import and refresh B.
9. Replay a saved capsule; show `BOUNCED` and no new transaction.
10. Show both txids, QVAC local-inference evidence, and limitations.

## Ranked remaining gaps

1. Compute a reserve that covers capacity creation and redemption without a
   manual note-wallet top-up.
2. Run and publish the fixed adversarial QVAC intent evaluation.
3. Add an explicit copied-capsule replay result with no second send.
4. Rehearse the three-minute flow and capture one clean evidence bundle.
5. Add offline capsule signatures if they do not destabilize the demo.
6. Consider WDK only after the RGB + QVAC primary experience is green.

## Sources and local evidence

- Official rgb-lib project and exclusive-wallet warning:
  <https://github.com/RGB-Tools/rgb-lib>
- rgb-lib beta.7 OOB and donation changes:
  <https://github.com/RGB-Tools/rgb-lib/releases>
- QVAC SDK 0.21 local Python runtime:
  <https://docs.qvac.tether.io/sdk/>
- QVAC runtime lifecycle:
  <https://docs.qvac.tether.io/sdk/how-it-works/>
- Current release validation counts and all three production firmware builds
  are recorded in [`LIVE-EVIDENCE.md`](LIVE-EVIDENCE.md).
- Accepted direct-mode evidence is in `docs/PROTOCOL.md`, including Mutinynet
  txid `1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f`.
- Accepted bearer-note txids, hashes, balances, and truth boundaries are in
  [`LIVE-EVIDENCE.md`](LIVE-EVIDENCE.md).
