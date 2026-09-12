{% autoescape off %}// Rendered by stockroom.views.service_worker. CACHE changes whenever a
// precached file or the offline page changes, so a deploy that ships new
// assets installs a new worker, which drops the old cache on activate.
const CACHE = "stockroom-{{ version }}";
const PRECACHE = {{ precache }};
const OFFLINE_URL = {{ offline_url }};
const LOGOUT_URL = {{ logout_url }};
const OFFLINE_FRAGMENT = {{ offline_fragment }};

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

function offlineFragment(request) {
  const headers = { "Content-Type": "text/html; charset=utf-8" };
  if (request.method !== "GET") {
    // Don't swap the message over a form the user just filled in; say so in a toast instead.
    headers["HX-Reswap"] = "none";
    headers["HX-Trigger"] = JSON.stringify({ toast: { message: "You're offline, so that wasn't saved. Try again when you're back online." } });
  }
  return new Response(OFFLINE_FRAGMENT, { headers });
}

self.addEventListener("install", (event) => event.waitUntil(precache()));

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

// A new worker waits until the page says the user tapped "reload".
self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.method === "POST" && url.pathname === LOGOUT_URL) {
    // Devices are shared: wipe every cache once the logout has gone through,
    // then put back only the public app shell so the offline page still works.
    const loggedOut = fetch(request).then(async (response) => {
      await clearCaches();
      return response;
    });
    event.respondWith(loggedOut.catch(offlinePage));
    event.waitUntil(loggedOut.then(precache).catch(() => {}));
  } else if (request.headers.has("HX-Request")) {
    event.respondWith(fetch(request).catch(() => offlineFragment(request)));
  } else if (request.mode === "navigate") {
    // Pages hold practice data and prices, so they're never cached.
    event.respondWith(fetch(request).catch(offlinePage));
  } else if (request.method === "GET") {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
  }
});
{% endautoescape %}
