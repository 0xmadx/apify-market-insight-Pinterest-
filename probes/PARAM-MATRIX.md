# Parameter matrix — what the params actually do

Live, profile `profile_ldu6ypke8`, 2026-08-18 21:14. One request per case.

**MATCH** = the wire agrees with the doc. **MISMATCH** = the doc is now wrong — fix it in the commit that found this. **NEW** = it errored/behaved as expected but differently in detail; read before trusting.

| Group | Case | Doc claim | Verdict | Actual |
|---|---|---|---|---|
| A | top/ event=OUTBOUND_CLICK | 200 with rows | **MATCH** | `200, 5 rows` |
| A | top/ event=ENGAGEMENT | 200 with rows | **MATCH** | `200, 5 rows` |
| A | top/ event=SAVE | 200 with rows | **MATCH** | `200, 5 rows` |
| A | top/ event=IMPRESSION | 500 (accepted by validator, fails server-side) | **MATCH** | `500 Something went wrong on our end. Sorry about that.` |
| A | top/ event=CLICK | 500 (accepted by validator, fails server-side) | **MATCH** | `500 Something went wrong on our end. Sorry about that.` |
| A | top/ event=LONG_CLICK | 500 (accepted by validator, fails server-side) | **MATCH** | `500 Something went wrong on our end. Sorry about that.` |
| A | top/ event=SEARCH | 500 (accepted by validator, fails server-side) | **MATCH** | `500 Something went wrong on our end. Sorry about that.` |
| B | top/ limit=522 (documented max) | 200 | **MATCH** | `200` |
| B | top/ limit=1000 (over ceiling) | 400 'limit' is too large: 1000 > 522 | **MATCH** | `'limit' is too large: 1000 > 522` |
| B | metrics predicted_days=91 (max) | 200 | **MATCH** | `200` |
| B | metrics predicted_days=180 (over) | 400 too large: 180 > 91 | **NEW** | `errored, but not with '91': 400` |
| B | top_trends numTermsToReturn=100 (max) | 200 | **MATCH** | `200, 100 rows` |
| B | top_trends numTermsToReturn=500 (over) | 400 | **MATCH** | `400` |
| B | moment/metrics interest_limit=24 (max) | 200 | **MATCH** | `200` |
| B | moment/metrics interest_limit=50 (over) | 400 too large: 50 > 24 | **MATCH** | `'interest_limit' is too large: 50 > 24` |
| B | moment/metrics lookback_days=1095 (over 730) | 400 too large: 1095 > 730 | **MATCH** | `'lookback_days' is too large: 1095 > 730` |
| C | metrics aggregation=2 (only valid) | 200 | **MATCH** | `200` |
| C | metrics aggregation=1 | 400 | **MATCH** | `400` |
| C | moment/metrics aggregation_level=daily | 200 — THE ONLY daily source in the API (~456 pts vs 66 weekly) | **MATCH** | `456 points (weekly baseline is ~66)` |
| C | moment/metrics aggregation_level=hourly | 400 | **MATCH** | `'aggregation_level' ('hourly') must be one of: daily,weekly,biweekly,monthly` |
| C | moment/metrics monthly + predicted_days=91 | 400 — 91 does not evenly divide into monthly | **MATCH** | `91 predicted days do not evenly divide into monthly aggregation level` |
| C | top_trends trendsPreset=5 (invalid) | 400 — only 1-4 | **MATCH** | `400` |
| D | prefix_match query=hallow (lowercase) | 200 with 10 rows | **MATCH** | `10 items` |
| D | prefix_match query=HALLOW (uppercase) | 200 + EMPTY — case-sensitive, silent | **MATCH** | `0 items (silent empty confirmed)` |
| D | prefix_match query=zzzqqq (no match) | 200 + empty, not an error | **MATCH** | `0 items (silent empty confirmed)` |
| E | top_products event=OUTBOUND_CLICK | 200 with products | **MATCH** | `200, 33 rows` |
| E | top_products event=SAVE | 200 + [] — WRONG PARAM, silent (not an error) | **MATCH** | `200, 0 rows (silent empty confirmed)` |
| E | top/ with an L3 id (wrong level) | 200 + 0 rows — WRONG LEVEL, silent | **MATCH** | `200, 0 rows (silent empty confirmed)` |
| E | editorial region=FR | 200 + 0 items — REGION UNSUPPORTED, silent | **MATCH** | `0 items (silent empty confirmed)` |
| E | editorial region=US (control) | 200 with 6 items | **MATCH** | `6 items` |
| H | metrics shouldMock=true | 200 with FAKE 2019 data — never use | **MATCH** | `200, first date 2019-06-12T00:00:00.000Z (doc says fake 2019 data)` |

Raw responses: `probes/results/params/*.json`.
