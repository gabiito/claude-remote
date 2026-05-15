(function () {
  var THRESHOLD = 70;
  var startY = 0;
  var pulling = false;
  var hint = null;

  function getHintEl() {
    if (!hint) {
      hint = document.createElement('div');
      hint.className = 'cr-ptr-hint';
      hint.textContent = '↻ Sincronizar';
      document.body.appendChild(hint);
    }
    return hint;
  }

  document.addEventListener('touchstart', function (e) {
    if (window.scrollY > 0) return;
    startY = e.touches[0].clientY;
    pulling = true;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!pulling) return;
    var delta = e.touches[0].clientY - startY;
    if (delta <= 0) { pulling = false; return; }
    var h = getHintEl();
    h.style.transform = 'translate(-50%, ' + Math.min(delta, THRESHOLD + 20) + 'px)';
    h.style.opacity = Math.min(delta / THRESHOLD, 1);
    h.dataset.armed = delta >= THRESHOLD ? '1' : '0';
  }, { passive: true });

  document.addEventListener('touchend', function () {
    if (!pulling) return;
    pulling = false;
    var h = getHintEl();
    var armed = h.dataset.armed === '1';
    h.style.transform = '';
    h.style.opacity = '';
    h.dataset.armed = '0';
    if (armed && window.htmx) {
      window.htmx.ajax('POST', '/ui/discovery/sync', {
        target: '#sync-toast',
        swap: 'innerHTML'
      });
    }
  });
})();
