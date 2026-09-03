(() => {
  "use strict";

  document.documentElement.dataset.frontendReady = "loading";
  window.addEventListener("error", event => {
    document.documentElement.dataset.frontendError = event.message || "JavaScript error";
  });
  window.addEventListener("unhandledrejection", event => {
    document.documentElement.dataset.frontendError =
      event.reason?.message || String(event.reason || "Unhandled promise rejection");
  });

  const state = JSON.parse(document.getElementById("initial-state").textContent);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const selectedTrip = state.selected?.name || "";
  const currencyCodes = new Set((state.currencies || []).map(currency => currency.code));

  async function api(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { ...options, headers });
    const payload = response.headers.get("content-type")?.includes("application/json")
      ? await response.json()
      : {};
    if (!response.ok) {
      const failure = new Error(payload.error || `Request failed (${response.status})`);
      failure.fields = payload.fields || {};
      failure.status = response.status;
      throw failure;
    }
    return payload;
  }

  function toast(message, isError = false, persistent = false) {
    const element = document.getElementById("toast");
    if (!element) return;
    element.textContent = message;
    element.className = `toast${isError ? " error" : ""}`;
    if (!persistent) window.setTimeout(() => element.classList.add("hidden"), 3500);
  }

  function openDialog(id) {
    document.getElementById(id)?.showModal();
  }

  function collapsePreference(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_failure) {
      return null;
    }
  }

  function saveCollapsePreference(key, collapsed) {
    try {
      window.localStorage.setItem(key, collapsed ? "collapsed" : "expanded");
    } catch (_failure) {
      // Collapsing still works when browser storage is unavailable.
    }
  }

  function collapseKey(kind, identifier) {
    return `nlp-expenses:${selectedTrip || "no-trip"}:${kind}:${identifier}`;
  }

  function hasUnsavedWork() {
    if (document.querySelector("form[data-dirty='true']")) return true;
    if (document.querySelector("dialog[open]")) return true;
    const active = document.activeElement;
    return Boolean(active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName));
  }

  function refreshPage() {
    if (window.NLPExpenses?.testHooks?.refreshPage) {
      window.NLPExpenses.testHooks.refreshPage();
      return;
    }
    window.location.reload();
  }

  function requestSafeRefresh(message = "Background work completed.") {
    if (!hasUnsavedWork()) {
      window.setTimeout(refreshPage, 350);
      return true;
    }
    let notice = document.getElementById("pending-refresh-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "pending-refresh-notice";
      notice.className = "pending-refresh-notice";
      const text = document.createElement("span");
      text.className = "pending-refresh-message";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button primary small";
      button.textContent = "Refresh when ready";
      button.addEventListener("click", refreshPage);
      notice.append(text, button);
      document.body.append(notice);
    }
    notice.querySelector(".pending-refresh-message").textContent =
      `${message} Your unsaved input is still on this page.`;
    toast("Background work completed; refresh when your current edit is saved.", false, true);
    return false;
  }

  document.addEventListener("input", event => {
    event.target.closest?.("form")?.setAttribute("data-dirty", "true");
  });
  document.addEventListener("change", event => {
    event.target.closest?.("form")?.setAttribute("data-dirty", "true");
  });

  window.NLPExpenses = {
    state,
    selectedTrip,
    currencyCodes,
    sourceFileSignature: state.selected?.file_state?.signature || "",
    api,
    toast,
    openDialog,
    collapsePreference,
    saveCollapsePreference,
    collapseKey,
    hasUnsavedWork,
    refreshPage,
    requestSafeRefresh,
    testHooks: {},
  };
})();
