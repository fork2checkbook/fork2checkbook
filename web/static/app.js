const logEl = document.getElementById("log");
const form = document.getElementById("form");
const text = document.getElementById("text");
const dialog = document.getElementById("confirm");
const confirmNote = document.getElementById("confirm-note");
const confirmPlan = document.getElementById("confirm-plan");
const confirmationPhrase = document.getElementById("confirmation-phrase");
const confirmForm = document.getElementById("confirm-form");
const scanDevice = document.getElementById("scan-device");
const connectDevice = document.getElementById("connect-device");
const registerDevice = document.getElementById("register-device");
const deviceList = document.getElementById("device-list");
const deviceBadge = document.getElementById("device-badge");
const deviceSummary = document.getElementById("device-summary");
const deviceMessage = document.getElementById("device-message");
const sendUsdt = document.getElementById("send-usdt");
const receiveUsdt = document.getElementById("receive-usdt");
const loadNote = document.getElementById("load-note");
const passNote = document.getElementById("pass-note");
const redeemNote = document.getElementById("redeem-note");
const recoverNote = document.getElementById("recover-note");
const refreshNote = document.getElementById("refresh-note");
const workflowStep = document.getElementById("workflow-step");
const workflowMessage = document.getElementById("workflow-message");
const qvacForm = document.getElementById("qvac-form");
const qvacText = document.getElementById("qvac-text");
const qvacSend = document.getElementById("qvac-send");
const qvacStatus = document.getElementById("qvac-status");
const confirmTitle = document.getElementById("confirm-title");
const confirmError = document.getElementById("confirm-error");
const recoveryEl = document.getElementById("recovery");
const recoveryNote = document.getElementById("recovery-note");
const retryStage = document.getElementById("retry-stage");
const walletHeading = document.getElementById("wallet-heading");
const walletNetwork = document.getElementById("wallet-network");
const walletRole = document.getElementById("wallet-role");
const walletFingerprint = document.getElementById("wallet-fingerprint");
const walletAsset = document.getElementById("wallet-asset");
const walletPurpose = document.getElementById("wallet-purpose");
const walletTarget = document.getElementById("wallet-target");
const walletConfiguration = document.getElementById("wallet-configuration");

let pendingId = null;
let selectedDevice = null;
let activeDevice = null;
let activeRole = null;
let busy = false;
let retryTarget = null;

function line(cls, msg) {
  const li = document.createElement("li");
  li.className = cls;
  li.textContent = msg;
  logEl.appendChild(li);
}

async function refresh() {
  try {
    const response = await fetch("/api/bill");
    const bill = await response.json();
    if (!bill || bill.role === undefined) throw new Error("bad bill reply");
    document.getElementById("denomination").textContent =
      "$" + (bill.cents / 100).toFixed(2);
    document.getElementById("status").textContent = bill.status;
    document.getElementById("meta").textContent =
      "role " + bill.role + " · " + bill.identity + " · " + bill.mode;
    renderDevice(bill.device);
    renderWallet(bill.wallet);
    renderRecovery(bill);
    const bearer = bill.bearer;
    workflowStep.textContent = bearer
      ? "Step " + bearer.step + " · " + (bearer.offline ? "offline" : "online")
      : "Ready · inspect card";
    const stepper = document.getElementById("workflow-stepper");
    if (stepper) stepper.dataset.step = bearer ? String(bearer.step) : "";
    activeRole = bill.role;
    const recoveryActive = Boolean(bill.recovery);
    const registered = Boolean(bill.device && bill.device.registered);
    sendUsdt.disabled =
      busy ||
      recoveryActive ||
      bill.role !== "A" ||
      !registered;
    receiveUsdt.disabled =
      busy ||
      recoveryActive ||
      bill.role !== "B" ||
      !registered;
    loadNote.disabled = busy || recoveryActive || !registered;
    passNote.disabled = busy || !registered;
    redeemNote.disabled = busy || recoveryActive || !registered;
    recoverNote.disabled =
      busy || !bill.bearer || !registered;
    refreshNote.disabled =
      busy || !bill.bearer || !registered;
    qvacSend.disabled = busy;
    document.getElementById("workflow-help").textContent =
      "An empty card may load this wallet's already-funded note. A held note may " +
      "pass its whole value offline or redeem into this dock wallet after exact confirmation.";
    if (!bill.pending && dialog.open && pendingId) {
      pendingId = null;
      confirmationPhrase.value = "";
      confirmError.textContent = "";
      dialog.close();
    }
    if (bill.pending && bill.pending.id !== pendingId && !dialog.open) {
      showConfirm(bill.pending);
    }
  } catch (error) {
    document.getElementById("status").textContent = "reconnecting…";
  }
}

