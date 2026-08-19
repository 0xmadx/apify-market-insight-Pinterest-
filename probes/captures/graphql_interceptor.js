/* ============================================================
   Pinterest Trends — capture the /_/graphql/ POST body
   ------------------------------------------------------------
   WHY THIS EXISTS: the moment page fires its GraphQL POST once,
   during hydration, then serves everything from the Apollo cache.
   "Disable cache" in the Network panel does NOT help — the cache
   that hides it is the in-page JS cache, not the HTTP cache.
   This snippet patches fetch + XHR, then you force a CLIENT-SIDE
   re-fetch (no full reload — a reload would wipe the patch).
   ============================================================ */

(() => {
  if (window.__cap) { console.log('%c[cap] already installed', 'color:#0a0'); return; }
  window.__cap = [];

  const ser = (b) => {
    if (b == null) return null;
    if (typeof b === 'string') return b;
    try { if (b instanceof FormData) { const o = {}; b.forEach((v, k) => o[k] = typeof v === 'string' ? v : '[file]'); return 'FORMDATA ' + JSON.stringify(o); } } catch (e) {}
    try { if (b instanceof URLSearchParams) return 'URLENCODED ' + b.toString(); } catch (e) {}
    try { if (b instanceof ArrayBuffer) return new TextDecoder().decode(b); } catch (e) {}
    try { if (ArrayBuffer.isView(b)) return new TextDecoder().decode(b); } catch (e) {}
    try { return String(b); } catch (e) { return '[unserializable]'; }
  };

  const hdrs = (h) => {
    const o = {};
    try {
      if (!h) return o;
      if (typeof h.forEach === 'function') h.forEach((v, k) => o[k] = v);
      else Object.keys(h).forEach(k => o[k] = h[k]);
    } catch (e) {}
    delete o.cookie; delete o.Cookie;          // never capture credentials
    return o;
  };

  const MATCH = /graphql/i;

  // ---- fetch ----
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    let url = '';
    try { url = typeof input === 'string' ? input : (input && input.url) || ''; } catch (e) {}
    let rec = null;
    if (MATCH.test(url)) {
      rec = {
        via: 'fetch',
        url,
        method: (init && init.method) || (input && input.method) || 'GET',
        headers: hdrs((init && init.headers) || (input && input.headers)),
        body: ser(init && init.body),
        response: null,
      };
      // Request-object bodies (rare) need to be read off a clone
      if (rec.body == null && input && typeof input.clone === 'function') {
        try { input.clone().text().then(t => { if (t) rec.body = t; }, () => {}); } catch (e) {}
      }
      window.__cap.push(rec);
    }
    return origFetch.apply(this, arguments).then((r) => {
      if (rec) { try { r.clone().text().then(t => { rec.response = t; }, () => {}); } catch (e) {} }
      return r;
    });
  };

  // ---- XMLHttpRequest ----
  const X = XMLHttpRequest.prototype;
  const oOpen = X.open, oSend = X.send, oSet = X.setRequestHeader;
  X.open = function (m, u) { this.__m = m; this.__u = String(u); this.__h = {}; return oOpen.apply(this, arguments); };
  X.setRequestHeader = function (k, v) { try { (this.__h = this.__h || {})[k] = v; } catch (e) {} return oSet.apply(this, arguments); };
  X.send = function (body) {
    if (this.__u && MATCH.test(this.__u)) {
      const rec = { via: 'xhr', url: this.__u, method: this.__m, headers: hdrs(this.__h), body: ser(body), response: null };
      window.__cap.push(rec);
      this.addEventListener('load', () => { try { rec.response = this.responseText; } catch (e) {} });
    }
    return oSend.apply(this, arguments);
  };

  // ---- reporting helpers ----
  window.__dump = () => {
    if (!window.__cap.length) { console.warn('[cap] nothing captured yet — trigger a client-side re-fetch first'); return; }
    window.__cap.forEach((r, i) => {
      console.groupCollapsed(`#${i}  ${r.method} ${r.url}  (${r.via})`);
      console.log('--- REQUEST BODY (this is the bit you want) ---');
      console.log(r.body);
      try { console.log('parsed:', JSON.parse(r.body)); } catch (e) {}
      console.log('--- HEADERS (no credentials) ---', r.headers);
      console.log('--- RESPONSE (first 4000 chars) ---');
      console.log(r.response ? r.response.slice(0, 4000) : '(pending)');
      console.groupEnd();
    });
    console.log('%cFull JSON on the clipboard-friendly line below:', 'color:#06c;font-weight:bold');
    console.log(JSON.stringify(window.__cap, null, 2));
    return window.__cap;
  };

  // Which capture actually carries the demographic buckets?
  window.__findDemo = () => {
    const re = /age|gender|18-24|25-34|35-44|45-49|50-54|55-64|65\+|unknown/i;
    const hits = window.__cap
      .map((r, i) => ({ i, r }))
      .filter(({ r }) => r.response && re.test(r.response));
    if (!hits.length) { console.warn('[cap] no capture mentions age/gender yet'); return []; }
    hits.forEach(({ i, r }) => {
      console.log(`%c#${i} MATCHES age/gender`, 'color:#0a0;font-weight:bold');
      console.log('operation:', (() => { try { return JSON.parse(r.body).operationName; } catch (e) { return '(non-JSON body)'; } })());
      console.log('body:', r.body);
    });
    return hits.map(h => h.i);
  };

  console.log('%c[cap] installed. Now force a CLIENT-SIDE re-fetch (see steps), then run __dump()', 'color:#0a0;font-weight:bold');
})();
