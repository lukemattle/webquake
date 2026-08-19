const CACHE_NAME = 'webquake-cache-v2';
const urlsToCache = [
  '/icons/webquake-192.png',
  '/icons/webquake-512.png'
];

self.addEventListener('install', function(event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(urlsToCache);
    }).catch(function(err) {
      console.error('[SW] Cache install failed:', err);
    })
  );
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', function(event) {
  // Never cache index.html — always fetch fresh so JS changes take effect immediately
  if (event.request.url.endsWith('/') || event.request.url.includes('index.html')) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    caches.match(event.request).then(function(response) {
      return response || fetch(event.request);
    })
  );
});