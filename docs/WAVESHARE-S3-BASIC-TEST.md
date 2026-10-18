# Waveshare ESP32-S3 basic hardware test

This diagnostic is the first isolated fork2checkbook profile for the
Waveshare ESP32-S3-Touch-AMOLED-2.06. It proves the AMOLED, PCF85063 RTC,
FT3168 touchscreen input, stable always-on USB/power-bank operation, and a
minimal RGBill BLE central before storage or the bearer protocol is introduced.

Status: **implemented, build-tested, and flashed on 2026-10-18. The 410x502
AMOLED, RTC, PSRAM, always-on USB operation, and initial FT3168 screen-touch
acceptance passed. Seven deliberate tap-and-release cycles produced seven
coordinate events and seven releases with no held-touch duplicates. The longer
twenty-tap/five-minute acceptance soak below remains optional follow-up. The
touch-driven BLE scan/pair image is also physically accepted: the first touch
found only the two authorized RGBill peers and selected `RgbBill` at -43 dBm;
the second touch authenticated, read identity `bill-unprovisioned`, and stored
one bond for address `30:ae:a4:07:2d:2e`.**

## Verified target

Read-only `esptool` probes on 2026-10-17 reported:

- ESP32-S3 QFN56 revision 0.2;
- embedded 8 MB PSRAM;
- 32 MB quad flash at 3.3 V;
- USB Serial/JTAG MAC `28:84:85:90:cc:04`;
- stable port
  `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_28:84:85:90:CC:04-if00`.

The probe resolves the discrepancy between the board summary and older
Arduino example instructions: this physical unit has 32 MB flash.

## Deliberately excluded

This profile does not expose a fork2checkbook GATT server or link storage,
Wi-Fi, filesystem, SD card, audio, IMU, QVAC, rgb-lib, or wallet code. Its
minimal BLE central scans only `RgbBill*` advertisements carrying the frozen
dock service UUID. A first touch selects the strongest authorized peer; a
second touch connects, requests Just Works bonding, and reads the frozen
identity characteristic. The result distinguishes an actual stored bond from
an unbonded but verified GATT connection. It draws directly with Arduino_GFX;
LVGL and Brookesia are not used.

## Build

```bash
.venv-platformio/bin/pio test -e native -f test_waveshare_basic
.venv-platformio/bin/pio run -e waveshare-s3-basic-test
```

Do not upload until a human explicitly asks. Once authorized:

```bash
.venv-platformio/bin/pio run -e waveshare-s3-basic-test -t upload
.venv-platformio/bin/pio device monitor -e waveshare-s3-basic-test
```

## Expected behavior

1. Boot shows `fork2checkbook / HARDWARE TEST / POWER ON`.
2. The clock screen reads the PCF85063 once per second. A valid RTC is never
   overwritten. An invalid RTC is seeded once from the firmware build time.
3. A screen touch produces an active-low GPIO38 interrupt. Firmware reads the
   FT3168 point over I2C and starts a five-second authorized RGBill scan.
4. If a peer is found, a second distinct touch connects, verifies the dock
   service and identity characteristic, requests Just Works bonding, and shows
   either `BONDED` or the honest fallback `VERIFIED`.
5. The display and MCU remain awake continuously. This demo watch has no
   battery and is intentionally powered from USB or a USB power bank.

## Physical acceptance

- Clock advances for five minutes and survives a reset without being reset.
- Twenty distinct screen taps produce exactly twenty events with coordinates.
- Initial touch acceptance completed with seven distinct screen taps: seven
  events, seven releases, and no duplicate event while a finger remained down.
- The clock and display remain active for the full powered test.
- Native USB logging remains usable throughout the test.
- The first touch lists only peers with the `RgbBill` prefix and frozen dock
  service UUID, selecting the strongest by RSSI.
- The second touch verifies the selected peer's dock identity. `BONDED` is
  accepted only when the ESP32 bond table contains that exact address;
  otherwise the screen reports `VERIFIED` rather than claiming pairing.
- Physical evidence on 2026-10-18: 57 total advertisements, two authorized
  RGBill peers, strongest peer `RgbBill` at -43 dBm, authentication `PASS`,
  identity `bill-unprovisioned`, `bonded=yes`, and `stored_bonds=1`.
