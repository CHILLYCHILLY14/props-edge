from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from pipeline.model import evaluate_quotes
from pipeline.providers.odds_api_io import parse_event as parse_odds_api_io_event
from pipeline.providers.espn import parse_summaries
from pipeline.providers.the_odds_api import parse_event as parse_the_odds_api_event
from pipeline.build import load_settings


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class ProviderParsingTests(unittest.TestCase):
    def test_primary_provider_parses_two_sided_props(self) -> None:
        quotes = parse_odds_api_io_event(fixture("odds_api_io_event.json"), "NFL")
        self.assertEqual(len(quotes), 6)
        self.assertEqual({row.book for row in quotes}, {"DraftKings", "FanDuel", "BetMGM"})
        board = evaluate_quotes(quotes, load_settings())
        over = next(row for row in board if row["side"] == "over")
        self.assertIn(over["tier"], {"GOOD", "BEST"})
        self.assertEqual(over["consensus_books"], 3)

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
        rows = parse_summaries(summaries, "WNBA", {"torontotempo": "Montreal @ Toronto"})
        points = next(row for row in rows if row.player == "Alex Example" and row.market == "Points")
        self.assertEqual(points.samples, 2)
        self.assertAlmostEqual(points.projection, 22.0)
        self.assertAlmostEqual(points.standard_deviation, 3.0)
        self.assertEqual(points.recent, [18.0, 24.0])


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
