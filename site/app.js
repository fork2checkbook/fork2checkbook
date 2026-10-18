"use strict";

const steps = [
  {
    source: "5 USDT",
    sourceLabel: "ISSUING WALLET",
    channel: "RGB FUNDING",
    destinationLabel: "NOTE WALLET N",
    destination: "ACTIVE",
    tag: "THE FIRST REAL RGB TRANSITION",
    title: "Create one disposable funded note.",
    description:
      "The issuing wallet sends exactly 5 USDT to an isolated one-use rgb-lib wallet. After Mutinynet confirms the transfer, the note wallet holds the allocation that backs the bearer capability; no final wallet is named.",
    detail: "Funding tx · 8bfa3219…d46f · 5,000,000 units",
  },
  {
    source: "CAPSULE",
    sourceLabel: "NOTE VAULT",
    channel: "GATT DOCK",
    destinationLabel: "BILL A",
    destination: "$5 NOTE",
    tag: "NO WALLET KEYS ON THE ESP32",
    title: "Load a canonical bearer capsule.",
    description:
      "The trusted note vault emits a compact capability that identifies the funded note and its one-shot redemption authority. The dock verifies and loads the exact artifact onto the starting bill.",
    detail: "Accepted live capsule · 603 bytes · SHA-256 159805d2…310c",
  },
  {
    source: "$5 NOTE",
    sourceLabel: "BILL A",
    channel: "BLE · TTGO → WATCH → WEMOS",
    destinationLabel: "BILL B",
    destination: "$5 NOTE",
    tag: "DOCKS AND INTERNET ARE OFF",
    title: "Physical possession moves offline.",
    description:
      "Press the empty bill once near the current holder. A 15-second high-RSSI window transfers artifact kind 0x04 in acknowledged chunks, promotes it for relay, and restores it after reboot.",
    detail: "Accepted live relay · session 0x220902c8 · 4 frames per send",
  },
  {
    source: "CAPSULE",
    sourceLabel: "BILL B",
    channel: "GATT DOCK",
    destinationLabel: "CHOSEN WALLET",
    destination: "INVOICE",
    tag: "THE FINAL WALLET IS CHOSEN NOW",
    title: "Extract exact bytes before planning.",
    description:
      "The destination dock extracts the capsule, verifies its length and SHA-256, then asks the holder's chosen wallet for a fresh transport-free invoice. The issuer did not know that wallet during funding or offline relay.",
    detail: "Exact extraction · transport_endpoints = []",
  },
  {
    source: "PLAN",
    sourceLabel: "NOTE WALLET N",
    channel: "RGB REDEMPTION",
    destinationLabel: "MUTINYNET",
    destination: "BROADCAST",
    tag: "THE SECOND REAL RGB TRANSITION",
    title: "Confirm one immutable redemption.",
    description:
      "The vault prepares an immutable plan tied to the fresh invoice. A human enters its exact confirmation phrase once. The recovery journal records any ambiguous result and never auto-resends.",
    detail: "Plan 1810b78e…8a49 · first redemption wins",
  },
  {
    source: "RGB PROOF",
    sourceLabel: "NOTE WALLET N",
    channel: "OOB IMPORT",
    destinationLabel: "CHOSEN WALLET",
    destination: "REDEEMED",
    tag: "INDEPENDENT RGB VALIDATION",
    title: "Import the proof and settle.",
    description:
      "The chosen wallet imports the exact rgb-lib consignment and refreshes against Mutinynet. The note is durably marked REDEEMED and replay recovery returns the recorded result without reopening or broadcasting again.",
    detail: "15,150-byte consignment · Wallet A 50 → 55 USDT",
  },
];

const tabs = [...document.querySelectorAll("[data-step]")];
const panel = document.getElementById("flow-panel");
let selected = 0;
function showStep(index, focus = false) {
  selected = (index + steps.length) % steps.length;
  const step = steps[selected];
  tabs.forEach((tab, i) => {
    tab.setAttribute("aria-selected", String(i === selected));
    tab.tabIndex = i === selected ? 0 : -1;
  });
  panel.setAttribute("aria-labelledby", tabs[selected].id);
  const values = {
    "flow-counter": `${String(selected + 1).padStart(2, "0")} / ${String(steps.length).padStart(2, "0")}`,
    "flow-source": step.source,
    "flow-source-label": step.sourceLabel,
    "flow-channel": step.channel,
    "flow-destination-label": step.destinationLabel,
    "flow-destination": step.destination,
    "flow-tag": step.tag,
    "flow-subtitle": step.title,
    "flow-description": step.description,
    "flow-detail": step.detail,
  };
  for (const [id, value] of Object.entries(values))
    document.getElementById(id).textContent = value;
  document.getElementById("next-step").innerHTML =
    selected === steps.length - 1
      ? 'Start again <span aria-hidden="true">↺</span>'
      : 'Next step <span aria-hidden="true">→</span>';
  if (focus) tabs[selected].focus();
}
tabs.forEach((tab, index) => {
  tab.setAttribute(
    "aria-label",
    `${index + 1}. ${tab.querySelector("strong").textContent}`,
  );
  tab.addEventListener("click", () => showStep(index));
  tab.addEventListener("keydown", (event) => {
    const target = {
      ArrowDown: selected + 1,
      ArrowUp: selected - 1,
      ArrowRight: selected + 1,
      ArrowLeft: selected - 1,
      Home: 0,
      End: steps.length - 1,
    }[event.key];
    if (target !== undefined) {
      event.preventDefault();
      showStep(target, true);
    }
  });
});
document
  .getElementById("next-step")
  .addEventListener("click", () => showStep(selected + 1));

const descriptions = {
  "01-system-architecture":
    "System architecture — an issuing wallet funds a trusted one-use note vault, bills relay its capability offline, and a previously unknown wallet redeems later.",
  "02-component-ownership":
    "Component ownership — the actual firmware, BLE dock, note-vault, web, and documentation boundaries.",
  "03-rgb-transfer-sequence":
    "RGB transfer sequence — exactly two real transitions around a dockless, internet-free bearer relay.",
  "04-bill-state-machine":
    "Bill state machine — empty, bearer-held, armed, receiving, extraction, and fail-closed recovery states.",
  "05-gatt-contract":
    "GATT contract — inventory metadata v3, bearer artifact kind 0x04, and chunk control/data characteristics.",
  "06-chunk-frame":
    "Chunk frame — the acknowledged binary envelope used for invoices, consignments, and bearer capsules.",
  "07-bill-hardware":
    "Bill hardware — WEMOS and TTGO OLED profiles plus the Waveshare ESP32-S3 touchscreen watch.",
  "08-trust-boundaries":
    "Trust boundaries — the online note vault, copyable bearer capability, human confirmation, and exact-byte verification.",
  "09-live-evidence-timeline":
    "Live evidence timeline — funded note, watch-mediated offline relay, one-shot redemption, and Wallet A settlement.",
  "10-note-vault-state":
    "Note-vault state machine — funded, active, redemption planned, broadcast recorded, imported, and redeemed with recovery edges.",
};
document
  .getElementById("diagram-select")
  .addEventListener("change", (event) => {
    const name = event.target.value;
    if (!Object.hasOwn(descriptions, name)) return;
    const path = `assets/diagrams/${name}.png`;
    const image = document.getElementById("diagram-image");
    image.src = path;
    image.alt = descriptions[name];
    // Preserve each diagram's own aspect ratio after switching.
    image.removeAttribute("width");
    image.removeAttribute("height");
    document.getElementById("diagram-full").href = path;
    document.getElementById("diagram-caption").textContent = descriptions[name];
  });
