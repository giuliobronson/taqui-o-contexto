// Victory modal and tear-off coupon (PRD RF-5).
(() => {
  const TEAR_DISTANCE = 48; // px dragged down before the stub tears
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const storageKey = (ticket) => `taqui:torn:${ticket.dataset.ticket}`;
  const wasTorn = (ticket) => {
    try {
      return localStorage.getItem(storageKey(ticket)) === "1";
    } catch {
      return false;
    }
  };
  const rememberTorn = (ticket) => {
    try {
      localStorage.setItem(storageKey(ticket), "1");
    } catch {
      /* private mode: the coupon is just sealed again next time */
    }
  };

  function seal(ticket) {
    if (ticket.dataset.ready) return;
    ticket.dataset.ready = "1";
    if (wasTorn(ticket)) {
      ticket.classList.add("is-torn");
      return;
    }
    ticket.classList.add("is-sealed");
    ticket.querySelector("[data-code]").inert = true;
  }

  function tear(ticket) {
    if (!ticket.classList.contains("is-sealed")) return;
    const code = ticket.querySelector("[data-code]");
    const stub = ticket.querySelector("[data-tear]");
    ticket.classList.remove("is-sealed");
    code.inert = false;
    rememberTorn(ticket);
    const done = () => {
      ticket.classList.remove("is-tearing");
      ticket.classList.add("is-torn");
      ticket.querySelector("[data-copy]")?.focus();
    };
    if (reducedMotion()) return done();
    ticket.classList.add("is-tearing");
    stub.addEventListener("animationend", done, { once: true });
  }

  // Drag the stub down (mouse or finger); a tap or click tears it too.
  let drag = null;
  document.addEventListener("pointerdown", (event) => {
    const stub = event.target.closest("[data-tear]");
    if (!stub || !stub.closest(".is-sealed")) return;
    drag = { stub, startY: event.clientY, moved: 0 };
    stub.setPointerCapture(event.pointerId);
  });
  document.addEventListener("pointermove", (event) => {
    if (!drag) return;
    drag.moved = Math.max(0, event.clientY - drag.startY);
    drag.stub.style.setProperty("--drag-y", `${drag.moved * 0.6}px`);
    drag.stub.style.setProperty("--drag-rotate", `${Math.min(drag.moved * 0.08, 8)}deg`);
  });
  const endDrag = () => {
    if (!drag) return;
    const { stub, moved } = drag;
    drag = null;
    if (moved >= TEAR_DISTANCE) {
      stub.dataset.dragged = "1"; // the click that follows must not tear again
      tear(stub.closest("[data-ticket]"));
    } else if (moved > 6) {
      stub.dataset.dragged = "1"; // a short drag springs back
      stub.style.removeProperty("--drag-y");
      stub.style.removeProperty("--drag-rotate");
    }
  };
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);

  document.addEventListener("click", (event) => {
    const stub = event.target.closest("[data-tear]");
    if (stub) {
      if (stub.dataset.dragged) {
        delete stub.dataset.dragged;
        return;
      }
      tear(stub.closest("[data-ticket]"));
      return;
    }

    const copy = event.target.closest("[data-copy]");
    if (copy) {
      const label = copy.textContent;
      const done = () => {
        copy.textContent = "Copiado!";
        setTimeout(() => (copy.textContent = label), 2000);
      };
      if (navigator.clipboard) {
        navigator.clipboard.writeText(copy.dataset.copy).then(done, () => selectCode(copy));
      } else {
        selectCode(copy);
      }
      return;
    }

    const opener = event.target.closest("[data-open-dialog]");
    if (opener) {
      document.getElementById(opener.dataset.openDialog)?.showModal();
      return;
    }

    // Clicking the backdrop closes the modal.
    if (event.target instanceof HTMLDialogElement) event.target.close();
  });

  function selectCode(button) {
    const code = button.closest("[data-code]").querySelector("code");
    const range = document.createRange();
    range.selectNodeContents(code);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function init(root = document) {
    root.querySelectorAll("[data-ticket]").forEach(seal);
    root.querySelectorAll("dialog[data-autoshow]").forEach((dialog) => {
      dialog.removeAttribute("data-autoshow");
      dialog.showModal();
      // Drop ?celebrate=1 so a reload does not reopen the modal.
      const url = new URL(window.location.href);
      url.searchParams.delete("celebrate");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init());
  } else {
    init();
  }
  document.addEventListener("htmx:load", (event) => init(event.detail.elt));
})();
