from __future__ import annotations

import json
import re
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pipeline.build as build_module
from pipeline.build import load_settings
from pipeline.http import ProviderError
from pipeline.model import (
    _power_devig,
    _projection_probabilities,
    evaluate_quotes,
    evaluate_quotes_against_projections,
    merge_boards,
    select_portfolio,
)
from pipeline.providers.espn import (
    EspnProjectionProvider,
    _nfl_season_year,
    _season_type,
    _team_and_matchups,
    parse_summaries,
)
from pipeline.providers.odds_api_io import OddsApiIoProvider
from pipeline.providers.odds_api_io import parse_event as parse_primary_event
from pipeline.providers.the_odds_api import parse_event as parse_secondary_event
from pipeline.schema import Projection, PropQuote


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def sample_projection(
    *,
    market: str = "Passing yards",
    projection: float = 282.0,
    samples: int = 8,
    confidence: float = 0.65,
    deviation: float = 22.0,
    recent: list[float] | None = None,
) -> Projection:
    values = recent or [250, 270, 281, 295, 260, 300, 289, 285]
    return Projection(
        sport="NFL",
        player="Jordan Example",
        team="Kansas City",
        matchup="Buffalo @ Kansas City",
        market=market,
        projection=projection,
        samples=samples,
        confidence=confidence,
        standard_deviation=deviation,
        recent=values,
        trend=2.0,
        start_time="2026-09-13T17:00:00Z",
        current_season_samples=samples,
    )


def target_quote(
    *,
    side: str = "over",
    line: float | None = 275.5,
    price_decimal: float = 2.1,
    market: str = "Passing yards",
    player: str = "Jordan Example",
    book: str = "DraftKings",
) -> PropQuote:
    if price_decimal >= 2:
        american = round((price_decimal - 1) * 100)
    else:
        american = round(-100 / (price_decimal - 1))
    return PropQuote(
        sport="NFL",
        event_id="1001",
        start_time="2026-09-13T17:00:00Z",
        matchup="Buffalo @ Kansas City",
        player=player,
        market=market,
        side=side,
        line=line,
        price_decimal=price_decimal,
        price_american=american,
        book=book,
        provider="fixture",
    )


class NflOnlyTests(unittest.TestCase):
    def test_settings_contain_only_nfl(self) -> None:
        self.assertEqual(list(load_settings()["sports"]), ["NFL"])

    def test_visible_site_is_nfl_only(self) -> None:
        text = "\n".join(
            [
                (ROOT / "site" / "index.html").read_text(),
                (ROOT / "README.md").read_text(),
                (ROOT / "config" / "settings.json").read_text(),
            ]
        )
        for unwanted in ("NCAAF", "MLB", "WNBA"):
            self.assertNotIn(unwanted, text)
        self.assertNotIn("Run update", text)

    def test_static_browser_ids_exist(self) -> None:
        app = (ROOT / "site" / "app.js").read_text()
        html = (ROOT / "site" / "index.html").read_text()
        referenced = set(re.findall(r'\$\("#([A-Za-z][A-Za-z0-9_-]*)"\)', app))
        declared = set(re.findall(r'id="([A-Za-z][A-Za-z0-9_-]*)"', html))
        self.assertFalse(referenced - declared, f"Missing HTML ids: {sorted(referenced - declared)}")

    def test_schedule_window_reaches_the_opening_slate(self) -> None:
        self.assertGreaterEqual(load_settings()["fetch"]["lookahead_days"], 21)
        self.assertGreaterEqual(
            load_settings()["projection_model"]["maximum_projection_rows"],
            5000,
        )

    def test_non_nfl_provider_input_is_rejected(self) -> None:
        self.assertEqual(parse_primary_event(fixture("odds_api_io_event.json"), "OTHER"), [])
        self.assertEqual(parse_secondary_event(fixture("the_odds_api_event.json"), "OTHER"), [])


