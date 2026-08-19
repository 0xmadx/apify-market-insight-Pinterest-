/* ============================================================================
   probes/captures/graphql_interceptor.js

   Captures the request body + response of Pinterest Trends' /_/graphql/ POST.

   WHY AN INTERCEPTOR AT ALL
   -------------------------
   The moment page fires its GraphQL POST once, during hydration, then serves
   from an in-page cache. "Disable cache" in the Network panel does not help:
   the cache hiding it is JS-side, not HTTP.

   And when driving Chrome through the claude-in-chrome MCP, the network tool
   only reports url / method / statusCode -- never payloads. So "Copy as cURL"
   is not reachable that way. An in-page interceptor is the only route to the
   body.

   HOW TO USE (see README.md for the full procedure)
   -------------------------------------------------
     1. Load a moment page, let it settle.
     2. Paste this whole file into the console.
     3. Do NOT reload. Click a DIFFERENT moment in the nav strip -- that is a
        client-side route change, which refires the query and leaves the patch
        installed. (The Region <select> does NOT work; see README.)
     4. __dump()  -- everything captured
        __findDemo()  -- just the capture whose response holds age/gender
   ========================================================================== */

(() => {
  if (window.__cap) { console.log('%c[cap] already installed -- call __reset() to clear', 'color:#0a0'); return; }
  window.__cap = [];

  const MATCH = /graphql/i;

  const SENSITIVE = /cookie|authorization|csrf|token|session|auth/i;
  const hdrs = (h) => {
    const o = {};
    try {
      if (!h) return o;
      if (typeof h.forEach === 'function') h.forEach((v, k) => o[k] = v);
      else Object.keys(h).forEach(k => o[k] = h[k]);
    } catch (e) {}
    // Never retain credentials. Names are kept, values dropped.
    Object.keys(o).forEach(k => { if (SENSITIVE.test(k)) o[k] = '[redacted]'; });
    return o;
  };

  const ser = (b) => {
    if (b == null) return null;
    if (typeof b === 'string') return b;
    try { if (b instanceof FormData) { const o = {}; b.forEach((v, k) => o[k] = typeof v === 'string' ? v : '[file]'); return 'FORMDATA ' + JSON.stringify(o); } } catch (e) {}
    try { if (b instanceof URLSearchParams) return 'URLENCODED ' + b.toString(); } catch (e) {}
    try { if (b instanceof ArrayBuffer || ArrayBuffer.isView(b)) return new TextDecoder().decode(b); } catch (e) {}
    try { return String(b); } catch (e) { return '[unserializable]'; }
  };

  // ---- fetch --------------------------------------------------------------
  // IMPORTANT: we read the ORIGINAL response and hand the app a rebuilt one.
  //
  // The obvious implementation -- r.clone().text() -- DEADLOCKS here. cloned
  // bodies are tee'd streams; if the app never drains the original (this app
  // often doesn't, it reads via its own cache layer), the clone never resolves
  // and every capture sits at response:null forever. Consuming the original
  // and reconstructing is the only reliable form.
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    let url = '';
    try { url = typeof input === 'string' ? input : (input && input.url) || ''; } catch (e) {}

    let rec = null;
    if (MATCH.test(url)) {
      rec = {
        via: 'fetch',
        endpoint: url.split('?')[0],
        method: (init && init.method) || (input && input.method) || 'GET',
        headers: hdrs((init && init.headers) || (input && input.headers)),
        body: ser(init && init.body),
        response: null,
      };
      window.__cap.push(rec);
    }

    return origFetch.apply(this, arguments).then((res) => {
      if (!rec) return res;
      return res.text().then(
        (t) => {
          rec.response = t;
          return new Response(t, { status: res.status, statusText: res.statusText, headers: res.headers });
        },
        () => res
      );
    });
  };

  // ---- XMLHttpRequest -----------------------------------------------------
  const X = XMLHttpRequest.prototype;
  const oOpen = X.open, oSend = X.send, oSet = X.setRequestHeader;
  X.open = function (m, u) { this.__m = m; this.__u = String(u); this.__h = {}; return oOpen.apply(this, arguments); };
  X.setRequestHeader = function (k, v) { try { (this.__h = this.__h || {})[k] = v; } catch (e) {} return oSet.apply(this, arguments); };
  X.send = function (body) {
    if (this.__u && MATCH.test(this.__u)) {
      const rec = { via: 'xhr', endpoint: this.__u.split('?')[0], method: this.__m, headers: hdrs(this.__h), body: ser(body), response: null };
      window.__cap.push(rec);
      this.addEventListener('load', () => { try { rec.response = this.responseText; } catch (e) {} });
    }
    return oSend.apply(this, arguments);
  };

  // ---- reporting ----------------------------------------------------------
  window.__reset = () => { window.__cap.length = 0; return 'cleared'; };

  window.__dump = () => {
    if (!window.__cap.length) { console.warn('[cap] nothing captured -- trigger a client-side route change first'); return; }
    window.__cap.forEach((r, i) => {
      console.groupCollapsed(`#${i}  ${r.method} ${r.endpoint}  (${r.via})`);
      console.log('--- REQUEST BODY ---');
      console.log(r.body);
      try { console.log('parsed:', JSON.parse(r.body)); } catch (e) {}
      console.log('--- HEADERS (credentials redacted) ---', r.headers);
      console.log('--- RESPONSE ---');
      console.log(r.response == null ? '(pending)' : r.response.slice(0, 4000));
      console.groupEnd();
    });
    console.log(JSON.stringify(window.__cap, null, 2));
    return window.__cap;
  };

  window.__findDemo = () => {
    const re = /ageDistribution|genderDistribution|18-24|25-34|65\+/;
    const hits = window.__cap.map((r, i) => ({ i, r })).filter(({ r }) => r.response && re.test(r.response));
    if (!hits.length) { console.warn('[cap] no capture carries age/gender yet'); return []; }
    hits.forEach(({ i, r }) => {
      console.log(`%c#${i} carries the demographics`, 'color:#0a0;font-weight:bold');
      console.log('queryHash:', (() => { try { return JSON.parse(r.body).queryHash; } catch (e) { return '(non-JSON body)'; } })());
      console.log('body:', r.body);
      console.log('response:', r.response);
    });
    return hits.map(h => h.i);
  };

  console.log('%c[cap] installed. Now click a different moment in the nav strip, then run __findDemo()', 'color:#0a0;font-weight:bold');
})();

