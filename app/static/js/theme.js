/* Applies a stored appearance choice before first paint, so there is no flash.
   Loaded synchronously in <head>; the rest of the behaviour lives in app.js. */
(function () {
  try {
    var stored = window.localStorage.getItem('indulink-theme');
    if (stored === 'dark' || stored === 'light') {
      document.documentElement.setAttribute('data-theme', stored);
    }
  } catch (error) {
    /* Private mode or storage disabled: fall back to the OS preference. */
  }
})();
