(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { state, selectedTrip, api, toast, openDialog } = NLP;
document.getElementById("open-approval-button")?.addEventListener("click", () => openDialog("approval-dialog"));

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
    NLP.refreshPage();
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
    NLP.refreshPage();
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

document.documentElement.dataset.frontendReady = "complete";

})();
