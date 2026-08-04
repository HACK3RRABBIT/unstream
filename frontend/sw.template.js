/* Unstream service worker — app-shell cache with automatic updates.
 *
 * __UNSTREAM_BUILD__ is replaced at build time with a hash of the built
 * index.html, so every release ships a byte-different sw.js. The browser
 * then installs the new worker; skipWaiting + clients.claim put it in
 * charge immediately, old caches are purged, and open tabs are notified
 * so the UI can offer a reload.
 */

const CACHE = `unstream-__UNSTREAM_BUILD__`
const SHELL = ['/', '/index.html', '/manifest.webmanifest']

self.addEventListener('install', (event) => {
  self.skipWaiting()
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key !== CACHE) await caches.delete(key)
      }
      await self.clients.claim()
      for (const client of await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      })) {
        client.postMessage({ type: 'unstream:update' })
      }
    })(),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  // Only same-origin: covers, previews and fonts stream straight through.
  if (url.origin !== self.location.origin) return
  // Job data must always be live.
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return

  // Navigations: network-first, so a new release lands on the very next
  // load; the cached shell is the offline fallback.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE).then((cache) => cache.put('/index.html', copy))
          return response
        })
        .catch(() => caches.match('/index.html')),
    )
    return
  }

  // Hashed build assets are immutable — cache-first.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            const copy = response.clone()
            caches.open(CACHE).then((cache) => cache.put(request, copy))
            return response
          }),
      ),
    )
    return
  }

  // Everything else (icons, manifest): stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((hit) => {
      const network = fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE).then((cache) => cache.put(request, copy))
          return response
        })
        .catch(() => hit)
      return hit || network
    }),
  )
})
