# Human-first transfer protocol and GATT contract

Rendered views of this contract are available as the
[GATT service map](diagrams/05-gatt-contract.png) and
[binary chunk-frame layout](diagrams/06-chunk-frame.png). Editable Mermaid
sources are indexed in [docs/diagrams/README.md](diagrams/README.md).

The first tap/chunk protocol was physically accepted on 2026-10-11. The
human-first inventory revision below was flashed and physically accepted on
both board profiles on 2026-10-15 with settled $5 Mutinynet transaction
`1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f`.
Bearer kind `0x04` and inventory v3 were physically accepted on 2026-10-15
with a 520-byte synthetic artifact across WEMOS → TTGO → WEMOS → TTGO,
including TTGO restart restoration. The source SHA-256 was
`925a4bf7cdf591eea7e5b78975964aee5801417761474fe3588b385ba00bc014`;
the later live 603-byte capsule crossed WEMOS → TTGO, survived restart, and was
extracted with exact SHA-256
`a38775213ac972dae871906e37b09750e25e4fe71a2d28a9bd4bd3fca4615144`.
A fresh live 603-byte capsule later crossed TTGO → Waveshare watch → WEMOS as
session `0x220902c8` and was extracted byte-for-byte with SHA-256
`159805d2dea99c3dd2601e3dcf07d0dfd6b4c6a400df1970f8b405710d5f310c`.
That note then redeemed into the previously unknown Wallet A with RGB txid
`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`;
Wallet A settled from 50 to 55 USDT and the note closed `REDEEMED` / `SPENT`.
`include/config.h` and `docs/devs/gatt-contract.json` stay in lockstep with
this document.

Device name: `RgbBill`.

## Primary procedure: carry value offline

This is the selected demo procedure. It deliberately separates online RGB
funding/redemption from offline possession transfer.

1. An issuing dock prepares a fresh disposable note wallet N and an immutable
   plan to fund it with exactly 5 USDT. It does not name a final wallet. A human
   types the exact fresh confirmation.
2. After N's receive is settled, that dock creates and loads one bearer capsule
   onto the starting bill. The capsule has kind `0x04` and contains no wallet
   mnemonic or datadir.
3. With docks/network unavailable, an empty bill is pressed once near the
   holder. It receives the same capsule, promotes it to `bearer-held`, and can
   relay it again. No button is held.
4. At the destination dock, `bearer-held` inventory is extracted through
   `CHUNK_OUT`, persisted privately, and compared to the source SHA-256 before
   parsing. Interrupted extraction leaves the bill copy intact.
5. Only after capsule validation does the holder's chosen dock wallet create
   its fresh transport-free invoice. The vault prepares an immutable redemption
   plan bound to that previously unknown destination; another exact one-use
   confirmation authorizes one RGB send from N.
6. That wallet imports the exact retained consignment and refreshes. The note is
   terminal `REDEEMED` only when the exact txid has one `receive_witness`
   transfer with one 5,000,000-unit assignment and the recorded balance delta
   is fully settled.

The browser maps these boundaries directly: ask local QVAC **“Prepare a $5
note”** → if needed, review and exactly confirm the issuing dock's immutable
Bitcoin fee-reserve top-up → after one confirmation ask **“Check my note”** →
review and exactly confirm the separate RGB funding plan → issuing dock **Load
note** → any dock **Pass note** (instruction only; the bills perform the offline
hop) → holder-selected dock **Redeem note** → exact confirmation → **Recover**
only if the durable journal requires it → **Refresh settlement**.
QVAC emits only a typed intent. Deterministic policy creates the plan and never
broadcasts or loads hardware from model output. A fresh note that lacks fee
sats no longer asks the human to use a faucet: deterministic code prepares a
separate `fund-note-bitcoin-reserve` plan that sends only the missing sats from
the issuing dock wallet after exact confirmation. That confirmation cannot
authorize the later RGB send. The separately confirmed Bitcoin capacity
fallback stays in the operator runbook. The current 2,000-sat default reserve
proved too small after one capacity transaction; the runbook therefore
requires a pre-confirmation vanilla-sat check until reserve sizing is fixed.

