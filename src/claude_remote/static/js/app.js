// Alpine.js root — extended in feature slices.

// Estimate the terminal size (cols/rows) for the current device, used as
// hx-vals on the Launch buttons so the tmux session is BORN at the device
// width — Claude renders right from the first paint, no resize-after-the-fact
// and therefore no duplicate startup banner. Approximate (no <pre> yet on the
// home); the fit/raw toggle covers any mismatch. Server clamps the values.
window.crEstimateTermSize = function () {
  let cw = 7; // monospace fallback ~ for 11.5px
  try {
    const probe = document.createElement('span');
    probe.style.cssText =
      "visibility:hidden;position:absolute;white-space:pre;" +
      "font:11.5px 'JetBrains Mono',ui-monospace,monospace";
    probe.textContent = '0'.repeat(50);
    document.body.appendChild(probe);
    const w = probe.getBoundingClientRect().width / 50;
    probe.remove();
    if (w && w >= 4) cw = w;
  } catch (_) { /* fallback cw */ }
  // ~40px: deep-view rail + terminal side padding. ~210px: header + tabs +
  // chips + input dock chrome. lineHeight 11.5*1.55 ≈ 18.
  // -1 right-edge safety margin: same off-by-one rationale as _measure()
  // in project_view.html; keeps launch sizing consistent with resize.
  const cols = Math.max(20, Math.floor((window.innerWidth - 40) / cw) - 1);
  const rows = Math.max(5, Math.floor((window.innerHeight - 210) / 18));
  return { cols, rows };
};
