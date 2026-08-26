from __future__ import annotations

from typing import Any

from ..http import JsonClient
from ..schema import PropQuote, american_to_decimal, as_float


SECONDARY_URL = "https://api.the-odds-api.com/v4"
NFL_MARKETS = {
    "player_pass_yds": "Passing yards",
    "player_pass_tds": "Passing touchdowns",
    "player_pass_attempts": "Pass attempts",
    "player_pass_completions": "Pass completions",
    "player_pass_interceptions": "Pass interceptions",
    "player_rush_yds": "Rushing yards",
    "player_rush_attempts": "Rush attempts",
    "player_rush_tds": "Rushing touchdowns",
    "player_pass_rush_yds": "Pass + rush yards",
    "player_receptions": "Receptions",
    "player_reception_yds": "Receiving yards",
    "player_reception_tds": "Receiving touchdowns",
    "player_rush_reception_yds": "Rush + receiving yards",
    "player_pass_rush_reception_yds": "Pass + rush + receiving yards",
    "player_rush_reception_tds": "Rush + receiving touchdowns",
    "player_pass_rush_reception_tds": "Pass + rush + receiving touchdowns",
    "player_targets": "Targets",
    "player_tds_over": "Touchdowns scored",
    "player_anytime_td": "Anytime touchdown",
    "player_pass_longest_completion": "Longest pass",
    "player_longest_pass_completion": "Longest pass",
    "player_rush_longest": "Longest rush",
    "player_longest_rush": "Longest rush",
    "player_reception_longest": "Longest reception",
    "player_longest_reception": "Longest reception",
    "player_field_goals": "Field goals made",
    "player_pats": "Extra points made",
    "player_kicking_points": "Kicking points",
    "player_solo_tackles": "Solo tackles",
    "player_tackles_assists": "Tackles + assists",
    "player_sacks": "Sacks",
}
NFL_MARKET_PRIORITY = {market: index for index, market in enumerate(NFL_MARKETS)}


def parse_event(event: dict[str, Any], sport: str) -> list[PropQuote]:
    if sport != "NFL":
        return []
    quotes: list[PropQuote] = []
    matchup = f"{event.get('away_team', 'Away')} @ {event.get('home_team', 'Home')}"
    for bookmaker in event.get("bookmakers") or []:
        book = str(bookmaker.get("title") or bookmaker.get("key") or "Unknown")
        for market in bookmaker.get("markets") or []:
            market_key = str(market.get("key") or "")
            canonical = NFL_MARKETS.get(market_key)
            if canonical is None:
                continue
            for outcome in market.get("outcomes") or []:
                player = str(outcome.get("description") or "").strip()
                side = str(outcome.get("name") or "").casefold()
                price = as_float(outcome.get("price"))
                if (
                    not player
                    or side not in ("over", "under", "yes", "no")
                    or price in (None, 0)
                ):
                    continue
                quotes.append(
                    PropQuote(
                        sport="NFL",
                        event_id=str(event.get("id") or ""),
                        start_time=str(event.get("commence_time") or ""),
                        matchup=matchup,
                        player=player,
                        market=canonical,
                        side=side,
                        line=as_float(outcome.get("point")),
                        price_decimal=american_to_decimal(price),
                        price_american=round(price),
                        book=book,
                        provider="The Odds API",
                        updated_at=market.get("last_update") or bookmaker.get("last_update"),
                    )
                )
    return quotes


class TheOddsApiProvider:
    name = "The Odds API"

    def __init__(self, api_key: str, settings: dict[str, Any]) -> None:
        self.api_key = api_key
        self.settings = settings
        self.client = JsonClient(self.name, SECONDARY_URL)

    def fetch(self, sport: str) -> list[PropQuote]:
        if sport != "NFL":
            return []
        sport_key = self.settings["sports"]["NFL"]["secondary_sport"]
        books = ",".join(self.settings["bookmakers"]["secondary_consensus"])
        events = self.client.get(f"/sports/{sport_key}/events", {"apiKey": self.api_key})
        quotes: list[PropQuote] = []
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            catalogue = self.client.get(
                f"/sports/{sport_key}/events/{event_id}/markets",
                {"apiKey": self.api_key, "bookmakers": books},
            )
            market_keys: list[str] = []
            for bookmaker in catalogue.get("bookmakers") or []:
                for market in bookmaker.get("markets") or []:
                    key = str(market.get("key") or "")
                    if key in NFL_MARKETS and key not in market_keys:
                        market_keys.append(key)
            if not market_keys:
                continue
            market_keys.sort(key=lambda key: NFL_MARKET_PRIORITY[key])
            limit = int(self.settings["fetch"]["secondary_max_markets_per_event"])
            response = self.client.get(
                f"/sports/{sport_key}/events/{event_id}/odds",
                {
                    "apiKey": self.api_key,
                    "bookmakers": books,
                    "markets": ",".join(market_keys[:limit]),
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                },
            )
            quotes.extend(parse_event(response, "NFL"))
        return quotes
