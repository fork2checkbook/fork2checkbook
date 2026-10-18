# Offline bearer-note live demo runbook

Accepted reference path: Wallet A → disposable note N → offline WEMOS/TTGO
relay → Wallet B. That A-to-B run is evidence, not an authorization rule. A/B
are isolated profiles: a funded Wallet B may issue a later note and Wallet A
may become its previously unknown redeemer. Network: Mutinynet only. The direct
invoice-first browser flow remains the fallback in
[`WEB-RGB-TEST.md`](WEB-RGB-TEST.md).

This runbook contains spend-capable commands. Every `confirm-*` operation must
stop at its own freshly printed immutable plan and receive a separate exact
human `CONFIRM <plan-id>` authorization. Never reuse a phrase copied from this
repository or an earlier run.

The dock browser now covers normal note preparation through the resident local
QVAC model plus **Load note**, **Pass note**, **Redeem note**, **Recover**, and
**Refresh settlement**. Ask **“Prepare a $5 note”**; QVAC proposes only
`LOAD_NOTE(500)`, deterministic code creates the no-broadcast plan, and the
human still types the exact confirmation. If the fresh wallet lacks fee sats,
the browser prepares a separate immutable Bitcoin top-up from the issuing dock
wallet. Review and confirm it, wait for one Mutinynet confirmation, then ask
**“Check my note”** to obtain a fresh RGB funding plan. No faucet address is
shown in the normal browser flow, and one confirmation never authorizes both
broadcasts. The current automatic 2,000-sat reserve is not sufficient in every
path: the accepted watch run needed a colorable-capacity transaction and then
an operator-added Mutinynet top-up before redemption. For the demo, pre-fund
the disposable note with enough valueless test sats or verify the fresh plan's
vanilla-sat inventory before confirming. Never reuse a consumed plan after
adding capacity or sats. The commands below remain the auditable operator equivalent and
are still required for the separately confirmed capacity fallback. Do not run
the CLI and web server against the same Wallet A/B datadir at the same time.

## 0. Safety and process isolation

- Stop `web.server`, `scripts.rgb_wallet`, and other rgb-lib processes using
  the same role datadir.
- Keep `.env.a`, `.env.b`, `.note-vault/`, capsule files, and consignments out
  of Git and screenshots.
- Keep both TTP223 inputs released/LOW during boot/reset because GPIO12 is an
  ESP32 strapping pin.
- A read/indexer timeout may be retried. A send with unknown outcome may not.

```bash
ps -eo pid,args | rg 'web\.server|rgb_wallet|rgb_tap|note_wallet_live'
.venv/bin/python -m pytest -q
```

### Automated pre-funded fallback

For the live desk fallback, prepare and settle the disposable $5 note before
the judging window. Keep both role web docks running; the fallback runner uses
their HTTP APIs so it never opens either rgb-lib wallet a second time. Its
accepted default direction matches the reciprocal evidence run: Wallet B and
TTGO issue, WEMOS carries the received note, and Wallet A becomes the
previously unknown redeemer.

First prove both registered bills are empty:

```bash
.venv/bin/python -m scripts.offline_bearer_fallback check
```

Then run one already-funded `ACTIVE` note. Give its ID explicitly whenever
more than one active note exists:

```bash
.venv/bin/python -m scripts.offline_bearer_fallback run \
  --note-id NOTE_ID
```

The runner loads the exact capsule, verifies the starting bill holds it, and
then prints a 12-second radio-quiet tap window. During that window press the
empty receiving bill once near the bill showing `NOTE`, then release. The
runner checks for the all-or-nothing transition (`source empty`, `destination
bearer-held`), extracts the capsule, verifies the SHA-256, and displays the
immutable redemption plan. It still requires the operator to type the exact
`CONFIRM <plan-id>` phrase. It never confirms, repeats a send, discards an
artifact, or weakens a recovery boundary automatically.

After broadcast it performs only import-only recovery when the dock explicitly
proves `broadcast=true` and `retry_send=false`, polls settlement read-only, and
finishes only after the note is `REDEEMED` and both physical bills report
`idle`, denomination zero, and `empty:empty`. A redacted mode-`0600` evidence
file is written under ignored `.note-vault/demo-runs/`; it contains no
confirmation phrase, mnemonic, claim secret, or capsule bytes.

To reverse the direction, swap the dock URLs. This does not change A/B into
sender/receiver authorization roles:

```bash
.venv/bin/python -m scripts.offline_bearer_fallback run \
  --issuer-url http://127.0.0.1:8788 \
  --redeemer-url http://127.0.0.1:8789 \
  --note-id NOTE_ID
```

The fallback intentionally starts from an already-funded note. Creating and
funding a fresh note during a three-minute presentation would make the demo
depend on two unpredictable Mutinynet confirmation windows. Use the QVAC web
flow before the presentation to create that note and retain its exact evidence.

### Archive a failed pre-broadcast test note

If preparation failed before any funding plan or send existed, the preserved
note shell may be explicitly archived so it cannot block the next demo. This
operation keeps its wallet directory and manifest, records `ABANDONED`, and
cannot run for an active, planned, funded, or ambiguous-send note. Stop the web
dock using that issuer profile first, then type the exact phrase requested by
the command:

```bash
.venv/bin/python -m scripts.note_wallet_live abandon-unbroadcast-note \
  --note-id NOTE_ID \
  --reason operator-test-reset
```

This is test-state cleanup, not transaction recovery. Never use it to dismiss
an unknown send outcome.

## 1. Prepare and fund a disposable note

Choose the issuing wallet only by who owns the value now. The final settlement
wallet is deliberately unknown. Load that wallet's isolated environment (A is
the reference example) and create a fresh no-broadcast plan:

```bash
set -a; source .env.a; set +a
.venv/bin/python -m scripts.note_wallet_live prepare-funding-plan \
  --asset-id "$RGB_ASSET_ID"
```

The CLI retains its low-level preflight output for diagnosis. The normal web
dock instead creates `fund-note-bitcoin-reserve`, binding the note address,
missing sat amount, source balances, fee rate, expiry, and a fresh plan ID. Its
exact confirmation broadcasts one `send_btc`; after confirmation the dock
supersedes the stale RGB plan and presents a new one.

Review the note ID, exact 5,000,000-unit amount, issuer balance, recipient,
fee, expiry, transport endpoints, and Bitcoin fee inventory. In a separate
operation, and only after explicit approval:

```bash
.venv/bin/python -m scripts.note_wallet_live confirm-funding \
  --note-id NOTE_ID \
  --plan-id PLAN_ID
```

In the web/QVAC flow, an `InsufficientAllocationSlots` rejection is not a
reason to repeat that confirmation. Ask QVAC **“check my note”**. The dock must
first prove that the RGB send remained pre-broadcast, then show a separate
immutable confirmation for one 8,000-sat source-wallet colorable UTXO. After
that Bitcoin transaction confirms, ask **“check my note”** again. The dock
archives the rejected plan and presents a fresh RGB funding plan with a new
confirmation phrase. Never reuse the consumed RGB confirmation.

Wait for confirmation and activate only after exact settlement proof:

```bash
.venv/bin/python -m scripts.note_wallet_live refresh-funding \
  --note-id NOTE_ID
```

Do not continue until `settled=true`, note state `ACTIVE`, transfer kind
`receive_witness`, and settled/future/spendable all equal 5,000,000.

## 2. Create and load the bearer capsule

```bash
.venv/bin/python -m scripts.note_wallet_live prepare-capsule \
  --note-id NOTE_ID

.venv/bin/python -m dock.bill_dock chunk-send \
  --address STARTING_BILL_ADDRESS \
  --kind bearer-note \
  --input .note-vault/notes/NOTE_ID/artifacts/bearer-note.rgbn
```

Record the capsule byte count and SHA-256 from redacted output. Do not print or
decode the claim secret. Inventory must become `bearer-held`; the onboard
display may show `$5.00 / NOTE` only after the complete file is durable.

## 3. Prove the offline physical handoff

Disconnect or gate the docks' network. Keep both laptop docks out of the tap
path. Place the bills close together, press the empty receiver once, and
release immediately. Expected receiver milestones:

```text
receiver armed for 15s
qualifying peer ... RSSI >= -60
receiving kind=4
bearer note promoted for offline relay
receive complete
```

The sender must report `sender complete`. Reboot the receiver and require a
boot line restoring the same kind/session/size. Repeat additional bill-to-bill
hops if desired; each hop moves the same bearer capability, not a new RGB
transition.

## 4. Extract and verify at the destination dock

After reconnecting the destination dock, pull the note from the final bill:

```bash
.venv/bin/python -m dock.bill_dock chunk-receive \
  --address DESTINATION_BILL_ADDRESS \
  --kind bearer-note \
  --output .note-vault/incoming/received.rgbn

sha256sum .note-vault/incoming/received.rgbn
```

The full digest must equal the source digest before the capsule is parsed or a
destination-wallet invoice is created. An interrupted extraction does not consume the
bill copy; retry extraction, not funding.

## 5. Choose the settlement wallet and prepare its redemption plan

Only the current holder chooses this wallet. It need not be known to the issuer
or any intermediary. Stop any web process using its datadir, then source that
wallet's profile (B is the reference example):

```bash
set -a; source .env.b; set +a
.venv/bin/python -m scripts.note_wallet_live prepare-redemption-plan \
  --capsule .note-vault/incoming/received.rgbn \
  --expected-sha256 EXPECTED_CAPSULE_SHA256
```

This creates the selected wallet's fresh transport-free invoice only after capsule and
vault validation. Review note ID, recipient, invoice hash, amount, fee, expiry,
and empty endpoints.

If the note lacks a free colorable UTXO, do not reuse the redemption
confirmation. Reconcile the known pre-broadcast rejection, then prepare,
confirm, and settle a separate capacity transaction:

```bash
.venv/bin/python -m scripts.note_wallet_live reconcile-redemption \
  --note-id NOTE_ID
.venv/bin/python -m scripts.note_wallet_live prepare-redemption-capacity \
  --note-id NOTE_ID
.venv/bin/python -m scripts.note_wallet_live confirm-redemption-capacity \
  --note-id NOTE_ID --plan-id CAPACITY_PLAN_ID
.venv/bin/python -m scripts.note_wallet_live refresh-redemption-capacity \
  --note-id NOTE_ID
.venv/bin/python -m scripts.note_wallet_live prepare-redemption-after-capacity \
  --capsule .note-vault/incoming/received.rgbn \
  --expected-sha256 EXPECTED_CAPSULE_SHA256
```

Before confirming that fresh redemption plan, inspect its recorded vanilla
sat inventory. The accepted watch run demonstrated that a 2,000-sat initial
reserve can leave only 691 sats after capacity creation, which is insufficient
for the final send. Add only Mutinynet test sats if necessary, refresh, and
prepare another fresh immutable plan; do not extend or reuse the earlier one.

## 6. Confirm once, import, and settle

Only after reviewing the fresh plan and obtaining explicit authorization:

```bash
.venv/bin/python -m scripts.note_wallet_live confirm-redemption \
  --note-id NOTE_ID \
  --plan-id REDEMPTION_PLAN_ID
```

This consumes the confirmation before the send, records the broadcast and
exact consignment, and imports that retained consignment into the selected wallet. If
import alone failed after a recorded broadcast, use `recover-redemption`; do
not confirm or send again.

```bash
.venv/bin/python -m scripts.note_wallet_live recover-redemption \
  --note-id NOTE_ID
```

Wait for one Mutinynet confirmation and run the read-only settlement gate:

```bash
.venv/bin/python -m scripts.note_wallet_live refresh-redemption \
  --note-id NOTE_ID
```

Acceptance requires exactly one matching txid, `receive_witness`, one
5,000,000-unit assignment, and the selected wallet's settled/future/spendable balance at
the recorded pre-balance plus 5,000,000. Terminal state must be `REDEEMED`
with claim `SPENT`. Repeating this command returns the recorded evidence
without reopening the wallet.

## 7. Demo closeout and evidence

- Show both RGB txids, not only the Bitcoin capacity txid.
- Show source/extracted capsule hashes and redemption consignment hash.
- State plainly: the offline segment relayed a bearer capability; settlement
  required the vault and Mutinynet later.
- State plainly: the tap moved the whole denomination, intermediaries needed no
  dock or account, and the issuer did not select the final wallet.
- Do not claim strict anonymity or fungibility: the trusted vault can correlate
  issuance/redemption, BLE identities are observable nearby, and copied
  capsules race under first-redemption-wins.
- State the trusted-vault, copy, ESP32 extraction, QVAC, and WDK limitations.
- Never run another live spend merely to improve a screenshot.

The accepted reference values are in [`LIVE-EVIDENCE.md`](LIVE-EVIDENCE.md),
and recovery rationale is in [`THREAT-MODEL.md`](THREAT-MODEL.md).
