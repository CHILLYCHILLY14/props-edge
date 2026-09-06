from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from pipeline import model
from pipeline.build import load_settings
from tests.test_offline import NOW, fixture, sample_projection, parse_primary_event


class QuoteRegressionTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()
        self.quotes = parse_primary_event(fixture('odds_api_io_event.json'), 'NFL')
        clock = patch.object(model, '_utc_now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def price(self, quotes=None, projection=None):
        return model.evaluate_quotes_against_projections(
            self.quotes if quotes is None else quotes,
            [sample_projection() if projection is None else projection], self.settings)

    def test_live_quotes_can_qualify(self):
        self.assertTrue(any(row['recommended_stake'] > 0 for row in self.price()))

    def test_old_missing_invalid_and_future_quotes_never_stake(self):
        for stamp in ['2026-08-01T00:00:00Z', None, 'broken', (NOW+timedelta(hours=1)).isoformat()]:
            with self.subTest(stamp=stamp):
                rows = self.price([replace(q, updated_at=stamp) for q in self.quotes])
                self.assertTrue(rows)
                self.assertTrue(all(r['tier']=='PASS' and r['recommended_stake']==0 for r in rows))

    def test_kickoff_blocks_even_with_a_fresh_quote(self):
        start = (NOW-timedelta(minutes=1)).isoformat()
        rows = self.price([replace(q, start_time=start, updated_at=NOW.isoformat()) for q in self.quotes],
                          replace(sample_projection(), start_time=start))
        self.assertTrue(rows)
        self.assertTrue(all(r['recommended_stake']==0 for r in rows))

    def test_wrong_week_opponent_and_ambiguous_player_do_not_match(self):
        projection = sample_projection()
        for changed in [replace(projection,start_time='2026-09-20T17:00:00Z'),
                        replace(projection,matchup='Buffalo @ Miami')]:
            self.assertEqual(self.price(projection=changed), [])
        rows = model.evaluate_quotes_against_projections(self.quotes,[projection,projection],self.settings)
        self.assertEqual(rows, [])

    def test_event_identity_allows_equivalent_timezone_and_different_provider_id(self):
        rows = self.price(projection=replace(sample_projection(), start_time='2026-09-13T13:00:00-04:00', event_id='espn-999'))
        self.assertTrue(any(r['recommended_stake']>0 for r in rows))

    def test_stale_best_price_cannot_displace_a_fresh_offer(self):
        stale = replace(self.quotes[0], price_decimal=3, price_american=200, updated_at='2026-08-01T00:00:00Z')
        rows = self.price([*self.quotes, stale])
        self.assertTrue(rows)
        self.assertTrue(all(r['price_decimal']!=3 for r in rows))
