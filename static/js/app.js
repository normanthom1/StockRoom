// All of StockRoom's page JavaScript. It lives in this file, not in inline
// <script>s or on*= attributes, so the Content Security Policy can forbid
// inline script entirely. Behaviour hangs off data-* attributes instead.

const toast = (message) => window.dispatchEvent(new CustomEvent("toast", { detail: { message } }));

// --- Alpine components. The CSP build only reads property paths ("open",
// "item.message") and calls methods, so every expression is a name here. ---

document.addEventListener("alpine:init", () => {
  Alpine.data("toastHub", () => ({
    items: [],
    add(event) {
      const { message, undo_url: undoUrl = null } = event.detail;
      // A repeat replaces the last one instead of stacking over the tiles being tapped.
      this.items = this.items.filter((item) => item.message !== message);
      const id = Date.now() + Math.random();
      this.items.push({ id, message, undoUrl });
      setTimeout(() => this.remove(id), 6000);
    },
    remove(id) {
      this.items = this.items.filter((item) => item.id !== id);
    },
    dismiss() {
      this.remove(this.item.id);
    },
    async undo() {
      const item = this.item;
      // htmx.ajax (not a plain fetch) so the response's own HX-Trigger
      // toast - e.g. confirming the undo - fires normally.
      await htmx.ajax("POST", item.undoUrl, { swap: "none" });
      this.remove(item.id);
    },
  }));

  // A Django message rendered with the page.
  Alpine.data("flash", () => ({
    show: true,
    init() {
      setTimeout(() => { this.show = false; }, 6000);
    },
    hide() {
      this.show = false;
    },
  }));

  Alpine.data("dropdown", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    },
  }));

  Alpine.data("disclosure", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    get arrow() {
      return this.open ? "▴" : "▾";
    },
  }));

});

// --- Delegated handlers for data-* attributes ---

document.addEventListener("click", (event) => {
  const target = event.target;
  const opener = target.closest("[data-open-dialog]");
  if (opener) document.getElementById(opener.dataset.openDialog).showModal();
  if (target.closest("[data-close-dialog]")) target.closest("dialog")?.close();
  // A click on the dialog itself (not its contents) is a click on the backdrop.
  if (target.matches("dialog[data-backdrop-close]")) target.close();
  if (target.closest("[data-reload]")) location.reload();
  target.closest("[data-select-all]")?.select();
  const tile = target.closest("[data-capture-tile]");
  if (tile) openCaptureSheet(tile);
  const codeKey = target.closest("[data-code-key]");
  if (codeKey) typeCode(codeKey.dataset.codeKey);
  if (target.closest("[data-code-clear]")) document.querySelector("[data-code-input]").value = "";
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  // Forms whose buttons post via htmx; Enter in a field mustn't do a full-page submit.
  if (form.matches("[data-htmx-only]")) event.preventDefault();
  if (form.matches("[data-disable-on-submit]")) {
    // Next tick: disabling now would drop the clicked button's name/value from the submission.
    setTimeout(() => form.querySelectorAll("button").forEach((button) => { button.disabled = true; }));
  }
});

document.addEventListener("input", (event) => {
  if (event.target.matches("[data-search-input]")) filterList(event.target.value);
  if (event.target.matches("[data-code-input]")) submitCodeIfComplete(event.target);
});

// --- Code pad: from the keypad or a keyboard. A manager's 4 digits submit
// themselves; an assistant's 2 need Go, since "12" could be the start of "1234". ---

function typeCode(digit) {
  const input = document.querySelector("[data-code-input]");
  if (input.value.length >= 4) return;
  input.value += digit;
  submitCodeIfComplete(input);
}

function submitCodeIfComplete(input) {
  if (/^\d{4}$/.test(input.value)) input.form.requestSubmit();
}

// --- Shared bottom sheet ---

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id === "sheet-content") document.getElementById("sheet").showModal();
});
document.addEventListener("sheet-close", () => document.getElementById("sheet")?.close());

// Ask StockRoom: clear the question once it's answered, and bring the answer into view.
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id !== "chat") return;
  document.querySelector("[data-ask]")?.reset();
  event.target.lastElementChild?.scrollIntoView({ block: "nearest" });
});
// Only reachable without a controlling service worker (it answers every htmx request itself).
document.addEventListener("htmx:sendError", () => toast("Couldn't reach StockRoom. Check your connection and try again."));

// --- Search-as-you-type over a page's [data-search] rows (the capture grid,
// the reorder list's "add" list). In the browser so it works with no signal. ---