The BLE hops are bearer-capability transfers, not RGB state transitions. A
copied capsule is first-redemption-wins because the trusted vault controls N
and permits only one claim. See
[`OFFLINE-BEARER-RUNBOOK.md`](OFFLINE-BEARER-RUNBOOK.md).

Bearer transfer is all-or-nothing: there is no partial amount or change at a
tap. The receiver must be empty, promotes the complete validated capsule, and
the sender deletes its ordinary copy only after the matching final ACK. A/B
remain isolated configuration and registration profiles; they do not grant
issue or redeem authority. Relay privacy and fixed denominations are design
goals, while the trusted vault, observable BLE identities, funding evidence,
and first-redemption race prevent claims of cryptographic anonymity or strict
fungibility.

## Direct-mode fallback procedure

There are only two primary dock actions. Dock A uses **Send USDT**. Dock B
uses **Receive USDT**. Every action connects and reads the bill inventory
before deciding whether to write or read; a missing artifact is an immediate
instruction, never a normal CHUNK_OUT timeout.

1. On Dock B, press **Receive USDT**. If Bill B is empty, the dock creates one
   $5 transport-free invoice and writes it to Bill B.
2. Bill B displays `$5.00 / INVOICE TAP`. Press Bill A once while the bills
   are close. Bill A receives the invoice; no button is held.
3. Bill A displays `$5.00 / INVOICE DOCK`. On Dock A, press **Send USDT**.
   The dock reads the invoice, validates it, and displays the immutable plan.
4. Type the exact `CONFIRM <plan-id>` phrase. Only this step may broadcast.
   After broadcast, the exact generated consignment is written to Bill A. A
   staging failure retains that same consignment and must never repeat send.
5. Bill A displays `$5.00 / PROOF TAP`. Press Bill B once nearby. Bill B
   receives the consignment.
6. Bill B displays `$5.00 / PROOF DOCK`. On Dock B, press **Receive USDT**.
   The dock reads and imports the consignment, then refreshes after Mutinynet
   confirmation.

No action silently discards or overwrites an artifact. If the stored artifact
does not match the selected action, the dock explains the next physical step.
If a complete dock artifact cannot be handed to the tap service, inventory v3
reports `recovery-blocked` instead of `empty`. Both primary actions then stop
before creating, reading, or sending another artifact. The screen shows
`RECOVERY` with the retained amount when its metadata is known, or `CLEAR
STORAGE` without an amount for an untyped orphan. A touch press is ignored in
this state. Recovery remains an explicit operator `chunk-discard`, followed by
retrying the retained dock action; firmware never discards automatically.
Each complete artifact has a small SPIFFS metadata sidecar. Firmware restores
the kind, session, size, denomination, and direction after reset; a missing or
invalid sidecar is never treated as permission to overwrite an existing file.
When upgrading from the sidecar-less protocol, finish the staged transfer
before flashing or explicitly run `chunk-discard`. If new firmware finds a
legacy/orphaned artifact, it blocks all new transfers until that explicit
discard; it never guesses the artifact kind or overwrites it.

## Bill inventory state

The amount on an onboard screen is an **artifact denomination**, not a wallet
balance. The screen shows no numeric amount when inventory is empty. Wallet
balances remain on the laptop dock.

| Stored state | Screen | Permitted next step |
| --- | --- | --- |
| empty | `EMPTY` (no amount) | Dock B: **Receive USDT** writes invoice |
| invoice, bill→peer | `$5.00 / INVOICE TAP` | Press the receiving bill once |
| invoice, bill→dock | `$5.00 / INVOICE DOCK` | Dock A: **Send USDT** reads invoice |
| consignment, bill→peer | `$5.00 / PROOF TAP` | Press the receiving bill once |
| consignment, bill→dock | `$5.00 / PROOF DOCK` | Dock B: **Receive USDT** imports proof |
| bearer note, held | `$5.00 / NOTE` | Empty receiver: press once nearby, or destination dock: extract for redemption |
| recovery blocked, known artifact | `$5.00 / RECOVERY` | Explicitly discard bill staging, then retry the retained dock action |
| recovery blocked, unknown orphan | `CLEAR STORAGE` (no amount) | Explicitly discard the orphan before starting a transfer |

