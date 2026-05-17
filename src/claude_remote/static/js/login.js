// Login page — password show/hide toggle. Separate file (not inline) so it
// works under the CSP (script-src 'self', no 'unsafe-inline').
(function () {
  'use strict';
  var input = document.getElementById('cr-pw');
  var btn = document.getElementById('cr-pw-toggle');
  if (!input || !btn) return;
  btn.addEventListener('click', function () {
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    btn.classList.toggle('cr-login-eye-on', show);
    input.focus();
  });
})();
