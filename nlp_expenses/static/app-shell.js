(() => {
  "use strict";
  const NLP = window.NLPExpenses;
  const { collapsePreference, saveCollapsePreference, collapseKey } = NLP;

function initializeCollapsibleSections() {
  document.querySelectorAll("main section.card").forEach((section, index) => {
    const heading = section.querySelector(":scope > .card-heading");
    if (!heading) return;
    const title = heading.querySelector("h3")?.textContent.trim() || `Section ${index + 1}`;
    const identifier = section.id || title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const key = collapseKey("section", identifier);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "button secondary small section-collapse-toggle";
    const setCollapsed = (collapsed, persist = true) => {
      section.classList.toggle("section-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", collapsed ? "Expand section" : "Collapse section");
      toggle.textContent = collapsed ? "⌃" : "⌄";
      toggle.title = `${collapsed ? "Expand" : "Collapse"} ${title}`;
      if (persist) saveCollapsePreference(key, collapsed);
    };
    setCollapsed(collapsePreference(key) === "collapsed", false);
    toggle.addEventListener("click", () => setCollapsed(!section.classList.contains("section-collapsed")));
    heading.append(toggle);
  });
}

function initializeCollapsibleSubsections() {
  document.querySelectorAll(".coverage-panel, .policy-warning-panel, .review-subsection").forEach((section, index) => {
    const heading = section.querySelector(":scope > .subsection-heading");
    if (!heading) return;
    const title = heading.querySelector("h4")?.textContent.trim() || `Subsection ${index + 1}`;
    const identifier = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const key = collapseKey("subsection", identifier);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "button secondary small section-collapse-toggle";
    const setCollapsed = (collapsed, persist = true) => {
      section.classList.toggle("subsection-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", collapsed ? "Expand subsection" : "Collapse subsection");
      toggle.textContent = collapsed ? "⌃" : "⌄";
      toggle.title = `${collapsed ? "Expand" : "Collapse"} ${title}`;
      if (persist) saveCollapsePreference(key, collapsed);
    };
    setCollapsed(collapsePreference(key) === "collapsed", false);
    toggle.addEventListener("click", () => setCollapsed(!section.classList.contains("subsection-collapsed")));
    heading.append(toggle);
  });
}


document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));

initializeCollapsibleSections();
initializeCollapsibleSubsections();
})();
