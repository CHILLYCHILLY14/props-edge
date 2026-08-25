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
from pipeline.schema import Projection, PropQuote


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

    def test_primary_generic_market_splits_player_and_prop_type(self) -> None:
        event = {
            "id": 22,
            "home": "Toronto",
            "away": "Boston",
            "bookmakers": {
                "DraftKings": [
                    {
                        "name": "Player Props",
                        "odds": [
                            {
                                "label": "Alex Example (Hits)",
                                "hdp": 1.5,
                                "over": 2.1,
                                "under": 1.7,
                            }
                        ],
                    }
                ]
            },
        }
        quotes = parse_odds_api_io_event(event, "MLB")
        self.assertEqual({row.player for row in quotes}, {"Alex Example"})
        self.assertEqual({row.market for row in quotes}, {"Batter hits"})

    def test_primary_generic_market_normalizes_nfl_targets_and_touchdowns(self) -> None:
        event = {
            "id": 23,
            "home": "Toronto",
            "away": "Buffalo",
            "bookmakers": {
                "DraftKings": [
                    {
                        "name": "Player Props",
                        "odds": [
                            {"label": "Sam Receiver (Receiving Targets)", "hdp": 7.5, "over": 1.91, "under": 1.91},
                            {"label": "Sam Receiver (Anytime Touchdown Scorer)", "yes": 2.5},
                        ],
                    }
                ]
            },
        }
        quotes = parse_odds_api_io_event(event, "NFL")
        self.assertEqual({row.player for row in quotes}, {"Sam Receiver"})
        self.assertEqual({row.market for row in quotes}, {"Targets", "Anytime touchdown"})

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

    def test_secondary_provider_normalizes_anytime_touchdown(self) -> None:
        event = {
            "id": "nfl-1",
            "commence_time": "2026-09-10T00:00:00Z",
            "away_team": "Buffalo Bills",
            "home_team": "Kansas City Chiefs",
            "bookmakers": [
                {
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "player_anytime_td",
                            "outcomes": [
                                {"description": "Sam Receiver", "name": "Yes", "price": 220}
                            ],
                        }
                    ],
                }
            ],
        }
        quotes = parse_the_odds_api_event(event, "NFL")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].market, "Anytime touchdown")
        self.assertEqual(quotes[0].side, "yes")

    def test_espn_nfl_projection_builds_targets_and_anytime_touchdown(self) -> None:
        def summary(rush_td, rush_yards, rush_attempts, rec_td, rec_yards, receptions, targets, completions_attempts):
            return {
                "boxscore": {
                    "players": [
                        {
                            "team": {"displayName": "Toronto Northmen"},
                            "statistics": [
                                {
                                    "type": "passing",
                                    "keys": ["completions/passingAttempts", "passingYards", "passingTouchdowns", "interceptions"],
                                    "athletes": [{"athlete": {"displayName": "Sam Quarterback"}, "stats": [completions_attempts, 225, 2, 1]}],
                                },
                                {
                                    "type": "rushing",
                                    "keys": ["rushingTouchdowns", "rushingYards", "rushingAttempts"],
                                    "athletes": [{"athlete": {"displayName": "Sam Receiver"}, "stats": [rush_td, rush_yards, rush_attempts]}],
                                },
                                {
                                    "type": "receiving",
                                    "keys": ["receivingTouchdowns", "receivingYards", "receptions", "targets"],
                                    "athletes": [{"athlete": {"displayName": "Sam Receiver"}, "stats": [rec_td, rec_yards, receptions, targets]}],
                                },
                            ],
                        }
                    ]
                }
            }

        schedule = {"torontonorthmen": [{"matchup": "Buffalo @ Toronto", "start_time": "2026-09-10T00:00:00Z"}]}
        rows = parse_summaries(
            [summary(1, 50, 10, 0, 20, 2, 3, "17/28"), summary(0, 30, 8, 1, 40, 4, 6, "20/30")],
            "NFL",
            schedule,
        )
        targets = next(row for row in rows if row.market == "Targets")
        touchdown = next(row for row in rows if row.market == "Anytime touchdown")
        self.assertAlmostEqual(targets.projection, 5.0)
        self.assertEqual(targets.recent, [3.0, 6.0])
        self.assertAlmostEqual(touchdown.projection, 1.0)
        self.assertEqual(touchdown.recent, [1.0, 1.0])
        completions = next(row for row in rows if row.market == "Pass completions")
        attempts = next(row for row in rows if row.market == "Pass attempts")
        self.assertAlmostEqual(completions.projection, 19.0)
        self.assertAlmostEqual(attempts.projection, 29.33)

    def test_anytime_touchdown_yes_price_can_qualify(self) -> None:
        quote = PropQuote(
            sport="NFL",
            event_id="nfl-1",
            start_time="2026-09-10T00:00:00Z",
            matchup="Buffalo @ Toronto",
            player="Sam Receiver",
            market="Anytime touchdown",
            side="yes",
            line=None,
            price_decimal=3.2,
            price_american=220,
            book="DraftKings",
            provider="fixture",
        )
        projection = Projection(
            sport="NFL",
            player="Sam Receiver",
            team="Toronto Northmen",
            matchup="Buffalo @ Toronto",
            market="Anytime touchdown",
            projection=0.8,
            samples=4,
            confidence=0.56,
            standard_deviation=0.5,
            recent=[1.0, 0.0, 1.0, 0.0],
            trend=0.0,
        )
        row = evaluate_quotes_against_projections([quote], [projection], load_settings())[0]
        self.assertEqual(row["tier"], "GOOD")
        self.assertGreater(row["recommended_stake"], 0)
        self.assertLessEqual(row["recommended_stake"], 50)

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
        pra_rows = [
            row
            for row in rows
            if row.player == "Alex Example" and row.market == "Points + Rebounds + Assists"
        ]
        self.assertEqual(len(pra_rows), 2)
        self.assertAlmostEqual(pra_rows[0].projection, 36.67)
        self.assertEqual(pra_rows[0].recent, [30.0, 40.0])


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
