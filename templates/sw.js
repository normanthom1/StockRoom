{% autoescape off %}// Rendered by stockroom.views.service_worker. CACHE changes whenever a
// precached file or the offline page changes, so a deploy that ships new
// assets installs a new worker, which drops the old cache on activate.
const CACHE = "stockroom-{{ version }}";
// The last-known capture grid, so taps still work offline. Holds practice
// data, so it's wiped with everything else at login and logout.
const DATA_CACHE = "stockroom-data";
const PRECACHE = {{ precache }};
const OFFLINE_URL = {{ offline_url }};
const CAPTURE_PAGE_URL = {{ capture_page_url }};
const SESSION_URLS = {{ session_urls }};
const OFFLINE_FRAGMENT = {{ offline_fragment }};
// ponytail: fixed timeout; a tap that hangs this long on patchy Wi-Fi is queued instead.
const CAPTURE_TIMEOUT_MS = 8000;

function precache() {
  // cache: "reload" skips the HTTP cache, so a new CACHE never starts from stale copies.
  return caches.open(CACHE).then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload" }))));
}

async function clearCaches() {
  for (const name of await caches.keys()) await caches.delete(name);
}

async function offlinePage() {
  return (await caches.match(OFFLINE_URL)) || new Response("You're offline.", { headers: { "Content-Type": "text/plain" } });
}

function hxResponse(body, headers) {
  return new Response(body, { headers: { "Content-Type": "text/html; charset=utf-8", ...headers } });
}

// Chromium UTF-8-encodes non-ASCII in a worker-made header, and htmx reads it
// back as Latin-1 ("·" arrives as "Â·"). \u escapes keep it ASCII.
function hxTrigger(events) {
  return JSON.stringify(events).replace(/[^\x20-\x7e]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function offlineFragment(request) {
  if (request.method === "GET") return hxResponse(OFFLINE_FRAGMENT, {});
  // Don't swap the message over a form the user just filled in; say so in a toast instead.
  return hxResponse("", {
    "HX-Reswap": "none",
    "HX-Trigger": hxTrigger({ toast: { message: "You're offline, so that wasn't saved. Try again when you're back online." } }),
  });
}

async function keepCapturePage(response) {
  // Not a redirect to the login page, and not an error page.
  if (response.ok && response.type === "basic") await (await caches.open(DATA_CACHE)).put(CAPTURE_PAGE_URL, response.clone());
}

// After a login wipes the caches, fetch the grid once so it's there if the
// signal drops before anyone has opened it.
async function warmCapturePage() {
  if (await caches.match(CAPTURE_PAGE_URL, { cacheName: DATA_CACHE })) return;
  try {
    await keepCapturePage(await fetch(CAPTURE_PAGE_URL, { credentials: "same-origin", redirect: "manual" }));
  } catch {
    // Offline; the next page load tries again.
  }
}

// --- Offline capture queue (IndexedDB, shared with nothing but this worker) ---

function openDb() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("stockroom", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("captures", { keyPath: "client_id" });
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function captures(mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("captures", mode);
    const request = fn(tx.objectStore("captures"));
    tx.oncomplete = () => resolve(request.result);
    tx.onerror = () => reject(tx.error);
  });
}

async function broadcast(message) {
  for (const client of await self.clients.matchAll({ includeUncontrolled: true })) client.postMessage(message);
}

async function capture(request) {
  const params = new URLSearchParams(await request.clone().text());
  try {
    const response = await fetch(request, { signal: AbortSignal.timeout(CAPTURE_TIMEOUT_MS) });
    // 403 is a stale CSRF token (e.g. a cached page); a replay with a fresh one will go through.
    if (response.status < 500 && response.status !== 403) return response;
  } catch {
    // Offline, or the Wi-Fi dropped mid-request: fall through and queue it.
  }
  if (!params.get("client_id")) params.set("client_id", crypto.randomUUID());
  params.set("occurred_at", new Date().toISOString());
  await captures("readwrite", (store) => store.put({
    client_id: params.get("client_id"),
    url: request.url,
    body: params.toString(),
    csrf: request.headers.get("X-CSRFToken"),
  }));
  if (self.registration.sync) self.registration.sync.register("capture-queue").catch(() => {});
  broadcast({ type: "queue", pending: await captures("readonly", (store) => store.count()), synced: 0, dropped: 0 });
  return hxResponse("", {
    "HX-Reswap": "none",
    "HX-Trigger": hxTrigger({ toast: { message: "Saved offline · will sync" }, "sheet-close": true }),
  });
}

let replaying = null;

// Send every queued tap. The server dedupes on client_id, so a tap that's
// sent twice (two triggers at once, or a reply lost on the way back) is
// still only logged once, and replay order doesn't matter.
function replay(csrf) {
  replaying ||= (async () => {
    let synced = 0;
    let dropped = 0;
    let offline = false;
    for (const entry of await captures("readonly", (store) => store.getAll())) {
      let response;
      try {
        response = await fetch(entry.url, {
          method: "POST",
          body: entry.body,
          headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf || entry.csrf },
          credentials: "same-origin",
          redirect: "manual", // logged out: keep it for later rather than "succeeding" on the login page
        });
      } catch {
        offline = true;
        break;
      }
      if (response.ok) synced++;
      // 400: too old or a future time; 404: the item's gone. Neither will ever succeed.
      else if (response.status === 400 || response.status === 404) dropped++;
      else continue; // 403/409/5xx/redirect: try again next time
      await captures("readwrite", (store) => store.delete(entry.client_id));
    }
    const pending = await captures("readonly", (store) => store.count());
    await broadcast({ type: "queue", pending, synced, dropped });
    return { pending, offline };
  })().finally(() => { replaying = null; });
  return replaying;
}

