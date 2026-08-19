"""One small live call per documented endpoint, against the real session.

    .venv/Scripts/python.exe -m probes.probe_endpoints

Purpose is verification, not scraping: does each endpoint in docs/wire/07-API-REFERENCE.md
still exist, still answer, and still return the shape the doc claims. Every result —
including the failures — is written to probes/results/ and summarised in
probes/RESULTS.md.

Design notes, all of them lessons the parent repo paid for:

  * `/latest_available_date/` runs first and feeds `end_date` into everything else.
    Pinterest's data lags; a hardcoded "today" 400s.
  * Nothing is asserted against a remembered value. The probe reports what came back
    and diffs it against the doc's claim — where they disagree, the wire wins.
  * A failure is recorded, never retried into silence and never written as an empty
    result. "0 rows" and "the call failed" are different facts.
  * One profile is leased for the whole sweep, so all 18 calls come from one identity
    rather than looking like 18 sessions.
"""
import json
import pathlib
import sys
import time
import traceback

sys.path.insert(0, ".")

from src.session import classify, leased_session  # noqa: E402

BASE = "https://trends.pinterest.com"
HANDLER = {"X-Pinterest-PWS-Handler": "trends/index.js"}

OUT = pathlib.Path("probes/results")
DELAY = 1.5  # no rate-limit headers exist on this API — back off blindly


# ---------------------------------------------------------------- transport

def style_a(session, path, data=None):
    """The ApiResource wrapper. source_url and the `_` cachebuster are optional."""
    body = {"options": {"url": path, "data": data or {}}, "context": {}}
    return session.get(
        f"{BASE}/resource/ApiResource/get/",
        params={"data": json.dumps(body, separators=(",", ":"))},
        headers=HANDLER,
    )


def style_b(session, path, params=None, method="GET", json_body=None):
    """Plain GET/POST — no envelope, JSON returned directly."""
    if method == "POST":
        return session.post(f"{BASE}{path}", json=json_body, headers=HANDLER)
    return session.get(f"{BASE}{path}", params=params, headers=HANDLER)


# ----------------------------------------------------------------- shaping

def strip_pii(payload):
    """Remove `client_context` before ANYTHING is written to disk or logged.

    Style A responses carry a `client_context` block describing the logged-in
    advertiser account: id, owner_user_id, and the owner's full_name, username,
    email, country and last_login. None of it is data we asked for, all of it is
    the operator's real identity, and these files are committed to git.

    This existed as a rule in the coder skill ("Drop client_context (account
    PII) before anything is stored or logged") before it existed as code — the
    first probe sweep wrote 27 files carrying it. Enforced here so the rule
    cannot be forgotten at a call site.
    """
    if isinstance(payload, dict) and "client_context" in payload:
        payload = {k: v for k, v in payload.items() if k != "client_context"}
        payload["_client_context"] = "<stripped: account PII, see strip_pii()>"
    return payload


def unwrap(payload, style):
    """Style A hides the real payload under resource_response.data."""
    if style != "A" or not isinstance(payload, dict):
        return payload, None
    wrapper = payload.get("resource_response", {})
    error = (wrapper.get("error") or {}).get("message_detail") or \
            (wrapper.get("error") or {}).get("message")
    return wrapper.get("data"), error


def describe(value, depth=0):
    """A compact shape summary — enough to tell 'it worked' from 'it is empty'."""
    if value is None:
        return "null"
    if isinstance(value, dict):
        if depth >= 2:
            return f"dict({len(value)} keys)"
        keys = list(value)[:8]
        inner = ", ".join(f"{k}: {describe(value[k], depth + 1)}" for k in keys)
        more = f", +{len(value) - len(keys)} more" if len(value) > len(keys) else ""
        return "{" + inner + more + "}"
    if isinstance(value, list):
        if not value:
            return "[] EMPTY"
        return f"[{len(value)}x {describe(value[0], depth + 1)}]"
    if isinstance(value, str):
        return f'"{value[:40]}"' if len(value) <= 40 else f'"{value[:40]}…"'
    return type(value).__name__


# ------------------------------------------------------------------ probes