class EspnFetchTests(unittest.TestCase):
    def test_fetches_seasons_instead_of_an_invalid_long_range(self) -> None:
        provider = EspnProjectionProvider(load_settings())

        class FakeClient:
            calls: list[dict] = []

            def get(self, path, params, retries=2):
                self.calls.append({"path": path, "params": dict(params)})
                return {"events": []}

        client = FakeClient()
        provider.client = client
        self.assertEqual(provider.fetch("NFL"), [])
        scoreboard_calls = [
            call for call in client.calls if call["path"].endswith("/scoreboard")
        ]
        self.assertEqual(len(scoreboard_calls), 3)
        historical = scoreboard_calls[:2]
        self.assertTrue(all(len(call["params"]["dates"]) == 4 for call in historical))
        self.assertTrue(all(call["params"]["seasontype"] == 2 for call in historical))
        self.assertIn("-", scoreboard_calls[2]["params"]["dates"])


class OddsAndMarketTests(unittest.TestCase):
    def test_power_devig_sums_to_one(self) -> None:
        fair = _power_devig(1.80, 2.05)
        self.assertIsNotNone(fair)
        self.assertAlmostEqual(sum(fair), 1.0)
        self.assertGreater(fair[0], fair[1])

    def test_primary_provider_parses_complete_nfl_props(self) -> None:
        quotes = parse_primary_event(fixture("odds_api_io_event.json"), "NFL")
        self.assertEqual(len(quotes), 6)
        self.assertEqual({row.book for row in quotes}, {"DraftKings", "FanDuel", "BetMGM"})
        self.assertEqual({row.market for row in quotes}, {"Passing yards"})

    def test_secondary_provider_parses_nfl_american_prices(self) -> None:
        quotes = parse_secondary_event(fixture("the_odds_api_event.json"), "NFL")
        self.assertEqual(len(quotes), 4)
        over = next(row for row in quotes if row.book == "DraftKings" and row.side == "over")
        self.assertEqual(over.price_american, 110)
        self.assertAlmostEqual(over.price_decimal, 2.1)

    def test_secondary_provider_supports_combined_and_kicking_markets(self) -> None:
        event = fixture("the_odds_api_event.json")
        event["bookmakers"] = event["bookmakers"][:1]
        event["bookmakers"][0]["markets"] = [
            {
                "key": "player_pass_rush_yds",
                "outcomes": [{"description": "Jordan Example", "name": "Over", "price": -110, "point": 310.5}],
            },
            {
                "key": "player_field_goals",
                "outcomes": [{"description": "Kicker Example", "name": "Over", "price": 105, "point": 1.5}],
            },
        ]
        markets = {quote.market for quote in parse_secondary_event(event, "NFL")}
        self.assertEqual(markets, {"Pass + rush yards", "Field goals made"})

    def test_consensus_alone_never_becomes_a_bet(self) -> None:
        quotes = parse_primary_event(fixture("odds_api_io_event.json"), "NFL")
        board = evaluate_quotes(quotes, load_settings())
        self.assertTrue(board)
        self.assertEqual({row["tier"] for row in board}, {"PASS"})
        self.assertEqual({row["recommended_stake"] for row in board}, {0.0})
        over = next(row for row in board if row["side"] == "over")
        self.assertEqual(over["consensus_books"], 2)
        self.assertIn("No independent NFL player projection", over["reason"])

    def test_primary_provider_retries_after_bookmaker_limit(self) -> None:
        provider = OddsApiIoProvider("fixture-key", load_settings())

        class FakeClient:
            calls: list[str] = []

            def get(self, path, params, retries=2):
                if path == "/events":
                    return [{"id": 1001, "league": {"name": "NFL"}}]
                self.calls.append(params["bookmakers"])
                if len(params["bookmakers"].split(",")) > 2:
                    raise ProviderError("Odds request failed: allowed max 2 bookmakers")
                return [fixture("odds_api_io_event.json")]

        provider.client = FakeClient()
        quotes = provider.fetch("NFL")
        self.assertEqual(len(quotes), 6)
        self.assertEqual(len(provider.client.calls[0].split(",")), 3)
        self.assertEqual(len(provider.client.calls[1].split(",")), 2)
        self.assertEqual(len(provider.client.calls[2].split(",")), 1)

    def test_primary_provider_maps_td_passes_market(self) -> None:
        event = fixture("odds_api_io_event.json")
        for markets in event["bookmakers"].values():
            markets[0]["name"] = "TD Passes"
        quotes = parse_primary_event(event, "NFL")
        self.assertEqual({quote.market for quote in quotes}, {"Passing touchdowns"})

    def test_primary_provider_retries_missing_book_on_documented_event_endpoint(self) -> None:
        provider = OddsApiIoProvider("fixture-key", load_settings())

        class FakeClient:
            calls: list[dict] = []

            def get(self, path, params, retries=2):
                self.calls.append({"path": path, "params": dict(params)})
                if path == "/events":
                    return [{"id": 1001, "league": {"name": "NFL"}}]
                event = fixture("odds_api_io_event.json")
                if path == "/odds/multi":
                    event["bookmakers"].pop("DraftKings")
                    return [event]
                if path == "/odds":
                    event["bookmakers"] = {"DraftKings": event["bookmakers"]["DraftKings"]}
                    return event
                raise AssertionError(path)

        provider.client = FakeClient()
        quotes = provider.fetch("NFL")
        self.assertTrue(any(row.book == "DraftKings" for row in quotes))
        fallback = [call for call in provider.client.calls if call["path"] == "/odds"]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["params"]["bookmakers"], "DraftKings")


