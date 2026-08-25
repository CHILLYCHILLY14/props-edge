from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

from ..http import JsonClient
from ..schema import PropQuote, as_float, decimal_to_american


PRIMARY_URL = "https://api.odds-api.io/v3"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")


def _is_league(event: dict[str, Any], aliases: Iterable[str]) -> bool:
    league = event.get("league") or {}
    text = _norm(f"{league.get('name', '')} {league.get('slug', '')}")
    return any(_norm(alias) in text for alias in aliases)


def _is_prop_market(name: str, rows: list[dict[str, Any]]) -> bool:
    lowered = name.casefold()
    if any(row.get("label") for row in rows):
        return not any(token in lowered for token in ("team total", "correct score", "moneyline", "spread"))
    return any(token in lowered for token in ("player", "passing", "rushing", "receiving", "batter", "pitcher"))


def parse_event(event: dict[str, Any], sport: str) -> list[PropQuote]:
    quotes: list[PropQuote] = []
    matchup = f"{event.get('away', 'Away')} @ {event.get('home', 'Home')}"
    for book, markets in (event.get("bookmakers") or {}).items():
        for market in markets or []:
            market_name = str(market.get("name") or "Unknown prop")
            rows = market.get("odds") or []
            if not _is_prop_market(market_name, rows):
                continue
            for row in rows:
                player = str(row.get("label") or "").strip()
                if not player:
                    continue
                for side in ("over", "under", "yes", "no"):
                    decimal_price = as_float(row.get(side))
                    if decimal_price is None or decimal_price <= 1:
                        continue
                    quotes.append(
                        PropQuote(
                            sport=sport,
                            event_id=str(event.get("id") or ""),
                            start_time=str(event.get("date") or ""),
                            matchup=matchup,
                            player=player,
                            market=market_name,
                            side=side,
                            line=as_float(row.get("hdp")),
                            price_decimal=decimal_price,
                            price_american=decimal_to_american(decimal_price),
                            book=str(book),
                            provider="Odds-API.io",
                            updated_at=market.get("updatedAt"),
                        )
                    )
    return quotes


class OddsApiIoProvider:
    name = "Odds-API.io"

    def __init__(self, api_key: str, settings: dict[str, Any]) -> None:
        self.api_key = api_key
        self.settings = settings
        self.client = JsonClient(self.name, PRIMARY_URL)

    def fetch(self, sport: str) -> list[PropQuote]:
        cfg = self.settings["sports"][sport]
        now = dt.datetime.now(dt.timezone.utc)
        lookahead = int(self.settings["fetch"]["lookahead_days"])
        events = self.client.get(
            "/events",
            {
                "apiKey": self.api_key,
                "sport": cfg["primary_sport"],
                "status": "pending",
                "from": now.isoformat().replace("+00:00", "Z"),
                "to": (now + dt.timedelta(days=lookahead)).isoformat().replace("+00:00", "Z"),
                "bookmaker": self.settings["bookmakers"]["target"],
                "limit": self.settings["fetch"]["primary_event_limit"],
            },
        )
        selected = [event for event in events if _is_league(event, cfg["league_aliases"])]
        ids = [str(event["id"]) for event in selected if event.get("id")]
        books = ",".join(self.settings["bookmakers"]["primary_consensus"])
        detailed: list[dict[str, Any]] = []
        for start in range(0, len(ids), 10):
            response = self.client.get(
                "/odds/multi",
                {
                    "apiKey": self.api_key,
                    "eventIds": ",".join(ids[start : start + 10]),
                    "bookmakers": books,
                },
            )
            detailed.extend(response if isinstance(response, list) else [response])
        return [quote for event in detailed for quote in parse_event(event, sport)]