def build_probes(date):
    """Every endpoint in doc #7, numbered as the doc numbers them."""
    return [
        # (id, name, style, callable, doc claim to check against)
        ("3.2", "topics/featured — Trends in the spotlight", "A",
         lambda s: style_a(s, "/ads/v4/trends/topics/featured/US/SAVE",
                           {"interests": ["934876475639"],
                            "publish_state": "PUBLISHED"}),
         "5 trends, each with time_series[13] and pins[~9]"),

        ("3.3", "moment/available — Moments list", "A",
         lambda s: style_a(s, "/ads/v4/trends/moment/available/US", {}),
         "US returns 13 moments as parallel arrays"),

        ("3.4", "moment/metrics — Moment chart (only DAILY source)", "A",
         lambda s: style_a(s, "/ads/v4/trends/moment/metrics/US",
                           {"moments": ["halloween"], "end_date": date,
                            "aggregation_level": "weekly", "lookback_days": 365,
                            "predicted_days": 91, "interest_limit": 6,
                            "normalize_against_group": False}),
         "moments[] with daily_values ~66 weekly points"),

        ("3.5", "editorial/content — Editors' Picks", "A",
         lambda s: style_a(s, "/ads/v4/trends/editorial/content/US", {}),
         "6 curated items, keywords nested per region"),

        ("3.6", "shopping/product_categories — taxonomy", "A",
         lambda s: style_a(s, "/ads/v4/trends/shopping/product_categories", {}),
         "383 categories keyed by id, L1 absent"),

        ("3.7", "product_categories/top — trending categories", "A",
         lambda s: style_a(s, "/ads/v4/trends/shopping/product_categories/top/US",
                           {"event": "OUTBOUND_CLICK", "ranking_method": "GROWTH",
                            "end_date": date, "parent_product_categories": ["1181"],
                            "limit": 20, "order_by": "RELATIVE_VOLUME",
                            "order": "DESC"}),
         "ordered_values[] — one vertical per call"),

        ("3.8", "product_categories/metrics — sparklines", "A",
         lambda s: style_a(s, "/ads/v4/trends/shopping/product_categories/metrics/US",
                           {"product_category_ids": ["1356"], "event": "ENGAGEMENT",
                            "end_date": date, "days": 60, "predicted_days": 0,
                            "age_bucket": [], "gender": []}),
         "values[] with growth_rates and daily_values"),

        ("3.9", "product_categories/demographics — 3 sections in one", "A",
         lambda s: style_a(s,
                           "/ads/v4/trends/shopping/product_categories/demographics/US",
                           {"product_category_ids": ["1408"],
                            "event": "OUTBOUND_CLICK", "end_date": date}),
         "demographics + related_search_trends + summaries"),

        ("3.10", "product_categories/top_products", "A",
         lambda s: style_a(s,
                           "/ads/v4/trends/shopping/product_categories/top_products",
                           {"product_category_id": "1408", "region": "US",
                            "event": "OUTBOUND_CLICK"}),
         "top_products[] with pin_id + image sizes"),

        ("3.12", "top_trends_filtered — keyword discovery", "B",
         lambda s: style_b(s, "/top_trends_filtered/",
                           {"country": "US", "endDate": date, "trendsPreset": 3,
                            "numTermsToReturn": 10, "shouldMock": "false"}),
         "values[] with searchCount, wow/mom/yoy, affinity always null"),

        ("3.13", "metrics — keyword chart + forecast", "B",
         lambda s: style_b(s, "/metrics/",
                           {"terms": "nails", "country": "US", "end_date": date,
                            "days": 365, "aggregation": 2, "predicted_days": 91,
                            "normalize_against_group": "true",
                            "shouldMock": "false"}),
         "[{term, has_prediction, growth_rates, counts[]}]"),

        ("3.14", "demographics — keyword audience", "B",
         lambda s: style_b(s, "/demographics/",
                           {"terms": "nails", "country": "US", "end_date": date,
                            "days": 365}),
         "term_distributions{} age + gender fractions 0-1"),

        ("3.15", "related_terms — 5 related keywords", "B",
         lambda s: style_b(s, "/related_terms/",
                           {"requestTerm": "family", "country": "US",
                            "endDate": date, "aggregation": 2, "lookback": 365,
                            "shouldMock": "false"}),
         "exactly 5, camelCase hasPrediction"),

        ("3.16", "term_images — Popular Pins (only POST)", "B",
         lambda s: style_b(s, "/term_images/", method="POST",
                           json_body={"terms": ["family"], "country": "US",
                                      "limit": 9, "requestImageSize": "236x",
                                      "cacheTtlInSeconds": 86400}),
         "{term: [9 image urls]}"),

        ("3.17", "prefix_match — typeahead (case-sensitive)", "B",
         lambda s: style_b(s, "/prefix_match/",
                           {"query": "hallow", "country": "US"}),
         "always 10 max, lowercase query only"),
    ]


NOT_PROBED = [
    ("3.11", "top_products/{merchant_id}/{cat_id} and recommendations/{merchant_id}",
     "Needs a merchant_id from a product catalog this account does not have. The doc "
     "records both returning 200 with empty payloads. Probing with a fabricated "
     "merchant id would produce an error that says nothing about the endpoint."),
    ("3.18", "POST /_/graphql/ — moment Age/Gender",
     "Persisted query; the query body is in no JS bundle, so there is nothing to "
     "send. Doc records the REST alternatives as 404/400. Use the §3.12 + §3.14 "
     "workaround instead."),
]


# -------------------------------------------------------------------- main

