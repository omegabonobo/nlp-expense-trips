(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const {
    state,
    selectedTrip,
    currencyCodes,
    api,
    toast,
    openDialog,
    jobs,
    collapsePreference,
    saveCollapsePreference,
    collapseKey,
  } = NLP;
  let lineItemSaveQueue = Promise.resolve();
function setReceiptItemsExpanded(article, expanded, persist = true) {
  const button = article.querySelector(".receipt-items-toggle");
  if (!button) return;
  article.classList.toggle("items-collapsed", !expanded);
  button.setAttribute("aria-expanded", String(expanded));
  const count = Number(button.dataset.itemCount || 0);
  button.textContent = expanded ? "Hide items" : `Show items (${count})`;
  if (persist) saveCollapsePreference(collapseKey("receipt", button.dataset.sourceFile), !expanded);
}

function receiptEditContextKey() {
  return `nlp-expenses:${selectedTrip || "no-trip"}:receipt-edit-context`;
}

function reloadPreservingReceipt(sourceFile) {
  const button = document.querySelector(
    `.receipt-items-toggle[data-source-file="${CSS.escape(sourceFile)}"]`
  );
  const article = button?.closest(".line-receipt");
  if (article) {
    saveCollapsePreference(collapseKey("receipt", sourceFile), false);
    try {
      window.sessionStorage.setItem(
        receiptEditContextKey(),
        JSON.stringify({ sourceFile, viewportTop: article.getBoundingClientRect().top })
      );
    } catch (_failure) { /* Optional position restoration only. */ }
  }
  NLP.refreshPage();
}

function restoreReceiptEditContext() {
  let context = null;
  try {
    context = JSON.parse(window.sessionStorage.getItem(receiptEditContextKey()) || "null");
    window.sessionStorage.removeItem(receiptEditContextKey());
  } catch (_failure) { return; }
  if (!context?.sourceFile) return;
  const button = document.querySelector(
    `.receipt-items-toggle[data-source-file="${CSS.escape(context.sourceFile)}"]`
  );
  const article = button?.closest(".line-receipt");
  if (!article) return;
  setReceiptItemsExpanded(article, true, false);
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    const offset = article.getBoundingClientRect().top - Number(context.viewportTop || 0);
    window.scrollBy({ top: offset, behavior: "auto" });
  }));
}

function initializeReceiptCollapsing() {
  const receipts = [...document.querySelectorAll(".line-receipt")];
  for (const article of receipts) {
    const button = article.querySelector(".receipt-items-toggle");
    if (!button) continue;
    const stored = collapsePreference(collapseKey("receipt", button.dataset.sourceFile));
    const defaultExpanded = article.classList.contains("needs-review");
    setReceiptItemsExpanded(article, stored == null ? defaultExpanded : stored !== "collapsed", false);
    button.addEventListener("click", () => setReceiptItemsExpanded(article, article.classList.contains("items-collapsed")));
  }
  document.getElementById("collapse-all-receipts")?.addEventListener("click", () => {
    receipts.forEach(article => setReceiptItemsExpanded(article, false));
  });
  document.getElementById("expand-review-receipts")?.addEventListener("click", () => {
    receipts.forEach(article => setReceiptItemsExpanded(article, article.classList.contains("needs-review")));
  });
  document.getElementById("expand-all-receipts")?.addEventListener("click", () => {
    receipts.forEach(article => setReceiptItemsExpanded(article, true));
  });
}

initializeReceiptCollapsing();
restoreReceiptEditContext();

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

