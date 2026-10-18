# fork2checkbook — design

Rendered architecture, sequence, state, protocol, hardware, and trust diagrams
are indexed in [docs/diagrams/README.md](diagrams/README.md). Their editable
Mermaid sources live beside the PNGs under `docs/diagrams/src/`.

Physical USDT notes. The primary dock exposes explicit **Load note**, **Pass
note**, **Redeem note**, **Recover**, and **Refresh settlement** actions. Tap
two bills once when instructed. No QR, no phone app, and no guessing whether
the bill should be read or written. The older **Send USDT** / **Receive USDT**
invoice-first procedure remains an advanced fallback.

## Roles

| Piece | Where | Job |
| --- | --- | --- |
| Dock A / Dock B | Two laptops | Python web desk (`web/`, :8788), Bleak GATT (`dock/`), rgb-lib-python, QVAC voice, Mutinynet broadcast |
| Bill A / Bill B | WEMOS LOLIN32 and LilyGO TTGO LoRa32 V1.3 / ESP32 | SSD1306 OLED, tap input, radio. **No** rgb-lib, **no** QVAC |
| Watch bill port | Waveshare ESP32-S3-Touch-AMOLED-2.06 | 410×502 AMOLED, FT3168 touch, PCF85063 clock, same opaque bearer/GATT contract. **No** rgb-lib, **no** QVAC |
| Disposable note vault | Local trusted process for the selected demo | One fresh rgb-lib wallet N per note, claim hash, immutable plans, recovery journal |
| GATT-dock | laptop ↔ its bill | On-demand connection. Inventory, commands, status, denomination, opaque chunks |
| GATT-tap | bill ↔ bill | A press edge arms a bounded receiver window; high RSSI gates the peer. Carries invoice, consignment, or an opaque bearer capsule |

The ESP32 bills are not rgb-lib wallets. Each dock profile has its own
mnemonic/datadir and registered bill. A/B are isolation and demo labels, not
bearer permissions: either dock wallet may fund a new note, and whichever dock
the eventual holder chooses may redeem it. The selected offline mode adds one
disposable note wallet N per note inside the trusted local vault. The bill
carries only an opaque bearer capsule; it never receives N's mnemonic or
datadir. Never open any datadir in two processes.

Each role-specific web dock registers one physical bill. Discovery requires
the frozen dock service UUID plus an RgbBill-family advertised name; a
read-only core-state connection verifies the target before its address is
saved. Every later connection reads the artifact inventory (empty, invoice,
or consignment; to-peer or to-dock) before selecting a transfer direction.
Transfers reconnect to that saved address on demand. Registration is
locked while a transfer or recovery boundary is active. This is prototype
device selection, not cryptographic device ownership authentication.

## Primary RGB mapping: offline bearer note

1. The issuing dock creates fresh isolated note wallet N. If N lacks its
   configured Bitcoin fee reserve, the dock prepares a separate immutable
   `send_btc` top-up from the issuer wallet; one exact confirmation authorizes
   that transaction, replacing the manual faucet step. New plans calculate the
   minimum reserve for the controlled one-input shape: a 1,000-sat colorable
   output, its measured 213-vB capacity fee, a 1,000-sat witness, and the
   measured 198-vB RGB-send fee. At the shared 1 sat/vB default this is exactly
   2,411 sats; a higher configured fee rate scales both fee components.
2. After the top-up confirms, the dock supersedes the stale RGB plan. A fresh
   exact confirmation funds N with exactly 5 USDT through a transport-free
   witness invoice.
3. After N settles, the vault creates a 603-byte bearer capsule containing a
   random claim capability and redacted funding evidence—never wallet keys.
4. The issuing dock loads the capsule onto a bill. Bills relay the same kind `0x04`
   artifact over BLE while docks and Internet are unavailable.
5. The final holder chooses a compatible dock. It extracts and hashes the exact
   capsule, and only then does that dock's local wallet create a fresh
   transport-free redemption invoice. The issuer did not know this wallet.
6. One separate exact confirmation authorizes N's one RGB send. The chosen wallet
   imports the exact retained consignment and becomes `REDEEMED` only after an
   exact txid/assignment/balance settlement check.

There are exactly two RGB transitions: issuer wallet → N and N → eventual
redeemer wallet. Every offline bill-to-bill hop transfers the complete fixed
denomination and does not alter RGB state. The empty receiver durably accepts
the capsule before the sender clears its ordinary copy on the final ACK. The
vault retains N's keys, so this is trusted custody and copied capsules are
first-redemption-wins.

