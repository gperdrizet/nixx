// Nixx PWA service worker
// Enables installability. Network-first strategy: serve fresh content when
// online, fall back to cache for the app shell when offline.

const CACHE = 'nixx-pwa-v1';
const SHELL = ['/app/', '/app/manifest.json', '/app/icon.svg'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Pass through API calls - never cache these
  if (url.pathname.startsWith('/v1/') || url.pathname === '/health') return;

  // Network-first for app shell
  event.respondWith(
    fetch(event.request)
      .then(resp => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