document.querySelectorAll(".receipt-scan-button").forEach(button => {
  button.addEventListener("click", async () => {
    const quality = document.querySelector('input[name="receipt-quality"]:checked')?.value || "basic";
    document.querySelectorAll(".receipt-scan-button").forEach(control => { control.disabled = true; });
    try {
      const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/sync`, {
        method: "POST",
        body: JSON.stringify({
          quality,
          only_unscanned: button.dataset.onlyUnscanned === "true"
        })
      });
      jobs.start("line_items", payload.job);
    } catch (failure) {
      toast(failure.message, true);
      document.querySelectorAll(".receipt-scan-button").forEach(control => {
        control.disabled = false;
      });
    }
  });
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
    NLP.refreshPage();
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
      NLP.refreshPage();
    } catch (failure) {
      input.checked = !input.checked;
      input.disabled = false;
      toast(failure.message, true);
    }
  });
});

document.querySelectorAll(".receipt-meal-toggle").forEach(input => {
  input.addEventListener("change", async () => {
    input.disabled = true;
    const expenseType = input.checked ? "meal" : (input.dataset.nonMealType || "other");
    try {
      await saveExpenseFields(input.dataset.sourceFile, { expense_type: expenseType });
      toast(input.checked ? "Receipt classified as a meal." : "Receipt classified as travel — non-meal.");
      NLP.refreshPage();
    } catch (failure) {
      input.checked = !input.checked;
      input.disabled = false;
      toast(failure.message, true);
    }
  });
});

function inlineExpenseStatus(control, message, kind = "") {
  const status = control.querySelector(".inline-save-status");
  if (!status) return;
  status.textContent = message;
  status.className = `inline-save-status${kind ? ` ${kind}` : ""}`;
}

document.querySelectorAll(".currency-review-form").forEach(control => {
  const input = control.querySelector(".currency-review-input");
  const autosave = async () => {
    if (input.dataset.saving === "true") return;
    const currency = String(input.value || "").trim().toUpperCase();
    if (currency === String(input.dataset.savedValue || "").toUpperCase()) return;
    if (!currencyCodes.has(currency)) {
      inlineExpenseStatus(control, "Choose a listed currency", "error");
      toast("Choose a currency from the list.", true);
      return;
    }
    input.dataset.saving = "true";
    input.disabled = true;
    inlineExpenseStatus(control, "Saving…", "saving");
    try {
      await saveExpenseFields(control.dataset.sourceFile, { currency });
      input.value = currency;
      input.dataset.savedValue = currency;
      inlineExpenseStatus(control, "Saved", "saved");
      toast("Receipt currency saved. Match suggestions updated.");
      NLP.refreshPage();
    } catch (failure) {
      input.dataset.saving = "false";
      input.disabled = false;
      inlineExpenseStatus(control, "Not saved", "error");
      toast(failure.message, true);
    }
  };
  input.addEventListener("change", autosave);
  input.addEventListener("blur", autosave);
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
  if (payload.trip?.file_state?.signature) NLP.sourceFileSignature = payload.trip.file_state.signature;

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
    if (label) label.textContent = "Review";
  }
  const badge = document.querySelector(`.receipt-status-badge${selector}`);
  if (badge) {
    badge.classList.remove("ready", "review", "ok");
    badge.classList.add(receipt.status);
    badge.textContent = receipt.status.replaceAll("_", " ");
  }
  document.getElementById(`receipt-${sourceFile}`)?.classList.toggle("needs-review", receipt.status === "review");

  const totals = document.querySelector(`[data-receipt-totals="${CSS.escape(sourceFile)}"]`);
  const setTotal = (scope, kind, label, value) => {
    const element = totals?.querySelector(`[data-total-scope="${scope}"][data-total-kind="${kind}"]`);
    if (element) element.textContent = `${label} ${value == null ? "—" : Number(value).toFixed(2)}`;
  };
  setTotal("full", "receipt", "Receipt", receipt.receipt_total);
  setTotal("full", "lines", "Lines", receipt.line_total);
  setTotal("full", "company", "Company", receipt.arvine_included_total);
  setTotal("full", "ivado", "IVADO", receipt.ivado_included_total);
  setTotal("full", "removed", "IVADO removed", receipt.ivado_excluded_total);
  setTotal("person", "receipt", "Receipt", receipt.per_person_receipt_total);
  setTotal("person", "lines", "Lines", receipt.per_person_line_total);
  setTotal("person", "company", "Company", receipt.per_person_arvine_included_total);
  setTotal("person", "ivado", "IVADO", receipt.per_person_ivado_included_total);
  setTotal("person", "removed", "IVADO removed", receipt.per_person_ivado_excluded_total);
  const difference = totals?.querySelector('[data-total-kind="difference"]');
  if (difference) {
    const value = Number(receipt.difference || 0);
    difference.textContent = `Difference ${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
    difference.classList.toggle("hidden", !value);
  }
  const reviewCount = document.getElementById("receipt-review-count");
  const okCount = document.getElementById("receipt-ok-count");
  const readyCount = document.getElementById("receipt-ready-count");
  if (reviewCount) {
    reviewCount.textContent = review.summary.review_count;
    reviewCount.parentElement?.classList.toggle("review", Boolean(review.summary.review_count));
  }
  if (okCount) okCount.textContent = review.summary.ok_count;
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
  if (field === "amount" && (!value || !Number.isFinite(Number(value)))) {
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
  const amount = window.prompt("Amount in the receipt currency (use a negative amount for a promotion or discount):", "");
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
    reloadPreservingReceipt(button.dataset.sourceFile);
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
    reloadPreservingReceipt(button.dataset.sourceFile);
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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));


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

function initializeExpenseMealControl(receipt) {
  const form = document.getElementById("expense-review-form");
  const toggle = document.getElementById("expense-meal-toggle");
  if (!form || !toggle) return;
  const expenseType = String(form.elements.expense_type.value || "other");
  form.dataset.nonMealExpenseType = expenseType.startsWith("meal")
    ? (receipt?.non_meal_expense_type || "other")
    : expenseType;
  toggle.checked = expenseType.startsWith("meal");
}

function renderExpenseFieldEvidence(receipt) {
  const container = document.getElementById("expense-field-evidence");
  const form = document.getElementById("expense-review-form");
  if (!container || !form) return;
  const labels = {
    date: "Date", vendor: "Vendor", description: "Description",
    expense_type: "Expense type", amount: "Receipt total", currency: "Currency",
    country: "Country", province: "Province / state", subtotal: "Subtotal",
    gst_hst: "GST/HST", qst: "QST", gst_hst_number: "GST/HST number",
    qst_number: "QST number", business_purpose: "Business purpose",
    attendees_client: "Meal attendees / client",
    tax_documentation_status: "Tax documentation"
  };
  const entries = Object.entries(receipt.field_evidence || {})
    .filter(([field]) => labels[field] && form.elements[field]);
  container.replaceChildren(...entries.map(([field, evidence]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = evidence.status || "ready";
    const label = document.createElement("strong");
    label.textContent = `${labels[field]} · ${Math.round(Number(evidence.confidence || 0) * 100)}%`;
    const reason = document.createElement("small");
    reason.textContent = evidence.reason || "No extractor explanation recorded.";
    button.append(label, reason);
    button.addEventListener("click", () => form.elements[field]?.focus());
    return button;
  }));
}

function syncReceiptTotalFromSubtotal() {
  const form = document.getElementById("expense-review-form");
  if (!form || form.dataset.receiptTotalEdited === "true") return;
  const subtotal = Number(form.elements.subtotal?.value);
  if (!form.elements.subtotal?.value || !Number.isFinite(subtotal)) return;
  const gstHst = Number(form.elements.gst_hst?.value || 0);
  const qst = Number(form.elements.qst?.value || 0);
  form.elements.amount.value = (subtotal + gstHst + qst).toFixed(2);
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
  form.dataset.receiptTotalEdited = "false";
  form.dataset.subtotalEdited = "false";
  initializeExpenseMealControl(receipt);
  syncExpenseIvadoReasonControl();
  renderExpenseFieldEvidence(receipt);
  document.getElementById("expense-dialog-title").textContent = receipt.vendor || receipt.source_file;
  openDialog("expense-dialog");
}));

document.querySelectorAll(".review-exception").forEach(button => {
  button.addEventListener("click", () => {
    const sourceFile = button.dataset.sourceFile;
    const section = document.querySelector(".line-item-card");
    if (section?.classList.contains("section-collapsed")) {
      section.querySelector(":scope > .card-heading .section-collapse-toggle")?.click();
    }
    if (button.dataset.field) {
      document.querySelector(
        `.edit-expense[data-source-file="${CSS.escape(sourceFile)}"]`
      )?.click();
      window.requestAnimationFrame(() => {
        const field = document.getElementById("expense-review-form")?.elements[button.dataset.field];
        field?.focus();
        field?.scrollIntoView({ block: "center" });
      });
      return;
    }
    const article = document.querySelector(
      `.line-receipt[data-source-file="${CSS.escape(sourceFile)}"]`
    );
    if (!article) return;
    setReceiptItemsExpanded(article, true);
    const target = button.dataset.lineId
      ? document.getElementById(`line-${button.dataset.lineId}`)
      : article;
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    target?.querySelector("input:not(:disabled), select:not(:disabled)")?.focus();
  });
});

document.querySelectorAll(".acknowledge-extraction-changes").forEach(button => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api(`/api/trips/${encodeURIComponent(selectedTrip)}/line-items/changes`, {
        method: "POST",
        body: JSON.stringify({ source_file: button.dataset.sourceFile })
      });
      toast("Extraction changes acknowledged; the before/after audit remains available.");
      reloadPreservingReceipt(button.dataset.sourceFile);
    } catch (failure) {
      button.disabled = false;
      toast(failure.message, true);
    }
  });
});

