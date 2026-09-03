(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { state, selectedTrip, api, toast, openDialog } = NLP;
  let deleteTripName = selectedTrip || "";
document.getElementById("new-trip-button")?.addEventListener("click", () => openDialog("new-trip-dialog"));
document.getElementById("empty-new-trip-button")?.addEventListener("click", () => openDialog("new-trip-dialog"));
document.querySelectorAll(".open-settings-button").forEach(button => {
  button.addEventListener("click", () => openDialog("settings-dialog"));
});
document.getElementById("open-accounting-button")?.addEventListener("click", () => openDialog("accounting-dialog"));
document.getElementById("open-trip-metadata-button")?.addEventListener("click", () => openDialog("trip-metadata-dialog"));

function openDeleteTripDialog(tripName, tripLabel, receiptCount = 0, statementCount = 0) {
  deleteTripName = tripName;
  const form = document.getElementById("delete-trip-form");
  form?.reset();
  const label = document.getElementById("delete-trip-label");
  if (label) label.textContent = tripLabel || tripName;
  const confirmationPrompt = document.getElementById("delete-trip-confirmation");
  if (confirmationPrompt) {
    const code = document.createElement("code");
    code.textContent = tripName;
    confirmationPrompt.replaceChildren(
      document.createTextNode("Type "),
      code,
      document.createTextNode(" to confirm"),
    );
  }
  const receipts = document.getElementById("delete-trip-receipts");
  if (receipts) receipts.textContent = receiptCount;
  const statements = document.getElementById("delete-trip-statements");
  if (statements) statements.textContent = statementCount;
  const error = document.getElementById("delete-trip-error");
  if (error) error.textContent = "";
  openDialog("delete-trip-dialog");
}
document.getElementById("open-delete-trip-button")?.addEventListener("click", () => {
  openDeleteTripDialog(
    selectedTrip,
    state.selected?.label,
    state.selected?.receipts?.length || 0,
    state.selected?.statements?.length || 0,
  );
});
document.querySelectorAll(".trip-delete-button").forEach(button => button.addEventListener("click", event => {
  event.preventDefault();
  event.stopPropagation();
  openDeleteTripDialog(
    button.dataset.tripName,
    button.dataset.tripLabel,
    Number(button.dataset.receipts || 0),
    Number(button.dataset.statements || 0),
  );
}));

document.getElementById("trip-select")?.addEventListener("change", event => {
  const archived = state.show_archived ? "&archived=1" : "";
  window.location.href = `/?trip=${encodeURIComponent(event.target.value)}${archived}`;
});


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
    NLP.refreshPage();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("delete-trip-form")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  const confirmation = String(data.get("confirmation") || "");
  const error = document.getElementById("delete-trip-error");
  const submit = form.querySelector('button[type="submit"]');
  error.textContent = "";
  if (confirmation !== deleteTripName) {
    error.textContent = `Type ${deleteTripName} exactly to confirm.`;
    return;
  }
  submit.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(deleteTripName)}`, {
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
    NLP.refreshPage();
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
      qst_receivable: data.get("account_qst_receivable"),
      expenses_recoverable_from_clients: data.get("account_expenses_recoverable_from_clients"),
      accounts_receivable: data.get("account_accounts_receivable"),
      bank_checking: data.get("account_bank_checking")
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
    NLP.refreshPage();
  } catch (failure) { error.textContent = failure.message; }
});

document.getElementById("claim-program-select")?.addEventListener("change", async event => {
  if (!window.confirm("Change this trip's reimbursement program? Existing files stay in place, but receipt review and finalization may need to be refreshed.")) {
    event.target.value = state.selected.claim_program;
    return;
  }
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/claim-program`, {
      method: "POST", body: JSON.stringify({ claim_program: event.target.value })
    });
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); event.target.value = state.selected.claim_program; }
});
})();