On every Connect, Send, or Receive action, the dock reads `IDENTITY`, `STATUS`,
`DENOMINATION`, and `ARTIFACT_META` in the same BLE session. The
`DENOMINATION` value must equal the inventory denomination. The artifact kind
and direction select CHUNK_IN versus CHUNK_OUT; human wording never selects a
blind read.

### Touchscreen input profile

The active Waveshare ESP32-S3-Touch-AMOLED-2.06 profile changes only the local
input and display adapter. One FT3168 tap-and-release is the same rising-edge,
one-shot event as pressing GPIO12 on the accepted boards: it arms one
15-second receiver window and does not need to be held. A second distinct tap
cancels. The controller is sampled after its GPIO38 interrupt rather than
continuously polling the shared I2C bus. All GATT UUIDs, inventory bytes,
artifact kinds, chunk frames, RSSI qualification, ACK semantics, persistence
rules, and recovery behavior remain byte-for-byte compatible. Touch navigation
is never a spend confirmation; exact RGB spend confirmation remains on the
laptop dock. A LilyGO T-Watch port may reuse the same logical adapter later.

## Dock service (`7c3e9a10-4b1e-4c8f-9d2a-00b111d0c001`)

Laptop is central. Bill is peripheral. One active transfer per connection.

| Name | UUID suffix | Properties | Payload |
| --- | --- | --- | --- |
| IDENTITY | `…c002` | Read | ASCII bill id (`bill-unprovisioned` until provisioned) |
| STATUS | `…c003` | Read, Notify | `idle` / `tap` / `pending` / `settled` / `error` |
| DENOMINATION | `…c004` | Read, Notify | ASCII integer cents, e.g. `500` for $5.00 |
| CMD | `…c005` | Write-with-response | UTF-8 command v1 |
| CHUNK_IN | `…c006` | Write-with-response | Binary frame v1, dock → bill |
| CHUNK_OUT | `…c007` | Notify | Binary frame v1, bill → dock |
| ARTIFACT_META | `…c008` | Read | 16-byte inventory v3 |

`ARTIFACT_META` v3 is little-endian: version (1 byte, `0x03`), direction (1
byte: empty `0`, bill-to-peer `1`, bill-to-dock `2`, recovery-blocked `3`,
bearer-held `4`), kind (1 byte: empty/unknown `0`, invoice `1`, consignment `2`,
bearer note `4`), reserved zero
(1 byte), session id (uint32), byte size (uint32), and denomination cents
(uint32). Docks decode v1 and v2 for upgrade compatibility; direction `3` was
introduced in v2 and direction `4` is valid only in v3. A known complete blocked artifact preserves its kind,
session, size, and denomination. An untyped orphan uses zero for those fields.

Dock core exposes one non-production display command:
`diag:display:empty`. It shows `DISPLAY TEST` without a numeric amount and
keeps `DENOMINATION=0`. It does not provision a bill, touch a wallet,
construct or broadcast a transaction, or survive a restart. Other unknown
command strings are rejected.

Chunk transport adds three non-wallet commands. `diag:chunk:echo` arms a
one-shot, byte-exact echo of the next complete `CHUNK_IN` transfer through
`CHUNK_OUT`. `chunk:receive` is written only after the laptop enables
`CHUNK_OUT` notifications; it explicitly requests a stored bill-to-dock or
bearer-held artifact and avoids depending on a stale ESP32 CCCD cache after a
reboot. `chunk:discard` deletes the staged transport artifact. None of these
commands parses an RGB invoice, imports a consignment, calls a wallet, or
broadcasts a transaction.

Before a normal dock→bill transfer, the dock writes
`artifact:prepare:<kind>:<denomination_cents>`. Firmware applies this amount
only if the completed chunk kind matches. The command alone never changes the
screen; only a complete stored artifact does.

`CHUNK_IN` accepts data frames from the dock and ACK frames for data sent by
the bill. `CHUNK_OUT` notifies an ACK for every accepted/rejected inbound data
frame and carries bill-originated data frames. Multi-byte integers are
little-endian:

