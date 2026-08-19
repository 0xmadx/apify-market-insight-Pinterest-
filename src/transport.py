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

from .session import classify

BASE = "https://trends.pinterest.com"

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


class TrendsClient:
    def __init__(self, session, delay=0.4):
        self.session = session
        self.delay = delay        # courtesy gap; the vault session is shared
        self.end_date = None
        self.request_count = 0

    # ------------------------------------------------------------ bootstrap

    def bootstrap(self):
        """The date every other call hangs off. Never `today()` — data lags ~4d
        and a future end_date is a 400."""
        if self.end_date is None:
            self.end_date = self.style_b("/latest_available_date/").get("date")
            if not self.end_date:
                raise TrendsAPIError("latest_available_date returned no date",
                                     endpoint="/latest_available_date/")
        return self.end_date

    # --------------------------------------------------------------- styles

    def style_a(self, path, data=None, source_url=None):
        """The ApiResource wrapper — every /ads/v4/trends/... endpoint.

        `source_url` and the `_` cachebuster are optional and their values are
        irrelevant (verified), so they are omitted entirely.
        """
        body = {"options": {"url": path, "data": data or {}}, "context": {}}
        params = {"data": json.dumps(body, separators=(",", ":"))}
        if source_url:
            params["source_url"] = source_url

        response = self._request("GET", f"{BASE}/resource/ApiResource/get/",
                                 params=params, endpoint=path)
        payload = self._json(response, path)
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

    def style_b(self, path, params=None, method="GET", json_body=None):
        """Plain GET/POST — no envelope, JSON returned directly."""
        response = self._request(method, f"{BASE}{path}", params=params,
                                 json_body=json_body, endpoint=path)
        return self._json(response, path)

    # -------------------------------------------------------------- plumbing

    def _request(self, method, url, params=None, json_body=None, endpoint=None):
        """One request, with blind backoff. There are NO rate-limit headers on
        this API — no x-ratelimit-*, no retry-after — so a 429 tells you nothing
        about the budget and backing off blindly is the only correct response."""
        for attempt in range(MAX_RETRIES):
            if self.delay:
                time.sleep(self.delay)
            self.request_count += 1

            if method == "POST":
                response = self.session.post(url, json=json_body, headers=HANDLER)
            else:
                response = self.session.get(url, params=params, headers=HANDLER)

            verdict = classify(response)
            if verdict == "rate_limited":
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
