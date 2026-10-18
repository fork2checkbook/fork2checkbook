# fork2checkbook

**fork2checkbook** turns RGB value into a physical, offline handoff. A dock
funds a disposable $5 RGB note, an ESP32 bill
carries its compact bearer capability, and one tap relays that capability to
another bill with both docks and the Internet unavailable. The final holder
later redeems into a wallet that was unknown when the note was created.
Bitcoin Mutinynet anchors the two real RGB transitions. No QR and no phone app
are required for the offline handoff.

The project was previously developed under the working name **RGB Cash Bill**.
The frozen BLE device name `RgbBill`, UUIDs, source paths, and historical
evidence retain that technical name for compatibility.

## Project goal

**fork2checkbook** explores how to recover a useful property of a paper
checkbook in digital form: a person can prepare portable value at their own
dock, walk away, hand it to another person without either party being online,
and let the eventual holder redeem it later at a different dock. The recipient
wallet does not have to be known when the value is created, and intermediate
carriers do not need a wallet, account, dock, or network connection.

The design aims for minimal identity disclosure and practical fungibility:
equal-denomination notes should be used in the same way regardless of their
holders or route. These are goals, not claims of perfect cryptographic
anonymity or fungibility. Bitcoin and RGB settlement history may still be
correlated. This hackathon prototype also deliberately accepts and documents
risks that a production system must solve: copied capsules and double-spend
races, first-redemption-wins behavior, loss or theft of a bearer device,
trusted online custody of disposable note keys, delayed settlement, and
denial-of-service. It runs on Mutinynet and must not be treated as real money.

## Team

**Team name:** fork2checkbook

