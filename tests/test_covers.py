import unittest
from unittest.mock import MagicMock, patch

from pipeline.providers import covers
from pipeline.providers.covers import parse_html
from pipeline.schema import Projection


def projection(player="Patrick Mahomes", market="Rushing yards", event_id="401872931"):
    return Projection(
        sport="NFL",
        player=player,
        team="Kansas City Chiefs",
        matchup="Denver Broncos @ Kansas City Chiefs",
        market=market,
        projection=23.5,
        samples=8,
        confidence=0.65,
        standard_deviation=10.0,
        recent=[12, 18, 24, 31],
        trend=0.0,
        start_time="2026-09-15T00:15:00+00:00",
        event_id=event_id,
    )


HTML = """
<html><body>
  <section class="prop-card">
    <h3>RUSHING YARDS</h3>
    <a>DEN @ KC</a>
    <img alt="Patrick Mahomes logo">
    <span>P. Mahomes</span><span>(QB)</span>
    <strong>o13.5 Rushing Yards</strong>
    <div><img alt="BetMGM logo"><b>o13.5</b> -130</div>
    <div><img alt="DraftKings logo"><b>o13.5</b> -115</div>
    <div><img alt="Unavailable Book logo"><b>o13.5</b> -105</div>
  </section>
  <section class="prop-card">
    <h3>RUSHING YARDS</h3>
    <a>DEN @ KC</a>
    <img alt="Patrick Mahomes logo">
    <span>P. Mahomes</span><span>(QB)</span>
    <strong>u13.5 Rushing Yards</strong>
    <div><img alt="BetMGM Sportsbook logo"><b>u13.5</b> +105</div>
    <div><img alt="DraftKings logo"><b>u13.5</b> -105</div>
  </section>
  <section class="prop-card">
    <h3>ANYTIME TOUCHDOWN</h3>
    <a>DEN @ KC</a>
    <img alt="Patrick Mahomes logo">
    <span>P. Mahomes</span><span>(QB)</span>
    <strong>Anytime Touchdown</strong>
    <div><img alt="FanDuel logo"><b>+700</b></div>
  </section>
</body></html>
"""


class CoversParserTests(unittest.TestCase):
    def test_extracts_real_books_lines_sides_and_prices(self):
        rows = parse_html(
            HTML,
            [projection(), projection(market="Anytime touchdown")],
            observed_at="2026-09-14T12:00:00+00:00",
        )
        self.assertEqual(len(rows), 5)
        by_offer = {(row.book, row.side, row.line): row for row in rows}
        self.assertEqual(by_offer[("BetMGM", "over", 13.5)].price_american, -130)
        self.assertEqual(by_offer[("DraftKings", "under", 13.5)].price_american, -105)
        self.assertEqual(by_offer[("FanDuel", "yes", None)].price_american, 700)
        self.assertTrue(all(row.event_id == "401872931" for row in rows))
        self.assertTrue(all(row.updated_at == "2026-09-14T12:00:00+00:00" for row in rows))
        self.assertNotIn("Unavailable Book", {row.book for row in rows})

    def test_requires_a_unique_scheduled_projection(self):
        self.assertEqual(parse_html(HTML, [], observed_at="2026-09-14T12:00:00+00:00"), [])
        duplicate = projection(event_id="different")
        rows = parse_html(
            HTML,
            [projection(), duplicate],
            observed_at="2026-09-14T12:00:00+00:00",
        )
        self.assertEqual(rows, [])

    def test_deduplicates_repeated_page_cards(self):
        rows = parse_html(
            HTML + HTML,
            [projection(), projection(market="Anytime touchdown")],
            observed_at="2026-09-14T12:00:00+00:00",
        )
        self.assertEqual(len(rows), 5)

    def test_retries_an_empty_html_variant_before_failing_over(self):
        response = MagicMock()
        response.headers.get.return_value = "text/html"
        response.read.return_value = b"<html></html>"
        connection = MagicMock()
        connection.__enter__.return_value = response
        connection.__exit__.return_value = False
        with (
            patch.object(covers.urllib.request, "urlopen", return_value=connection) as opened,
            patch.object(covers, "parse_html", side_effect=[[], ["verified quote"]]),
            patch.object(covers.time, "sleep"),
        ):
            rows = covers.CoversProvider({}).fetch("NFL", [])
        self.assertEqual(rows, ["verified quote"])
        self.assertEqual(opened.call_count, 2)

    def test_rejects_other_sports_at_provider_boundary(self):
        self.assertEqual(covers.CoversProvider({}).fetch("NBA", []), [])


if __name__ == "__main__":
    unittest.main()
