// Observe SPA navigation as well as full page loads, without opening the popup.
(() => {
  let handledUrl = '', busy = false;
  async function check() {
    const url = location.href;
    if (busy || document.visibilityState !== 'visible' || url === handledUrl ||
        !/^\/models\/\d+(?:\/|$)/.test(location.pathname)) return;
    busy = true;
    try {
      const result = await chrome.runtime.sendMessage({type: 'auto-wait-page', url});
      if (result?.handled && location.href === url) handledUrl = url;
    } catch (_) { /* Extension reload or app offline: retry on next check. */ }
    finally { busy = false; }
  }
  void check();
  setInterval(check, 5000);
  document.addEventListener('visibilitychange', check);
})();