function shortenedAsset(assetId) {
  if (!assetId || assetId.length <= 28) return assetId || "Not configured";
  return assetId.slice(0, 16) + "…" + assetId.slice(-10);
}

function renderWallet(wallet) {
  if (!wallet) {
    walletHeading.textContent = "Wallet identity unavailable";
    walletNetwork.textContent = "Unavailable";
    walletRole.textContent = "—";
    walletFingerprint.textContent = "—";
    walletAsset.textContent = "—";
    walletPurpose.textContent = "—";
    walletTarget.textContent = "—";
    walletConfiguration.textContent =
      "The dock has not published a safe wallet identity.";
    return;
  }
  walletHeading.textContent = wallet.label;
  walletNetwork.textContent = wallet.network;
  walletRole.textContent = "Wallet " + wallet.role;
  walletFingerprint.textContent = wallet.master_fingerprint || "Not configured";
  walletAsset.textContent = shortenedAsset(wallet.asset_id);
  walletAsset.title = wallet.asset_id || "";
  walletPurpose.textContent = wallet.purpose;
  walletTarget.textContent = wallet.bearer_target;
  walletConfiguration.textContent =
    "Configured by " + wallet.profile_setting + ". Restart required to switch.";
}

const RECOVERY_ACTIONS = {
  "retry-consignment-stage": {
    note: "Broadcast finished, but staging the consignment onto the bill failed. Keep the bill in place and retry.",
    endpoint: "/api/rgb/retry-stage",
  },
  "retry-consignment-import": {
    note: "A consignment arrived, but the import did not finish. Keep the bill in place and retry.",
    endpoint: "/api/rgb/import",
  },
  "retry-invoice-stage": {
    note: "The invoice was created, but staging it onto the bill failed. Retry only the BLE step.",
    endpoint: "/api/rgb/invoice",
    body: { cents: 500 },
  },
  "inspect-wallet-before-any-new-plan": {
    note: "The last send outcome is unknown. Inspect the wallet before starting a new transfer.",
    endpoint: null,
  },
  "retry-bearer-load": {
    note: "The funded capsule remains in the vault. Retry only loading that exact capsule onto the empty bill.",
    endpoint: "/api/bearer/load",
  },
  "retry-bearer-plan": {
    note: "The capsule was extracted and retained. Retry verification and plan creation; do not load or fund another note.",
    endpoint: "/api/bearer/redeem",
  },
  "recover-bearer-redemption": {
    note: "The confirmation was consumed. Recover only the recorded consignment import; never resend.",
    endpoint: "/api/bearer/recover",
  },
  "manual-bearer-reconciliation": {
    note: "The send outcome is ambiguous. Do not confirm again or create another redemption plan.",
    endpoint: null,
  },
  "inspect-note-funding": {
    note: "The note-funding record needs inspection. Ask QVAC ‘check my note’; never create or confirm a second funding send.",
    endpoint: null,
  },
};

function renderRecovery(bill) {
  const recovery = bill && bill.recovery;
  const action = recovery && recovery.action;
  const spec = action ? RECOVERY_ACTIONS[action] : null;
  if (!action || !spec) {
    recoveryEl.hidden = true;
    retryTarget = null;
    return;
  }
  const txid = recovery.txid
    ? " (txid " + String(recovery.txid).slice(0, 12) + "…)"
    : "";
  recoveryNote.textContent = spec.note + txid;
  retryTarget = spec.endpoint
    ? { endpoint: spec.endpoint, body: spec.body || {} }
    : null;
  retryStage.textContent = action.startsWith("retry-bearer-plan")
    ? "Retry plan"
    : action.startsWith("recover-bearer")
      ? "Recover import"
      : action.startsWith("retry-bearer-load")
        ? "Retry load"
        : "Retry BLE step";
  retryStage.hidden = !retryTarget;
  retryStage.disabled = busy || !retryTarget;
  recoveryEl.hidden = false;
}

