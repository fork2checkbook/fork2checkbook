# Public fork2checkbook website

Static project presentation for GitHub Pages. The local wallet desk remains in
`web/`; this website explains the project and performs no wallet or BLE operations.

The public-page and Python dock visual designs were submitted by
[Anonymous0304](https://github.com/creatorofsomethingthatisgood).

## Preview

From the repository root:

```bash
python3 scripts/build_site.py
python3 -m http.server 8790 --bind 127.0.0.1 --directory .site-build
```

Open <http://127.0.0.1:8790/>. The build copies an explicit list of HTML,
CSS, JavaScript, the favicon, and all ten documentation PNGs. It never copies
the repository root, local environments, wallet datadirs, or model caches.
Relative asset URLs support the GitHub Pages `/fork2checkbook/` project path.

The site uses optional Google Fonts with system fallbacks. The six-step
explainer and diagram selector work without dependencies; the page remains
readable with JavaScript disabled. Illustrations are conceptual, rather than
photographs of the hardware or live wallet balances.

## Deployment

In GitHub repository Settings → Pages, choose **GitHub Actions** as the source.
The workflow at `.github/workflows/pages.yml` packages `.site-build/` and deploys
on relevant pushes to `main`, or through a manual workflow run. Expected URL:
<https://fork2checkbook.github.io/fork2checkbook/>.

The repository and Pages configuration are public. Deployment uses only the
explicit `.site-build/` allowlist produced by `scripts/build_site.py`; it never
uploads the repository root, local environments, wallet data, or model caches.
The deploy job runs after every successful site build without a separate
repository-variable gate.

Workflow reference:
[GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Content evidence

- `docs/DESIGN.md` and `docs/PROTOCOL.md`: architecture, hardware, radio behavior.
- `docs/RGB-TAP-LIVE.md`: accepted 20 USDT test transfer on 2026-10-11.
- `docs/WEB-RGB-TEST.md`: accepted browser-driven physical transfer.
- `docs/LIVE-EVIDENCE.md`: accepted bearer-note txids, hashes, balances, and
  explicit proof boundaries.
- `docs/OFFLINE-BEARER-RUNBOOK.md`: exact operator and recovery sequence.
- `docs/WAVESHARE-S3-BASIC-TEST.md`: accepted AMOLED, storage, GATT,
  touchscreen relay, and live funded-capsule evidence.
- `docs/TTGO-WATCH-APP-PLAN.md`: retained LilyGO reference/future-port plan.

Keep the status copy aligned with those records. The browser-driven physical
transfer, human-first bearer web flow, and the Waveshare-mediated offline
round trip are accepted. Local QVAC is connected to deterministic,
confirmation-gated actions; the fixed adversarial intent evaluation remains
open. WDK is not integrated.
