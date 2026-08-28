"""The two Pinterest Trends call styles, as one client.

Lifted from `probes/probe_endpoints.py`, which has been wire-proven against all
16 endpoints — this is that logic promoted to product code, not a rewrite.

    client = TrendsClient(session)          # session from src.session.leased_session
    date   = client.bootstrap()             # ALWAYS first — feeds every end_date
    data   = client.style_a("/ads/v4/trends/shopping/product_categories", {})
    data   = client.style_b("/metrics/", {"terms": "nails", ...})

Both styles raise `TrendsAPIError` rather than returning a half-answer, because
the two failure surfaces are not symmetric and a caller cannot tell them apart:

  Style A  errors arrive inside an HTTP 200, at resource_response.error, with a
           readable message_detail ("'limit' is too large: 1000 > 522").
  Style B  errors arrive as a bare HTTP 400 **with an empty body** — verified
           2026-08-19 (predicted_days=180). There is no message to read, ever.

That asymmetry is why ceilings are enforced in `vocab.py` before the wire: on
Style B the wire will not tell you what you got wrong.
"""
import json
import time
import uuid

from .session import classify

BASE = "https://trends.pinterest.com"
# A SECOND host. Pin commerce data (merchant, price, outbound link) lives only
# here — the trends surface never carries it in any form. Same `.pinterest.com`
# cookie scope, so one vault session covers both, but it is a different and
# more defended property with its own page-specific PWS handlers.
WWW = "https://www.pinterest.com"

# Exact-match allowlist, not a presence check. Every other value — including the
# empty string and omitting the header — returns 403 "Invalid Resource Request".
HANDLER = {"X-Pinterest-PWS-Handler": "trends/index.js"}

MAX_RETRIES = 5


class TrendsAPIError(RuntimeError):
    """Pinterest refused the request. `kind` says whose fault it is."""

    def __init__(self, message, status=None, kind="error", endpoint=None):
        super().__init__(message)
        self.status = status
        self.kind = kind          # malformed | auth_expired | rate_limited | blocked | error
        self.endpoint = endpoint


class StaleQueryHash(TrendsAPIError):
    """The persisted GraphQL hash no longer resolves.

    Its own type because the alternative is catastrophic and silent: a rotated
    hash returns 200 with no `data`, which a generic handler reports as "this
    moment has no audience" — a plausible wrong number of exactly the kind this
    codebase exists to prevent.
    """


