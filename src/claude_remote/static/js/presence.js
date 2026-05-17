// Presence emitter — tells the Service Worker "this device is being used
// right now" so a push for something you're already watching doesn't buzz
// the phone (presence-aware push, #6).
//
// "Open" is NOT enough: a tab left open while you walked away must still
// notify. So we only ping on real interaction (pointer/keyboard) and when
// the page becomes visible — the SW treats activity older than its window
// as "away" and shows the notification normally.
(function () {
  'use strict';

  // Throttle: at most one ping per THROTTLE_MS of continuous activity.
  var THROTTLE_MS = 20000;
  var lastPing = 0;

  function ping() {
    var now = Date.now();
    if (now - lastPing < THROTTLE_MS) return;
    lastPing = now;
    var sw = navigator.serviceWorker;
    if (sw && sw.controller) {
      sw.controller.postMessage({ type: 'cr-activity', at: now });
    }
  }

  if ('serviceWorker' in navigator) {
    ['pointerdown', 'keydown'].forEach(function (ev) {
      document.addEventListener(ev, ping, { passive: true });
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        lastPing = 0; // becoming visible is itself a fresh signal
        ping();
      }
    });
    // Initial signal if the page opens already focused.
    if (document.visibilityState === 'visible') ping();
  }
})();
