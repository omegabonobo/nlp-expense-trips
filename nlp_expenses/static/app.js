const state = JSON.parse(document.getElementById("initial-state").textContent);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const selectedTrip = state.selected?.name;
let sourceFileSignature = state.selected?.file_state?.signature || "";
let sourceFilePollTimer = null;
let lineItemSaveQueue = Promise.resolve();

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const payload = response.headers.get("content-type")?.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    const failure = new Error(payload.error || `Request failed (${response.status})`);
    failure.fields = payload.fields || {};
    throw failure;
  }
  return payload;
}

function toast(message, isError = false) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.className = `toast${isError ? " error" : ""}`;
  window.setTimeout(() => element.classList.add("hidden"), 3500);
}

function openDialog(id) { document.getElementById(id)?.showModal(); }
document.getElementById("new-trip-button")?.addEventListener("click", () => openDialog("new-trip-dialog"));
document.getElementById("empty-new-trip-button")?.addEventListener("click", () => openDialog("new-trip-dialog"));
document.querySelectorAll(".open-settings-button").forEach(button => {
  button.addEventListener("click", () => openDialog("settings-dialog"));
});
document.getElementById("open-accounting-button")?.addEventListener("click", () => openDialog("accounting-dialog"));
document.getElementById("open-trip-metadata-button")?.addEventListener("click", () => openDialog("trip-metadata-dialog"));
document.getElementById("open-approval-button")?.addEventListener("click", () => openDialog("approval-dialog"));
document.getElementById("open-delete-trip-button")?.addEventListener("click", () => {
  const form = document.getElementById("delete-trip-form");
  form?.reset();
  const error = document.getElementById("delete-trip-error");
  if (error) error.textContent = "";
  openDialog("delete-trip-dialog");
});
document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));

const receiptPreviewDialog = document.getElementById("receipt-preview-dialog");
document.querySelectorAll(".receipt-preview").forEach(button => button.addEventListener("click", () => {
  const sourceFile = button.dataset.sourceFile;
  const url = `/api/trips/${encodeURIComponent(selectedTrip)}/receipt?filename=${encodeURIComponent(sourceFile)}`;
  document.getElementById("receipt-preview-title").textContent = sourceFile;
  document.getElementById("receipt-preview-frame").src = url;
  document.getElementById("receipt-preview-full").href = url;
  receiptPreviewDialog.showModal();
}));
receiptPreviewDialog?.addEventListener("click", event => {
  if (event.target === receiptPreviewDialog) receiptPreviewDialog.close();
});
receiptPreviewDialog?.addEventListener("close", () => {
  document.getElementById("receipt-preview-frame").src = "about:blank";
});

document.getElementById("trip-select")?.addEventListener("change", event => {
  const archived = state.show_archived ? "&archived=1" : "";
  window.location.href = `/?trip=${encodeURIComponent(event.target.value)}${archived}`;
});

document.getElementById("refresh-files-button")?.addEventListener("click", () => window.location.reload());

function dashboardHasActiveEditing() {
  if (document.querySelector("dialog[open]")) return true;
  const active = document.activeElement;
  return active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName);
}

