import unittest

from pipeline import parlays


SETTINGS = {
    "daily_parlays": {
        "timezone": "America/Toronto",
        "minimum_samples": 6,
        "minimum_confidence": 0.52,
        "minimum_model_probability": 0.22,
        "minimum_value_ratio": 0.90,
        "maximum_projection_gap": 0.25,
        "maximum_legs_per_game": 4,
        "minimum_market_groups": 2,
        "maximum_legs_per_market_group": 2,
        "minimum_parlay_value_ratio": 0.75,
        "same_game_pair_haircut": 0.90,
    }
}


def build(rows, projections, settings):
    return parlays.build(rows, projections, settings, generated_at="2026-09-11T01:00:00Z")


def row(player, price, probability, event="1", book="FanDuel",
        market="Anytime TD", side="over", line=0.5):
    decimal = 1 + price / 100 if price > 0 else 1 + 100 / -price
    return {
        "event_id": event, "start_time": "2026-09-13T17:00:00Z",
        "updated_at": "2026-09-11T00:00:00Z", "push_prob": 0.0,
        "matchup": f"A{event} @ H{event}", "player": player,
        "market": market, "side": side, "line": line,
        "pick": f"{player} — {side.title()} {line} {market}", "book": book,
        "price_american": price, "price_decimal": decimal,
        "breakeven": 1 / decimal, "model_prob_no_push": probability,
        "confidence": 0.65, "projection_samples": 8,
        "current_season_samples": 1, "raw_projection_market_gap": 0.08,
        "roster_verified": True, "reason": "", "matchup_quality": "Neutral",
    }


class DailyParlayTests(unittest.TestCase):
    def projections(self):
        return [{"event_id": str(i), "start_time": "2026-09-13T17:00:00Z",
                 "matchup": f"A{i} @ H{i}"} for i in range(1, 5)]

    def test_builds_same_book_three_or_four_leg_cards(self):
        rows = [
            row("One", 150, .42, "1", market="Anytime TD"),
            row("Two", 175, .38, "2", market="Receiving yards"),
            row("Three", 200, .34, "3", market="Passing touchdowns"),
            row("Four", 225, .31, "4", market="Rushing yards"),
        ]
        feed = build(rows, self.projections(), SETTINGS)
        self.assertEqual(len(feed["dates"][0]["cards"]), 4)
        ready = [card for card in feed["dates"][0]["cards"] if card["status"] == "ready"]
        self.assertTrue(ready)
        self.assertTrue(all(3 <= card["leg_count"] <= 4 for card in ready))
        self.assertTrue(all(card["book"] == "FanDuel" for card in ready))
        self.assertTrue(all(len({leg["market_group"] for leg in card["legs"]}) >= 2
                            for card in ready if card["key"] != "touchdown_ticket"))

    def test_dedicated_touchdown_ticket_uses_only_anytime_touchdowns(self):
        rows = [row(str(index), 300, .27, str(index), market="Anytime TD")
                for index in range(1, 5)]
        feed = build(rows, self.projections(), SETTINGS)
        ticket = feed["dates"][0]["cards"][0]
        self.assertEqual(ticket["key"], "touchdown_ticket")
        self.assertEqual(ticket["status"], "ready")
        self.assertTrue(all(leg["market_group"] == "Touchdowns"
                            for leg in ticket["legs"]))

    def test_touchdown_ticket_rejects_multi_touchdown_alternate(self):
        rows = [row(str(index), 300, .27, str(index), market="Anytime TD")
                for index in range(1, 5)]
        rows[0]["line"] = 1.5
        feed = build(rows, self.projections(), SETTINGS)
        ticket = feed["dates"][0]["cards"][0]
        self.assertTrue(all(leg["player"] != "1" for leg in ticket.get("legs", [])))

    def test_moonshot_can_be_reached_without_adding_a_fifth_leg(self):
        markets = ["Anytime TD", "Receiving yards", "Passing touchdowns", "Rushing yards"]
        rows = [row(str(index), 220, .30, str(index), market=markets[index - 1])
                for index in range(1, 5)]
        feed = build(rows, self.projections(), SETTINGS)
        moonshot = next(card for card in feed["dates"][0]["cards"]
                        if card["key"] == "moonshot")
        self.assertEqual(moonshot["status"], "ready")
        self.assertEqual(moonshot["leg_count"], 4)

    def test_never_mixes_books_to_reach_a_target(self):
        rows = [
            row("One", 200, .34, "1", "FanDuel", "Anytime TD"),
            row("Two", 200, .34, "2", "FanDuel", "Receiving yards"),
            row("Three", 200, .34, "3", "DraftKings", "Passing touchdowns"),
            row("Four", 200, .34, "4", "DraftKings", "Rushing yards"),
        ]
        feed = build(rows, self.projections(), SETTINGS)
        self.assertTrue(all(card["status"] == "waiting" for card in feed["dates"][0]["cards"]))

    def test_hard_safety_reason_blocks_a_leg(self):
        markets = ["Anytime TD", "Receiving yards", "Passing touchdowns", "Rushing yards"]
        rows = [row(str(index), 150, .42, str(index), market=markets[index - 1])
                for index in range(1, 5)]
        rows[0]["reason"] = "Odds are stale; waiting for a live price refresh"
        feed = build(rows, self.projections(), SETTINGS)
        ready = [card for card in feed["dates"][0]["cards"] if card["status"] == "ready"]
        self.assertTrue(all(all(leg["player"] != "1" for leg in card["legs"]) for card in ready))

    def test_missing_stale_future_or_started_leg_cannot_enter_ticket(self):
        rows = [row(str(i), 300, .27, str(i)) for i in range(1, 4)]
        for field, value in [("updated_at", None),
                             ("updated_at", "2026-09-10T00:00:00Z"),
                             ("updated_at", "2026-09-12T00:00:00Z"),
                             ("start_time", "2026-09-11T00:30:00Z")]:
            with self.subTest(field=field, value=value):
                changed = [{**r} for r in rows]
                changed[0][field] = value
                ticket = build(changed, self.projections(), SETTINGS)["dates"][0]["cards"][0]
                self.assertEqual(ticket["status"], "waiting")

    def test_full_payout_probability_excludes_pushes(self):
        rows = [row(str(i), 200, .5, str(i)) for i in range(1, 4)]
        for r in rows:
            r["push_prob"] = .2
        ticket = build(rows, self.projections(), SETTINGS)["dates"][0]["cards"][0]
        self.assertEqual(ticket["status"], "ready")
        self.assertAlmostEqual(ticket["model_probability"], .4 ** 3, places=5)
        self.assertTrue(all(leg["updated_at"] for leg in ticket["legs"]))

    def test_invalid_probability_cannot_enter_ticket(self):
        for value in [-.1, 1.1, float("nan")]:
            rows = [row(str(i), 300, .27, str(i)) for i in range(1, 4)]
            rows[0]["model_prob_no_push"] = value
            self.assertEqual(build(rows, self.projections(), SETTINGS)["dates"][0]["cards"][0]["status"], "waiting")

    def test_game_day_is_emitted_even_without_prices(self):
        feed = build([], self.projections(), SETTINGS)
        self.assertEqual(feed["dates"][0]["date"], "2026-09-13")
        self.assertTrue(all(card["status"] == "waiting" for card in feed["dates"][0]["cards"]))


if __name__ == "__main__":
    unittest.main()
