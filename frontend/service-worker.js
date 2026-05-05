// Taskboard PWA service worker — caches the app shell so the UI loads
// offline, and lets API requests fall through to network with a graceful
// fallback when the device is offline.
//
// Bump SHELL_VERSION to invalidate the shell cache on the next page load.
const SHELL_VERSION = 'v1';
const SHELL_CACHE = `taskboard-shell-${SHELL_VERSION}`;
const CDN_CACHE   = `taskboard-cdn-${SHELL_VERSION}`;

// App shell URLs — same-origin static assets that we want available offline.
const SHELL_URLS = [
  '/',
  '/index.html',
  '/login.html',
  '/admin.html',
  '/manifest.webmanifest',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
  '/favicon-32.png',
];

// Cross-origin CDNs the app pulls from. We runtime-cache them, so a slow
// connection or dead CDN doesn't bring down the UI on subsequent loads.
const CDN_HOSTS = new Set([
  'unpkg.com',
  'cdn.jsdelivr.net',
  'fonts.googleapis.com',
  'fonts.gstatic.com',
]);

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // Best-effort: ignore individual misses so the install doesn't fail
    // wholesale if (say) admin.html isn't yet generated.
    await Promise.all(SHELL_URLS.map(async (u) => {
      try { await cache.add(u); } catch (e) { /* skip */ }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter(k => k !== SHELL_CACHE && k !== CDN_CACHE)
      .map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

// Allow the page to ask the SW to clear caches (e.g. after logout).
self.addEventListener('message', (event) => {
  const msg = event.data || {};
  if (msg.type === 'CLEAR_CACHES') {
    event.waitUntil(caches.keys().then(keys =>
      Promise.all(keys.map(k => caches.delete(k)))
    ));
  } else if (msg.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle GETs. POST/PATCH/DELETE flow through to the network as-is.
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // ---- Same-origin requests ----
  if (url.origin === self.location.origin) {
    // Never intercept API calls — auth-sensitive, often stateful, and the
    // app is built to handle network failures itself (localStorage cache,
    // BroadcastChannel sync). Letting the browser do its thing keeps the
    // SW out of correctness questions around per-user data.
    if (url.pathname.startsWith('/api/')) return;

    // Navigation requests (HTML pages): network-first, fall back to the
    // cached shell so the app still boots offline.
    if (req.mode === 'navigate') {
      event.respondWith((async () => {
        try {
          const fresh = await fetch(req);
          // Stash a copy for next time
          const cache = await caches.open(SHELL_CACHE);
          cache.put(req, fresh.clone()).catch(() => {});
          return fresh;
        } catch (e) {
          const cache = await caches.open(SHELL_CACHE);
          return (await cache.match(req)) ||
                 (await cache.match('/index.html')) ||
                 new Response('Offline', { status: 503, statusText: 'Offline' });
        }
      })());
      return;
    }

    // Static assets: cache-first.
    event.respondWith((async () => {
      const cache = await caches.open(SHELL_CACHE);
      const cached = await cache.match(req);
      if (cached) return cached;
      try {
        const fresh = await fetch(req);
        if (fresh.ok) cache.put(req, fresh.clone()).catch(() => {});
        return fresh;
      } catch (e) {
        return cached || new Response('Offline', { status: 503 });
      }
    })());
    return;
  }

  // ---- Cross-origin: cache the known CDNs (fonts, React, marked, hljs) ----
  if (CDN_HOSTS.has(url.host)) {
    event.respondWith((async () => {
      const cache = await caches.open(CDN_CACHE);
      const cached = await cache.match(req);
      if (cached) {
        // Refresh in the background so updates roll in over time
        fetch(req).then(res => {
          if (res && res.ok) cache.put(req, res.clone()).catch(() => {});
        }).catch(() => {});
        return cached;
      }
      try {
        const fresh = await fetch(req);
        if (fresh.ok) cache.put(req, fresh.clone()).catch(() => {});
        return fresh;
      } catch (e) {
        return new Response('Offline', { status: 503 });
      }
    })());
  }
});
