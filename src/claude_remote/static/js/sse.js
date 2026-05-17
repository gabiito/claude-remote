// SSE client — replaces the old 5s HTMX polling of the home list and the
// metrics body. The server pushes a re-rendered partial whenever a hook
// event changes session state (see routes/sse.py). Alpine 3's own
// MutationObserver re-initializes swapped-in cards, exactly as it did under
// the HTMX innerHTML swap, so no manual init is needed here.
(function () {
  'use strict';

  // Wire one target element to an SSE endpoint.
  //   urlFn:    () => string   (re-evaluated on every (re)connect)
  //   blockFn:  () => bool     (when true, defer the swap — e.g. a card is
  //                             expanded; the latest frame is stashed and
  //                             applied as soon as it returns false)
  function wire(el, urlFn, blockFn) {
    var es = null;
    var pending = null;

    function apply(html) { el.innerHTML = html; }

    function onMessage(ev) {
      if (blockFn && blockFn()) { pending = ev.data; return; }
      apply(ev.data);
    }

    function open() {
      if (es) es.close();
      es = new EventSource(urlFn());
      es.onmessage = onMessage;
      // EventSource auto-reconnects on transport error; nothing to do.
    }

    if (blockFn) {
      // Flush the stashed frame the moment the blocking condition clears
      // (e.g. the expanded card collapses) so the list is never stale.
      new MutationObserver(function () {
        if (pending !== null && !blockFn()) {
          apply(pending);
          pending = null;
        }
      }).observe(el, {
        attributes: true,
        subtree: true,
        attributeFilter: ['data-expanded'],
      });
    }

    open();
    return { reconnect: open };
  }

  function start() {
    var list = document.querySelector('.cr-list');
    if (list) {
      var domain = function () {
        return window.location.hash.slice(1) || 'all';
      };
      var conn = wire(
        list,
        function () {
          return '/sse/home?domain=' + encodeURIComponent(domain());
        },
        function () {
          return !!list.querySelector('.cr-card[data-expanded="1"]');
        }
      );
      // Filter change → reconnect; the initial frame of the new stream is
      // the freshly filtered list (no separate fetch needed).
      window.addEventListener('hashchange', conn.reconnect);
    }

    var metrics = document.getElementById('cr-metrics-body');
    if (metrics) {
      wire(metrics, function () { return '/sse/metrics'; }, null);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