- Arduino BLE may print `retrieveDescriptors(): ... Unknown` while enumerating
  characteristics without descriptors; this did not prevent the identity read,
  authentication result, or stored-bond check.
- This diagnostic image emits no RGBill advertisement of its own.
- Existing WEMOS and TTGO profiles continue to build unchanged.

Sleep and screen-tap wake are deliberately excluded from the Sunday demo
profile. They can be reconsidered after the externally powered flow is stable.

## Production bill image

The separate `waveshare-s3-bill` environment is the production port. It reuses
the frozen dock UUIDs, chunk-v1 framing, inventory v3, bearer kind `0x04`, and
the accepted `TapGatt` state machine. The FT3168 touchscreen supplies the same
rising-edge, one-shot receiver action that GPIO12 supplies on WEMOS and TTGO.

Implemented boundaries:

- CO5300 AMOLED clock plus `EMPTY`/durable bearer amount/status display;
- identity `bill-waveshare-s3-v1`, advertising name `RgbBill-Watch`;
- one composition-owned, non-formatting 4 MiB SPIFFS mount using the dedicated
  `partitions_waveshare_s3.csv` no-OTA layout;
- fail-closed boot classification: `.part`, missing sidecars, malformed
  metadata, and mismatched payload sizes become recovery-blocked;
- dock GATT staging and diagnostic echo compatibility;
- no Wi-Fi, wallet, QVAC, or rgb-lib in this image.

Build and host-side checks:

```bash
.venv-platformio/bin/pio test -e native -f test_waveshare_basic
.venv/bin/python -m pytest -q pytests/test_firmware_hardware.py
.venv-platformio/bin/pio run -e waveshare-s3-bill
.venv-platformio/bin/pio run -e esp32-bill
.venv-platformio/bin/pio run -e ttgo-lora32-oled-test
```

The production image was physically flashed on 2026-10-18 to the verified
ESP32-S3 at the stable USB by-id path above. Because the new fail-closed image
never formats storage automatically, the first boot correctly reported an
unformatted filesystem as recovery-blocked. The exact 4 MiB SPIFFS partition
at `0x490000` was then provisioned once with an empty filesystem image; the
next boot reported `SPIFFS mounted total=3848081 used=0` and advertised the
dock service with tap disabled.

The physical P3 transport gate used watch BLE address `28:84:85:90:CC:05` and
the following byte-verifying synthetic echo:

```bash
.venv/bin/python -m dock.bill_dock scan --timeout 8
.venv/bin/python -m dock.bill_dock identity \
  --address WATCH_BLE_ADDRESS
.venv/bin/python -m dock.bill_dock chunk-loopback \
  --address WATCH_BLE_ADDRESS \
  --kind bearer-note \
  --denomination-cents 500 \
  --input test/fixtures/chunk-smoke.payload
```

Acceptance requires identity `bill-waveshare-s3-v1`, a successful exact
520-byte loopback with the fixture SHA-256, an empty inventory afterward, and
an `EMPTY` screen after the final ACK. Reboot persistence and deliberate
orphan recovery are the following physical checks; passing this echo does not
authorize offline tap transfer.

Physical transport evidence on 2026-10-18:

- scan found `RgbBill-Watch` at `28:84:85:90:CC:05`;
- the identity read returned `bill-waveshare-s3-v1`;
- two physical bearer-note loopbacks returned `loopback=ok`, 520 bytes, and
  SHA-256
  `925a4bf7cdf591eea7e5b78975964aee5801417761474fe3588b385ba00bc014`;
- the monitored run completed session `0x22180f3f` on the firmware and logged
  both `chunk transfer complete` and `outbound chunk transfer complete`;
- the post-ACK inventory read returned `status=idle`, `denomination=0`, and
  `artifact=empty:empty`;
- the operator visually confirmed the production AMOLED displayed `EMPTY` /
  `NO NOTE LOADED` after the final ACK;
- that P1-P3 production boot intentionally reported `tap=disabled` before the
  separately gated touchscreen integration.

The physical dock echo and production empty-screen checks are accepted.

Physical persistence and recovery evidence on 2026-10-18:

- a normal non-echo bearer write staged session `0x8df5551b`, 520 bytes,
  denomination 500 cents, and the expected fixture SHA-256;
- before reboot, inventory was fail-closed as `blocked:bearer_note` because the
  P1-P3 watch has no `TapGatt` owner;
