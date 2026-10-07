// deploy.yml replaces __BUILD__ with the commit SHA, so every deploy gets fresh asset URLs and a fresh cache.
const BUILD = '__BUILD__';
const CACHE_NAME = `storm-desk-shell-${BUILD}`;
const APP_ROOT = self.registration.scope;
const SHELL = ['', `style.css?v=${BUILD}`, `app.js?v=${BUILD}`, 'site.webmanifest', 'app-icon.svg'].map(path => new URL(path, APP_ROOT).href);

function remember(request, response) {
  if (!response.ok) return;
  const copy = response.clone();
  caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
}

self.addEventListener('install', event => {
  // Bypass the HTTP cache so a new worker never precaches the previous deploy's files.
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL.map(url => new Request(url, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (/\/(dashboard|layers)\.json$/.test(url.pathname)) return;
  // Network first: the cache is only an offline fallback, so a deploy can never be masked by a stale shell.
  if (request.mode === 'navigate') {
    // Revalidate the page itself; it names the build-stamped script and stylesheet.
    event.respondWith(fetch(request.url, { cache: 'no-cache' }).then(response => {
      if (url.href === APP_ROOT) remember(APP_ROOT, response);
      return response;
    }).catch(async () => (await caches.match(APP_ROOT)) || Response.error()));
    return;
  }
  event.respondWith(fetch(request, { cache: 'no-cache' }).then(response => {
    remember(request, response);
    return response;
  }).catch(async () => (await caches.match(request)) || Response.error()));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
    const app = clients.find(client => new URL(client.url).origin === self.location.origin);
    return app ? app.focus() : self.clients.openWindow(APP_ROOT);
  }));
});
