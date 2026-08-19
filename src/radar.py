"""The Trend Radar traversal — Pinterest's curated layer, cheap by design.

    topics/featured/{region}/SAVE   → 5 spotlight trends (detail included free)
    editorial/content/{region}      → 6 Editors' Picks with campaign windows

Two calls, eleven records. Built to run on a schedule: the seen-set (already in
the freshness layer) makes a scheduled run emit only what changed, and the
`fields` chosen in scraper.py are the movement fields so a re-order or a new
unread field does not re-emit the world.

Both sources are curated BY PINTEREST — that is their value (an editor decided
this deserves attention) and their bias (an editor decided). The records say
`curated_by: pinterest` so a customer never mistakes this for organic ranking.
"""
from . import parsers, vocab
from .transport import TrendsAPIError


class RadarScraper:
    def __init__(self, client, region="US", interest=None, log=print):
        self.client = client
        self.region = vocab.region(region)
        self.interest = str(interest) if interest else None
        self.log = log

    def run(self, include_spotlight=True, include_editorial=True):
        end_date = self.client.bootstrap()

        if include_spotlight:
            yield from self._spotlight(end_date)
        if include_editorial:
            yield from self._editorial(end_date)

    # ------------------------------------------------------------ spotlight

    def _spotlight(self, end_date):
        data = {"publish_state": "PUBLISHED"}
        if self.interest:
            # Exactly one interest id, or omit for cross-interest "All" —
            # multiple ids → 400 (doc #7 §3.2).
            data["interests"] = [self.interest]

        trends = parsers.parse_spotlight(self.client.style_a(
            f"/ads/v4/trends/topics/featured/{self.region}/SAVE", data))
        self.log(f"[radar] spotlight: {len(trends)} trends")

        for trend in trends:
            yield {
                "source": "spotlight",
                "curated_by": "pinterest",
                "id": trend["id"],
                "name": trend["name"],
                "description": trend["description"],
                "region": self.region,
                "interests": trend["interests"],
                "pct_growth_mom": trend["pct_growth_mom"],
                "keywords": trend["keywords"],
                "series": trend["series"],
                "pins": trend["pins"],
                "_meta": {
                    "end_date": end_date,
                    "basis": "measured",
                    "normalization_scope":
                        f"spotlight:{self.region}:{trend['id']}",
                    "note": "time_series is normalised to the trend itself "
                            "(peak=100); never compare counts across trends",
                },
            }

    # ------------------------------------------------------------ editorial

    def _editorial(self, end_date):
        try:
            vocab.region(self.region, capability="editorial")
        except vocab.InvalidParam as exc:
            # A region outside US/CA/GB+IE would return 200 with 0 items — a
            # silent nothing. Refused here with the reason instead.
            self.log(f"[radar] editorial skipped: {exc}")
            return

        items = parsers.parse_editorial(self.client.style_a(
            f"/ads/v4/trends/editorial/content/{self.region}"), self.region)
        self.log(f"[radar] editorial: {len(items)} items")

        for item in items:
            yield {
                "source": "editorial",
                "curated_by": "pinterest",
                "id": item["id"],
                "name": item["title"],
                "description": item["body"],
                "region": self.region,
                "interests": item["interests"],
                "keywords": item["keywords"],
                # F3 — the campaign window: when the editorial push began.
                "campaign_start": item["campaign_start"],
                "campaign_end": item["campaign_end"],
                "pins": item["pins"],
                "_meta": {
                    "end_date": end_date,
                    "basis": "curated",
                    "regions_covered": item["regions"],
                    "note": "hand-written by Pinterest editors; keywords are "
                            "this region's list only",
                },
            }