| Field | Size | Meaning |
| --- | ---: | --- |
| version | 1 | `0x01` |
| kind | 1 | `0x01` invoice, `0x02` consignment, `0x03` ACK, `0x04` bearer note |
| session_id | 4 | Random per transfer session |
| sequence | 2 | Zero-based frame index |
| total | 2 | Total number of frames |
| payload_length | 2 | Payload bytes in this frame, maximum 200 |
| payload | 0–200 | Raw invoice, consignment, bearer-capsule, or ACK bytes |
| crc32 | 4 | CRC-32 over all preceding frame bytes |

Data payloads are 1–200 bytes. The dock defaults to 180 bytes, making the
largest normal frame 196 bytes under the firmware's requested ATT MTU of 247.
The receiver accepts invoice (`0x01`), consignment (`0x02`), and bearer-note
(`0x04`) data,
requires sequence zero first, enforces one stable kind/session/total tuple,
and appends only the exact next sequence. An exact retry of the immediately
previous frame is idempotently ACKed without being appended twice.

ACK (`0x03`) repeats the data frame's `session_id`, `sequence`, and `total`.
Its one-byte payload is:

| Value | Meaning |
| ---: | --- |
| `0x00` | accepted |
| `0x01` | malformed envelope or counters |
| `0x02` | unsupported version or kind |
| `0x03` | CRC mismatch |
| `0x04` | kind or session mismatch |
| `0x05` | sequence mismatch |
| `0x06` | total mismatch |
| `0x07` | transfer exceeds the receiver limit |
| `0x08` | staging-storage failure |
| `0x09` | receiver already holds a completed transfer |

The sender uses stop-and-wait: write/notify one frame, wait for its matching
successful ACK, then advance. Firmware retries a `CHUNK_OUT` notification up
to three times at one-second intervals. The dock relies on GATT
write-with-response for delivery to `CHUNK_IN` and fails closed on a missing or
negative ACK.

The current ESP32 staging limit is 655360 bytes (640 KiB), leaving filesystem
metadata and garbage-collection headroom in the 896 KiB SPIFFS partition. One
completed opaque artifact may be staged at a time. Complete artifacts survive
a dock disconnect for the future tap layer;
incomplete sessions are deleted on disconnect or after the five-minute
session timeout. `chunk:discard` explicitly frees dock and tap staging slots.

This layer transports exact opaque invoice, consignment, and bearer-capsule
bytes only. It does
not prove that the payload is valid RGB data. The existing wallet adapter is
still the authority that creates invoices and imports received OOB
consignments.

## Tap service (`7c3e9a10-4b1e-4c8f-9d2a-00b1117a0002`)

Bill–bill only. The bill holding a staged artifact is always the BLE
peripheral/sender. A bill with no staged artifact becomes central/receiver
when a debounced rising press edge arms a one-shot 15-second discovery window.
Both the WEMOS prototype and TTGO external-OLED profile use an active-high digital TTP223
touch module on GPIO12. The input may be released immediately: release does
not cancel scanning or an accepted BLE session. This removes central/central
races and the need to hold either input.
The receiver considers only advertisements whose name begins with `RgbBill`,
requires RSSI >= -60 dBm, and consumes the one-shot authorization when it
attempts the first qualifying connection. Duplicate advertisement reports are
kept during the arm window so a peer first observed below the threshold may
qualify on a later, stronger RSSI sample. It then validates the tap service and
metadata before requesting bytes. Success, connection/session failure, or the
15-second pre-connection timeout leaves the receiver disarmed. A second press
explicitly cancels an armed window or active receive session.

| Name | UUID suffix | Properties | Payload |
| --- | --- | --- | --- |
| META | `…0003` | Read | 16-byte tap metadata v3 |
| CONTROL | `…0004` | Write-with-response | `0x01` START, `0x02` CANCEL |
| CHUNK_IN | `…0005` | Write-with-response | chunk-v1 ACK, receiver → sender |
| CHUNK_OUT | `…0006` | Notify | chunk-v1 data, sender → receiver |

Tap metadata is little-endian:

| Field | Size | Meaning |
| --- | ---: | --- |
| version | 1 | `0x03` (`0x01` 12-byte and `0x02` 16-byte metadata are decode-only legacy) |
| state | 1 | `0x00` empty, `0x01` artifact ready |
| kind | 1 | chunk-v1 invoice `0x01`, consignment `0x02`, or bearer note `0x04`; zero if empty |
| reserved | 1 | Must be zero |
| session_id | 4 | Same session identifier carried by every chunk frame |
| size | 4 | Exact artifact byte count, maximum 655360 |
| denomination_cents | 4 | Amount to display only while this artifact exists |

