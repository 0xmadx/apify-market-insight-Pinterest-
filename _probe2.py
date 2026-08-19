import datetime as dt
from src.session import leased_session
from src.transport import TrendsClient, TrendsAPIError
from src import parsers, vocab

with leased_session() as (sess, _i):
    c = TrendsClient(sess); newest = dt.date.fromisoformat(c.bootstrap())
    past = str(newest - dt.timedelta(days=120))
    print(f"newest={newest}  past={past}\n")

    def moments(pred):
        try:
            d = c.style_a("/ads/v4/trends/moment/metrics/US",
                {"moments": ["halloween"], "end_date": past,
                 "aggregation_level": "weekly", "lookback_days": 90,
                 "predicted_days": pred, "interest_limit": 6,
                 "normalize_against_group": False}, kind=None)
            n = len((parsers.parse_moment_metrics(d).get("halloween") or {}).get("series") or [])
            return f"OK ({n} pts)"
        except TrendsAPIError as e: return str(e)[:26]

    def shopping(pred):
        try:
            d = c.style_a("/ads/v4/trends/shopping/product_categories/metrics/US",
                {"product_category_ids": ["1408"], "event": "OUTBOUND_CLICK",
                 "end_date": past, "days": 180, "predicted_days": pred},
                kind=None)
            return f"OK ({len(parsers.parse_category_metrics(d))} rows)"
        except TrendsAPIError as e: return str(e)[:26]

    print("moment/metrics at -120d:")
    for p in (0, 7, 91): print(f"  predicted_days={p:3} -> {moments(p)}")
    print("shopping category metrics at -120d:")
    for p in (0, 28): print(f"  predicted_days={p:3} -> {shopping(p)}")
