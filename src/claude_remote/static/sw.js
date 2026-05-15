// Service Worker — claude-remote
// SW_VERSION bumps trigger a re-install on next page load (browser refetches
// /sw.js whenever the byte stream differs from the cached version).

const SW_VERSION = 'v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload;
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: 'claude-remote', body: event.data ? event.data.text() : '' };
  }
  const title = payload.title || 'claude-remote';
  const body = payload.body || '';
  const data = payload.data || {};
  const tag = data.event_type || 'claude-remote';

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      data,
      icon: '/static/favicon.svg',
      badge: '/static/favicon.svg',
      tag,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of all) {
      if (client.url.includes(url) && 'focus' in client) {
        return client.focus();
      }
    }
    if (self.clients.openWindow) {
      return self.clients.openWindow(url);
    }
    return undefined;
  })());
});