async function detectExternalFileChanges() {
  if (!selectedTrip || document.hidden || dashboardHasActiveEditing()) return;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/file-state`, { method: "GET" });
    if (payload.busy) return;
    const nextSignature = payload.file_state?.signature || "";
    if (sourceFileSignature && nextSignature && nextSignature !== sourceFileSignature) {
      window.clearInterval(sourceFilePollTimer);
      toast("Receipt or statement folder changed. Refreshing…");
      window.setTimeout(() => window.location.reload(), 350);
      return;
    }
    sourceFileSignature = nextSignature;
  } catch (_failure) {
    // The launcher may be stopping; the next successful poll will catch up.
  }
}

if (selectedTrip) {
  sourceFilePollTimer = window.setInterval(detectExternalFileChanges, 2500);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) detectExternalFileChanges();
  });
  window.addEventListener("focus", detectExternalFileChanges);
}

document.getElementById("new-trip-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const error = document.getElementById("new-trip-error");
  error.textContent = "";
  try {
    const payload = await api("/api/trips", {
      method: "POST",
      body: JSON.stringify({
        month: form.get("month"),
        description: form.get("description"),
        claim_program: form.get("claim_program"),
        metadata: {
          claim_program: form.get("claim_program"),
          traveller: form.get("traveller"),
          start_date: form.get("start_date"),
          end_date: form.get("end_date"),
          business_purpose: form.get("business_purpose")
        }
      })
    });
    window.location.href = `/?trip=${encodeURIComponent(payload.trip.name)}`;
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("trip-metadata-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const data = new FormData(event.target);
  const metadata = {
    traveller: data.get("traveller"),
    claim_program: state.selected?.claim_program || "",
    start_date: data.get("start_date"),
    end_date: data.get("end_date"),
    business_purpose: data.get("business_purpose"),
    default_paid_by: data.get("default_paid_by"),
  };
  const error = document.getElementById("trip-metadata-error");
  error.textContent = "";
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/metadata`, {
      method: "POST",
      body: JSON.stringify({ metadata })
    });
    toast("Trip details saved.");
    window.location.reload();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("approval-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const data = new FormData(event.target);
  const error = document.getElementById("approval-error");
  error.textContent = "";
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        workbook: data.get("workbook"),
        reviewer: state.selected?.metadata?.traveller || "Self-reviewed",
        note: data.get("note") || "Reviewed in the trip app."
      })
    });
    toast("Trip version approved and hashed.");
    window.location.reload();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("export-package-button")?.addEventListener("click", async event => {
  event.target.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/export-package`, {
      method: "POST",
      body: JSON.stringify({})
    });
    toast(`${payload.package.name} created.`);
    window.location.reload();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

document.querySelectorAll(".archive-trip-button").forEach(button => button.addEventListener("click", async () => {
  const archived = button.dataset.archived === "true";
  if (archived && !window.confirm("Archive this trip? Files stay in place and the trip can be restored.")) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived })
    });
    window.location.href = archived ? "/" : `/?trip=${encodeURIComponent(selectedTrip)}`;
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.getElementById("delete-trip-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const confirmation = String(data.get("confirmation") || "");
  const error = document.getElementById("delete-trip-error");
  const submit = form.querySelector('button[type="submit"]');
  error.textContent = "";
  if (confirmation !== selectedTrip) {
    error.textContent = `Type ${selectedTrip} exactly to confirm.`;
    return;
  }
  submit.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation })
    });
    window.location.href = "/";
  } catch (failure) {
    error.textContent = failure.message;
    submit.disabled = false;
  }
});

document.getElementById("settings-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const error = document.getElementById("settings-error");
  error.textContent = "";
  try {
    await api("/api/settings/openai", { method: "POST", body: JSON.stringify({ api_key: form.get("api_key") }) });
    window.location.reload();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("accounting-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const percentage = name => Number(data.get(name)) / 100;
  const profile = {
    company_legal_name: data.get("company_legal_name"),
    version: data.get("version"),
    effective_date: data.get("effective_date"),
    traveller_reimbursement_type: data.get("traveller_reimbursement_type"),
    counter_account: data.get("counter_account"),
    gst_hst_registrant: data.has("gst_hst_registrant"),
    qst_registrant: data.has("qst_registrant"),
    commercial_use_pct: percentage("commercial_use_pct"),
    normal_tax_recovery_pct: percentage("normal_tax_recovery_pct"),
    meal_tax_recovery_pct: percentage("meal_tax_recovery_pct"),
    normal_deduction_pct: percentage("normal_deduction_pct"),
    meal_deduction_pct: percentage("meal_deduction_pct"),
    tax_calculation_method: data.get("tax_calculation_method"),
    account_mapping: {
      non_meal: data.get("account_non_meal"),
      meal_deductible: data.get("account_meal_deductible"),
      meal_nondeductible: data.get("account_meal_nondeductible"),
      gst_hst_receivable: data.get("account_gst_hst_receivable"),
      qst_receivable: data.get("account_qst_receivable")
    }
  };
  const error = document.getElementById("accounting-error");
  error.textContent = "";
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/accounting-profile`, {
      method: "POST",
      body: JSON.stringify({ profile, make_default: data.has("make_default") })
    });
    toast("Accounting assumptions saved for this trip.");
    window.location.reload();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("claim-program-select")?.addEventListener("change", async event => {
  if (!window.confirm("Change this trip's claim program? Existing files stay in place, but receipt review and finalization may need to be refreshed.")) {
    event.target.value = state.selected.claim_program;
    return;
  }
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/claim-program`, {
      method: "POST", body: JSON.stringify({ claim_program: event.target.value })
    });
    window.location.reload();
  } catch (failure) { toast(failure.message, true); event.target.value = state.selected.claim_program; }
});

async function uploadFiles(kind, files) {
  if (!files?.length) return;
  const data = new FormData();
  for (const file of files) data.append("files", file);
  toast(`Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/upload/${kind}`, { method: "POST", body: data });
    const duplicates = payload.files.filter(file => file.status === "duplicate").length;
    if (duplicates) toast(`${duplicates} identical file${duplicates === 1 ? " was" : "s were"} already present.`);
    window.location.reload();
  } catch (failure) { toast(failure.message, true); }
}

document.querySelectorAll(".drop-zone").forEach(zone => {
  const input = zone.querySelector("input");
  input.addEventListener("change", () => uploadFiles(zone.dataset.uploadKind, input.files));
  for (const eventName of ["dragenter", "dragover"]) zone.addEventListener(eventName, event => {
    event.preventDefault(); zone.classList.add("dragging");
  });
  for (const eventName of ["dragleave", "drop"]) zone.addEventListener(eventName, event => {
    event.preventDefault(); zone.classList.remove("dragging");
  });
  zone.addEventListener("drop", event => uploadFiles(zone.dataset.uploadKind, event.dataTransfer.files));
});

document.querySelectorAll(".remove-file").forEach(button => button.addEventListener("click", async () => {
  if (!window.confirm(`Remove ${button.dataset.filename} from this trip?`)) return;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/remove-file`, {
      method: "POST", body: JSON.stringify({ kind: button.dataset.kind, filename: button.dataset.filename })
    });
    window.location.reload();
  } catch (failure) { toast(failure.message, true); }
}));

document.querySelectorAll(".save-date-convention").forEach(button => button.addEventListener("click", async () => {
  const select = document.querySelector(`.date-convention-select[data-filename="${CSS.escape(button.dataset.filename)}"]`);
  if (!select) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/statement-date-convention`, {
      method: "POST",
      body: JSON.stringify({ filename: button.dataset.filename, convention: select.value })
    });
    toast("Statement date convention saved. Sync again before generating.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.querySelectorAll(".finder-button").forEach(button => button.addEventListener("click", async () => {
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reveal`, {
      method: "POST", body: JSON.stringify({ target: button.dataset.target })
    });
  } catch (failure) { toast(failure.message, true); }
}));

document.querySelectorAll(".open-workbook").forEach(button => button.addEventListener("click", async () => {
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/open-workbook`, {
      method: "POST", body: JSON.stringify({ filename: button.dataset.filename })
    });
  } catch (failure) { toast(failure.message, true); }
}));

document.querySelectorAll(".reveal-workbook").forEach(button => button.addEventListener("click", async () => {
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reveal`, {
      method: "POST", body: JSON.stringify({ target: "workbook", filename: button.dataset.filename })
    });
  } catch (failure) { toast(failure.message, true); }
}));

