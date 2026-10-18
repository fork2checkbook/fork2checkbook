# fork2checkbook diagrams

These diagrams are generated from the current design, GATT contract, and live
evidence. The direct invoice/consignment flow remains the fallback; the primary
views now show the accepted disposable-note funding, watch-mediated offline
bearer relay, and Wallet A redemption completed on 2026-10-18. A/B identify
the current evidence run; the bearer contract permits either isolated dock
profile to issue and any eventual holder's compatible dock wallet to redeem.

| Diagram | Purpose | Source |
| --- | --- | --- |
| [System architecture](01-system-architecture.png) | Two docks, two bills, custody, radio, and Mutinynet | [Mermaid](src/01-system-architecture.mmd) |
| [Component ownership](02-component-ownership.png) | Repository modules and their responsibilities | [Mermaid](src/02-component-ownership.mmd) |
| [RGB transfer sequence](03-rgb-transfer-sequence.png) | Two real RGB transitions around an offline bearer-capability relay | [Mermaid](src/03-rgb-transfer-sequence.mmd) |
| [Bill state machine](04-bill-state-machine.png) | Bearer-held, one-shot receive, extraction, and recovery states | [Mermaid](src/04-bill-state-machine.mmd) |
| [GATT contract](05-gatt-contract.png) | Frozen dock-core v1 and implemented tap service v1 | [Mermaid](src/05-gatt-contract.mmd) |
| [Chunk frame](06-chunk-frame.png) | Binary frame v1 byte layout and validation | [Mermaid](src/06-chunk-frame.mmd) |
| [Bill hardware](07-bill-hardware.png) | LOLIN32, TTGO, and Waveshare watch peripherals | [Mermaid](src/07-bill-hardware.mmd) |
| [Trust boundaries](08-trust-boundaries.png) | Secrets, confirmation, network, and OOB constraints | [Mermaid](src/08-trust-boundaries.mmd) |
| [Live evidence timeline](09-live-evidence-timeline.png) | Funding, physical relay, capacity, redemption, and settlement facts | [Mermaid](src/09-live-evidence-timeline.mmd) |
| [Note-vault state](10-note-vault-state.png) | One-shot plan, recovery, capacity, and terminal redemption states | [Mermaid](src/10-note-vault-state.mmd) |

## Status colors

- Green: live-verified or settled behavior.
- Blue: implemented local hardware/software behavior.
- Amber: specified but not yet integrated or implemented.
- Orange: accepted prototype trust or residual-risk boundary.
- Gray: external system or invariant boundary.

## Render

From the bill project root:

```bash
docs/diagrams/render.sh
```

The renderer pins `@mermaid-js/mermaid-cli@12.0.0`, uses the installed Google
Chrome binary, and regenerates every PNG from `src/*.mmd`. Generated PNGs are
documentation artifacts; edit the Mermaid sources, not the images.