/* ============================================================================
   REPLAY HELPER
   ----------------------------------------------------------------------------
   Once any real /_/graphql/ request has passed through the patch above, its
   init (headers incl. X-CSRFToken) is reusable. That lets you fire arbitrary
   variable sets without touching the UI. Credentials are reused, never read.

     __demo('halloween')                        -> whole-moment audience
     __demo('halloween', '925056443165')        -> moment x interest
     __demo('christmas', '918530398158', 'CA')  -> + region

   Returns a promise resolving to the parsed response.
   ========================================================================== */
(() => {
  const HASH = '85bfe810f1f9a895ec901e57dcbb9b193bfade5c8504299d645ca89053b31a50';

  window.__lastInit = window.__lastInit || null;
  const origFetch2 = window.fetch;
  window.fetch = function (input, init) {
    try {
      const u = typeof input === 'string' ? input : (input && input.url) || '';
      if (/graphql/i.test(u) && init) { window.__lastInit = init; window.__gqlUrl = u; }
    } catch (e) {}
    return origFetch2.apply(this, arguments);
  };

  window.__demo = (moment, interestId, region, endDate) => {
    if (!window.__lastInit) { console.warn('[cap] no init stashed yet -- click a moment link first'); return Promise.resolve(null); }
    const variables = {
      terms: [interestId ? `${moment}:${interestId}` : moment],
      region: region || 'US',
      endDate: endDate || new Date(Date.now() - 5 * 864e5).toISOString().slice(0, 10),
      event: null,
      category: interestId ? 'MOMENT_INTEREST' : 'MOMENT',
    };
    return origFetch2(window.__gqlUrl, {
      method: 'POST',
      headers: window.__lastInit.headers,
      credentials: 'include',
      body: JSON.stringify({ queryHash: HASH, variables }),
    })
      .then(r => r.json())
      .then(j => {
        const items = j && j.data && j.data.trendsDemographicsRead && j.data.trendsDemographicsRead.items;
        if (!items || !items.length) console.warn('[cap] empty items -- bad interest id, or category/term shape mismatch');
        return j;
      });
  };

  console.log('%c[cap] __demo(moment, interestId?, region?, endDate?) ready', 'color:#0a0');
})();
