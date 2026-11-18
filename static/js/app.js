// Shows server errors from HTMX requests (e.g. 409 "Este player já deu palpites")
// in a small toast, instead of failing silently.
(() => {
  function toast(message) {
    let box = document.getElementById("toast");
    if (!box) {
      box = document.createElement("div");
      box.id = "toast";
      box.className = "toast";
      box.setAttribute("role", "alert");
      document.body.append(box);
    }
    box.textContent = message;
    box.classList.add("is-visible");
    clearTimeout(box.hideTimer);
    box.hideTimer = setTimeout(() => box.classList.remove("is-visible"), 5000);
  }

  document.addEventListener("htmx:responseError", (event) => {
    const xhr = event.detail.xhr;
    let message = "Algo deu errado. Tente de novo.";
    try {
      const detail = JSON.parse(xhr.responseText).detail;
      if (typeof detail === "string") message = detail;
    } catch {
      /* not JSON: keep the generic message */
    }
    if (xhr.status === 401) message = "Sua sessão expirou. Entre de novo.";
    toast(message);
  });

  document.addEventListener("htmx:sendError", () =>
    toast("Sem conexão com o servidor. Verifique sua internet."),
  );
})();
