(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { state, selectedTrip, api, toast, openDialog, jobs } = NLP;
async function startReconciliation(button, onlyUnmatched = true) {
  button.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconcile`, {
      method: "POST", body: JSON.stringify({ only_unmatched: onlyUnmatched })
    });
    jobs.start("reconciliation", payload.job);
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}

document.getElementById("sync-reconciliation-button")?.addEventListener("click", event => {
  startReconciliation(event.currentTarget, true);
});

document.getElementById("rebuild-reconciliation-button")?.addEventListener("click", event => {
  startReconciliation(event.currentTarget, false);
});

document.querySelectorAll(".resync-card-matcher").forEach(button => {
  button.addEventListener("click", () => startReconciliation(button, true));
});

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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
}));

let pendingReceiptMatchFile = null;

function cardAccountKey(transaction) {
  return `${transaction.provider || "card"}|${transaction.account_label || "Account"}`;
}

function isoDateDistance(left, right) {
  if (!left || !right) return null;
  const leftTime = Date.parse(`${left}T00:00:00Z`);
  const rightTime = Date.parse(`${right}T00:00:00Z`);
  return Number.isFinite(leftTime) && Number.isFinite(rightTime)
    ? Math.abs(Math.round((leftTime - rightTime) / 86400000))
    : null;
}

function cardMatchAmountContext(expense, transaction) {
  const people = Math.max(1, Number(expense.number_of_people || 1));
  const receiptShare = Math.abs(Number(expense.amount || 0)) / people;
  let cardAmount = null;
  if (String(expense.currency || "").toUpperCase() === String(transaction.purchase_currency || "").toUpperCase()) {
    cardAmount = Math.abs(Number(transaction.purchase_amount));
  } else if (String(expense.currency || "").toUpperCase() === "CAD") {
    cardAmount = Math.abs(Number(transaction.cad_amount));
  }
  if (!receiptShare || !Number.isFinite(cardAmount) || cardAmount <= receiptShare) return "";
  const shortfall = (1 - receiptShare / cardAmount) * 100;
  return shortfall >= 5 && shortfall <= 32
    ? `Receipt is ${Math.round(shortfall)}% below the card total — tax or tip may explain the difference.`
    : "";
}

async function chooseReceiptMatch(groupId, button) {
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/mapping`, {
      method: "POST",
      body: JSON.stringify({ group_id: groupId, expense_file: pendingReceiptMatchFile, use_auto: false })
    });
    toast("Card transaction matched to this receipt.");
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}

function renderCardMatchResults() {
  const expense = state.reconciliation?.expenses?.find(item => item.source_file === pendingReceiptMatchFile);
  const results = document.getElementById("card-match-results");
  if (!expense || !results) return;
  const suggestionRank = new Map((expense.match_suggestions || []).map((item, index) => [item.group_id, { ...item, index }]));
  const search = document.getElementById("card-match-search").value.trim().toLowerCase();
  const account = document.getElementById("card-match-account").value;
  const filterDate = document.getElementById("card-match-date").value;
  const dateWindow = document.getElementById("card-match-date-window").value;
  const transactions = (state.reconciliation?.transactions || [])
    .filter(item => item.match_eligible && !item.ignored && !(item.allocations || []).length)
    .filter(item => !account || cardAccountKey(item) === account)
    .filter(item => {
      if (!filterDate || dateWindow === "all") return true;
      const distance = isoDateDistance(filterDate, item.transaction_date);
      return distance != null && distance <= Number(dateWindow);
    })
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
    const amountContext = cardMatchAmountContext(expense, transaction);
    if (amountContext) {
      const context = document.createElement("small");
      context.className = "card-match-context";
      context.textContent = amountContext;
      details.append(context);
    }
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
      if (suggestion.reason) {
        const reason = document.createElement("small");
        reason.textContent = suggestion.reason;
        details.append(reason);
      }
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
  const people = Math.max(1, Number(expense.number_of_people || 1));
  const receiptAmount = Number(expense.amount);
  const employeeMatchTarget = Number.isFinite(receiptAmount)
    ? (receiptAmount / people).toFixed(2)
    : "?";
  document.getElementById("card-match-title").textContent = expense.vendor || expense.source_file;
  document.getElementById("card-match-receipt").textContent =
    `${expense.amount ?? "?"} ${expense.currency || ""} full receipt · ` +
    `${employeeMatchTarget} ${expense.currency || ""} per employee match target (÷ ${people}) · ` +
    `${expense.date || "date missing"}`;
  document.getElementById("card-match-search").value = "";
  document.getElementById("card-match-date").value = expense.date || "";
  document.getElementById("card-match-date-window").value = expense.date ? "3" : "all";
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
document.getElementById("card-match-date")?.addEventListener("change", renderCardMatchResults);
document.getElementById("card-match-date-window")?.addEventListener("change", renderCardMatchResults);
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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.querySelectorAll(".confirm-receipt-match").forEach(button => button.addEventListener("click", async () => {
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/mapping`, {
      method: "POST",
      body: JSON.stringify({
        group_id: button.dataset.groupId,
        expense_file: button.dataset.sourceFile,
        use_auto: false
      })
    });
    toast("Card match confirmed.");
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

async function saveStatementBasis(control) {
  const select = control.querySelector(".statement-basis-select");
  select.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/statement-basis`, {
      method: "POST",
      body: JSON.stringify({
        source_file: control.dataset.sourceFile,
        basis: select.value
      })
    });
    toast("Card amount basis updated.");
    NLP.refreshPage();
  } catch (failure) {
    toast(failure.message, true);
    select.disabled = false;
  }
}

document.querySelectorAll(".statement-basis-select").forEach(select => {
  select.addEventListener("change", () => saveStatementBasis(select.closest(".statement-basis-review")));
});
async function persistTransactionDisposition(button, action) {
  button.disabled = true;
  await api(`/api/trips/${encodeURIComponent(selectedTrip)}/reconciliation/transaction-decision`, {
    method: "POST",
    body: JSON.stringify({ group_id: button.dataset.groupId, action })
  });
  toast(
    action === "ignore"
      ? "Transaction excluded. Restore it below if needed."
      : "Transaction restored to the review list."
  );
  NLP.refreshPage();
}

document.querySelectorAll(".exclude-transaction").forEach(button => button.addEventListener("click", async () => {
  try {
    await persistTransactionDisposition(button, "ignore");
  } catch (failure) {
    toast(failure.message, true);
    button.disabled = false;
  }
}));

document.querySelectorAll(".restore-transaction, .keep-transaction").forEach(button => {
  button.addEventListener("click", async () => {
    try {
      await persistTransactionDisposition(button, "keep");
    } catch (failure) {
      toast(failure.message, true);
      button.disabled = false;
    }
  });
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
    NLP.refreshPage();
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
    NLP.refreshPage();
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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});

})();