function renderDevice(device) {
  activeDevice = device || null;
  const record = device && device.device;
  const connection = device && device.connection;
  const address = device && device.active_address;
  deviceBadge.className = "device-badge";
  if (record) {
    const verifiedRegistered =
      connection &&
      connection.connected &&
      connection.address === record.address;
    deviceBadge.textContent =
      verifiedRegistered ? "Verified" : "Registered";
    deviceBadge.classList.add("ready");
    deviceSummary.textContent =
      record.name + " · " + record.identity + " · " + record.address;
  } else if (address) {
    const verifiedConfigured =
      connection && connection.connected && connection.address === address;
    deviceBadge.textContent =
      verifiedConfigured ? "Verified" : "Configured";
    deviceBadge.classList.add("configured");
    deviceSummary.textContent =
      "Environment address " +
      address +
      " · scan and register it to save this selection.";
  } else {
    deviceBadge.textContent = "Not registered";
    deviceSummary.textContent =
      "Scan for a nearby RgbBill advertising the authorized dock service.";
  }
  connectDevice.disabled = !selectedDevice && !address;
}

function selectedAddress() {
  const checked = deviceList.querySelector(
    'input[name="device-address"]:checked',
  );
  return checked ? checked.value : null;
}

function renderCandidates(devices) {
  deviceList.replaceChildren();
  selectedDevice = null;
  registerDevice.disabled = true;
  const legend = document.createElement("legend");
  legend.textContent = "Authorized nearby bills";
  deviceList.appendChild(legend);
  if (!devices.length) {
    const empty = document.createElement("p");
    empty.className = "empty-device-list";
    empty.textContent = "No authorized RGBill advertisements found.";
    deviceList.appendChild(empty);
    renderDevice(activeDevice);
    return;
  }
  for (const device of devices) {
    const label = document.createElement("label");
    label.className = "device-option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "device-address";
    radio.value = device.address;
    if (device.registered) {
      radio.checked = true;
      selectedDevice = device.address;
      registerDevice.disabled = false;
    }
    radio.addEventListener("change", () => {
      selectedDevice = device.address;
      connectDevice.disabled = false;
      registerDevice.disabled = false;
      deviceMessage.textContent =
        "Selected " + device.name + ". Connect to verify or register it now.";
    });
    const description = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent =
      device.name + (device.registered ? " · registered" : "");
    const details = document.createElement("small");
    details.textContent =
      device.address + " · RSSI " + device.rssi + " dBm";
    description.append(title, details);
    label.append(radio, description);
    deviceList.appendChild(label);
  }
}

async function postDevice(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!data.ok) throw new Error(data.error || "Device operation failed.");
  return data;
}

scanDevice.addEventListener("click", async () => {
  scanDevice.disabled = true;
  deviceMessage.textContent = "Scanning for authorized RGBill devices…";
  try {
    const data = await postDevice("/api/device/scan", { timeout: 5 });
    renderCandidates(data.devices);
    deviceMessage.textContent = data.count
      ? "Found " +
        data.count +
        " authorized bill" +
        (data.count === 1 ? "." : "s.")
      : "No authorized bills found. Check power and keep the bill nearby.";
  } catch (error) {
    deviceMessage.textContent = error.message;
  } finally {
    scanDevice.disabled = false;
  }
});

connectDevice.addEventListener("click", async () => {
  connectDevice.disabled = true;
  deviceMessage.textContent = "Connecting for a read-only identity check…";
  try {
    const data = await postDevice("/api/device/connect", {
      address:
        selectedAddress() || (activeDevice && activeDevice.active_address),
    });
    const connection = data.connection;
    deviceMessage.textContent =
      "Verified " +
      connection.name +
      " · " +
      connection.identity +
      " · " +
      connection.address;
    if (connection.artifact) {
      const artifact = connection.artifact;
      const kind =
        artifact.kind === 1
          ? "invoice"
          : artifact.kind === 2
            ? "consignment"
            : artifact.kind === 4
              ? "bearer note"
              : "empty";
      deviceMessage.textContent +=
        " · " + artifact.direction + " " + kind +
        (artifact.denomination_cents
          ? " $" + (artifact.denomination_cents / 100).toFixed(2)
          : "");
    }
    renderDevice(data.device);
  } catch (error) {
    deviceMessage.textContent = error.message;
  } finally {
    connectDevice.disabled =
      !selectedDevice && !(activeDevice && activeDevice.active_address);
  }
});