`CHUNK_OUT` and `CHUNK_IN` reuse the dock service's binary chunk-v1 envelope,
CRC32, strict ordering, exact duplicate ACK behavior, and stop-and-wait retry
rules. The sender keeps `/tap-out.ready` after cancellation, disconnect, or
timeout, and removes it only after the matching successful final ACK. The
receiver writes `/tap-in.part`, atomically promotes it to `/tap-in.ready`, and
then makes it available through dock `CHUNK_OUT`. No RGB parsing occurs on the
ESP32.

Bearer notes are the exception to the dock-facing handoff: after the complete
`0x04` file and its sidecar are durable, the receiver atomically promotes them
to `/tap-out.ready`. Inventory becomes `bearer-held`, and that bill can be the
sender for the next offline hop without either laptop or Internet access. The
original sender keeps its file until the successful final ACK and only then
deletes it. Invoice and consignment behavior stays unchanged.

At the destination, a laptop that reads `bearer-held` subscribes to dock
`CHUNK_OUT`, writes `chunk:receive`, and requests kind `0x04`. TapGatt exposes
`/tap-out.ready` to DockGatt only after that explicit post-subscription
request. The bill retains the
capsule through the final dock ACK; only then is `/tap-out.ready` deleted and
its sidecar reconciled to empty inventory. The dock must persist the received
file privately and verify its full SHA-256 before parsing or planning
redemption. A connection, inventory read, timeout, or interrupted extraction
does not consume the bearer note.

After the receiver disconnects its tap client, dock advertising is restarted
from the main loop after a short BLE-stack teardown delay rather than directly
inside the disconnect callback. A bill-to-bill connection is not treated as a
dock retrieval session: the received artifact is queued to dock `CHUNK_OUT`
only after the laptop has enabled that characteristic's notification CCCD.

The discovery advertisement remains the frozen dock advertisement so it
continues to fit one legacy 31-byte PDU. Tap discovery uses the `RgbBill*`
name and validates the tap service after connection; no invoice, consignment,
bearer capsule, amount, or session metadata is placed in advertising data.

Security: dock link may request bonding (`ENC_NO_MITM`) like the Waveshare
gateway. Tap link is short-lived; Mutinynet demo may stay unauthenticated.
Mainnet would need a pairing story.

## Bearer-note settlement state

The vault/dock state is intentionally separate from firmware inventory:

```text
PREPARING -> AWAITING_FUNDING -> ACTIVE -> CLAIM_PLANNED
          -> CLAIMING -> BROADCAST_RECORDED -> REDEMPTION_IMPORTED -> REDEEMED
                    \-> AMBIGUOUS           \-> IMPORT_RECOVERY_REQUIRED

PRE_BROADCAST_REJECTED -> CAPACITY_PLANNED
 -> CAPACITY_BROADCAST_RECORDED -> CAPACITY_READY -> CLAIM_PLANNED
```

Funding has the same separately authorized capacity fallback on the issuing
dock wallet:

```text
PRE_BROADCAST_REJECTED -> FUNDING_CAPACITY_PLANNED
 -> FUNDING_CAPACITY_BROADCAST_RECORDED -> FUNDING_CAPACITY_READY
 -> AWAITING_FUNDING (fresh immutable RGB plan)
```

The original RGB confirmation is consumed before the failed send attempt and
is never reused. The capacity transaction has its own exact confirmation. Only
after it confirms may the dock archive the rejected RGB plan and prepare a new
one.

Firmware `bearer-held` means only that a complete capsule is durable. It does
not mean the RGB allocation is unspent or settled. The vault is authoritative
for claim state; the chosen destination wallet's exact settlement verifier is authoritative for
`REDEEMED`. A web or indexer timeout never grants permission to resend.

Both current bill profiles sample the TTP223 output on GPIO12 as an active-high
digital input. GPIO12 is also the ESP32 MTDI flash-voltage strap, so each
TTP223 must remain released/LOW during power-up, reset, and flashing. Do not
configure either module for a latched mode that could retain a HIGH output
across reset.