document.querySelector("#expense-review-form [name='included_in_ivado']")?.addEventListener(
  "change",
  syncExpenseIvadoReasonControl
);

document.getElementById("expense-meal-toggle")?.addEventListener("change", event => {
  const form = event.target.form;
  const currentType = String(form.elements.expense_type.value || "other");
  if (event.target.checked) {
    if (!currentType.startsWith("meal")) form.dataset.nonMealExpenseType = currentType;
    form.elements.expense_type.value = "meal";
  } else {
    form.elements.expense_type.value = form.dataset.nonMealExpenseType || "other";
  }
});

document.querySelector("#expense-review-form [name='amount']")?.addEventListener("input", event => {
  event.target.form.dataset.receiptTotalEdited = "true";
});

document.querySelector("#expense-review-form [name='subtotal']")?.addEventListener("input", event => {
  event.target.form.dataset.subtotalEdited = "true";
  syncReceiptTotalFromSubtotal();
});

document.querySelectorAll("#expense-review-form [name='gst_hst'], #expense-review-form [name='qst']")
  .forEach(input => input.addEventListener("input", event => {
    if (event.target.form.dataset.subtotalEdited === "true") syncReceiptTotalFromSubtotal();
  }));

document.querySelectorAll(".people-review-form").forEach(control => {
  const input = control.querySelector(".people-review-input");
  input.addEventListener("change", async () => {
    const numberOfPeople = Number(input?.value || 0);
    if (String(numberOfPeople) === String(input.dataset.savedValue || "")) return;
    if (!Number.isInteger(numberOfPeople) || numberOfPeople < 1 || numberOfPeople > 99) {
      inlineExpenseStatus(control, "Enter 1–99", "error");
      toast("Enter a whole number of employees from 1 to 99.", true);
      return;
    }
    input.disabled = true;
    inlineExpenseStatus(control, "Saving…", "saving");
    try {
      await saveExpenseFields(control.dataset.sourceFile, { number_of_people: numberOfPeople });
      input.dataset.savedValue = String(numberOfPeople);
      inlineExpenseStatus(control, "Saved", "saved");
      toast(`Traveller share saved as 1/${numberOfPeople}. Match suggestions updated.`);
      NLP.refreshPage();
    } catch (failure) {
      input.disabled = false;
      inlineExpenseStatus(control, "Not saved", "error");
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
    toast("Expense saved. Card matches and CAD values updated.");
    NLP.refreshPage();
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
  fields.paid_by = receipt.auto_paid_by ?? receipt.paid_by ?? "traveller_personal";
  if (form.elements.included_in_ivado) {
    fields.included_in_ivado = receipt.extracted.included_in_ivado ?? true;
  }
  clearExpenseErrors();
  try {
    await saveExpenseFields(receipt.source_file, fields);
    toast("Extracted expense values restored.");
    NLP.refreshPage();
  } catch (failure) { showExpenseErrors(failure); }
});

})();