class BuildFallbackTests(unittest.TestCase):
    def test_ontario_key_provider_is_preferred_when_it_has_eligible_prices(self) -> None:
        primary = [
            row for row in parse_primary_event(fixture("odds_api_io_event.json"), "NFL")
            if row.book != "DraftKings"
        ]
        secondary = parse_secondary_event(fixture("the_odds_api_event.json"), "NFL")

        class Primary:
            def __init__(self, *_): pass
            def fetch(self, _): return primary

        class Secondary:
            def __init__(self, *_): pass
            def fetch(self, _): return secondary

        class Espn:
            def __init__(self, *_): pass
            def fetch(self, _): return []

        with patch.dict("os.environ", {"ODDS_API_IO_KEY":"primary", "THE_ODDS_API_KEY":"secondary"}), \
             patch.object(build_module, "OddsApiIoProvider", Primary), \
             patch.object(build_module, "TheOddsApiProvider", Secondary), \
             patch.object(build_module, "EspnProjectionProvider", Espn), \
             patch.object(build_module, "_write_json"):
            meta = build_module.build()

        self.assertEqual(meta["source_by_sport"]["NFL"]["source"], "The Odds API (Ontario keys)")
        self.assertGreater(meta["counts"]["eligible_priced_quotes"], 0)
        self.assertGreater(meta["source_by_provider"]["odds_api_io"]["eligible_priced_quotes"], 0)
        self.assertGreater(meta["source_by_provider"]["the_odds_api"]["eligible_priced_quotes"], 0)

    def test_regulated_brand_feed_is_continuity_fallback(self) -> None:
        primary = parse_primary_event(fixture("odds_api_io_event.json"), "NFL")

        class Primary:
            def __init__(self, *_): pass
            def fetch(self, _): return primary

        class Secondary:
            def __init__(self, *_): pass
            def fetch(self, _): return []

        class Espn:
            def __init__(self, *_): pass
            def fetch(self, _): return []

        with patch.dict("os.environ", {"ODDS_API_IO_KEY":"primary", "THE_ODDS_API_KEY":"secondary"}), \
             patch.object(build_module, "OddsApiIoProvider", Primary), \
             patch.object(build_module, "TheOddsApiProvider", Secondary), \
             patch.object(build_module, "EspnProjectionProvider", Espn), \
             patch.object(build_module, "_write_json"):
            meta = build_module.build()

        self.assertEqual(
            meta["source_by_sport"]["NFL"]["source"],
            "Odds-API.io (regulated-brand fallback)",
        )
        self.assertEqual(meta["counts"]["eligible_books"], 3)


class ProjectionPricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings()
        self.quotes = parse_primary_event(fixture("odds_api_io_event.json"), "NFL")

    def test_projection_and_market_are_separate_inputs(self) -> None:
        board = evaluate_quotes_against_projections(
            self.quotes, [sample_projection()], self.settings
        )
        over = next(row for row in board if row["side"] == "over")
        self.assertEqual(over["mode"], "projection-and-market")
        self.assertEqual(over["market_basis"], "other Ontario books' no-vig median")
        self.assertNotEqual(over["projection_prob"], over["market_fair_prob"])
        self.assertAlmostEqual(
            over["edge_raw"],
            over["model_prob_no_push"] / over["market_fair_prob"] - 1,
            places=4,
        )
        self.assertAlmostEqual(
            over["edge_price"],
            over["model_prob"] * over["price_decimal"] + over["push_prob"] - 1,
            places=4,
        )
        self.assertIn(over["tier"], {"LEAN", "GOOD", "BEST"})
        self.assertGreaterEqual(over["recommended_stake"], 5)

    def test_incomplete_offered_market_fails_closed(self) -> None:
        draftkings_over = [row for row in self.quotes if row.book == "DraftKings" and row.side == "over"]
        board = evaluate_quotes_against_projections(
            draftkings_over, [sample_projection()], self.settings
        )
        row = board[0]
        self.assertEqual(row["tier"], "PASS")
        self.assertEqual(row["recommended_stake"], 0)
        self.assertIn("Complete two-sided", row["reason"])

    def test_best_regulated_price_is_selected_for_each_side(self) -> None:
        board = evaluate_quotes(self.quotes, self.settings)
        over = next(row for row in board if row["side"] == "over")
        under = next(row for row in board if row["side"] == "under")
        self.assertEqual((over["book"], over["price_decimal"]), ("DraftKings", 2.1))
        self.assertEqual((under["book"], under["price_decimal"]), ("FanDuel", 2.05))

    def test_non_allowlisted_book_is_never_published(self) -> None:
        offshore = [
            target_quote(side="over", price_decimal=2.5, book="Unregulated Example"),
            target_quote(side="under", price_decimal=2.5, book="Unregulated Example"),
        ]
        board = evaluate_quotes(self.quotes + offshore, self.settings)
        self.assertNotIn("Unregulated Example", {row["book"] for row in board})
        over = next(row for row in board if row["side"] == "over")
        self.assertEqual(over["price_decimal"], 2.1)

    def test_one_sided_price_does_not_hide_best_complete_offer(self) -> None:
        incomplete_high = target_quote(
            side="over", price_decimal=2.5, book="BetRivers"
        )
        board = evaluate_quotes(self.quotes + [incomplete_high], self.settings)
        over = next(row for row in board if row["side"] == "over")
        self.assertEqual((over["book"], over["price_decimal"]), ("DraftKings", 2.1))

    def test_ontario_provider_label_is_canonicalized(self) -> None:
        labelled = [
            target_quote(side="over", price_decimal=2.2, book="BetMGM (CA - ON)"),
            target_quote(side="under", price_decimal=1.75, book="BetMGM (CA - ON)"),
        ]
        board = evaluate_quotes(labelled, self.settings)
        self.assertEqual({row["book"] for row in board}, {"BetMGM"})

    def test_thin_sample_is_watch_with_zero_stake(self) -> None:
        projection = sample_projection(samples=3, confidence=0.50, recent=[290, 286, 280])
        row = next(
            row
            for row in evaluate_quotes_against_projections(
                self.quotes, [projection], self.settings
            )
            if row["side"] == "over"
        )
        self.assertEqual(row["tier"], "PASS")
        self.assertEqual(row["recommended_stake"], 0)
        self.assertIn("regular-season samples", row["reason"])

    def test_extreme_projection_market_gap_is_rejected(self) -> None:
        projection = sample_projection(
            projection=360,
            confidence=0.70,
            deviation=10,
            recent=[345, 350, 360, 355, 365, 370, 358, 362],
        )
        over = next(
            row
            for row in evaluate_quotes_against_projections(
                self.quotes, [projection], self.settings
            )
            if row["side"] == "over"
        )
        self.assertEqual(over["tier"], "PASS")
        self.assertIn("disagreement exceeds", over["reason"])

    def test_moderate_projection_gap_is_lean_with_reduced_stake(self) -> None:
        projection = sample_projection(projection=310, confidence=0.70)
        reduced = next(
            row
            for row in evaluate_quotes_against_projections(
                self.quotes, [projection], self.settings
            )
            if row["side"] == "over"
        )
        full_settings = deepcopy(self.settings)
        full_settings["projection_model"]["raw_gap_stake_multiplier"] = 1.0
        unreduced = next(
            row
            for row in evaluate_quotes_against_projections(
                self.quotes, [projection], full_settings
            )
            if row["side"] == "over"
        )
        self.assertGreater(reduced["raw_projection_market_gap"], 0.18)
        self.assertLessEqual(reduced["raw_projection_market_gap"], 0.25)
        self.assertEqual(reduced["tier"], "LEAN")
        self.assertIn("reduced stake", reduced["reason"])
        self.assertGreater(reduced["recommended_stake"], 0)
        self.assertLess(reduced["recommended_stake"], unreduced["recommended_stake"])

    def test_small_model_sized_stake_is_not_erased_by_old_five_dollar_floor(self) -> None:
        projection = sample_projection(
            projection=280,
            samples=4,
            confidence=0.45,
            recent=[276, 278, 280, 282],
        )
        row = next(
            row
            for row in evaluate_quotes_against_projections(
                self.quotes, [projection], self.settings
            )
            if row["side"] == "over"
        )
        self.assertEqual(row["tier"], "LEAN")
        self.assertGreaterEqual(row["recommended_stake"], 1)
        self.assertLess(row["recommended_stake"], 5)

    def test_integer_line_tracks_push_probability(self) -> None:
        quotes = [
            target_quote(side="over", line=2.0, price_decimal=2.1, market="Passing touchdowns"),
            target_quote(side="under", line=2.0, price_decimal=1.77, market="Passing touchdowns"),
            target_quote(side="over", line=2.0, price_decimal=1.91, market="Passing touchdowns", book="FanDuel"),
            target_quote(side="under", line=2.0, price_decimal=1.91, market="Passing touchdowns", book="FanDuel"),
        ]
        projection = sample_projection(
            market="Passing touchdowns",
            projection=2.0,
            confidence=0.65,
            deviation=0.8,
            recent=[2, 1, 3, 2, 2, 1, 3, 2],
        )
        over = next(
            row
            for row in evaluate_quotes_against_projections(quotes, [projection], self.settings)
            if row["side"] == "over"
        )
        self.assertGreater(over["push_prob"], 0.1)
        self.assertAlmostEqual(
            over["model_prob"] + over["push_prob"] + (1 - over["model_prob_no_push"]) * (1 - over["push_prob"]),
            1.0,
            places=4,
        )

    def test_touchdown_probability_is_conservative(self) -> None:
        quote = target_quote(
            side="yes",
            line=None,
            price_decimal=3.2,
            market="Anytime touchdown",
        )
        projection = sample_projection(
            market="Anytime touchdown",
            projection=0.5,
            confidence=0.65,
            deviation=0.5,
            recent=[1, 0, 1, 0, 0, 1, 0, 1],
        )
        win, push, loss = _projection_probabilities(quote, projection)
        self.assertEqual(push, 0)
        self.assertAlmostEqual(win + loss, 1)
        self.assertGreater(win, 0.18)
        self.assertLess(win, 0.55)

    def test_prior_season_form_receives_less_weight(self) -> None:
        current = sample_projection()
        prior = replace(current, current_season_samples=0)
        current_row = next(
            row for row in evaluate_quotes_against_projections(self.quotes, [current], self.settings)
            if row["side"] == "over"
        )
        prior_row = next(
            row for row in evaluate_quotes_against_projections(self.quotes, [prior], self.settings)
            if row["side"] == "over"
        )
        self.assertLess(prior_row["projection_weight"], current_row["projection_weight"])
        self.assertLess(prior_row["season_maturity"], current_row["season_maturity"])

    def test_out_player_cannot_qualify(self) -> None:
        projection = replace(sample_projection(), injury_status="Out")
        rows = evaluate_quotes_against_projections(self.quotes, [projection], self.settings)
        self.assertTrue(rows)
        self.assertEqual({row["tier"] for row in rows}, {"PASS"})
        self.assertTrue(all(row["recommended_stake"] == 0 for row in rows))
        self.assertTrue(all("roster status" in row["reason"] for row in rows))

    def test_unverified_roster_cannot_qualify(self) -> None:
        projection = replace(sample_projection(), roster_verified=False)
        rows = evaluate_quotes_against_projections(self.quotes, [projection], self.settings)
        self.assertEqual({row["tier"] for row in rows}, {"PASS"})
        self.assertTrue(all(row["recommended_stake"] == 0 for row in rows))
        self.assertTrue(all("roster verification" in row["reason"] for row in rows))

    def test_merge_prefers_projection_evaluation_even_when_it_passes(self) -> None:
        watch = evaluate_quotes(self.quotes, self.settings)
        thin = evaluate_quotes_against_projections(
            self.quotes,
            [sample_projection(samples=2, confidence=0.35, recent=[280, 282])],
            self.settings,
        )
        merged = merge_boards(watch, thin)
        over = next(row for row in merged if row["side"] == "over")
        self.assertEqual(over["mode"], "projection-and-market")
        self.assertIn("samples", over["reason"])