This prototype targets cash-like relay privacy and denomination
interchangeability, not proven cryptographic anonymity or strict fungibility.
No final wallet or person identity is embedded when the note is issued, and BLE
advertisements expose no artifact metadata. However, device addresses are
locally observable, the capsule carries funding evidence, and the trusted vault
can correlate issuance with first redemption. Those limits must be stated in
the demo.

## Direct-mode fallback

The accepted browser flow remains available: Dock B creates a $5 invoice,
Bill B taps it to Bill A, Dock A confirms one send, Bill A taps the exact
consignment back, and Dock B imports it. Both modes require empty RGB
`transport_endpoints`; docks never exchange the payload over Wi‑Fi. The board
displays an amount only while a complete artifact is durable.

The dock adapter enforces this as two isolated processes, not one process with
two wallet objects. Bill B creates the witness invoice. Bill A reads its full
spendable asset balance, presents an immutable plan, consumes a one-use human
confirmation, and calls rgb-lib with `donation=true` and empty transport
endpoints. The generated consignment bytes leave the dock only through the
out-of-band chunk path. Bill B imports those bytes with
`provide_out_of_band_consignment`. An ambiguous send is never automatically
retried because it may already have broadcast.

On-chain rgb-lib, not RLN. Lightning is a channel, not a note.

## Radio

- GATT-dock: Arduino `BLEDevice` **peripheral** on the bill, Bleak **central** on the laptop. Same shape as `/media/kestl/andor/github/waveshares3` (patterns only).
- GATT-dock chunk v1 is implemented: CRC32, ordered stop-and-wait frames,
  per-frame ACKs, and one opaque SPIFFS-staged artifact up to 640 KiB. Its
  diagnostic echo validates both directions without invoking a wallet.
- Dock artifact inventory v3 is a 16-byte read-only characteristic. It makes
  connect-time direction selection deterministic and prevents blind
  CHUNK_OUT waits. Direction `recovery-blocked` prevents a retained DockGatt
  artifact or tap-storage orphan from being misreported as empty and blocks a
  second invoice until explicit operator recovery.
- Kind `0x04` is an opaque bearer-note capsule. Direction `bearer-held` means
  the complete capsule is durable and ready for another offline bill-to-bill
  hop. The receiving bill promotes it to its next tap-out slot; the sender
  deletes only after the successful final ACK. Firmware emits v3 while keeping
  inventory/tap metadata v1/v2 decode compatibility.
- A destination dock may subscribe to dock `CHUNK_OUT` while inventory is
  `bearer-held`, then issue the explicit `chunk:receive` request to extract
  that same capsule for redemption. This post-subscription handshake avoids a
  stale ESP32 CCCD cache after reboot. The bill keeps the file through the
  final dock ACK, then clears the bearer file and sidecar. Merely connecting or
  reading inventory never consumes the note.
- GATT-tap: the bill holding the staged artifact stays **peripheral/sender**;
  one debounced tap on the empty bill arms a 15-second **central/receiver**
  window. The operator may release immediately. The receiver connects only if
  RSSI ≥ −60 dBm and validates tap metadata before START; the accepted BLE
  session then continues independently of the input level. A second press
  cancels. It reuses 180-byte chunk-v1 payloads and CRC32. Advertising PDUs
  never carry invoice or consignment bytes.
- Session timeout on the dock link is minutes (pending confirmation ~30 s), not 60 s.

## Bill hardware

- Board: WEMOS LOLIN32 V1.0.0 (`board = lolin32`), ESP32-D0WDQ6, 4 MB flash.
- Flash layout: one 3 MiB factory application, no OTA slot, 896 KiB SPIFFS,
  and 64 KiB coredump. USB flashing is the update path for the prototype.
- Display: 128x64 SSD1306 over I2C, SDA GPIO21, SCL GPIO22, address probed at `0x3C` then `0x3D`.
- Tap input: active-high TTP223 digital output on GPIO12. GPIO12 is an ESP32
  flash-voltage strapping pin, so keep the sensor released/LOW during power-up,
  reset, and flashing.
- The production `esp32-bill` and diagnostic `oled-test` environments use the
  stable CP2104 USB by-id path. Do not upload an ESP32-S3 image to this board.
- Second test bill: LilyGO TTGO LoRa32 V1.3-868. The active
  `ttgo-lora32-oled-test` profile uses a replacement external 128x64
  SSD1306-compatible display on SDA GPIO21 / SCL GPIO22 with no reset lead,
  plus the active-high TTP223 touch input on GPIO12. GPIO12 is also an ESP32
  flash-voltage strapping pin, so the sensor must not be touched or held high
  during reset. Its stable CP2102 USB path is kept separate from the WEMOS.
  The former ILI9341 build remains available only as
  `ttgo-lora32-tft-legacy`; the failed onboard OLED and older diagnostic
  profiles remain documented. See
  [TTGO-LORA32-V1.3-TEST.md](TTGO-LORA32-V1.3-TEST.md).

