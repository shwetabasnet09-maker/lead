document.addEventListener("DOMContentLoaded", () => {
  const menu = document.querySelector("[data-menu-button]");
  if (menu) {
    menu.addEventListener("click", () => {
      const nav = document.getElementById("primary-nav");
      menu.setAttribute("aria-expanded", nav.classList.toggle("open"));
    });
  }

  // Success messages fade after a few seconds; errors stay until the page changes.
  document.querySelectorAll(".message.success").forEach((message) => {
    setTimeout(() => message.classList.add("fade"), 6000);
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // While a scan runs, check every few seconds and reload once it finishes so new leads and runs show.
  const status = document.querySelector("[data-scan-status]");
  if (status && status.dataset.running === "true") {
    const poll = async () => {
      try {
        const response = await fetch(status.dataset.scanStatus, { headers: { Accept: "application/json" } });
        const data = await response.json();
        if (!data.running) return window.location.reload();
      } catch (error) {
        /* Network blip: try again on the next tick. */
      }
      setTimeout(poll, 5000);
    };
    setTimeout(poll, 5000);
  }
});