async function runWorkflow(path) {
  if (busy) return;
  busy = true;
  sendUsdt.disabled = true;
  receiveUsdt.disabled = true;
  workflowMessage.textContent = "Inspecting the registered bill…";
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cents: 500 }),
    });
    const data = await response.json();
    workflowMessage.textContent = data.reply || data.error || "Step completed.";
    line("bot", workflowMessage.textContent);
    if (data.pending) showConfirm(data.pending);
  } catch (error) {
    workflowMessage.textContent = error.message;
  } finally {
    busy = false;
    await refresh();
  }
}

async function runBearer(path) {
  if (busy) return;
  busy = true;
  workflowMessage.textContent = "Reading the registered bill inventory first…";
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await response.json();
    const message = data.reply || data.error || "Bearer step completed.";
    workflowMessage.textContent = message;
    line("bot", message);
    if (data.pending) showConfirm(data.pending);
  } catch (error) {
    workflowMessage.textContent = error.message;
  } finally {
    busy = false;
    await refresh();
  }
}

sendUsdt.addEventListener("click", () => runWorkflow("/api/workflow/send"));
receiveUsdt.addEventListener("click", () => runWorkflow("/api/workflow/receive"));
loadNote.addEventListener("click", () => runBearer("/api/bearer/load"));
passNote.addEventListener("click", () => runBearer("/api/bearer/pass"));
redeemNote.addEventListener("click", () => runBearer("/api/bearer/redeem"));
recoverNote.addEventListener("click", () => runBearer("/api/bearer/recover"));
refreshNote.addEventListener("click", () => runBearer("/api/bearer/refresh"));

qvacForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = qvacText.value.trim();
  if (!value || busy) return;
  busy = true;
  qvacSend.disabled = true;
  qvacStatus.textContent = "QVAC is classifying locally…";
  line("you", value);
  try {
    const response = await fetch("/api/qvac", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: value }),
    });
    const data = await response.json();
    const message = data.reply || data.error || "QVAC request completed.";
    qvacStatus.textContent = data.qvac
      ? "Local " + data.qvac.model + " → " + data.qvac.intent +
        " · " + data.qvac.latency_ms + " ms"
      : message;
    workflowMessage.textContent = message;
    line("bot", message);
    if (data.pending) showConfirm(data.pending);
  } catch (error) {
    qvacStatus.textContent = error.message;
    workflowMessage.textContent = error.message;
  } finally {
    busy = false;
    await refresh();
  }
});

retryStage.addEventListener("click", async () => {
  if (!retryTarget || busy) return;
  busy = true;
  retryStage.disabled = true;
  workflowMessage.textContent = "Retrying the retained BLE step…";
  try {
    const response = await fetch(retryTarget.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(retryTarget.body),
    });
    const data = await response.json();
    const message = !data.ok
      ? data.error || "The BLE step still failed. Try again."
      : data.recovery_required
        ? "Still not finished; keep the bill in place and retry."
        : "Retry finished. The retained step is complete.";
    workflowMessage.textContent = message;
    line("bot", message);
  } catch (error) {
    workflowMessage.textContent = error.message;
  } finally {
    busy = false;
    await refresh();
  }
});

registerDevice.addEventListener("click", async () => {
  const address = selectedAddress();
  if (!address) return;
  registerDevice.disabled = true;
  deviceMessage.textContent = "Verifying and saving this bill…";
  try {
    const data = await postDevice("/api/device/register", { address });
    deviceMessage.textContent = data.reply;
    renderDevice(data.device);
    await refresh();
  } catch (error) {
    deviceMessage.textContent = error.message;
    registerDevice.disabled = false;
  }
});

