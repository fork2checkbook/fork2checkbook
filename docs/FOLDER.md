# Folder structure

```
ideas/bill/                 this project (git + PlatformIO root)
  README.md                 humans
  site/                     public GitHub Pages project presentation
  scripts/build_site.py     explicit public asset packaging into .site-build/
  .github/workflows/pages.yml  static website deployment
  AGENTS.md                 agents: rules, Palimnex parent silo, build order
  platformio.ini            VS Code / PIO firmware
  install_python.sh         .venv (Bleak dock)
  install_qvac.sh           fat linux-x64 tetherto-qvac-sdk into .venv
  install_platformio.sh     .venv-platformio (pio)
  requirements.txt          dock runtime (Bleak)
  requirements-rgb.txt      pinned optional rgb-lib 0.3.0b7 native wheel
  requirements-qvac.txt     tetherto-qvac-sdk==0.21.0 (needs GitHub -f)
  requirements-dev.txt      pytest / ruff
  requirements-platformio.txt
  .env.example              Mutinynet paths, BLE name, bill role, QVAC_CACHE_DIR
  scripts/qvac_smoke.py     worker heartbeat + GPU flags; no GGUF
  scripts/qvac_fetch_instruct.py  QWEN3_600M_INST_Q4 into .qvac + one completion
  scripts/rgb_tap.py        real invoice/consignment wallet-to-BLE operator flow
  scripts/note_wallet_live.py  disposable-note funding/redemption gates + refresh
  scripts/offline_bearer_fallback.py  pre-funded two-dock demo + tap/settlement wait
  .vscode/extensions.json   PlatformIO IDE + Python
  include/config.h          pins + frozen dock UUIDs
  src/main.cpp              firmware entry (composition only)
  src/oled_test.cpp         standalone OLED diagnostic entry
  src/ttgo_lora32_test.cpp  TTGO V1.3 display + BLE diagnostic entry
  src/ttgo_oled_reference.cpp  LilyGO ThingPulse OLED-only comparison
  lib/BillDisplay/          shared SSD1306 driver for main + OLED test
  lib/TftBillDisplay/       legacy TTGO external ILI9341 rollback driver
  lib/ChunkProtocol/        portable frame codec + receive-session FSM
  lib/DockGatt/             BLE peripheral + SPIFFS staging + ACK sender
  lib/                      BillDisplay and implemented dock transport; TapGatt next
  test/                     native PlatformIO FSM tests
  pytests/                  Python tests for the web dock
  dock/                     Bleak central + dock core + chunk sender/receiver
  web/                      Python web dock (HTTP + live isolated RGB tap flow)
    server.py               role-specific loopback HTTP + safety state machine
    device_registry.py      strict BLE picker + one role-scoped saved bill
    rgb_web.py              long-lived role wallet + retained-artifact recovery
    static/                 bill picker, denomination, chat, confirmation gate
    rgb/                    isolated rgb-lib-python adapter + config check
    rust/                   optional native sidecar (not weekend path)
  docs/
    DESIGN.md               product + radio + RGB mapping
    PROTOCOL.md             human-first inventory + chunk/tap contract
    FOLDER.md               this file
    LOOKUP.md               Palimnex: parent rgbhackathon, no new silo
    agents-prompt.md        paste for other coding agents
    agents-prompt-openai-56.md  OpenAI 5.6: test env + Palimnex bring-up
    TTGO-LORA32-V1.3-TEST.md    second bill hardware test protocol
    WAVESHARE-S3-BASIC-TEST.md  accepted AMOLED/watch transport and live relay
    RGB-TAP-LIVE.md         controlled real-wallet tap acceptance and recovery
    WEB-RGB-TEST.md         browser-driven two-dock acceptance and API contract
    OFFLINE-RGB-DEMO-DESIGN.md  selected trusted-vault architecture and gaps
    OFFLINE-BEARER-RUNBOOK.md  exact accepted operator procedure and recovery
    TTGO-WATCH-APP-PLAN.md  inspected watch reference and phased port gates
    LIVE-EVIDENCE.md        canonical public txids, hashes, balances, status
    THREAT-MODEL.md         prototype trust boundaries and failure policy
    diagrams/               editable Mermaid, rendered PNGs, render instructions
  .agents/skills/
    source-cache/           Palimnex from rgbhackathon root
    bill-firmware/          PlatformIO / ESP32
    bill-dock/              Bleak dock client
```

Lookup layer is the parent hackathon Palimnex (`/media/kestl/andor/github/rgbhackathon`). This folder is extra corpus, not a second project_id.

External read-only implementation references:

- `/media/kestl/andor/github/waveshares3`: BLE session patterns only.
- `/media/kestl/xwing/Old_documents/Documents/PlatformIO/Projects/My-TTGO-Watch`:
  T-Watch 2020 V1 hardware, LVGL app, touch, power, and sleep/wake patterns.
- `/media/kestl/andor/github/ESP32-S3-Touch-AMOLED-2.06`:
  Waveshare vendor examples and board documentation reference.