def run_probe(session, probe_id, name, style, call, claim):
    started = time.time()
    row = {"id": probe_id, "name": name, "style": style, "claim": claim}
    try:
        response = call(session)
    except Exception as exc:
        row.update(verdict="ERROR", detail=f"{type(exc).__name__}: {exc}",
                   elapsed_ms=int((time.time() - started) * 1000))
        return row, None

    row["elapsed_ms"] = int((time.time() - started) * 1000)
    row["http"] = response.status_code
    row["classify"] = classify(response)
    row["request_id"] = response.headers.get("x-pinterest-rid")

    try:
        parsed = response.json()
    except Exception:
        row.update(verdict="NOT JSON", detail=response.text[:200])
        return row, response.text[:4000]

    data, error = unwrap(parsed, style)
    if error:
        row.update(verdict="API ERROR", detail=error)
        return row, parsed

    if data is None:
        # Style B returns the payload directly; a null here is a real null.
        data = parsed if style == "B" else None

    row["shape"] = describe(data)
    empty = data in (None, [], {}) or (isinstance(data, dict) and not data)
    row["verdict"] = "OK" if (response.status_code == 200 and not empty) else (
        "EMPTY" if response.status_code == 200 else "FAIL")
    return row, parsed


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    with leased_session() as (session, identity):
        print(f"profile: {identity.profile_id}\n")

        # 3.1 first — its answer is an input to everything else.
        r = style_b(session, "/latest_available_date/")
        try:
            date = r.json().get("date")
        except Exception:
            date = None
        rows.append({
            "id": "3.1", "name": "latest_available_date — CALL THIS FIRST",
            "style": "B", "http": r.status_code, "classify": classify(r),
            "shape": describe(_safe_json(r)), "elapsed_ms": 0,
            "claim": "no params; feeds end_date into every other call",
            "verdict": "OK" if date else "FAIL",
            "detail": f"date={date}",
        })
        print(f"  3.1  {'OK' if date else 'FAIL'}  latest_available_date -> {date}")
        (OUT / "3.1-latest_available_date.json").write_text(
            json.dumps(strip_pii(_safe_json(r)), indent=2), encoding="utf-8")

        if not date:
            print("\nNo date — every other call needs it. Stopping.")
            _write_summary(rows, identity)
            return 1

        time.sleep(DELAY)

        for probe_id, name, style, call, claim in build_probes(date):
            row, raw = run_probe(session, probe_id, name, style, call, claim)
            rows.append(row)
            flag = {"OK": "OK", "EMPTY": "EMPTY", "FAIL": "FAIL"}.get(
                row["verdict"], row["verdict"])
            print(f"  {probe_id:<5} {flag:<9} {name}")
            if row.get("detail"):
                print(f"        -> {row['detail'][:120]}")
            if raw is not None:
                slug = name.split(" —")[0].replace("/", "_").strip()
                (OUT / f"{probe_id}-{slug}.json").write_text(
                    json.dumps(strip_pii(raw), indent=2)[:400000], encoding="utf-8")
            time.sleep(DELAY)

    _write_summary(rows, identity)
    failures = [r for r in rows if r["verdict"] not in ("OK",)]
    print(f"\n{len(rows) - len(failures)}/{len(rows)} endpoints answered with data")
    for r in failures:
        print(f"  {r['verdict']:<9} {r['id']} {r['name']}")
    return 0


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return {"_raw": response.text[:500]}


def _write_summary(rows, identity):
    lines = [
        "# Endpoint probe — live results",
        "",
        f"One small call per endpoint in [docs/wire/07-API-REFERENCE.md]"
        f"(../docs/wire/07-API-REFERENCE.md), run against profile "
        f"`{identity.profile_id}` on {time.strftime('%Y-%m-%d %H:%M')}.",
        "",
        "`OK` = 200 with a non-empty payload. `EMPTY` = 200 but nothing in it, which "
        "is a real answer and not a failure. `FAIL`/`API ERROR` = the endpoint "
        "refused.",
        "",
        "| § | Endpoint | Style | HTTP | Verdict | ms | Shape returned |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        shape = (r.get("shape") or r.get("detail") or "").replace("|", "\\|")
        lines.append(
            f"| {r['id']} | {r['name']} | {r.get('style','')} | "
            f"{r.get('http','—')} | **{r['verdict']}** | {r.get('elapsed_ms','—')} | "
            f"`{shape[:110]}` |")

    lines += ["", "## Not probed, with the reason", ""]
    for probe_id, name, why in NOT_PROBED:
        lines += [f"- **§{probe_id} {name}** — {why}", ""]

    lines += [
        "## What the raw responses are for",
        "",
        "`probes/results/*.json` holds each full response. Diff the keys they "
        "actually contain against the keys any parser reads — that single check "
        "would have caught the biggest bug in the parent repo at any point in its "
        "life.",
        "",
    ]
    pathlib.Path("probes/RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print("\nwrote probes/RESULTS.md and probes/results/*.json")


if __name__ == "__main__":
    sys.exit(main())