- **Edwin Kestler** — [@EdwinKestler](https://github.com/EdwinKestler) —
  project owner and lead.
- **Anonymous0304** —
  [@creatorofsomethingthatisgood](https://github.com/creatorofsomethingthatisgood)
  — collaborator; submitted the visual design for the public project page
  and the Python dock frontend.

## Hackathon delivery mode

The organizers authorized final-project work on 2026-10-15; this repository is
in active delivery, not planning-only or waiting for Saturday. The submission
must remain transparent about prior work and must be publicly visible to the
judges. Verify repository visibility without credentials before submission;
publishing the website alone does not make a private source repository public.

Official scoring is RGB 25%, local QVAC 20%, execution 20%, UX/UI 15%,
innovation 10%, and marketability 10%. A meaningful WDK integration can add up
to one bonus point. The priority is therefore a clear live RGB + QVAC flow;
WDK follows only if that primary path is stable.

### Declared pre-start baseline

The existing Git history is intentional prior-work evidence. Before the final
delivery phase, the repository already contained:

- two ESP32 bill profiles with OLED, touch, BLE inventory, chunking, and tap;
- two isolated rgb-lib Mutinynet wallets and confirmed witness/OOB transfers;
- a confirmation-gated Python web dock with retained-artifact recovery;
- a prepared project website and local QVAC installation/smoke tooling;
- visual designs for the project website and Python dock frontend submitted
  by Anonymous0304 and subsequently integrated into this repository.

QVAC was not yet the accepted user-facing intent layer, WDK was not integrated,
and anonymous multi-hop offline bearer circulation was not implemented. Final
submission documentation must keep implemented, trusted prototype, and future
work clearly separated.

### Current verified deliverable

The bearer-note vertical slice is now live accepted on Mutinynet:

1. Wallet A funded a fresh isolated note wallet N with exactly 5 USDT.
2. Dock A loaded a 603-byte bearer capsule onto WEMOS.
3. WEMOS relayed it to TTGO over local BLE; TTGO persisted it across restart.
4. The destination dock extracted the same SHA-256 and only then created
   Wallet B's fresh transport-free redemption invoice.
5. One exact confirmation authorized one note-wallet redemption broadcast.
6. Wallet B imported the 15,057-byte consignment and settled from 50 to 55
   USDT. The note state is durably `REDEEMED`; its bearer claim is `SPENT`.

The offline hop moves a **bearer capability**, not an RGB state transition.
The two actual RGB transitions are Wallet A → note wallet N and note wallet N
→ Wallet B. The trusted note vault retains N's keys, so this is explicitly a
Mutinynet trusted-custody prototype with first-redemption-wins copy risk—not a
production bearer instrument or an offline double-spend solution.

The Waveshare watch port is also physically accepted in the live carry path.
A fresh settled 603-byte $5 capsule crossed TTGO → Waveshare watch → WEMOS and
was extracted byte-for-byte at Dock A. The retained note then completed its
separately confirmed RGB redemption into the previously unknown Wallet A:
transaction
[`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`](https://mutinynet.com/tx/65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5)
settled one 5,000,000-unit assignment and moved Wallet A from 50 to 55 USDT.
The note is terminal `REDEEMED`, its claim is `SPENT`, and the physical devices
finished empty.

Wallet A and Wallet B are the accepted reference run, not permanent sender and
recipient roles. In the current bearer protocol either isolated dock wallet may
issue a note it funds, every tap moves the note's complete value, and the
eventual holder may redeem at any compatible dock wallet. The final wallet is
bound only in the separately confirmed redemption plan.

This folder is a **git project** nested under the Agentic Dollars workspace
(`rgbhackathon/ideas/bill`). Lookup uses that workspace’s Palimnex — **not** a
second silo. See [docs/LOOKUP.md](docs/LOOKUP.md).

## Humans

The public project website is deployed at
<https://fork2checkbook.github.io/fork2checkbook/>. Source, local preview, and deployment
instructions are in [site/README.md](site/README.md).

1. Open **this folder** in VS Code (`File → Open Folder` on `ideas/bill`).
2. Install the **PlatformIO IDE** extension (prompted via `.vscode/extensions.json`).
3. From a terminal in this folder:

```bash
./install_python.sh
./install_qvac.sh                       # fat linux-x64 QVAC wheel; no GGUF
./install_platformio.sh
.venv-platformio/bin/pio run
.venv/bin/python -m dock.bill_dock scan
.venv/bin/python -m dock.bill_dock identity
.venv/bin/python -m dock.bill_dock state
.venv/bin/python -m dock.bill_dock diagnostic
.venv/bin/python -m dock.bill_dock chunk-loopback --address ADDRESS --kind invoice --input PATH
.venv/bin/python -m web.server          # role host/port from .env
.venv/bin/python scripts/qvac_smoke.py  # worker + GPU flags, no model download
.venv/bin/python scripts/qvac_fetch_instruct.py  # QWEN3_600M_INST_Q4 → .qvac
```

Copy `.env.example` to `.env` for dock roles. Mutinynet only.

Design: [docs/DESIGN.md](docs/DESIGN.md). Diagrams:
[docs/diagrams/README.md](docs/diagrams/README.md). Folders:
[docs/FOLDER.md](docs/FOLDER.md). Two-board tap acceptance:
[docs/TAP-GATT-TEST.md](docs/TAP-GATT-TEST.md).
Real wallet tap acceptance: [docs/RGB-TAP-LIVE.md](docs/RGB-TAP-LIVE.md).
The pre-funded operator fallback is automated by
[`scripts/offline_bearer_fallback.py`](scripts/offline_bearer_fallback.py): it
waits in radio-quiet windows for the physical tap, preserves the exact human
redemption confirmation, verifies final settlement, and requires both bills
to finish empty.
Web RGB acceptance: [docs/WEB-RGB-TEST.md](docs/WEB-RGB-TEST.md).
Offline bearer-note decision and proof plan:
[docs/OFFLINE-RGB-DEMO-DESIGN.md](docs/OFFLINE-RGB-DEMO-DESIGN.md).
Accepted bearer-note operator sequence:
[docs/OFFLINE-BEARER-RUNBOOK.md](docs/OFFLINE-BEARER-RUNBOOK.md).
Planned T-Watch 2020 V1 bill-app port:
[docs/TTGO-WATCH-APP-PLAN.md](docs/TTGO-WATCH-APP-PLAN.md).
Canonical transaction and acceptance ledger:
[docs/LIVE-EVIDENCE.md](docs/LIVE-EVIDENCE.md). Threat model:
[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).
Agent rules: [AGENTS.md](./AGENTS.md).

## Agents

Paste [docs/agents-prompt.md](docs/agents-prompt.md). For OpenAI 5.6 bring-up
(test env + Palimnex): [docs/agents-prompt-openai-56.md](docs/agents-prompt-openai-56.md).
For the Sunday offline bearer-note build, use
[docs/agents-prompt-offline-note.md](docs/agents-prompt-offline-note.md).
Search Palimnex from `/media/kestl/andor/github/rgbhackathon`. Skills under
`.agents/skills/`.

## Stack

| Layer | Tech |
| --- | --- |
| Bill radio | WEMOS LOLIN32 / ESP32-WROOM-32, Arduino, PlatformIO `esp32-bill` |
| Wearable bill | Waveshare ESP32-S3-Touch-AMOLED-2.06; accepted always-on clock, AMOLED bill UI, fail-closed storage, dock GATT, and touchscreen one-shot TapGatt. LilyGO T-Watch 2020 V1 remains a read-only reference/future port |
| Dock radio | Python 3.10+, Bleak 3.0.2 (`dock/`) |
| Dock desk | Python HTTP + static UI (`web/`, :8788). Rust sidecar optional (`web/rust/`) |
| Bearer note vault | One ignored rgb-lib datadir and mnemonic per disposable note; one-shot recovery journal |
| Settlement | rgb-lib 0.3.0-beta.7, witness + out-of-band consignment, Mutinynet |
| Voice | QVAC `tetherto-qvac-sdk==0.21.0` + `QWEN3_600M_INST_Q4` in `.qvac` |
| BLE cookbook | `/media/kestl/andor/github/waveshares3` (patterns only) |
| Watch hardware/app reference | `/media/kestl/xwing/Old_documents/Documents/PlatformIO/Projects/My-TTGO-Watch` at inspected commit `a7eb49ea` (read-only reference) |

Firmware boots at `EMPTY / BLE READY` with no numeric amount and advertises frozen dock-core v1 plus
implemented chunk-v1 GATT. The read-only state command reports
identity/status/denomination; the fixed display diagnostic and byte-exact
chunk loopback do not touch a wallet. Tap GATT v1 reuses the opaque chunk
frames with deterministic sender/receiver roles, a rising-edge one-shot touch,
and a −60 dBm RSSI gate. Bidirectional two-board transport was physically
accepted on 2026-10-11. The isolated wallet-to-BLE bridge was then live
accepted with a confirmed 20 USDT Mutinynet transfer in both wallet directions:
invoice B→A and exact out-of-band consignment A→B. Web-desk integration is
implemented. The human-first inventory-v2 browser flow was physically accepted
on both boards on 2026-10-15 with a settled $5 Mutinynet transfer
(`1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f`).

The web confirmation path now uses that accepted adapter. It exposes the
immutable RGB plan, requires the typed exact phrase, records the broadcast
before BLE staging, and retains exact artifacts for stage/import-only recovery.
Both role servers pass live startup, LAN/localhost binding, balance refresh,
and browser-driven physical transfer acceptance checks.

The newer offline bearer path is accepted at the operator CLI and firmware
layers, and is now integrated into the local web docks as explicit **Load**,
**Pass**, **Redeem**, **Recover**, and **Refresh settlement** actions. The web
implementation preserves the same exact confirmation and no-resend recovery
boundaries. It has now passed live browser/API acceptance in both directions,
including the Waveshare watch carry path. The latest accepted run used funding
txid
`8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f`
and redemption txid
`65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5`;
Wallet A finished with 55,000,000 settled/future/spendable units. Local QVAC now maps
the primary “Prepare a $5 note” request into the guarded bearer-funding plan;
the deterministic dock now replaces manual note-faucet funding with a separate
immutable Bitcoin top-up from the issuing wallet, while retaining distinct
exact confirmations for the BTC and RGB broadcasts, settlement proof, and
hardware-load gates. The live watch run exposed a reserve-sizing gap: the
automatic 2,000-sat note reserve was insufficient after creating redemption
capacity, so the operator added Mutinynet sats before the fresh redemption
plan. Automating that larger reserve and the fixed adversarial QVAC intent
evaluation remain open. WDK is not integrated.
