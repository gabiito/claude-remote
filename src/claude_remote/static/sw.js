// Service Worker — claude-remote
// SW_VERSION bumps trigger a re-install on next page load (browser refetches
// /sw.js whenever the byte stream differs from the cached version).

const SW_VERSION = 'v2';

// Presence-aware push (#6): pages post {type:'cr-activity'} on real user
// interaction. We keep only the latest timestamp. A push is suppressed
// only if a window is focused/visible AND activity is within this window —
// an open-but-idle tab (you walked away) still buzzes the phone. If the SW
// was restarted lastActivityAt is 0 → we fail OPEN (show the notification).
const ACTIVITY_WINDOW_MS = 5 * 60 * 1000;
let lastActivityAt = 0;

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'cr-activity') {
    // Use SW receipt time — don't trust the page's clock.
    lastActivityAt = Date.now();
  }
});

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

  event.waitUntil((async () => {
    const recentlyActive = Date.now() - lastActivityAt < ACTIVITY_WINDOW_MS;
    if (recentlyActive) {
      const wins = await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      });
      const present = wins.some(
        (c) => c.focused || c.visibilityState === 'visible'
      );
      // You're actively using the app and saw it live via SSE — no buzz.
      if (present) return;
    }
    await self.registration.showNotification(title, {
      body,
      data,
      icon: '/static/favicon.svg',
      badge: '/static/favicon.svg',
      tag,
    });
  })());
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
