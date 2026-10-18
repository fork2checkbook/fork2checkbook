import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE = PROJECT_ROOT / "site"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_site_describes_the_verified_bearer_flow_honestly():
    html = _read(SITE / "index.html")
    javascript = _read(SITE / "app.js")

    assert len(re.findall(r'\bdata-step="\d+"', html)) == 6
    assert 'id="flow-counter">01 / 06' in html
    assert "first-redemption-wins" in html
    assert "trusted vault" in html.lower()
    assert "QVAC" in html
    assert "WDK" in html
    assert "ILI9341 color display" not in html
    assert "8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f" in html
    assert "65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5" in html
    assert "Waveshare ESP32-S3" in html
    assert "Wallet A 50 → 55 USDT" in javascript
    assert "String(steps.length).padStart" in javascript


def test_public_site_lists_every_block_verified_transaction():
    html = _read(SITE / "index.html")
    evidence = _read(PROJECT_ROOT / "docs" / "LIVE-EVIDENCE.md")
    artifact = _read(
        PROJECT_ROOT
        / "docs"
        / "evidence-artifacts"
        / "transacionEvicende.md"
    )
    transactions = {
        "9d26e9f5130fae0dc1373e8e21f1456566147d84df3ff1e0ccc071f7c717aac4": "3514180",
        "8bfa3219a931dc1d3017183698465fb7e15a74b7c688eb8af6350351c437d46f": "3514181",
        "8301d3893e1c1f6a7697304fd7c65365c527284dc8f9443bf5cbb7904b952b02": "3514215",
        "65b4a62bf3d55af3e8968ea4bbb2a9b257725c5c945de715f94a35d87795c6d5": "3514231",
        "b3427bbbe07dcf25c27d7871f2ecc1a8db9dff8cd0a818d831b455b28980294b": "3513524",
        "4e5c6c9401a0a1b7d778b61215ea70cb81a42619a528a597cdef27e51d70494d": "3513528",
        "3d9dfb341d129c9debf56e0b36f6a266fa4bdcfaceb2818b5c34a442850e6557": "3513614",
        "83fa1a8b1eb61c6bbd7509d723f9cd02a28b5f848017c8dbbe6f1eee0d2506c9": "3513623",
        "b325cbe40c282e661c40ff8555cb6cb0fc9867a32855feb8b17d7756c9a83963": "3508184",
        "089a8231b4c8916d9b1aac3cfd445b11dc9ec87583f15cd8eeec072b4f0734bb": "3508518",
        "dfe138b0983f6238103d2e5c57bf0848fdd5b7fd733162299fb4c6e5e9f427bf": "3508531",
        "72b6d753eaf788032cdd72ebd9e0c831380fc4e4c97ef5d66f538e62c5e460de": "3495492",
        "97885ddffc2305aabf1574d78be8cf102d0afb1f104529c817fc4dcff541a1fb": "3498861",
        "109d2d24c572d0b74fbd50c1dd2911b3030f0fc963f6018916397d9891b650e3": "3506146",
        "1f8953866f24f5b64ec02875aa51507fc6c76777646680eddfb573247cf5d45f": "3507561",
        "921614229c1420fa75bf4f2f5aeb3a925b705a1b4a99f4797b5218fc121c3f85": "3495147",
    }

    assert 'class="evidence-table"' in html
    assert "Every accepted transaction, verified in a real block." in html
    assert "not additional RGB value transfers" in evidence
    for txid, height in transactions.items():
        assert html.count(txid) == 2
        assert txid in evidence
        assert txid in artifact
        assert f">{int(height):,}<" in html
        assert f"`{height}`" in evidence
        assert f"[`{height}`]" in artifact

    assert "Transactions checked: 16" in artifact
    assert "16 confirmed, 0 unconfirmed, 0 missing" in artifact


def test_public_site_lists_every_documentation_diagram():
    html = _read(SITE / "index.html")
    javascript = _read(SITE / "app.js")
    builder = _read(PROJECT_ROOT / "scripts" / "build_site.py")

    for number, name in (
        ("01", "system-architecture"),
        ("02", "component-ownership"),
        ("03", "rgb-transfer-sequence"),
        ("04", "bill-state-machine"),
        ("05", "gatt-contract"),
        ("06", "chunk-frame"),
        ("07", "bill-hardware"),
        ("08", "trust-boundaries"),
        ("09", "live-evidence-timeline"),
        ("10", "note-vault-state"),
    ):
        stem = f"{number}-{name}"
        assert f'value="{stem}"' in html
        assert f'"{stem}"' in javascript
        assert f'"{stem}"' in builder
        assert (PROJECT_ROOT / "docs" / "diagrams" / "src" / f"{stem}.mmd").is_file()


def test_canonical_docs_link_to_evidence_runbook_and_threat_model():
    readme = _read(PROJECT_ROOT / "README.md")
    design = _read(PROJECT_ROOT / "docs" / "DESIGN.md")
    protocol = _read(PROJECT_ROOT / "docs" / "PROTOCOL.md")

    for document in (
        "docs/LIVE-EVIDENCE.md",
        "docs/OFFLINE-BEARER-RUNBOOK.md",
        "docs/THREAT-MODEL.md",
    ):
        assert document in readme
    assert "exactly two RGB transitions" in design
    assert "Primary procedure: carry value offline" in protocol
