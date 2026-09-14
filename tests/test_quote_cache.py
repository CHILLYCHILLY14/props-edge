import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.build import (
    _load_quote_cache,
    _merge_quote_cache,
    _write_quote_cache,
    load_settings,
)
from pipeline.schema import PropQuote, american_to_decimal


NOW = dt.datetime(2026, 9, 14, 19, 0, tzinfo=dt.timezone.utc)


def quote(player="Bo Nix", book="DraftKings", price=-110,
          updated_at="2026-09-14T18:00:00+00:00"):
    return PropQuote(
        sport="NFL",
        event_id="401872931",
        start_time="2026-09-15T00:15:00+00:00",
        matchup="Denver Broncos @ Kansas City Chiefs",
        player=player,
        market="Pass completions",
        side="over",
        line=21.5,
        price_decimal=american_to_decimal(price),
        price_american=price,
        book=book,
        provider="Covers public prop comparison",
        updated_at=updated_at,
    )


class QuoteCacheTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()

    def test_cache_reuses_only_fresh_consistent_regulated_offers(self):
        rows = [
            quote("Fresh"),
            quote("Stale", updated_at="2026-09-14T06:00:00+00:00"),
            quote("Future", updated_at="2026-09-14T20:00:00+00:00"),
            quote("Unregulated", book="Offshore Book"),
            replace(quote("Bad price"), price_decimal=4.0),
        ]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "quotes.json"
            path.write_text(json.dumps({"schema": 1, "quotes": [row.to_dict() for row in rows]}))
            loaded = _load_quote_cache(self.settings, path=path, now=NOW)
        self.assertEqual([row.player for row in loaded], ["Fresh"])
        self.assertEqual(loaded[0].updated_at, "2026-09-14T18:00:00+00:00")

    def test_current_observation_replaces_same_cached_offer(self):
        cached = quote(price=120)
        current = quote(price=-120, updated_at="2026-09-14T18:55:00+00:00")
        other = quote("RJ Harvey", price=105)
        merged = _merge_quote_cache([cached, other], [current])
        self.assertEqual(len(merged), 2)
        bo = next(row for row in merged if row.player == "Bo Nix")
        self.assertEqual(bo.price_american, -120)
        self.assertEqual(bo.updated_at, "2026-09-14T18:55:00+00:00")

    def test_writing_cache_does_not_refresh_observation_time(self):
        original = quote(updated_at="2026-09-14T18:00:00+00:00")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "quotes.json"
            _write_quote_cache([original], path=path)
            stored = json.loads(path.read_text())
        self.assertEqual(
            stored["quotes"][0]["updated_at"],
            "2026-09-14T18:00:00+00:00",
        )
        self.assertIn("must never", stored["note"])


if __name__ == "__main__":
    unittest.main()
