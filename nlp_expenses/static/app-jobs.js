(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { state, api, toast, requestSafeRefresh } = NLP;

  const configurations = {
    generation: {
      panel: "job-panel",
      message: "job-message",
      status: "job-status",
      error: "job-error",
      warnings: "job-warnings",
      progress: "progress-bar",
      fallback: "Working…",
      controls: "#generate-button",
      completed: "Workbook generation completed.",
    },
    reconciliation: {
      panel: "reconciliation-job-panel",
      message: "reconciliation-job-message",
      status: "reconciliation-job-status",
      error: "reconciliation-job-error",
      warnings: "reconciliation-job-warnings",
      progress: "reconciliation-progress-bar",
      fallback: "Syncing…",
      controls: "#sync-reconciliation-button, #rebuild-reconciliation-button, .resync-card-matcher",
      completed: "Statement reconciliation completed.",
    },
    line_items: {
      panel: "line-item-job-panel",
      message: "line-item-job-message",
      status: "line-item-job-status",
      error: "line-item-job-error",
      warnings: "line-item-job-warnings",
      progress: "line-item-progress-bar",
      fallback: "Scanning…",
      controls: ".receipt-scan-button",
      completed: "Receipt scanning completed.",
    },
  };

  function setControls(kind, disabled) {
    document.querySelectorAll(configurations[kind].controls)
      .forEach(control => { control.disabled = disabled; });
  }

  function render(kind, job) {
    const config = configurations[kind];
    const panel = document.getElementById(config.panel);
    if (!panel) return;
    panel.classList.remove("hidden");
    document.getElementById(config.message).textContent = job.message || config.fallback;
    document.getElementById(config.status).textContent = (job.status || "").replaceAll("_", " ");
    document.getElementById(config.error).textContent = job.error || "";
    const warnings = document.getElementById(config.warnings);
    warnings?.replaceChildren(...(job.warnings || []).map(message => {
      const item = document.createElement("li");
      item.textContent = message;
      return item;
    }));
    const percentage = job.stage === "complete"
      ? 100
      : Math.min(96, Math.max(8, (job.current / Math.max(job.total, 1)) * 100));
    const progress = document.getElementById(config.progress);
    if (progress) progress.style.width = `${percentage}%`;
  }

  async function poll(kind, jobId) {
    try {
      const payload = await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "GET" });
      render(kind, payload.job);
      if (["queued", "running"].includes(payload.job.status)) {
        window.setTimeout(() => poll(kind, jobId), 900);
      } else if (["succeeded", "succeeded_warnings"].includes(payload.job.status)) {
        requestSafeRefresh(configurations[kind].completed);
      } else {
        setControls(kind, false);
      }
    } catch (failure) {
      toast(failure.message, true);
      setControls(kind, false);
    }
  }

  function start(kind, job) {
    setControls(kind, true);
    render(kind, job);
    poll(kind, job.id);
  }

  function restore(kind, job) {
    if (!job) return;
    render(kind, job);
    if (["queued", "running"].includes(job.status)) poll(kind, job.id);
  }

  NLP.jobs = { start, poll, render, setControls };
  restore("generation", state.job);
  restore("reconciliation", state.reconciliation_job);
  restore("line_items", state.line_item_job);
})();