### Planned T-Watch 2020 V1 profile

This section records the earlier LilyGO reference evaluation. The active watch
port now targets the Waveshare ESP32-S3-Touch-AMOLED-2.06; the LilyGO tree
remains a read-only application/reference source only.

The local reference tree
`/media/kestl/xwing/Old_documents/Documents/PlatformIO/Projects/My-TTGO-Watch`
is registered as a read-only implementation reference. Its inspected default
target is the square LilyGO T-Watch 2020 V1: ESP32-D0WDQ6, 16 MB flash,
240×240 ST7789 display, FT6336 touch controller, AXP202 power management,
BMA423 motion sensor, PCF8563 RTC, vibration motor, LVGL 7.11, SPIFFS, and
NimBLE. The existing watch framework discovers apps through
`app_autocall_function` and supplies display, touch, power, and wake handling.

The watch is a planned replacement physical bill, not a new dock. Its app must
implement the existing dock service, tap service, chunk framing, inventory v3,
bearer kind `0x04`, durable promotion, final-ACK deletion, one-shot receive,
and RSSI policy without changing UUIDs or payloads. A deliberate screen action
replaces the external GPIO12 press: **Receive note** arms one 15-second window,
and a second action cancels it. The screen shows only artifact denomination and
state; it never shows or stores a wallet balance.

The watch framework already owns the process-wide NimBLE server and SPIFFS.
The dedicated demo profile does not have to preserve that full framework. It
keeps only a basic RTC-backed clock/home screen and the RGB Bill app, reusing
the reference board, display, touch, PMU, sleep/wake, haptic, and filesystem
layers. The reference Gadgetbridge/device-info/battery/step BLE services and
all unrelated apps are excluded so RGB Bill can be the sole NimBLE owner.
Wi-Fi, OTA, web-server, phone-companion, maps, notifications, and audio are
excluded from the build. SPIFFS remains mounted once and RGB Bill files are
namespaced; an app error must never format storage. Full implementation and
acceptance gates are in
[TTGO-WATCH-APP-PLAN.md](TTGO-WATCH-APP-PLAN.md).

### Active Waveshare ESP32-S3 watch port

The physically verified Waveshare baseline uses a 410×502 CO5300 AMOLED,
FT3168 touch controller, PCF85063 RTC, 32 MB flash, 8 MB PSRAM, and native USB
Serial/JTAG. It is externally powered and intentionally always-on for the
demo. The `waveshare-s3-bill` image implements phases P1-P3: clock/bill
display, single-owner fail-closed SPIFFS, and the frozen dock GATT/chunk
contract. The physical dock echo passed on 2026-10-18 with the byte-exact
520-byte bearer fixture, empty post-ACK inventory, and a visually confirmed
`EMPTY` / `NO NOTE LOADED` production display. Complete-artifact restoration,
fail-closed orphan boot, blocked-write rejection, explicit discard recovery,
and clean-state persistence also passed physically. The next gate added the
same `TapGatt` protocol with an FT3168 interrupt-driven, rising-edge one-shot
input. A physical WEMOS -> watch -> WEMOS synthetic $5 round trip passed with
the exact 520-byte payload and SHA-256, sender deletion only after final ACK,
and both devices empty afterward. A later live funded 603-byte capsule crossed
TTGO -> watch -> WEMOS as session `0x220902c8` and was extracted byte-exactly
with SHA-256
`159805d2dea99c3dd2601e3dcf07d0dfd6b4c6a400df1970f8b405710d5f310c`.
The touch adapter remains interrupt-driven while idle and briefly samples an
active contact until release, allowing repeated one-shot taps without polling
the shared I2C bus continuously. See
[WAVESHARE-S3-BASIC-TEST.md](WAVESHARE-S3-BASIC-TEST.md).

## Delivery status and Sunday priorities

1. Two rgb-lib wallets, witness + OOB consignment over USB serial, docks drive OLEDs/LEDs.
2. Replace the cable with dock GATT chunks. **Implemented and physically
   accepted on both boards.**
3. Tap GATT + RSSI/button v1. **Implemented and physically accepted in both
   directions with byte-exact 520-byte invoice/consignment fixtures.**
4. Replace fixtures with real isolated-wallet artifacts. **Bridge implemented
   and live accepted with a confirmed 20 USDT Mutinynet transfer on
   2026-10-11.**
