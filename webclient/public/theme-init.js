// Applies the persisted (or system) theme to <html> before first paint, so the
// page never flashes the wrong theme while the React bundle loads.
//
// This lives in public/ as a separate file rather than inline in index.html on
// purpose: an inline <script> forces script-src 'unsafe-inline' in the CSP,
// which defeats most of what the CSP is there to do (#502, #133). A classic
// (non-module, non-defer) <script src> in <head> is still render-blocking, so
// the no-flash guarantee is unchanged — the file is served same-origin and is
// covered by script-src 'self'.
//
// Keep this file dependency-free and synchronous. Anything async here
// reintroduces the flash.
(function () {
  var base = localStorage.getItem('scf-theme-base');
  if (base !== 'light' && base !== 'dark') {
    base = localStorage.getItem('scf-theme-preference');
  }
  if (base !== 'light' && base !== 'dark') {
    base = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', base);
})();
