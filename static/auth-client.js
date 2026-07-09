/* GateGuard auth client — included by every protected HTML page.
 *
 * Responsibilities:
 *   - Bootstrap: if no token in storage, redirect to /login.
 *   - Monkey-patch window.fetch to attach Authorization: Bearer <token>
 *     on every same-origin API call.
 *   - Monkey-patch WebSocket so connections to /ws automatically receive
 *     ?token=<token> in the URL.
 *   - On 401 from any API call, clear stored token and bounce to /login.
 *
 * Must load BEFORE any other inline scripts that issue fetch / open WS.
 */
(function() {
  'use strict';

  const STORAGE_KEY = 'gg_token';
  const LOGIN_PATH = '/login';

  function getToken() {
    return localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY) || '';
  }

  function clearToken() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  function redirectToLogin(reason) {
    if (location.pathname === LOGIN_PATH) return;
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace(`${LOGIN_PATH}?next=${next}`);
  }

  // ── 1. Block page render if no token ──
  const token = getToken();
  if (!token) {
    redirectToLogin('no-token');
    // Stop further script execution by leaving the body hidden
    document.documentElement.style.visibility = 'hidden';
    return;
  }

  // ── 2. Patch fetch ──
  const origFetch = window.fetch;
  window.fetch = function patchedFetch(input, init) {
    init = init || {};
    // Don't touch cross-origin requests
    let urlStr = '';
    if (typeof input === 'string') urlStr = input;
    else if (input && input.url) urlStr = input.url;

    const isAbsolute = /^https?:\/\//i.test(urlStr);
    if (isAbsolute) {
      try {
        const u = new URL(urlStr);
        if (u.origin !== location.origin) {
          return origFetch.call(this, input, init);
        }
      } catch (e) {}
    }

    // Attach Authorization header (merge with existing headers)
    const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
    if (!headers.has('Authorization')) {
      headers.set('Authorization', 'Bearer ' + getToken());
    }
    init.headers = headers;

    return origFetch.call(this, input, init).then(resp => {
      if (resp.status === 401) {
        clearToken();
        redirectToLogin('401');
      }
      return resp;
    });
  };

  // ── 3. Patch WebSocket so /ws gets ?token=... ──
  const OrigWS = window.WebSocket;
  function PatchedWS(url, protocols) {
    try {
      // Only inject for same-origin paths starting with ws(s):
      const parsed = new URL(url, location.origin.replace(/^http/, 'ws'));
      if (parsed.origin === location.origin.replace(/^http/, 'ws')) {
        if (!parsed.searchParams.get('token')) {
          parsed.searchParams.set('token', getToken());
          url = parsed.toString();
        }
      }
    } catch (e) { /* fallback: try unmodified */ }
    return protocols !== undefined ? new OrigWS(url, protocols) : new OrigWS(url);
  }
  // Preserve prototype + static fields
  PatchedWS.prototype = OrigWS.prototype;
  Object.keys(OrigWS).forEach(k => { try { PatchedWS[k] = OrigWS[k]; } catch (e) {} });
  // OPEN/CLOSING/CLOSED constants
  PatchedWS.CONNECTING = OrigWS.CONNECTING;
  PatchedWS.OPEN       = OrigWS.OPEN;
  PatchedWS.CLOSING    = OrigWS.CLOSING;
  PatchedWS.CLOSED     = OrigWS.CLOSED;
  window.WebSocket = PatchedWS;

  // ── Expose token helpers for <img>/<video>/<a> media URLs ──
  // The fetch monkey-patch (above) only covers fetch/XHR. Plain <img src>,
  // <video src> and direct links can't carry an Authorization header, so they
  // must append ?token=<token> (the backend's _extract_token accepts it).
  window.ggToken = getToken;
  window.ggAuthedUrl = function(url) {
    const t = getToken();
    if (!t) return url;
    return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t);
  };

  // ── 4. Expose a logout helper for UIs ──
  window.ggLogout = async function() {
    try {
      await origFetch('/api/logout', {
        method: 'POST',
        headers: {'Authorization': 'Bearer ' + getToken()},
      });
    } catch (e) {}
    clearToken();
    redirectToLogin('logout');
  };

  // ── 5. Cache the current user info for quick access ──
  window.ggCurrentUser = null;
  async function loadMe() {
    try {
      const r = await fetch('/api/auth/me');
      if (r.ok) {
        const d = await r.json();
        window.ggCurrentUser = d.authenticated ? d.user : null;
      }
    } catch {}
  }
  loadMe();   // fire & forget

  // Page is allowed to render
  // (Other scripts will run after this one finishes.)
})();
