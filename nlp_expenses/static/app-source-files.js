(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { state, selectedTrip, api, toast } = NLP;
  let sourceFileSignature = NLP.sourceFileSignature;
  let sourceFilePollTimer = null;
document.getElementById("refresh-files-button")?.addEventListener("click", () => NLP.refreshPage());

function dashboardHasActiveEditing() {
  return NLP.hasUnsavedWork();
}

async function detectExternalFileChanges() {
  if (!selectedTrip || document.hidden || dashboardHasActiveEditing()) return;
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/file-state`, { method: "GET" });
    if (payload.busy) return;
    const nextSignature = payload.file_state?.signature || "";
    if (sourceFileSignature && nextSignature && nextSignature !== sourceFileSignature) {
      window.clearInterval(sourceFilePollTimer);
      NLP.requestSafeRefresh("Receipt or statement folders changed.");
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


async function uploadFiles(kind, files) {
  if (!files?.length) return;
  const data = new FormData();
  for (const file of files) data.append("files", file);
  toast(`Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
  try {
    const payload = await api(`/api/trips/${encodeURIComponent(selectedTrip)}/upload/${kind}`, { method: "POST", body: data });
    const duplicates = payload.files.filter(file => file.status === "duplicate").length;
    if (duplicates) toast(`${duplicates} identical file${duplicates === 1 ? " was" : "s were"} already present.`);
    NLP.refreshPage();
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
    NLP.refreshPage();
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
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

document.querySelectorAll(".save-statement-mapping").forEach(button => button.addEventListener("click", async () => {
  const form = button.closest(".statement-mapping-form");
  if (!form) return;
  const mapping = {};
  form.querySelectorAll(".statement-column-map").forEach(select => {
    if (select.value !== "") mapping[select.dataset.field] = Number(select.value);
  });
  button.disabled = true;
  try {
    await api(`/api/trips/${encodeURIComponent(selectedTrip)}/statement-import-profile`, {
      method: "POST",
      body: JSON.stringify({
        filename: button.dataset.filename,
        header_row: Number(form.querySelector(".statement-header-row")?.value || 0),
        mapping,
        sign_convention: form.querySelector(".statement-sign-convention")?.value || "",
        date_convention: form.querySelector(".statement-profile-date-convention")?.value || ""
      })
    });
    toast("Statement mapping saved and previewed. Sync again before generating.");
    NLP.refreshPage();
  } catch (failure) { toast(failure.message, true); button.disabled = false; }
}));

})();