// --- Lifecycle ---

self.addEventListener("install", (event) => event.waitUntil(precache()));

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== CACHE && name !== DATA_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  // A new worker waits until the page says the user tapped "reload".
  if (event.data === "skip-waiting") self.skipWaiting();
  // The page asks on load and on the "online" event; iOS has no Background Sync.
  if (event.data?.type === "replay") event.waitUntil(Promise.all([replay(event.data.csrf), warmCapturePage()]));
});

self.addEventListener("sync", (event) => {
  if (event.tag !== "capture-queue") return;
  // Rejecting tells the browser to retry later.
  event.waitUntil(replay().then(({ offline }) => { if (offline) throw new Error("still offline"); }));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.method === "POST" && SESSION_URLS.includes(url.pathname)) {
    // Devices are shared: wipe every cache when someone logs in, out or switches, then
    // put back only the public app shell so the offline page still works.
    // Queued taps stay; the server only accepts them from the user who made them.
    const done = fetch(request).then(async (response) => {
      await clearCaches();
      return response;
    });
    event.respondWith(done.catch(offlinePage));
    event.waitUntil(done.then(precache).catch(() => {}));
  } else if (request.method === "POST" && request.headers.has("X-Capture")) {
    event.respondWith(capture(request));
  } else if (request.headers.has("HX-Request")) {
    event.respondWith(fetch(request).catch(() => offlineFragment(request)));
  } else if (request.mode === "navigate" && request.method === "GET" && url.pathname === CAPTURE_PAGE_URL) {
    event.respondWith(fetch(request).then(async (response) => {
      await keepCapturePage(response);
      return response;
    }).catch(async () => (await caches.match(CAPTURE_PAGE_URL, { cacheName: DATA_CACHE })) || offlinePage()));
  } else if (request.mode === "navigate") {
    // Every other page holds practice data or prices, so it's never cached.
    event.respondWith(fetch(request).catch(offlinePage));
  } else if (request.method === "GET") {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
  }
});
{% endautoescape %}
