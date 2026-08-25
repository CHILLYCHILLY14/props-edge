from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

from ..http import JsonClient, ProviderError
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


def _canonical_detail_market(sport: str, detail: str) -> str:
    key = _norm(detail)
    maps = {
        "MLB": {
            "total-bases": "Batter total bases",
            "hits-runs-rbis": "Batter hits + runs + RBIs",
            "hits": "Batter hits",
            "runs-batted-in": "Batter RBIs",
            "runs-scored": "Batter runs",
            "home-runs": "Batter home runs",
            "batter-walks": "Batter walks",
            "stolen-bases": "Batter stolen bases",
            "doubles": "Batter doubles",
            "triples": "Batter triples",
            "singles": "Batter singles",
            "pitcher-strikeouts": "Pitcher strikeouts",
            "pitcher-outs": "Pitcher outs",
            "earned-runs": "Pitcher earned runs",
            "pitcher-hits-allowed": "Pitcher hits allowed",
        },
        "WNBA": {
            "points": "Points",
            "rebounds": "Rebounds",
            "assists": "Assists",
            "pts-rebs": "Points + Rebounds",
            "pts-asts": "Points + Assists",
            "pts-rebs-asts": "Points + Rebounds + Assists",
            "rebs-asts": "Rebounds + Assists",
            "3-point-fg": "Threes",
        },
    }
    return maps.get(sport, {}).get(key, detail.strip())


def _player_and_market(player_label: str, market_name: str, sport: str) -> tuple[str, str]:
    if _norm(market_name) not in ("player-prop", "player-props"):
        return player_label.strip(), market_name
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", player_label.strip())
    if not match:
        return player_label.strip(), market_name
    return match.group(1).strip(), _canonical_detail_market(sport, match.group(2))


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
                player, parsed_market = _player_and_market(
                    str(row.get("label") or ""), market_name, sport
                )
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
                            market=parsed_market,
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
        active_books = list(dict.fromkeys(self.settings["bookmakers"]["primary_consensus"]))
        detailed: list[dict[str, Any]] = []
        for start in range(0, len(ids), 10):
            while True:
                try:
                    response = self.client.get(
                        "/odds/multi",
                        {
                            "apiKey": self.api_key,
                            "eventIds": ",".join(ids[start : start + 10]),
                            "bookmakers": ",".join(active_books),
                        },
                    )
                    break
                except ProviderError as exc:
                    maximum = re.search(r"allowed max (\d+) bookmakers", str(exc), re.I)
                    if maximum and len(active_books) > int(maximum.group(1)):
                        active_books = active_books[: int(maximum.group(1))]
                        continue
                    invalid = re.search(r'["\']([^"\']+) is not a valid bookmaker', str(exc))
                    if not invalid:
                        raise
                    bad_book = invalid.group(1).casefold()
                    remaining = [book for book in active_books if book.casefold() != bad_book]
                    if not remaining or len(remaining) == len(active_books):
                        raise
                    active_books = remaining
            detailed.extend(response if isinstance(response, list) else [response])
        return [quote for event in detailed for quote in parse_event(event, sport)]