document.getElementById("sync-reconciliation-button")?.addEventListener("click", async event => {
  event.target.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconcile`, {
      method: "POST", body: JSON.stringify({})
    });
    renderReconciliationJob(payload.job);
    pollReconciliationJob(payload.job.id);
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

document.getElementById("sync-line-items-button")?.addEventListener("click", async event => {
  const quality = document.querySelector('input[name="receipt-quality"]:checked')?.value || "basic";
  event.target.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/sync`, {
      method: "POST", body: JSON.stringify({ quality })
    });
    renderLineItemJob(payload.job);
    pollLineItemJob(payload.job.id);
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

async function updateLineItem(input, field) {
  input.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/item`, {
      method: "POST",
      body: JSON.stringify({
        source_file: input.dataset.sourceFile,
        line_id: input.dataset.lineId,
        fields: { [field]: input.checked }
      })
    });
    toast(
      field === "reviewed"
        ? (input.checked ? "Line marked ready." : "Line returned to review.")
        : field.startsWith("included")
          ? "Line inclusion saved."
          : "Alcohol classification saved."
    );
    window.location.reload();
  } catch (failure) {
    input.checked = !input.checked;
    input.disabled = false;
    toast(failure.message, true);
  }
}

document.querySelectorAll(".line-item-arvine").forEach(input => {
  input.addEventListener("change", () => updateLineItem(input, "included_in_arvine"));
});
document.querySelectorAll(".line-item-ivado").forEach(input => {
  input.addEventListener("change", () => updateLineItem(input, "included_in_ivado"));
});
document.querySelectorAll(".line-item-alcohol").forEach(input => {
  input.addEventListener("change", () => updateLineItem(input, "is_alcohol"));
});
document.querySelectorAll(".line-item-reviewed").forEach(input => {
  input.addEventListener("change", () => updateLineItem(input, "reviewed"));
});

document.querySelectorAll(".receipt-reviewed").forEach(input => {
  input.addEventListener("change", async () => {
    input.disabled = true;
    try {
      await saveExpenseFields(input.dataset.sourceFile, { reviewed: input.checked });
      toast(input.checked ? "Receipt and its lines marked ready." : "Receipt returned to review.");
      window.location.reload();
    } catch (failure) {
      input.checked = !input.checked;
      input.disabled = false;
      toast(failure.message, true);
    }
  });
});

document.querySelectorAll(".currency-review-form").forEach(form => {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const input = form.querySelector(".currency-review-input");
    const currency = String(input.value || "").trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency)) {
      toast("Use a three-letter currency code such as CAD or QAR.", true);
      return;
    }
    input.disabled = true;
    try {
      await saveExpenseFields(form.dataset.sourceFile, { currency });
      toast("Receipt currency saved. Resync statements to refresh matching.");
      window.location.reload();
    } catch (failure) {
      input.disabled = false;
      toast(failure.message, true);
    }
  });
});

function normalizedLineItemValue(input, field) {
  if (field === "description") return String(input.value || "").trim().replace(/\s+/g, " ");
  if (field === "amount") {
    const value = Number(input.value);
    return input.value !== "" && Number.isFinite(value) ? value.toFixed(2) : "";
  }
  return String(input.value || "");
}

function lineItemStatus(input, message, kind = "") {
  const selector = `[data-source-file="${CSS.escape(input.dataset.sourceFile)}"][data-line-id="${CSS.escape(input.dataset.lineId)}"]`;
  const status = document.querySelector(`.line-save-status${selector}`);
  if (!status) return;
  status.textContent = message;
  status.className = `line-save-status${kind ? ` ${kind}` : ""}`;
}

function updateAutosavedReceipt(payload, sourceFile, lineId) {
  const review = payload.line_item_review;
  const receipt = review?.receipts?.find(value => value.source_file === sourceFile);
  if (!receipt) return;
  if (state.selected) state.selected.line_item_review = review;
  if (payload.trip?.file_state?.signature) sourceFileSignature = payload.trip.file_state.signature;

  const selector = `[data-source-file="${CSS.escape(sourceFile)}"]`;
  const lineSelector = `${selector}[data-line-id="${CSS.escape(lineId)}"]`;
  const lineReviewed = document.querySelector(`.line-item-reviewed${lineSelector}`);
  if (lineReviewed) {
    lineReviewed.checked = false;
    lineReviewed.closest(".review-check")?.classList.remove("ready");
    const label = lineReviewed.closest(".review-check")?.querySelector("span");
    if (label) label.textContent = "Review";
  }
  const receiptReviewed = document.querySelector(`.receipt-reviewed${selector}`);
  if (receiptReviewed) {
    receiptReviewed.checked = false;
    receiptReviewed.closest(".review-check")?.classList.remove("ready");
    const label = receiptReviewed.closest(".review-check")?.querySelector("span");
    if (label) label.textContent = "Reviewed";
  }
  const badge = document.querySelector(`.receipt-status-badge${selector}`);
  if (badge) {
    badge.classList.remove("ready", "review");
    badge.classList.add(receipt.status);
    badge.textContent = receipt.status.replaceAll("_", " ");
  }

  const totals = document.querySelector(`[data-receipt-totals="${CSS.escape(sourceFile)}"]`);
  const setTotal = (kind, label, value) => {
    const element = totals?.querySelector(`[data-total-kind="${kind}"]`);
    if (element) element.textContent = `${label} ${value == null ? "—" : Number(value).toFixed(2)}`;
  };
  setTotal("receipt", "Receipt", receipt.receipt_total);
  setTotal("lines", "Lines", receipt.line_total);
  setTotal("arvine", "Arvine", receipt.arvine_included_total);
  setTotal("ivado", "IVADO", receipt.ivado_included_total);
  setTotal("removed", "IVADO removed", receipt.ivado_excluded_total);
  const difference = totals?.querySelector('[data-total-kind="difference"]');
  if (difference) {
    const value = Number(receipt.difference || 0);
    difference.textContent = `Difference ${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
    difference.classList.toggle("hidden", !value);
  }
  const reviewCount = document.getElementById("receipt-review-count");
  const readyCount = document.getElementById("receipt-ready-count");
  if (reviewCount) reviewCount.textContent = review.summary.review_count;
  if (readyCount) readyCount.textContent = review.summary.ready_count;
}

function autosaveLineItem(input) {
  const field = input.dataset.autosaveField;
  const value = normalizedLineItemValue(input, field);
  if (value === input.dataset.savedValue) return;
  if (field === "description" && !value) {
    lineItemStatus(input, "Description required", "error");
    return;
  }
  if (field === "amount" && (!value || Number(value) < 0)) {
    lineItemStatus(input, "Invalid amount", "error");
    return;
  }
  lineItemStatus(input, "Saving…", "saving");
  lineItemSaveQueue = lineItemSaveQueue.catch(() => {}).then(async () => {
    try {
      const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/item`, {
        method: "POST",
        body: JSON.stringify({
          source_file: input.dataset.sourceFile,
          line_id: input.dataset.lineId,
          fields: { [field]: value }
        })
      });
      input.dataset.savedValue = value;
      updateAutosavedReceipt(payload, input.dataset.sourceFile, input.dataset.lineId);
      const latestValueWasSaved = normalizedLineItemValue(input, field) === value;
      lineItemStatus(input, latestValueWasSaved ? "Saved" : "Saving…", latestValueWasSaved ? "saved" : "saving");
    } catch (failure) {
      lineItemStatus(input, "Not saved", "error");
      toast(failure.message, true);
    }
  });
}

document.querySelectorAll("[data-autosave-field]").forEach(input => {
  input.addEventListener(input.tagName === "SELECT" ? "change" : "blur", () => autosaveLineItem(input));
});

document.querySelectorAll(".add-line-item").forEach(button => button.addEventListener("click", async () => {
  const description = window.prompt("Description for the new receipt line:", "");
  if (description === null || !description.trim()) return;
  const amount = window.prompt("Amount in the receipt currency:", "");
  if (amount === null) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/add`, {
      method: "POST",
      body: JSON.stringify({
        source_file: button.dataset.sourceFile,
        description,
        amount,
        included: true,
        is_alcohol: false
      })
    });
    toast("Receipt line added.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.querySelectorAll(".remove-line-item").forEach(button => button.addEventListener("click", async () => {
  if (!window.confirm("Remove this line from the receipt review? Reset automatic will restore extracted lines.")) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/remove`, {
      method: "POST",
      body: JSON.stringify({
        source_file: button.dataset.sourceFile,
        line_id: button.dataset.lineId
      })
    });
    toast("Receipt line removed.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));
document.querySelectorAll(".reset-line-items").forEach(button => button.addEventListener("click", async () => {
  if (!window.confirm(`Reset all line-item choices for ${button.dataset.sourceFile} to the automatic result?`)) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/reset`, {
      method: "POST",
      body: JSON.stringify({ source_file: button.dataset.sourceFile })
    });
    toast("Automatic line-item result restored.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.querySelectorAll(".mapping-select").forEach(select => select.addEventListener("change", async event => {
  const value = event.target.value;
  event.target.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/mapping`, {
      method: "POST",
      body: JSON.stringify({
        group_id: event.target.dataset.groupId,
        expense_file: value && value !== "__auto__" ? value : null,
        use_auto: value === "__auto__"
      })
    });
    toast("Invoice mapping saved. It will be used in the next Excel workbook.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
}));

let pendingReceiptMatchFile = null;

function cardAccountKey(transaction) {
  return `${transaction.provider || "card"}|${transaction.account_label || "Account"}`;
}

async function chooseReceiptMatch(groupId, button) {
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/mapping`, {
      method: "POST",
      body: JSON.stringify({ group_id: groupId, expense_file: pendingReceiptMatchFile, use_auto: false })
    });
    toast("Card transaction matched to this receipt.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}

function renderCardMatchResults() {
  const expense = state.reconciliation?.expenses?.find(item => item.source_file === pendingReceiptMatchFile);
  const results = document.getElementById("card-match-results");
  if (!expense || !results) return;
  const suggestionRank = new Map((expense.match_suggestions || []).map((item, index) => [item.group_id, { ...item, index }]));
  const search = document.getElementById("card-match-search").value.trim().toLowerCase();
  const account = document.getElementById("card-match-account").value;
  const transactions = (state.reconciliation?.transactions || [])
    .filter(item => item.match_eligible && !item.ignored && !(item.allocations || []).length)
    .filter(item => !account || cardAccountKey(item) === account)
    .filter(item => {
      const haystack = [
        item.transaction_date, item.provider, item.account_label, item.description,
        item.purchase_amount, item.purchase_currency, item.cad_amount, "CAD"
      ].join(" ").toLowerCase();
      return !search || haystack.includes(search);
    })
    .sort((left, right) => {
      const leftRank = suggestionRank.get(left.group_id)?.index ?? 9999;
      const rightRank = suggestionRank.get(right.group_id)?.index ?? 9999;
      return leftRank - rightRank || String(right.transaction_date || "").localeCompare(String(left.transaction_date || ""));
    });

  results.replaceChildren();
  if (!transactions.length) {
    const empty = document.createElement("p");
    empty.className = "empty-line";
    empty.textContent = "No eligible card transactions match this search.";
    results.append(empty);
    return;
  }
  for (const transaction of transactions) {
    const row = document.createElement("div");
    row.className = "card-match-option";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = transaction.description || "No description";
    const source = document.createElement("span");
    source.textContent = `${transaction.transaction_date || "date missing"} · ${(transaction.provider || "card").toUpperCase()} ${transaction.account_label || ""}`;
    const amount = document.createElement("small");
    const purchase = transaction.purchase_amount == null ? "?" : Number(transaction.purchase_amount).toFixed(2);
    const cad = transaction.cad_amount == null ? "CAD unavailable" : `${Number(transaction.cad_amount).toFixed(2)} CAD`;
    amount.textContent = `${purchase} ${transaction.purchase_currency || ""} · ${cad}`;
    details.append(title, source, amount);
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button small";
    const suggestion = suggestionRank.get(transaction.group_id);
    const current = transaction.expense_file === expense.source_file;
    action.textContent = current ? "Current match" : "Match";
    action.classList.add(current ? "primary" : "secondary");
    action.disabled = current;
    if (!current) action.addEventListener("click", () => chooseReceiptMatch(transaction.group_id, action));
    if (suggestion && !current) {
      const badge = document.createElement("span");
      badge.className = "match-badge suggested";
      badge.textContent = `${Math.round(suggestion.score * 100)}% likely`;
      details.append(badge);
    } else if (transaction.expense_file && !current) {
      const badge = document.createElement("span");
      badge.className = "match-badge review";
      badge.textContent = "Matched to another receipt";
      details.append(badge);
    }
    row.append(details, action);
    results.append(row);
  }
}

document.querySelectorAll(".open-receipt-matcher").forEach(button => button.addEventListener("click", () => {
  pendingReceiptMatchFile = button.dataset.sourceFile;
  const expense = state.reconciliation.expenses.find(item => item.source_file === pendingReceiptMatchFile);
  document.getElementById("card-match-title").textContent = expense.vendor || expense.source_file;
  document.getElementById("card-match-receipt").textContent =
    `${expense.amount ?? "?"} ${expense.currency || ""} · ${expense.date || "date missing"} · employee share 1/${expense.number_of_people || 1}`;
  document.getElementById("card-match-search").value = "";
  const account = document.getElementById("card-match-account");
  account.replaceChildren(new Option("All cards and accounts", ""));
  const accounts = new Map();
  for (const transaction of state.reconciliation.transactions || []) {
    if (transaction.match_eligible && !transaction.ignored) {
      accounts.set(cardAccountKey(transaction), `${(transaction.provider || "card").toUpperCase()} ${transaction.account_label || "Account"}`);
    }
  }
  for (const [value, label] of [...accounts].sort((a, b) => a[1].localeCompare(b[1]))) {
    account.add(new Option(label, value));
  }
  renderCardMatchResults();
  openDialog("card-match-dialog");
}));

document.getElementById("card-match-search")?.addEventListener("input", renderCardMatchResults);
document.getElementById("card-match-account")?.addEventListener("change", renderCardMatchResults);
document.getElementById("card-match-dialog")?.addEventListener("click", event => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

document.querySelectorAll(".receipt-unmatch").forEach(button => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/mapping`, {
      method: "POST",
      body: JSON.stringify({ group_id: button.dataset.groupId, expense_file: null, use_auto: false })
    });
    toast("Card match removed.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

let pendingTransactionDisposition = null;

function currentTransactionDisposition(select) {
  const current = state.reconciliation.transactions.find(
    item => item.group_id === select.dataset.groupId
  );
  if (current?.ignored) return "ignore";
  return select.dataset.possibleDuplicate === "true"
    ? current?.duplicate_resolution || "unresolved"
    : "keep";
}

async function persistTransactionDisposition(select, action, note = "") {
  select.disabled = true;
  await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/transaction-decision`, {
    method: "POST",
    body: JSON.stringify({ group_id: select.dataset.groupId, action, note })
  });
  const possibleDuplicate = select.dataset.possibleDuplicate === "true";
  toast(
    action === "ignore"
      ? (possibleDuplicate ? "Duplicate ignored with an audit note." : "Transaction excluded from this trip.")
      : "Transaction disposition saved."
  );
  window.location.reload();
}

document.querySelectorAll(".transaction-decision").forEach(select => select.addEventListener("change", async event => {
  const action = event.target.value;
  if (action === "ignore") {
    pendingTransactionDisposition = event.target;
    const form = document.getElementById("transaction-disposition-form");
    form.reset();
    form.elements.group_id.value = event.target.dataset.groupId;
    form.elements.possible_duplicate.value = event.target.dataset.possibleDuplicate;
    const possibleDuplicate = event.target.dataset.possibleDuplicate === "true";
    document.getElementById("transaction-disposition-title").textContent =
      possibleDuplicate ? "Ignore duplicate transaction" : "Exclude transaction from this trip";
    document.getElementById("transaction-disposition-help").textContent = possibleDuplicate
      ? "The duplicate stays in the statement audit trail, but it will not fund an expense."
      : "The transaction stays in the statement audit trail, but it will not be matched to a trip expense.";
    document.getElementById("transaction-disposition-error").textContent = "";
    openDialog("transaction-disposition-dialog");
    return;
  }
  try {
    await persistTransactionDisposition(event.target, action);
  } catch (failure) {
    toast(failure.message, true);
    event.target.value = currentTransactionDisposition(event.target);
    event.target.disabled = false;
  }
}));

function cancelTransactionDisposition() {
  if (pendingTransactionDisposition) {
    pendingTransactionDisposition.value = currentTransactionDisposition(pendingTransactionDisposition);
  }
  pendingTransactionDisposition = null;
  document.getElementById("transaction-disposition-dialog")?.close();
}

document.querySelectorAll(".cancel-transaction-disposition").forEach(button => {
  button.addEventListener("click", cancelTransactionDisposition);
});

document.getElementById("transaction-disposition-dialog")?.addEventListener("cancel", event => {
  event.preventDefault();
  cancelTransactionDisposition();
});

document.getElementById("transaction-disposition-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!pendingTransactionDisposition) return;
  const data = new FormData(event.target);
  const note = String(data.get("note") || "").trim();
  const error = document.getElementById("transaction-disposition-error");
  if (!note) {
    error.textContent = "Add a reason so the exclusion remains auditable.";
    return;
  }
  error.textContent = "";
  try {
    await persistTransactionDisposition(pendingTransactionDisposition, "ignore", note);
  } catch (failure) {
    error.textContent = failure.message;
    pendingTransactionDisposition.disabled = false;
  }
});

document.querySelectorAll(".policy-exception").forEach(button => button.addEventListener("click", async () => {
  const current = button.dataset.note || "";
  const note = window.prompt(
    current
      ? "Update the documented exception. Leave blank to remove it."
      : "Explain why this policy exception is acceptable for this trip.",
    current
  );
  if (note === null) return;
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/policy-exception`, {
      method: "POST",
      body: JSON.stringify({ warning_id: button.dataset.warningId, note })
    });
    toast(note.trim() ? "Policy exception documented." : "Policy exception removed.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[character]);
}

function allocationRowHtml(allocation = {}) {
  const expenseOptions = (state.reconciliation?.expenses || []).map(expense => {
    const selected = expense.source_file === allocation.invoice_file ? " selected" : "";
    const label = `${expense.vendor || expense.source_file} · ${expense.amount ?? "?"} ${expense.currency || ""}`;
    return `<option value="${escapeHtml(expense.source_file)}"${selected}>${escapeHtml(label)}</option>`;
  }).join("");
  const typeOptions = ["purchase", "refund", "fee", "personal", "ignored"].map(type => (
    `<option value="${type}"${type === (allocation.type || "purchase") ? " selected" : ""}>${type}</option>`
  )).join("");
  return `
    <div class="allocation-row">
      <label>Type<select class="select" data-allocation-field="type">${typeOptions}</select></label>
      <label>Invoice / category
        <select class="select" data-allocation-field="invoice_file">
          <option value="">No invoice</option>${expenseOptions}
        </select>
        <input class="input" data-allocation-field="category" placeholder="Personal / fee category" value="${escapeHtml(allocation.category || "")}">
      </label>
      <label>CAD amount<input class="input allocation-cad" data-allocation-field="cad_amount" type="number" step="0.01" value="${escapeHtml(allocation.cad_amount ?? "")}"></label>
      <label>Original amount<input class="input" data-allocation-field="original_amount" type="number" step="0.01" value="${escapeHtml(allocation.original_amount ?? "")}"></label>
      <label>Audit note<input class="input" data-allocation-field="note" value="${escapeHtml(allocation.note || "")}"></label>
      <button class="icon-button remove-allocation-row" type="button" title="Remove allocation">×</button>
    </div>`;
}

function addAllocationRow(allocation = {}) {
  const container = document.getElementById("allocation-rows");
  container.insertAdjacentHTML("beforeend", allocationRowHtml(allocation));
  const row = container.lastElementChild;
  row.querySelector(".remove-allocation-row").addEventListener("click", () => {
    row.remove();
    updateAllocationBalance();
  });
  row.querySelectorAll("input, select").forEach(input => input.addEventListener("input", updateAllocationBalance));
  updateAllocationBalance();
}

function activeAllocationTransaction() {
  const groupId = document.getElementById("allocation-form")?.elements.group_id.value;
  return state.reconciliation?.transactions?.find(item => item.group_id === groupId);
}

function updateAllocationBalance() {
  const transaction = activeAllocationTransaction();
  const target = Number(transaction?.cad_amount || 0);
  const total = Array.from(document.querySelectorAll(".allocation-cad")).reduce(
    (sum, input) => sum + (Number(input.value) || 0), 0
  );
  const balance = Math.round((target - total) * 100) / 100;
  const element = document.getElementById("allocation-balance");
  if (!element) return;
  element.textContent = `Statement ${target.toFixed(2)} CAD · allocated ${total.toFixed(2)} CAD · balance ${balance.toFixed(2)} CAD`;
  element.classList.toggle("balanced", Math.abs(balance) <= 0.01);
}

document.querySelectorAll(".edit-allocations").forEach(button => button.addEventListener("click", () => {
  const transaction = state.reconciliation?.transactions?.find(item => item.group_id === button.dataset.groupId);
  if (!transaction) return;
  const form = document.getElementById("allocation-form");
  form.elements.group_id.value = transaction.group_id;
  document.getElementById("allocation-title").textContent = transaction.description || "Split transaction";
  document.getElementById("allocation-target").textContent =
    `${transaction.transaction_date || "date missing"} · ${Number(transaction.cad_amount || 0).toFixed(2)} CAD statement amount`;
  document.getElementById("allocation-error").textContent = "";
  document.getElementById("allocation-rows").replaceChildren();
  const allocations = transaction.allocations?.length
    ? transaction.allocations
    : [{ type: transaction.transaction_type === "refund" ? "refund" : "purchase", invoice_file: transaction.expense_file, cad_amount: transaction.cad_amount }];
  allocations.forEach(addAllocationRow);
  openDialog("allocation-dialog");
}));

document.getElementById("add-allocation-row")?.addEventListener("click", () => addAllocationRow({type: "purchase"}));

function collectAllocations() {
  return Array.from(document.querySelectorAll(".allocation-row")).map(row => {
    const value = field => row.querySelector(`[data-allocation-field="${field}"]`)?.value ?? "";
    return {
      type: value("type"),
      invoice_file: value("invoice_file") || null,
      category: value("category"),
      cad_amount: value("cad_amount"),
      original_amount: value("original_amount"),
      note: value("note")
    };
  });
}

async function saveAllocations(allocations) {
  const form = document.getElementById("allocation-form");
  const error = document.getElementById("allocation-error");
  error.textContent = "";
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/allocations`, {
      method: "POST",
      body: JSON.stringify({ group_id: form.elements.group_id.value, allocations })
    });
    toast(allocations.length ? "Transaction allocations saved." : "Simple invoice mapping restored.");
    window.location.reload();
  } catch (failure) { error.textContent = failure.message; }
}

document.getElementById("allocation-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  await saveAllocations(collectAllocations());
});

document.getElementById("clear-allocations")?.addEventListener("click", async () => {
  await saveAllocations([]);
});

document.getElementById("save-coverage-settings")?.addEventListener("click", async event => {
  const raw = document.getElementById("expected-accounts")?.value || "";
  const expectedAccounts = raw.split(/[,\n]/).map(value => value.trim()).filter(Boolean);
  event.target.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/coverage-settings`, {
      method: "POST",
      body: JSON.stringify({ expected_accounts: expectedAccounts })
    });
    toast("Expected account list saved.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

const expenseFields = [
  "date", "vendor", "description", "expense_type", "amount", "currency", "country", "province",
  "subtotal", "gst_hst", "qst", "gst_hst_number", "qst_number", "business_purpose",
  "attendees_client", "tax_documentation_status", "review_note", "number_of_people",
  "manual_cad_override", "manual_cad_note", "paid_by", "ivado_exclusion_reason"
];

function clearExpenseErrors() {
  document.getElementById("expense-review-error").textContent = "";
  document.querySelectorAll("#expense-review-form .field-error").forEach(element => { element.textContent = ""; });
}

function showExpenseErrors(failure) {
  clearExpenseErrors();
  document.getElementById("expense-review-error").textContent = failure.message;
  for (const [field, message] of Object.entries(failure.fields || {})) {
    const element = document.querySelector(`#expense-review-form [data-error-for="${CSS.escape(field)}"]`);
    if (element) element.textContent = message;
  }
}

function reviewedReceipt(sourceFile) {
  return state.selected?.line_item_review?.receipts?.find(item => item.source_file === sourceFile);
}

function syncExpenseIvadoReasonControl() {
  const form = document.getElementById("expense-review-form");
  const included = form?.elements.included_in_ivado;
  const reason = form?.elements.ivado_exclusion_reason;
  if (!included || !reason) return;
  reason.disabled = included.checked;
  if (included.checked) reason.value = "";
}

document.querySelectorAll(".edit-expense").forEach(button => button.addEventListener("click", () => {
  const receipt = reviewedReceipt(button.dataset.sourceFile);
  if (!receipt) return;
  const form = document.getElementById("expense-review-form");
  clearExpenseErrors();
  form.elements.source_file.value = receipt.source_file;
  if (form.elements.included_in_ivado) {
    form.elements.included_in_ivado.checked = receipt.included_in_ivado !== false;
  }
  for (const field of expenseFields) {
    if (form.elements[field]) form.elements[field].value = receipt[field] ?? "";
  }
  syncExpenseIvadoReasonControl();
  document.getElementById("expense-dialog-title").textContent = receipt.vendor || receipt.source_file;
  openDialog("expense-dialog");
}));

document.querySelector("#expense-review-form [name='included_in_ivado']")?.addEventListener(
  "change",
  syncExpenseIvadoReasonControl
);

document.querySelectorAll(".people-review-form").forEach(form => {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const input = form.querySelector(".people-review-input");
    const numberOfPeople = Number(input?.value || 0);
    if (!Number.isInteger(numberOfPeople) || numberOfPeople < 1 || numberOfPeople > 99) {
      toast("Enter a whole number of employees from 1 to 99.", true);
      return;
    }
    input.disabled = true;
    try {
      await saveExpenseFields(form.dataset.sourceFile, { number_of_people: numberOfPeople });
      toast(`Employee share saved as 1/${numberOfPeople}. Resync statements to refresh matching.`);
      window.location.reload();
    } catch (failure) {
      input.disabled = false;
      toast(failure.message, true);
    }
  });
});

async function saveExpenseFields(sourceFile, fields) {
  return api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/expense`, {
    method: "POST",
    body: JSON.stringify({ source_file: sourceFile, fields })
  });
}

document.getElementById("expense-review-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const fields = Object.fromEntries(
    expenseFields
      .filter(field => form.elements[field])
      .map(field => [field, data.get(field)])
  );
  if (form.elements.included_in_ivado) {
    fields.included_in_ivado = data.has("included_in_ivado");
  }
  clearExpenseErrors();
  try {
    await saveExpenseFields(data.get("source_file"), fields);
    toast("Expense saved. Resync statements if matching fields changed.");
    window.location.reload();
  } catch (failure) { showExpenseErrors(failure); }
});

document.getElementById("restore-expense-button")?.addEventListener("click", async () => {
  const form = document.getElementById("expense-review-form");
  const receipt = reviewedReceipt(form.elements.source_file.value);
  if (!receipt?.extracted || !window.confirm("Restore the extracted expense fields? Line-item choices will remain.")) return;
  const fields = Object.fromEntries(
    expenseFields
      .filter(field => form.elements[field])
      .map(field => [field, receipt.extracted[field]])
  );
  fields.paid_by = receipt.auto_paid_by ?? receipt.paid_by ?? "employee_personal";
  if (form.elements.included_in_ivado) {
    fields.included_in_ivado = receipt.extracted.included_in_ivado ?? true;
  }
  clearExpenseErrors();
  try {
    await saveExpenseFields(receipt.source_file, fields);
    toast("Extracted expense values restored.");
    window.location.reload();
  } catch (failure) { showExpenseErrors(failure); }
});

document.getElementById("generate-button")?.addEventListener("click", async event => {
  event.target.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/generate`, {
      method: "POST",
      body: JSON.stringify({})
    });
    renderJob(payload.job);
    pollJob(payload.job.id);
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

document.getElementById("finalize-button")?.addEventListener("click", async event => {
  const statementsComplete = document.getElementById("statements-complete")?.checked || false;
  const coverageAcknowledgement = document.getElementById("coverage-acknowledgement")?.value || "";
  event.target.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/finalize`, {
      method: "POST",
      body: JSON.stringify({
        statements_complete: statementsComplete,
        coverage_acknowledgement: coverageAcknowledgement
      })
    });
    toast("Current claim finalized. Excel export is now available.");
    window.location.reload();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

function renderJob(job) {
  const panel = document.getElementById("job-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  document.getElementById("job-message").textContent = job.message || "Working…";
  document.getElementById("job-status").textContent = (job.status || "").replaceAll("_", " ");
  document.getElementById("job-error").textContent = job.error || "";
  const warnings = document.getElementById("job-warnings");
  warnings.replaceChildren(...(job.warnings || []).map(message => {
    const item = document.createElement("li"); item.textContent = message; return item;
  }));
  const percentage = job.stage === "complete" ? 100 : Math.min(96, Math.max(8, (job.current / Math.max(job.total, 1)) * 100));
  document.getElementById("progress-bar").style.width = `${percentage}%`;
}

function renderReconciliationJob(job) {
  const panel = document.getElementById("reconciliation-job-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  document.getElementById("reconciliation-job-message").textContent = job.message || "Syncing…";
  document.getElementById("reconciliation-job-status").textContent = (job.status || "").replaceAll("_", " ");
  document.getElementById("reconciliation-job-error").textContent = job.error || "";
  const warnings = document.getElementById("reconciliation-job-warnings");
  warnings.replaceChildren(...(job.warnings || []).map(message => {
    const item = document.createElement("li"); item.textContent = message; return item;
  }));
  const percentage = job.stage === "complete" ? 100 : Math.min(96, Math.max(8, (job.current / Math.max(job.total, 1)) * 100));
  document.getElementById("reconciliation-progress-bar").style.width = `${percentage}%`;
}

function renderLineItemJob(job) {
  const panel = document.getElementById("line-item-job-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  document.getElementById("line-item-job-message").textContent = job.message || "Scanning…";
  document.getElementById("line-item-job-status").textContent = (job.status || "").replaceAll("_", " ");
  document.getElementById("line-item-job-error").textContent = job.error || "";
  const warnings = document.getElementById("line-item-job-warnings");
  warnings.replaceChildren(...(job.warnings || []).map(message => {
    const item = document.createElement("li"); item.textContent = message; return item;
  }));
  const percentage = job.stage === "complete" ? 100 : Math.min(96, Math.max(8, (job.current / Math.max(job.total, 1)) * 100));
  document.getElementById("line-item-progress-bar").style.width = `${percentage}%`;
}

async function pollJob(jobId) {
  try {
    const payload = await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "GET" });
    renderJob(payload.job);
    if (["queued", "running"].includes(payload.job.status)) {
      window.setTimeout(() => pollJob(jobId), 900);
    } else if (["succeeded", "succeeded_warnings"].includes(payload.job.status)) {
      window.setTimeout(() => window.location.reload(), 700);
    } else {
      document.getElementById("generate-button").disabled = false;
    }
  } catch (failure) { toast(failure.message, true); document.getElementById("generate-button").disabled = false; }
}

async function pollReconciliationJob(jobId) {
  try {
    const payload = await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "GET" });
    renderReconciliationJob(payload.job);
    if (["queued", "running"].includes(payload.job.status)) {
      window.setTimeout(() => pollReconciliationJob(jobId), 900);
    } else if (["succeeded", "succeeded_warnings"].includes(payload.job.status)) {
      window.setTimeout(() => window.location.reload(), 700);
    } else {
      document.getElementById("sync-reconciliation-button").disabled = false;
    }
  } catch (failure) { toast(failure.message, true); document.getElementById("sync-reconciliation-button").disabled = false; }
}

async function pollLineItemJob(jobId) {
  try {
    const payload = await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "GET" });
    renderLineItemJob(payload.job);
    if (["queued", "running"].includes(payload.job.status)) {
      window.setTimeout(() => pollLineItemJob(jobId), 900);
    } else if (["succeeded", "succeeded_warnings"].includes(payload.job.status)) {
      window.setTimeout(() => window.location.reload(), 700);
    } else {
      document.getElementById("sync-line-items-button").disabled = false;
    }
  } catch (failure) { toast(failure.message, true); document.getElementById("sync-line-items-button").disabled = false; }
}

if (state.job && ["queued", "running"].includes(state.job.status)) pollJob(state.job.id);
if (state.job) renderJob(state.job);
if (state.reconciliation_job && ["queued", "running"].includes(state.reconciliation_job.status)) {
  pollReconciliationJob(state.reconciliation_job.id);
}
if (state.reconciliation_job) renderReconciliationJob(state.reconciliation_job);
if (state.line_item_job && ["queued", "running"].includes(state.line_item_job.status)) {
  pollLineItemJob(state.line_item_job.id);
}
if (state.line_item_job) renderLineItemJob(state.line_item_job);