- after an `esptool` hard reset, inventory restored the exact kind, session,
  520-byte size, and 500-cent denomination rather than reporting empty;
- the valid retained fixture was removed only with explicit `chunk:discard`;
- a controlled SPIFFS image containing only `/chunk-in.part` reproduced an
  interrupted write and booted as `status=error`, denomination zero,
  `artifact=blocked:empty`;
- a new bearer write was rejected at frame zero with `STORAGE_FAILURE` while
  the orphan existed;
- explicit `chunk:discard` restored `idle`, denomination zero,
  `artifact=empty:empty`, and that empty state survived another hard reset.

P1-P3 storage, reboot persistence, orphan blocking, and explicit recovery are
physically accepted.

## Touchscreen TapGatt acceptance

The next production gate was implemented, flashed, and physically accepted on
2026-10-18. Production now samples the FT3168 only after its GPIO38 interrupt,
matching the accepted diagnostic. An early continuous-polling build exposed
repeated shared-I2C failures; that build was rejected before a transfer and
replaced by the interrupt-driven adapter. The accepted boot reported:

- `SPIFFS mounted total=3848081 used=0`;
- `tap: GATT ready; tap screen once near sender`;
- `watch touch: FT3168 ready address=0x38`;
- `tap=enabled touch=ready`, with no repeated I2C errors.

Physical synthetic round-trip evidence:

1. WEMOS staged bearer session `0x805bbfe4`, denomination 500 cents, 520
   bytes, SHA-256
   `925a4bf7cdf591eea7e5b78975964aee5801417761474fe3588b385ba00bc014`.
2. One watch touch at `(360,234)` armed one 15-second receiver session. The
   watch received and promoted kind `0x04`; WEMOS became empty only after the
   final ACK.
3. One GPIO12 press on the now-empty WEMOS made the watch send three frames.
   The watch logged `sender complete`, became empty, and WEMOS alone held the
   same $5 bearer session.
4. Dock extraction from WEMOS returned session `0x805bbfe4`, 520 bytes, and
   the exact expected SHA-256. `cmp` against
   `test/fixtures/chunk-smoke.payload` succeeded, after which WEMOS returned
   to `artifact=empty:empty`.

Both watch and WEMOS finished empty. This proves the synthetic touchscreen
watch-to-bill transport and deletion-after-final-ACK behavior.

## Live funded-capsule relay

A separately authorized live Mutinynet gate passed on 2026-10-18:

- Wallet B funded disposable note
  `feb839fb-17a9-48b2-a378-f6a89b4f6eef` with exactly 5,000,000 USDT base
  units. Its RGB funding txid is
  `8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f`;
- settlement was verified before capsule preparation. The resulting capsule
  is 603 bytes with SHA-256
  `159805d2dea99c3dd2601e3dcf07d0dfd6b4c6a400df1970f8b405710d5f310c`;
- TTGO sent session `0x220902c8` to the empty watch. The watch received kind
  `0x04`, promoted it to `bearer-held`, and TTGO cleared only after final ACK;
- WEMOS then received the same note from the watch. The watch logged a
  four-frame `sender complete` and became empty;
- controlled dock extraction from WEMOS returned the same session, 603 bytes,
  and exact SHA-256. A byte-for-byte `cmp` against the private vault capsule
  succeeded; WEMOS cleared only after the final dock ACK.

The first live tap also found a release-sampling defect: GPIO38 reliably
signaled contact but not a second release edge. Production was repaired to
remain interrupt-driven while idle and poll only during an active contact
until release. The rebuilt image passed native/policy tests and the repeated
touch armed correctly. An empty WEMOS placed at -8 dBm initially won the RSSI
selection over the intended sender; no artifact moved, but current demo setup
must move unrelated bills out of tap range. Peer inventory-aware retry remains
a post-demo UX improvement.

All three devices finished empty after the relay and extraction. In the
separately authorized settlement gate, Wallet A then created the previously
unknown redemption invoice. After a safe pre-broadcast allocation-slot
rejection and separately confirmed capacity transaction, fresh plan
`1810b78e-8dd0-43de-be80-b6ba9e178a49` broadcast RGB transaction
`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`.
Wallet A imported one exact 5,000,000-unit assignment, moved from 50 to 55
USDT, and the note closed `REDEEMED` with claim `SPENT`.