function showConfirm(pending) {
  pendingId = pending.id;
  confirmTitle.textContent = "Confirm Mutinynet broadcast";
  confirmError.textContent = "";
  confirmNote.textContent =
    pending.note + " Type exactly: " + pending.confirmation_phrase;
  const capacity = pending.action === "bearer-funding-capacity";
  const btcTopup = pending.action === "bearer-note-btc-topup";
  const planLines = btcTopup
    ? [
        "operation: fund disposable note Bitcoin reserve",
        "amount: " + pending.topup_amount_sat + " sats",
        "note reserve before: " +
          pending.note_vanilla_spendable_before +
          " sats",
        "note reserve target: " + pending.target_note_vanilla_sat + " sats",
        "issuing wallet balance before: " +
          pending.source_vanilla_spendable_before +
          " sats",
        "destination: " + pending.destination_address,
        "fee: " + pending.fee_rate_sat_vb + " sat/vB",
        "$5 RGB note funding: not performed by this confirmation",
      ]
    : capacity
    ? [
        "operation: create RGB source-wallet capacity",
        "outputs: " + pending.utxo_count,
        "output size: " + pending.utxo_size_sat + " sats",
        "vanilla balance before: " + pending.vanilla_spendable_before + " sats",
        "free colorable UTXOs before: " +
          pending.free_colorable_utxo_count_before,
        "fee: " + pending.fee_rate_sat_vb + " sat/vB",
        "RGB note funding: not performed by this confirmation",
      ]
    : [
        "amount: $" + (pending.cents / 100).toFixed(2) + " USDT",
        "base units: " + pending.amount_base_units,
        "recipient: " + pending.recipient_id,
        "fee: " + pending.fee_rate_sat_vb + " sat/vB",
        "invoice sha256: " + pending.invoice_sha256,
        "transport endpoints: []",
      ];
  if (!capacity && !btcTopup && pending.balance_before_cents !== undefined) {
    planLines.splice(
      2,
      0,
      "balance before: $" +
        (pending.balance_before_cents / 100).toFixed(2),
    );
  }
  if (!capacity && !btcTopup && pending.capsule_sha256) {
    planLines.splice(2, 0, "capsule sha256: " + pending.capsule_sha256);
  }
  confirmPlan.textContent = planLines.join("\n");
  confirmationPhrase.value = "";
  confirmationPhrase.placeholder = pending.confirmation_phrase;
  dialog.showModal();
  confirmationPhrase.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = text.value.trim();
  if (!value) return;
  line("you", value);
  text.value = "";
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: value }),
  });
  const data = await res.json();
  line("bot", data.reply || data.error || "Request completed.");
  if (data.pending) showConfirm(data.pending);
  await refresh();
});

confirmForm.addEventListener("submit", async (event) => {
  const submitter = event.submitter;
  if (submitter && submitter.id === "approve" && pendingId) {
    event.preventDefault();
    const res = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: pendingId,
        phrase: confirmationPhrase.value,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      const failure = data.error || "Confirmation failed; nothing was sent.";
      confirmError.textContent = failure;
      line("bot", failure);
      confirmationPhrase.focus();
      confirmationPhrase.select();
      await refresh();
      return;
    }
    dialog.close();
    pendingId = null;
    confirmationPhrase.value = "";
    confirmError.textContent = "";
    line(
      "bot",
      data.ok && data.completed && data.receipt && data.receipt.action === "bearer-redemption"
        ? "Bearer redemption broadcast once and the exact consignment was imported into this dock wallet."
        : data.ok && data.completed && data.receipt && data.receipt.action === "bearer-funding"
          ? "The disposable note was funded once. After one confirmation, click Load note."
        : data.ok && data.completed && data.receipt && data.receipt.action === "bearer-funding-capacity"
          ? "Source-wallet capacity was created once. After one confirmation, ask QVAC ‘check my note’."
        : data.ok && data.completed && data.receipt && data.receipt.action === "bearer-note-btc-topup"
          ? "The dock funded the note’s Bitcoin reserve once. After one confirmation, ask QVAC ‘check my note’."
        : data.ok && data.completed
          ? "Broadcast complete and exact consignment staged over BLE."
        : data.ok && data.recovery_required
          ? "Broadcast complete. Consignment retained; use retry stage only."
          : data.error,
    );
    await refresh();
  }
});

dialog.addEventListener("close", async () => {
  if (dialog.returnValue === "cancel") {
    const cancelledId = pendingId;
    pendingId = null;
    confirmError.textContent = "";
    confirmationPhrase.value = "";
    if (cancelledId) {
      const response = await fetch("/api/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: cancelledId }),
      });
      const data = await response.json();
      line(
        "bot",
        data.ok
          ? "Transfer plan cancelled. Nothing was broadcast."
          : data.error || "The durable plan was not cancelled.",
      );
      await refresh();
    }
  }
});

refresh();
setInterval(refresh, 5000);