class PortfolioTests(unittest.TestCase):
    def test_portfolio_limits_alternate_lines_and_player_count(self) -> None:
        settings = load_settings()
        base = {
            "sport": "NFL",
            "event_id": "g1",
            "start_time": "2026-09-13T17:00:00Z",
            "matchup": "Away @ Home",
            "player": "Player One",
            "side": "over",
            "price_american": -110,
            "edge": 0.06,
            "edge_real": 0.05,
            "tier": "GOOD",
            "recommended_stake": 20,
        }
        board = [
            {**base, "market": "Receiving yards", "line": 60.5, "edge_real": 0.06},
            {**base, "market": "Receiving yards", "line": 70.5, "edge_real": 0.04},
            {**base, "market": "Receptions", "line": 5.5},
            {**base, "market": "Targets", "line": 7.5},
        ]
        selected = select_portfolio(board, settings)
        active = [row for row in selected if row["tier"] != "PASS"]
        self.assertEqual(len(active), 2)
        self.assertEqual(sum(row["market"] == "Receiving yards" for row in active), 1)
        self.assertTrue(all(row["recommended_stake"] > 0 for row in active))
        self.assertTrue(all(row["recommended_stake"] == 0 for row in selected if row["tier"] == "PASS"))


class EspnTests(unittest.TestCase):
    def test_january_belongs_to_the_previous_nfl_season(self) -> None:
        import datetime as dt

        self.assertEqual(_nfl_season_year(dt.date(2027, 1, 3)), 2026)
        self.assertEqual(_nfl_season_year(dt.date(2026, 8, 26)), 2026)

    def test_preseason_type_is_identified(self) -> None:
        self.assertEqual(_season_type({"season": {"type": 1}}), 1)
        self.assertEqual(_season_type({"season": {"type": 2}}), 2)

    def test_upcoming_schedule_identifies_opponent_and_venue(self) -> None:
        rows = _team_and_matchups([
            {
                "id": "401",
                "date": "2026-09-13T17:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Kansas City"}},
                        {"homeAway": "away", "team": {"displayName": "Buffalo"}},
                    ]
                }],
            }
        ])
        self.assertEqual(rows["kansascity"][0]["opponent"], "Buffalo")
        self.assertEqual(rows["kansascity"][0]["venue"], "Home")
        self.assertEqual(rows["buffalo"][0]["opponent"], "Kansas City")
        self.assertEqual(rows["buffalo"][0]["event_id"], "401")

    def test_opponent_allowance_adjusts_projection_conservatively(self) -> None:
        def game(kansas_yards: int, buffalo_yards: int):
            return {
                "_props_edge_season_year": 2026,
                "boxscore": {
                    "players": [
                        {
                            "team": {"displayName": "Kansas City"},
                            "statistics": [{
                                "type": "passing",
                                "keys": ["passingYards"],
                                "athletes": [{
                                    "athlete": {
                                        "displayName": "Jordan Example",
                                        "position": {"abbreviation": "QB"},
                                    },
                                    "stats": [kansas_yards],
                                }],
                            }],
                        },
                        {
                            "team": {"displayName": "Buffalo"},
                            "statistics": [{
                                "type": "passing",
                                "keys": ["passingYards"],
                                "athletes": [{
                                    "athlete": {
                                        "displayName": "Casey Example",
                                        "position": {"abbreviation": "QB"},
                                    },
                                    "stats": [buffalo_yards],
                                }],
                            }],
                        },
                    ]
                },
            }

        schedule = {
            "kansascity": [{
                "event_id": "next",
                "matchup": "Kansas City @ Buffalo",
                "start_time": "2026-09-13T17:00:00Z",
                "opponent": "Buffalo",
                "venue": "Away",
            }]
        }
        rows = parse_summaries(
            [game(250 + index, 350 + index) for index in range(8)],
            "NFL",
            schedule,
            current_season_year=2026,
        )
        passing = next(row for row in rows if row.market == "Passing yards")
        self.assertEqual(passing.position, "QB")
        self.assertEqual(passing.opponent, "Buffalo")
        self.assertEqual(passing.opponent_defense_samples, 8)
        self.assertEqual(passing.opponent_defense_rank, 1)
        self.assertLess(passing.defense_adjustment, 0)
        self.assertLess(passing.projection, passing.base_projection)
        self.assertGreaterEqual(passing.defense_adjustment, -0.12)

    def test_current_roster_filters_stale_players_and_sets_position(self) -> None:
        def one_team_game(player: str):
            return {
                "_props_edge_season_year": 2026,
                "boxscore": {"players": [{
                    "team": {"displayName": "Kansas City"},
                    "statistics": [{
                        "type": "receiving",
                        "keys": ["receivingYards"],
                        "athletes": [{"athlete": {"displayName": player}, "stats": [70]}],
                    }],
                }]},
            }

        schedule = {"kansascity": [{
            "matchup": "Buffalo @ Kansas City",
            "start_time": "2026-09-13T17:00:00Z",
            "opponent": "Buffalo",
        }]}
        roster = {
            ("kansascity", "activeexample"): {"position": "WR", "injury_status": "Questionable"}
        }
        rows = parse_summaries(
            [one_team_game("Active Example"), one_team_game("Active Example"), one_team_game("Stale Example"), one_team_game("Stale Example")],
            "NFL",
            schedule,
            current_season_year=2026,
            current_roster=roster,
        )
        self.assertEqual({row.player for row in rows}, {"Active Example"})
        self.assertEqual({row.position for row in rows}, {"WR"})
        self.assertEqual({row.injury_status for row in rows}, {"Questionable"})

    def test_current_roster_remaps_traded_player_history_to_new_team(self) -> None:
        summary = {
            "_props_edge_season_year": 2025,
            "boxscore": {"players": [{
                "team": {"displayName": "Old Team"},
                "statistics": [{
                    "type": "receiving",
                    "keys": ["receivingYards", "receptions"],
                    "athletes": [{
                        "athlete": {"displayName": "Traded Example"},
                        "stats": [72, 5],
                    }],
                }],
            }]},
        }
        rows = parse_summaries(
            [summary, summary],
            "NFL",
            {"newteam": [{
                "matchup": "Away Team @ New Team",
                "start_time": "2026-09-13T17:00:00Z",
                "opponent": "Away Team",
            }]},
            current_season_year=2026,
            current_roster={
                ("newteam", "tradedexample"): {
                    "player": "Traded Example",
                    "team": "New Team",
                    "position": "WR",
                    "injury_status": "",
                }
            },
            verified_roster_teams={"newteam"},
        )
        receiving = next(row for row in rows if row.market == "Receiving yards")
        self.assertEqual(receiving.team, "New Team")
        self.assertEqual(receiving.position, "WR")
        self.assertEqual(receiving.samples, 2)

    def test_partial_roster_feed_keeps_unverified_team_visible(self) -> None:
        summary = {
            "_props_edge_season_year": 2026,
            "boxscore": {"players": [{
                "team": {"displayName": "Kansas City"},
                "statistics": [{
                    "type": "passing",
                    "keys": ["passingYards"],
                    "athletes": [{
                        "athlete": {"displayName": "Jordan Example"},
                        "stats": [275],
                    }],
                }],
            }]},
        }
        rows = parse_summaries(
            [summary, summary],
            "NFL",
            {"kansascity": [{
                "matchup": "Buffalo @ Kansas City",
                "start_time": "2026-09-13T17:00:00Z",
                "opponent": "Buffalo",
            }]},
            current_season_year=2026,
            current_roster={
                ("buffalo", "caseyexample"): {"position": "QB", "injury_status": ""}
            },
            verified_roster_teams={"buffalo"},
        )
        self.assertTrue(rows)
        self.assertTrue(all(row.roster_verified is False for row in rows))

    def test_regular_box_scores_build_nfl_markets(self) -> None:
        def summary(yards, touchdowns, targets, receptions, receiving_yards):
            return {
                "boxscore": {
                    "players": [
                        {
                            "team": {"displayName": "Kansas City"},
                            "statistics": [
                                {
                                    "type": "passing",
                                    "keys": ["completions/passingAttempts", "passingYards", "passingTouchdowns"],
                                    "athletes": [{"athlete": {"displayName": "Jordan Example"}, "stats": ["20/30", yards, touchdowns]}],
                                },
                                {
                                    "type": "receiving",
                                    "keys": ["receivingTouchdowns", "receivingYards", "receptions", "targets"],
                                    "athletes": [{"athlete": {"displayName": "Receiver Example"}, "stats": [1, receiving_yards, receptions, targets]}],
                                },
                            ],
                        }
                    ]
                }
            }

        schedule = {
            "kansascity": [
                {"matchup": "Buffalo @ Kansas City", "start_time": "2026-09-13T17:00:00Z"}
            ]
        }
        rows = parse_summaries(
            [
                summary(250, 2, 5, 4, 55),
                summary(270, 3, 8, 6, 80),
                summary(290, 2, 7, 5, 72),
                summary(280, 1, 9, 7, 88),
            ],
            "NFL",
            schedule,
        )
        passing = next(row for row in rows if row.player == "Jordan Example" and row.market == "Passing yards")
        targets = next(row for row in rows if row.player == "Receiver Example" and row.market == "Targets")
        touchdown = next(row for row in rows if row.player == "Receiver Example" and row.market == "Anytime touchdown")
        self.assertEqual(passing.samples, 4)
        self.assertGreater(passing.projection, 270)
        self.assertEqual(targets.recent, [5.0, 8.0, 7.0, 9.0])
        self.assertEqual(touchdown.recent, [1.0, 1.0, 1.0, 1.0])
        self.assertIn("regular-season", passing.source)

    def test_kicking_and_combined_props_are_projected(self) -> None:
        summary = {
            "boxscore": {
                "players": [{
                    "team": {"displayName": "Kansas City"},
                    "statistics": [
                        {
                            "type": "passing",
                            "keys": ["passingYards", "passingTouchdowns"],
                            "athletes": [{"athlete": {"displayName": "Quarterback Example"}, "stats": [250, 2]}],
                        },
                        {
                            "type": "rushing",
                            "keys": ["rushingYards", "rushingTouchdowns"],
                            "athletes": [{"athlete": {"displayName": "Quarterback Example"}, "stats": [35, 1]}],
                        },
                        {
                            "type": "kicking",
                            "keys": ["fieldGoalsMade/fieldGoalAttempts", "extraPointsMade/extraPointAttempts"],
                            "athletes": [{"athlete": {"displayName": "Kicker Example"}, "stats": ["2/3", "3/3"]}],
                        },
                    ],
                }]
            }
        }
        rows = parse_summaries([summary, summary], "NFL", {}, current_season_year=2026)
        by_market = {(row.player, row.market): row for row in rows}
        self.assertEqual(by_market[("Quarterback Example", "Pass + rush yards")].projection, 285.0)
        self.assertEqual(by_market[("Quarterback Example", "Pass + rush + receiving touchdowns")].projection, 3.0)
        self.assertEqual(by_market[("Kicker Example", "Field goals made")].projection, 2.0)
        self.assertEqual(by_market[("Kicker Example", "Kicking points")].projection, 9.0)


class SecurityTests(unittest.TestCase):
    def test_repository_has_no_embedded_secret_shaped_tokens(self) -> None:
        suspicious = re.compile(r"(?<![A-Za-z0-9])[a-f0-9]{32,}(?![A-Za-z0-9])", re.I)
        allowed = {".json", ".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".txt"}
        hits = []
        for path in ROOT.rglob("*"):
            if "site/data" in path.as_posix() or not path.is_file() or path.suffix not in allowed:
                continue
            if suspicious.search(path.read_text(errors="ignore")):
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], f"secret-looking token found in: {hits}")


if __name__ == "__main__":
    unittest.main()
