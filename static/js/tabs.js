// Ranking / Dicas tabs on narrow screens (PRD: layout responsivo). Without this
// script both panels are simply stacked; on wide screens both are always shown.
(() => {
  function setup() {
    const tablist = document.querySelector(".game-tabs");
    const layout = document.querySelector(".game-layout");
    if (!tablist || !layout) return;
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];

    const select = (tab, focus = false) => {
      for (const other of tabs) {
        const selected = other === tab;
        other.setAttribute("aria-selected", String(selected));
        other.tabIndex = selected ? 0 : -1;
      }
      layout.dataset.activeTab = tab.id.replace("tab-", "");
      if (focus) tab.focus();
    };

    tablist.hidden = false;
    layout.classList.add("has-tabs");
    for (const panel of layout.querySelectorAll("[data-panel]")) {
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", `tab-${panel.dataset.panel}`);
    }

    tablist.addEventListener("click", (event) => {
      const tab = event.target.closest('[role="tab"]');
      if (tab) select(tab);
    });
    // Arrow keys move between tabs (WAI-ARIA tabs pattern).
    tablist.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) {
        event.preventDefault();
        select(tabs[(index + step + tabs.length) % tabs.length], true);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