5. Integrate the accepted flow into `python -m web.server`. **Implemented with
   exact typed confirmation and no-resend recovery; browser-driven physical
   acceptance completed on 2026-10-15.**
6. Human-first inventory/tap metadata v2. **Implemented, flashed, and
   physically accepted on both boards with a settled $5 Mutinynet transfer on
   2026-10-15.**
7. Offline bearer-note mode. **Implemented and live accepted:** isolated note
   funding, 603-byte capsule, physical WEMOS → TTGO relay/restart, exact
   extraction, separately gated capacity, one-shot redemption, Wallet B
   settlement, and durable `REDEEMED` closure.
8. Integrate the accepted operator bearer flow into the web dock without
   weakening confirmation/recovery boundaries. **Implemented and physically
   accepted in both directions.** The web process
   remains the sole owner of Wallet A/B, extraction is private and
   content-addressed, and confirmation/recovery reuse the accepted durable
   note-vault plan and journal.
9. Connect local QVAC typed intent parsing to the accepted deterministic
   actions and capture a reproducible evaluation. **Integrated locally:** the
   resident `QWEN3_600M_INST_Q4` model maps natural language to the frozen
   intent grammar, while deterministic policy prepares the no-broadcast
   funding plan and preserves exact confirmation. Real local checks mapped
   “Prepare a $5 note” to `LOAD_NOTE(500)` and “Check my note status” to
   `INSPECT_NOTE`; live-browser wallet acceptance passed, while the fixed
   adversarial evaluation corpus remains pending.
10. Rehearse one non-technical three-minute live flow with understandable
   device/dock states and recovery messages. Add WDK only after the RGB + QVAC
   primary flow is stable; WDK is bonus, not a blocker.
11. Port the accepted physical-bill role to the Waveshare ESP32-S3 watch while
    keeping the laptop dock unchanged. **P1-P3 implemented, flashed, and
    physically dock-echo tested:** AMOLED bill display, fail-closed
    single-owner SPIFFS, and dock GATT compatibility are present. The
    byte-exact 520-byte bearer echo and empty post-ACK inventory passed on
    2026-10-18, and the operator visually confirmed the production empty
    screen. Exact reboot restoration and deliberate orphan recovery also
    passed physically. **Touchscreen-driven `TapGatt` is implemented, flashed,
    and physically accepted:** WEMOS -> watch -> WEMOS moved one complete
    synthetic $5 bearer note byte-exactly and left both devices empty after
    dock extraction. **A real funded capsule then passed TTGO → watch → WEMOS,
    was extracted exactly, and redeemed into Wallet A; all three devices
    finished empty.** The accepted WEMOS/TTGO pair remains the demo fallback.

## Web dock language

Python is the dock application: HTTP + static UI in `web/`, BLE in `dock/`,
rgb-lib-python in `web/rgb/`. That matches Bleak and the 0.3.0b7 wheels.

Rust rgb-lib stays optional under `web/rust/` if wheels fail. Do not run two
wallet processes on one datadir. QVAC is `tetherto-qvac-sdk` in `.venv`; the
desk UI is already a browser page.

## BLE reference (do not copy protocol)

`/media/kestl/andor/github/waveshares3`: ESP32-S3 reference only, PlatformIO, Bleak 3.0.2, frozen GATT JSON, write-with-response, notify, bonding, session FSM. Locker UUIDs, `0xA4`, barcodes, relays, and board pin maps stay there.

## Watch reference (reuse hardware and app patterns)

`/media/kestl/xwing/Old_documents/Documents/PlatformIO/Projects/My-TTGO-Watch`:
local fork of `sharandac/My-TTGO-Watch`, inspected at commit `a7eb49ea`. Reuse
its T-Watch 2020 V1 hardware initialization, LVGL app lifecycle, touch, PMU,
sleep/wake, haptic, and SPIFFS conventions. Do not reuse its Gadgetbridge GATT
contract as the RGB Bill protocol. The watch target is deliberately reduced to
a basic clock/home screen plus the RGB Bill app; no other reference app or
network service must survive. Do not edit the reference tree without separate
human authorization. The reference is GPL-2.0; preserve notices and review
distribution obligations before copying source.

## Judging

Real RGB is proven by the two accepted Mutinynet transitions in each complete
cycle. QVAC stays on the dock and is wired to the guarded note-preparation
path; live-browser acceptance passed, while the reproducible adversarial
evaluation remains open. WDK is not integrated. The selected
bearer-note demo is explicitly trusted-vault custody; do not describe it as a
production self-custodial note.
