from __future__ import annotations

from typing import Any

from ..http import JsonClient
from ..schema import PropQuote, american_to_decimal, as_float


SECONDARY_URL = "https://api.the-odds-api.com/v4"
PROP_PREFIXES = ("player_", "batter_", "pitcher_")


def parse_event(event: dict[str, Any], sport: str) -> list[PropQuote]:
    quotes: list[PropQuote] = []
    matchup = f"{event.get('away_team', 'Away')} @ {event.get('home_team', 'Home')}"
    for bookmaker in event.get("bookmakers") or []:
        book = str(bookmaker.get("title") or bookmaker.get("key") or "Unknown")
        for market in bookmaker.get("markets") or []:
            market_key = str(market.get("key") or "")
            if not market_key.startswith(PROP_PREFIXES):
                continue
            for outcome in market.get("outcomes") or []:
                player = str(outcome.get("description") or "").strip()
                side = str(outcome.get("name") or "").casefold()
                price = as_float(outcome.get("price"))
                if not player or side not in ("over", "under", "yes", "no") or price in (None, 0):
                    continue
                quotes.append(
                    PropQuote(
                        sport=sport,
                        event_id=str(event.get("id") or ""),
                        start_time=str(event.get("commence_time") or ""),
                        matchup=matchup,
                        player=player,
                        market=market_key,
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
        sport_key = self.settings["sports"][sport]["secondary_sport"]
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
                    if key.startswith(PROP_PREFIXES) and key not in market_keys:
                        market_keys.append(key)
            if not market_keys:
                continue
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
            quotes.extend(parse_event(response, sport))
        return quotes

