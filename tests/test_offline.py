from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from pipeline.http import ProviderError
from pipeline.model import evaluate_quotes, evaluate_quotes_against_projections
from pipeline.providers.odds_api_io import OddsApiIoProvider
from pipeline.providers.odds_api_io import parse_event as parse_odds_api_io_event
from pipeline.providers.espn import parse_summaries
from pipeline.providers.the_odds_api import parse_event as parse_the_odds_api_event
from pipeline.build import load_settings
from pipeline.schema import Projection


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class ProviderParsingTests(unittest.TestCase):
    def test_primary_provider_parses_two_sided_props(self) -> None:
        self.assertLessEqual(len(load_settings()["bookmakers"]["primary_consensus"]), 2)
        quotes = parse_odds_api_io_event(fixture("odds_api_io_event.json"), "NFL")
        self.assertEqual(len(quotes), 6)
        self.assertEqual({row.book for row in quotes}, {"DraftKings", "FanDuel", "BetMGM"})
        board = evaluate_quotes(quotes, load_settings())
        over = next(row for row in board if row["side"] == "over")
        self.assertIn(over["tier"], {"GOOD", "BEST"})
        self.assertEqual(over["consensus_books"], 3)

    def test_primary_provider_retries_without_rejected_bookmaker(self) -> None:
        settings = json.loads(json.dumps(load_settings()))
        settings["bookmakers"]["primary_consensus"].append("Pinnacle")
        provider = OddsApiIoProvider("fixture-key", settings)

        class FakeClient:
            calls: list[str] = []

            def get(self, path, params, retries=2):
                if path == "/events":
                    return [{"id": 1001, "league": {"name": "NFL"}}]
                self.calls.append(params["bookmakers"])
                if "Pinnacle" in params["bookmakers"]:
                    raise ProviderError(
                        'Odds-API.io request failed (HTTP 400: {"error":"Pinnacle is not a valid bookmaker"})'
                    )
                return [fixture("odds_api_io_event.json")]

        fake = FakeClient()
        provider.client = fake
        quotes = provider.fetch("NFL")
        self.assertEqual(len(quotes), 6)
        self.assertIn("Pinnacle", fake.calls[0])
        self.assertNotIn("Pinnacle", fake.calls[1])

    def test_draftkings_price_can_use_espn_projection(self) -> None:
        draftkings = [
            row
            for row in parse_odds_api_io_event(fixture("odds_api_io_event.json"), "NFL")
            if row.book == "DraftKings"
        ]
        projections = [
            Projection(
                sport="NFL",
                player="Jordan Example",
                team="Kansas City",
                matchup="Buffalo @ Kansas City",
                market="Passing yards",
                projection=315.0,
                samples=4,
                confidence=0.56,
                standard_deviation=12.0,
                recent=[298.0, 311.0, 320.0, 326.0],
                trend=4.0,
            )
        ]
        board = evaluate_quotes_against_projections(draftkings, projections, load_settings())
        over = next(row for row in board if row["side"] == "over")
        self.assertEqual(over["tier"], "BEST")
        self.assertEqual(over["mode"], "projection-priced")
        self.assertEqual(over["projection"], 315.0)

    def test_secondary_provider_parses_american_prices(self) -> None:
        quotes = parse_the_odds_api_event(fixture("the_odds_api_event.json"), "MLB")
        self.assertEqual(len(quotes), 4)
        draftkings_over = next(
            row for row in quotes if row.book == "DraftKings" and row.side == "over"
        )
        self.assertEqual(draftkings_over.price_american, 110)
        self.assertAlmostEqual(draftkings_over.price_decimal, 2.1)

    def test_espn_projection_fallback_has_no_odds(self) -> None:
        summaries = fixture("espn_summaries.json")
        schedule = {
            "torontotempo": [
                {"matchup": "Montreal @ Toronto", "start_time": "2026-08-25T23:00:00Z"},
                {"matchup": "Toronto @ New York", "start_time": "2026-08-26T23:00:00Z"},
            ]
        }
        rows = parse_summaries(summaries, "WNBA", schedule)
        point_rows = [row for row in rows if row.player == "Alex Example" and row.market == "Points"]
        self.assertEqual(len(point_rows), 2)
        points = point_rows[0]
        self.assertEqual(points.samples, 2)
        self.assertAlmostEqual(points.projection, 22.0)
        self.assertAlmostEqual(points.standard_deviation, 3.0)
        self.assertEqual(points.recent, [18.0, 24.0])
        self.assertEqual(
            {row.start_time for row in point_rows},
            {"2026-08-25T23:00:00Z", "2026-08-26T23:00:00Z"},
        )


class SecurityTests(unittest.TestCase):
    def test_repository_has_no_embedded_secret_shaped_hex_tokens(self) -> None:
        suspicious = re.compile(r"(?<![A-Za-z0-9])[a-f0-9]{32,}(?![A-Za-z0-9])", re.I)
        allowed_suffixes = {".json", ".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".example", ".txt"}
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in allowed_suffixes:
                continue
            if suspicious.search(path.read_text(errors="ignore")):
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"secret-looking token found in: {hits}")


if __name__ == "__main__":
    unittest.main()