class TrendsClient:
    def __init__(self, session, cache=None, delay=0.4, force_refresh=False):
        self.rate_limited = 0
        self.session = session
        # The response cache (src/cache.py). Optional so tests and probes can
        # run without Redis, but the actor ALWAYS passes one: without it the
        # 383-row taxonomy is re-fetched on every run and two customers asking
        # the same question in the same hour cost Pinterest two requests.
        self.cache = cache
        self.force_refresh = force_refresh
        self.delay = delay        # courtesy gap; the vault session is shared
        self.end_date = None
        self.request_count = 0
        self.cache_hits = 0
        # keys this client won the right to fill, so _store knows what
        # to unlock. A set, because one run fills many keys.
        self._holding_fill = set()
        self.coalesced = 0

    # ------------------------------------------------------------ bootstrap

    def bootstrap(self):
        """The date every other call hangs off. Never `today()` — data lags ~4d
        and a future end_date is a 400."""
        if self.end_date is None:
            # kind="bootstrap" (300s): every run asks this, and leaving it
            # uncached meant every run leased an identity for it alone — which
            # defeated lazy leasing entirely. Short TTL because this value is
            # what invalidates every other cache key (they all carry end_date).
            self.end_date = self.style_b(
                "/latest_available_date/", kind="bootstrap").get("date")
            if not self.end_date:
                raise TrendsAPIError("latest_available_date returned no date",
                                     endpoint="/latest_available_date/")
        return self.end_date

    # --------------------------------------------------------------- styles

    def style_a(self, path, data=None, source_url=None, kind=None):
        """The ApiResource wrapper — every /ads/v4/trends/... endpoint.

        `source_url` and the `_` cachebuster are optional and their values are
        irrelevant (verified), so they are omitted entirely.

        `kind` selects the cache TTL (see Config.CACHE_TTLS). Omit it to bypass
        the cache for anything whose response is only valid once.
        """
        body = {"options": {"url": path, "data": data or {}}, "context": {}}
        params = {"data": json.dumps(body, separators=(",", ":"))}
        if source_url:
            params["source_url"] = source_url

        url = f"{BASE}/resource/ApiResource/get/"
        payload = self._cached(kind, url, params)
        if payload is None:
            try:
                response = self._request("GET", url, params=params, endpoint=path)
                payload = self._json(response, path)
                self._store(kind, url, response, params)
            except Exception:
                self._release_fills()
                raise
        wrapper = payload.get("resource_response") or {}

        error = wrapper.get("error")
        if error:
            raise TrendsAPIError(
                error.get("message_detail") or error.get("message") or "unknown",
                status=error.get("http_status"), endpoint=path)

        # `client_context` rides along on every Style A response and carries the
        # operator's advertiser identity (email, username, ids). It is never
        # returned to a caller and never stored — see probes/probe_endpoints.py.
        return wrapper.get("data")

    def style_b(self, path, params=None, method="GET", json_body=None,
                kind=None):
        """Plain GET/POST — no envelope, JSON returned directly."""
        url = f"{BASE}{path}"
        # POSTs are never served from cache: their bodies are not part of the
        # cache key, so a hit could return another request's answer.
        cache_key = params if method == "GET" else None
        if method == "GET":
            payload = self._cached(kind, url, cache_key)
            if payload is not None:
                return payload

        try:
            response = self._request(method, url, params=params,
                                     json_body=json_body, endpoint=path)
            payload = self._json(response, path)
            if method == "GET":
                self._store(kind, url, response, cache_key)
        except Exception:
            self._release_fills()
            raise
        return payload

    def graphql(self, query_hash, variables, operation_name, handler, kind=None):
        """Style C — the persisted-query GraphQL POST. One endpoint, §3.18.

        Three things here differ from every other call, all captured live and
        none of them guessable:

        1. The PWS handler is **per page**, not the global `trends/index.js`.
           The moment demographics query needs `trends/moments/[momentId].js`.
        2. The body carries `queryHash`, NOT `doc_id`, and has **no
           `operationName` field at all** — the operation name travels in the
           `X-Pinterest-GraphQL-Name` header. Anything grepping bodies for an
           operation name finds nothing.
        3. `queryHash` is a persisted-query hash: it **rotates when Pinterest
           redeploys**. `StaleQueryHash` is raised for that case specifically,
           so a rotation reads as "re-capture the hash", never as "no data".
        """
        url = f"{BASE}/_/graphql/"
        body = {"queryHash": query_hash, "variables": variables}

        payload = self._cached(kind, url, body)
        if payload is None:
            headers = {
                "X-Pinterest-GraphQL-Name": operation_name,
                "X-Pinterest-PWS-Handler": handler,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            try:
                response = self._request("POST", url, json_body=body,
                                         endpoint="/_/graphql/", headers=headers)
                payload = self._json(response, "/_/graphql/")
                self._store(kind, url, response, body)
            except Exception:
                self._release_fills()
                raise

        # THREE outcomes, only one an HTTP-level failure. Telling them apart is
        # the whole job:
        #   data:null + errors[]    malformed variables ("CLIENT GRAPHQL
        #                           ERROR") or a rotated hash -> raise
        #   data...items: []        HTTP 200, no error: unknown interest id,
        #                           terms/category out of sync, or a genuinely
        #                           empty cell. Returned; the CALLER decides.
        #   data with items         the answer
        # And the silent one: an unknown variable added to a persisted query is
        # IGNORED, returning a byte-identical baseline response. No single
        # response reveals that — diff against a known baseline.
        errors = (payload or {}).get("errors")
        data = (payload or {}).get("data")
        if errors or not data:
            raise StaleQueryHash(
                f"/_/graphql/ returned no `data` for {operation_name}. The "
                f"persisted queryHash has almost certainly rotated — re-capture "
                f"it (probes/captures/README.md), do NOT read this as an empty "
                f"result. errors={errors}")
        return data

    def pin_resource(self, pin_id, kind="detail"):
        """`www.pinterest.com/resource/PinResource/get/` — one pin's commerce data.

        Captured live 2026-08-19 (probes/captures/pin_closeup.md). This is the
        ONLY source of price and the outbound merchant URL: the whole trends
        surface — including `top_products`, which powers the very strip these
        pins come from — carries `pin_id`, `merchant_name`, `title` and images,
        and nothing else. Verified: 210 anchors on the shopping page, zero
        external.

        Two things are load-bearing and neither is guessable:
          * `field_set_key: "auth_web_main_pin"` — what pulls in the
            `rich_summary` subtree where price lives. Other field sets omit it.
          * the handler is `www/pin/[id].js`, page-specific like the moment
            page's. There is no global value that works everywhere.

        ⚠️ Costs one request PER PIN — there is no batch form. See
        `ShoppingScraper(enrich_top_n=...)`, which caps it deliberately.
        """
        options = {
            "id": str(pin_id),
            "field_set_key": "auth_web_main_pin",
            "noCache": True,
            "fetch_visual_search_objects": False,
            "get_page_metadata": False,
        }
        params = {
            "source_url": f"/pin/{pin_id}/",
            "data": json.dumps({"options": options, "context": {}},
                               separators=(",", ":")),
        }
        url = f"{WWW}/resource/PinResource/get/"

        payload = self._cached(kind, url, params)
        if payload is None:
            try:
                # The browser capture listed 12 headers on this call. The ones below
                # are every one we can produce *correctly*; the rest are omitted on
                # purpose rather than faked:
                #   X-Pinterest-Platform-BID — an opaque build/session id. A made-up
                #     value is worse than no value: it is a wrong claim about who we
                #     are, and it would be checkable.
                #   X-APP-VERSION — same reasoning; it pins a specific web build.
                # B3 trace ids ARE generated fresh per request, which is exactly
                # what a browser does with them (they are distributed-tracing ids,
                # not identity), so producing our own is honest, not spoofing.
                trace = uuid.uuid4().hex[:16]
                span = uuid.uuid4().hex[:16]
                response = self._request(
                    "GET", url, params=params, endpoint="/resource/PinResource/get/",
                    headers={"X-Pinterest-PWS-Handler": "www/pin/[id].js",
                             "X-Requested-With": "XMLHttpRequest",
                             "Accept": "application/json",
                             # Route context — the www host is stricter than trends
                             # and this is the page the call legitimately came from.
                             "X-Pinterest-Source-Url": f"/pin/{pin_id}/",
                             "X-Pinterest-AppState": "active",
                             "screen-dpr": "2",
                             "X-B3-TraceId": trace,
                             "X-B3-SpanId": span,
                             "X-B3-ParentSpanId": trace,
                             "X-B3-Flags": "0",
                             "Referer": f"{WWW}/pin/{pin_id}/",
                             "Origin": WWW})
                payload = self._json(response, "/resource/PinResource/get/")
                self._store(kind, url, response, params)
            except Exception:
                self._release_fills()
                raise

        wrapper = payload.get("resource_response") or {}
        error = wrapper.get("error")
        if error:
            raise TrendsAPIError(
                error.get("message_detail") or error.get("message") or "unknown",
                status=error.get("http_status"),
                endpoint="/resource/PinResource/get/")
        return wrapper.get("data")

    # ----------------------------------------------------------- cache glue

    def _cached(self, kind, url, params):
        """A hit returns the parsed payload; None means 'go to the wire'.

        On a MISS this also runs singleflight: it claims the right to fill the
        key, and if another worker already holds that right it waits for their
        answer instead of fetching a duplicate. Measured without it, 8 clients
        asking one uncached question made 8 requests and tied up 8 identities.
        """
        if not (self.cache and kind) or self.force_refresh:
            return None

        payload = self._decode(self.cache.get(kind, url, params))
        if payload is not None:
            self.cache_hits += 1
            return payload

        # Miss. Exactly one caller should go to the wire for this key.
        if self.cache.acquire_fill(kind, url, params):
            self._holding_fill.add(self.cache._key(kind, url, params, None))
            return None                      # we own it — caller fetches

        payload = self._decode(self.cache.wait_for_fill(kind, url, params))
        if payload is not None:
            self.cache_hits += 1
            self.coalesced += 1
            return payload
        # The winner was slower than the wait, or died. Fetch it ourselves
        # rather than fail the customer — a duplicate beats a silence.
        return None

    @staticmethod
    def _decode(hit):
        """A cache entry that will not parse is treated as absent, never as
        data: replaying an unusable body for a whole TTL is the worse error."""
        if hit is None:
            return None
        try:
            return hit.json()
        except Exception:
            return None

    def _store(self, kind, url, response, params):
        """Only usable 200s are stored — cache.put() enforces that itself, so a
        403 or an empty body can never be replayed for a whole TTL.

        This releases the fill lock on the SUCCESS path. The failure path is
        `_release_fills()`, and the two together are what make the release
        unconditional — this method alone is not reached when the request
        raises, which is exactly when the lock matters most.
        """
        if self.cache and kind:
            self.cache.put(kind, url, response, params)
            key = self.cache._key(kind, url, params, None)
            if key in self._holding_fill:
                self._holding_fill.discard(key)
                self.cache.release_fill(kind, url, params)

    def _release_fills(self):
        """Drop every fill lock this client still holds. Safe to call twice.

        THE BUG THIS EXISTS FOR (found by probes/stress.py, 2026-08-20). Every
        fetch site reads:

            response = self._request(...)     # can raise
            payload  = self._json(response)   # can raise
            self._store(...)                  # only reached on success

        so any failure left the lock set for its full FILL_LOCK_TTL (45s), and
        `_store`'s docstring claimed the opposite. Meanwhile every other client
        asking that same question sat out the 30s `wait_for_fill` timeout and
        then fetched anyway — one failed request turning into a half-minute
        latency spike plus N duplicates, aimed at an endpoint that was already
        failing. The worst possible moment to retry in a crowd.

        Releasing on failure is right even though it lets the next caller retry
        immediately: a lock exists to stop a stampede on a key someone is
        actively filling, and nobody is filling this one any more.
        """
        if not self.cache:
            return
        for key in list(self._holding_fill):
            self._holding_fill.discard(key)
            try:
                self.cache.release_fill_key(key)
            except Exception:
                # Redis is already unhappy; the lock's own TTL is the backstop.
                # Never let cleanup mask the original exception being raised.
                pass

    # -------------------------------------------------------------- plumbing

    def _request(self, method, url, params=None, json_body=None, endpoint=None,
                 headers=None):
        """One request, with blind backoff. There are NO rate-limit headers on
        this API — no x-ratelimit-*, no retry-after — so a 429 tells you nothing
        about the budget and backing off blindly is the only correct response."""
        for attempt in range(MAX_RETRIES):
            if self.delay:
                time.sleep(self.delay)
            self.request_count += 1

            # HANDLER is the default; `headers` overrides it for the GraphQL
            # call, whose handler is page-specific.
            sent = dict(HANDLER)
            if headers:
                sent.update(headers)
            if method == "POST":
                response = self.session.post(url, json=json_body, headers=sent)
            else:
                response = self.session.get(url, params=params, headers=sent)

            verdict = classify(response)
            if verdict == "rate_limited":
                # A 429 has never been seen in this project's life. That is a
                # belief, not a measurement, until something counts them.
                self.rate_limited += 1
                if attempt == MAX_RETRIES - 1:
                    raise TrendsAPIError("429 after retries", status=429,
                                         kind="rate_limited", endpoint=endpoint)
                time.sleep(2 ** attempt)
                continue

            if verdict == "malformed":
                # OUR request is wrong (the PWS handler header). Never a session
                # problem — do not evict the profile, do not re-login.
                raise TrendsAPIError(
                    "403 Invalid Resource Request — the request is malformed, "
                    "not the session. Check X-Pinterest-PWS-Handler.",
                    status=response.status_code, kind="malformed", endpoint=endpoint)

            if verdict in ("auth_expired", "blocked"):
                raise TrendsAPIError(f"session rejected ({verdict})",
                                     status=response.status_code, kind=verdict,
                                     endpoint=endpoint)
            return response

        raise TrendsAPIError("exhausted retries", kind="rate_limited",
                             endpoint=endpoint)

    @staticmethod
    def _json(response, endpoint):
        if response.status_code >= 400:
            # Style B's whole error surface: a status code and an empty body.
            # Say so explicitly rather than reporting "no data".
            raise TrendsAPIError(
                f"HTTP {response.status_code} with no error body "
                f"(Style B carries no message — check params against vocab.py)",
                status=response.status_code, endpoint=endpoint)
        try:
            return response.json()
        except Exception as exc:
            raise TrendsAPIError(f"response was not JSON: {exc}",
                                 status=response.status_code,
                                 endpoint=endpoint) from exc
