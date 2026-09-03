(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { selectedTrip, api, toast, jobs } = NLP;
document.getElementById("generate-button")?.addEventListener("click", async event => {
  event.target.disabled = true;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/generate`, {
      method: "POST",
      body: JSON.stringify({})
    });
    jobs.start("generation", payload.job);
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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); event.target.disabled = false; }
});
})();
