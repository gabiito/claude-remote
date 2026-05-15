// claude-remote push subscription helper.
// Exposes window.subscribePush / window.unsubscribePush / window.isStandalonePWA.
// Registers the service worker on DOMContentLoaded (idempotent).

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const output = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    output[i] = rawData.charCodeAt(i);
  }
  return output;
}

async function registerSW() {
  if (!('serviceWorker' in navigator)) return null;
  try {
    const existing = await navigator.serviceWorker.getRegistration('/');
    if (existing) return existing;
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  } catch (e) {
    console.warn('SW registration failed', e);
    return null;
  }
}

async function fetchVapidPublicKey() {
  const r = await fetch('/api/push/vapid-key', { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`vapid-key HTTP ${r.status}`);
  const j = await r.json();
  return j.public_key;
}

async function subscribePush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    return { ok: false, reason: 'unsupported' };
  }
  const reg = await navigator.serviceWorker.ready;
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') return { ok: false, reason: 'permission-denied' };

  // REQ-9.6: reuse an existing browser subscription when present, otherwise
  // ask the push service for a new one.
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    const publicKey = await fetchVapidPublicKey();
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
  }
  const subJson = sub.toJSON();
  const r = await fetch('/api/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({
      endpoint: subJson.endpoint,
      keys: subJson.keys,
    }),
  });
  if (!r.ok) return { ok: false, reason: `subscribe HTTP ${r.status}` };
  return { ok: true, endpoint: subJson.endpoint };
}

async function unsubscribePush(endpoint) {
  // Server-side unsubscribe
  await fetch('/api/push/unsubscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ endpoint }),
  });
  // Client-side unsubscribe (best-effort)
  try {
    if ('serviceWorker' in navigator) {
      const reg = await navigator.serviceWorker.getRegistration('/');
      const sub = await reg?.pushManager.getSubscription();
      if (sub && sub.endpoint === endpoint) {
        await sub.unsubscribe();
      }
    }
  } catch (_) {
    // best-effort; server already removed the row
  }
}

function isStandalonePWA() {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true
  );
}

document.addEventListener('DOMContentLoaded', () => { registerSW(); });
window.subscribePush = subscribePush;
window.unsubscribePush = unsubscribePush;
window.isStandalonePWA = isStandalonePWA;

function pushSettingsComponent() {
  return {
    permission: 'default',
    subscribed: false,
    currentEndpoint: null,
    devices: [],
    standalone: false,
    isIOS: false,
    status: '',

    get permissionLabel() {
      switch (this.permission) {
        case 'granted': return 'Granted';
        case 'denied': return 'Denied';
        default: return 'Not set';
      }
    },

    async init() {
      this.standalone = window.isStandalonePWA();
      this.isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
      if ('Notification' in window) {
        this.permission = Notification.permission;
      }
      if ('serviceWorker' in navigator) {
        const reg = await navigator.serviceWorker.getRegistration('/');
        const sub = await reg?.pushManager.getSubscription();
        if (sub) {
          this.subscribed = true;
          this.currentEndpoint = sub.endpoint;
        }
      }
      await this.loadDevices();
    },

    async loadDevices() {
      try {
        const r = await fetch('/api/push/subscriptions', { credentials: 'same-origin' });
        if (!r.ok) return;
        const j = await r.json();
        this.devices = j.subscriptions || [];
      } catch (_) { /* best-effort */ }
    },

    async onSubscribe() {
      this.status = 'Requesting permission…';
      const result = await window.subscribePush();
      if (!result.ok) {
        this.status = `Failed: ${result.reason}`;
        if ('Notification' in window) this.permission = Notification.permission;
        return;
      }
      this.subscribed = true;
      this.currentEndpoint = result.endpoint;
      this.permission = 'granted';
      this.status = 'Subscribed.';
      await this.loadDevices();
    },

    async onUnsubscribe(endpoint) {
      this.status = 'Unsubscribing…';
      await window.unsubscribePush(endpoint);
      if (endpoint === this.currentEndpoint) {
        this.subscribed = false;
        this.currentEndpoint = null;
      }
      this.status = 'Unsubscribed.';
      await this.loadDevices();
    },
  };
}

// Register with Alpine before it initializes. This file must be loaded BEFORE
// the Alpine core script (see base.html): with all scripts deferred, Alpine
// auto-starts in a microtask that runs before later deferred scripts execute,
// so a bare window global would not exist yet when x-data is evaluated.
document.addEventListener('alpine:init', () => {
  window.Alpine.data('pushSettings', pushSettingsComponent);
});