function filterList(query) {
  const q = query.trim().toLowerCase();
  let shown = 0;
  for (const row of document.querySelectorAll("[data-search]")) {
    row.hidden = !row.dataset.search.includes(q);
    if (!row.hidden) shown++;
  }
  document.getElementById("no-match").hidden = shown > 0;
}

// --- Log usage: the capture grid works with no signal, so the tap sheet
// runs here rather than on the server. ---

function openCaptureSheet(tile) {
  const item = tile.dataset;
  const sheet = document.getElementById("capture-sheet").content.cloneNode(true);
  sheet.querySelector("[data-slot=name]").textContent = item.name;
  if (item.hasQty) {
    sheet.querySelector("[data-slot=used-one]").textContent = `Used 1 ${item.unit}`;
    sheet.querySelector("[data-slot=change]").textContent = `${item.qty} → ${item.after}`;
  } else {
    sheet.querySelector("[data-action=usedOne]").remove();
  }
  for (const button of sheet.querySelectorAll("[data-action]")) {
    button.setAttribute(`hx-${button.dataset.method || "post"}`, item[button.dataset.action]);
  }
  const content = document.getElementById("sheet-content");
  content.replaceChildren(sheet);
  htmx.process(content);
  document.getElementById("sheet").showModal();
}

// Every capture tap gets a client_id minted here, before its first send, so a
// tap the server saved but whose reply got lost dedupes when the service
// worker replays it. X-Capture tells the worker to queue it if it can't get through.
document.addEventListener("htmx:configRequest", (event) => {
  const capture = event.detail.elt.closest("[data-capture]");
  if (!capture) return;
  event.detail.headers["X-Capture"] = "1";
  event.detail.parameters.client_id = crypto.randomUUID();
  event.detail.parameters.user_id = capture.dataset.capture;
});

// --- Service worker: registration, the update toast, and the offline queue's status bar ---

if ("serviceWorker" in navigator) {
  let updating = false;
  const offerUpdate = (worker) => {
    const button = document.getElementById("sw-update");
    button.hidden = false;
    button.onclick = () => {
      updating = true;
      worker.postMessage("skip-waiting");
    };
  };
  // Only reload once the user asked for it; the first install also changes the controller.
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (updating) location.reload();
  });
  window.addEventListener("load", async () => {
    const registration = await navigator.serviceWorker.register("/sw.js");
    if (registration.waiting && navigator.serviceWorker.controller) offerUpdate(registration.waiting);
    registration.addEventListener("updatefound", () => {
      const worker = registration.installing;
      worker.addEventListener("statechange", () => {
        if (worker.state === "installed" && navigator.serviceWorker.controller) offerUpdate(worker);
      });
    });
  });

  const status = document.getElementById("sync-status");
  if (status) {
    let pending = 0;
    const taps = (n) => `${n} ${n === 1 ? "tap" : "taps"}`;
    const render = () => {
      status.textContent = navigator.onLine
        ? `${taps(pending)} waiting to sync`
        : pending ? `Offline · ${taps(pending)} waiting to sync` : "Offline · taps are saved and sync later";
      status.hidden = navigator.onLine && pending === 0;
    };
    const csrf = JSON.parse(document.body.getAttribute("hx-headers"))["X-CSRFToken"];
    const replay = () => navigator.serviceWorker.ready.then((registration) => registration.active.postMessage({ type: "replay", csrf }));
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type !== "queue") return;
      pending = event.data.pending;
      render();
      if (event.data.synced) toast(`${taps(event.data.synced)} logged offline ${event.data.synced === 1 ? "has" : "have"} synced.`);
      if (event.data.dropped) toast(`${taps(event.data.dropped)} couldn't be synced: more than 7 days old, or the item was removed.`);
    });
    window.addEventListener("online", () => { render(); replay(); });
    window.addEventListener("offline", render);
    render();
    replay();
  }
}

// --- iOS install hint: Safari has no install prompt of its own, so iOS users
// need telling how to add the app to their home screen by hand. ---

(function () {
  const hint = document.getElementById("ios-install-hint");
  if (!hint) return;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const isStandalone = window.navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
  let dismissed = false;
  try {
    dismissed = localStorage.getItem("stockroom-a2hs-dismissed") === "1";
  } catch {
    // Private browsing can throw; just show the hint every time.
  }
  if (isIOS && !isStandalone && !dismissed) hint.hidden = false;
  document.getElementById("ios-install-hint-dismiss").addEventListener("click", () => {
    hint.hidden = true;
    try {
      localStorage.setItem("stockroom-a2hs-dismissed", "1");
    } catch {
      // Nothing to persist to in private browsing; it'll just show again.
    }
  });
})();
