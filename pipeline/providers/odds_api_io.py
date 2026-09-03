from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

from ..http import JsonClient, ProviderError
from ..schema import PropQuote, as_float, decimal_to_american


PRIMARY_URL = "https://api.odds-api.io/v3"
NFL_MARKETS = {
    "passing-yards": "Passing yards",
    "pass-yards": "Passing yards",
    "passing-touchdowns": "Passing touchdowns",
    "passing-tds": "Passing touchdowns",
    "pass-tds": "Passing touchdowns",
    "td-passes": "Passing touchdowns",
    "passing-attempts": "Pass attempts",
    "pass-attempts": "Pass attempts",
    "passing-completions": "Pass completions",
    "pass-completions": "Pass completions",
    "completions": "Pass completions",
    "interceptions-thrown": "Pass interceptions",
    "passing-interceptions": "Pass interceptions",
    "pass-interceptions": "Pass interceptions",
    "pass-rush-yards": "Pass + rush yards",
    "passing-rushing-yards": "Pass + rush yards",
    "rushing-yards": "Rushing yards",
    "rush-yards": "Rushing yards",
    "rushing-attempts": "Rush attempts",
    "rush-attempts": "Rush attempts",
    "carries": "Rush attempts",
    "rushing-touchdowns": "Rushing touchdowns",
    "rushing-tds": "Rushing touchdowns",
    "rush-tds": "Rushing touchdowns",
    "rush-receiving-yards": "Rush + receiving yards",
    "rushing-receiving-yards": "Rush + receiving yards",
    "pass-rush-receiving-yards": "Pass + rush + receiving yards",
    "passing-rushing-receiving-yards": "Pass + rush + receiving yards",
    "rush-receiving-touchdowns": "Rush + receiving touchdowns",
    "rushing-receiving-touchdowns": "Rush + receiving touchdowns",
    "pass-rush-receiving-touchdowns": "Pass + rush + receiving touchdowns",
    "passing-rushing-receiving-touchdowns": "Pass + rush + receiving touchdowns",
    "receiving-yards": "Receiving yards",
    "reception-yards": "Receiving yards",
    "rec-yards": "Receiving yards",
    "receptions": "Receptions",
    "receptions-made": "Receptions",
    "receiving-targets": "Targets",
    "targets": "Targets",
    "receiving-touchdowns": "Receiving touchdowns",
    "receiving-tds": "Receiving touchdowns",
    "reception-tds": "Receiving touchdowns",
    "anytime-touchdown": "Anytime touchdown",
    "anytime-touchdown-scorer": "Anytime touchdown",
    "anytime-td-scorer": "Anytime touchdown",
    "to-score-a-touchdown": "Anytime touchdown",
    "touchdowns": "Touchdowns scored",
    "touchdowns-scored": "Touchdowns scored",
    "longest-pass": "Longest pass",
    "longest-pass-completion": "Longest pass",
    "longest-rush": "Longest rush",
    "longest-reception": "Longest reception",
    "field-goals-made": "Field goals made",
    "field-goals": "Field goals made",
    "points-after-touchdown": "Extra points made",
    "extra-points": "Extra points made",
    "extra-points-made": "Extra points made",
    "kicking-points": "Kicking points",
    "tackles-assists": "Tackles + assists",
    "tackles-and-assists": "Tackles + assists",
    "solo-tackles": "Solo tackles",
    "sacks": "Sacks",
}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")


def _is_league(event: dict[str, Any], aliases: Iterable[str]) -> bool:
    league = event.get("league") or {}
    text = _norm(f"{league.get('name', '')} {league.get('slug', '')}")
    return any(_norm(alias) in text for alias in aliases)


def _is_prop_market(name: str, rows: list[dict[str, Any]]) -> bool:
    lowered = name.casefold()
    if any(row.get("label") for row in rows):
        return not any(
            token in lowered
            for token in ("team total", "correct score", "moneyline", "spread")
        )
    return any(
        token in lowered
        for token in (
            "player",
            "passing",
            "rushing",
            "receiving",
            "touchdown",
            "quarterback",
            "kicking",
            "field goal",
            "defense",
        )
    )


def _canonical_market(detail: str) -> str:
    key = re.sub(r"^(?:player-)?(?:total-)?", "", _norm(detail))
    key = re.sub(r"-(?:o-u|over-under)$", "", key)
    if key in NFL_MARKETS:
        return NFL_MARKETS[key]
    for alias in sorted(NFL_MARKETS, key=len, reverse=True):
        if key.endswith(alias):
            return NFL_MARKETS[alias]
    return detail.strip()


def _player_and_market(player_label: str, market_name: str) -> tuple[str, str]:
    if _norm(market_name) not in ("player-prop", "player-props"):
        return player_label.strip(), _canonical_market(market_name)
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", player_label.strip())
    if not match:
        return player_label.strip(), market_name
    return match.group(1).strip(), _canonical_market(match.group(2))


def parse_event(event: dict[str, Any], sport: str) -> list[PropQuote]:
    if sport != "NFL":
        return []
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
                    str(row.get("label") or ""), market_name
                )
                if not player:
                    continue
                for side in ("over", "under", "yes", "no"):
                    decimal_price = as_float(row.get(side))
                    if decimal_price is None or decimal_price <= 1:
                        continue
                    quotes.append(
                        PropQuote(
                            sport="NFL",
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
        if sport != "NFL":
            return []
        cfg = self.settings["sports"]["NFL"]
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
                "limit": self.settings["fetch"]["primary_event_limit"],
            },
        )
        selected = [event for event in events if _is_league(event, cfg["league_aliases"])]
        ids = [str(event["id"]) for event in selected if event.get("id")]
        active_books = list(dict.fromkeys(self.settings["bookmakers"]["primary_consensus"]))

        def fetch_book_batch(event_ids: list[str], books: list[str]) -> list[dict[str, Any]]:
            """Fetch every requested book, chunking instead of silently dropping extras."""
            try:
                response = self.client.get(
                    "/odds/multi",
                    {
                        "apiKey": self.api_key,
                        "eventIds": ",".join(event_ids),
                        "bookmakers": ",".join(books),
                    },
                )
                return response if isinstance(response, list) else [response]
            except ProviderError as exc:
                maximum = re.search(r"allowed max (\d+) bookmakers", str(exc), re.I)
                if maximum and len(books) > int(maximum.group(1)):
                    size = max(1, int(maximum.group(1)))
                    return [
                        event
                        for start in range(0, len(books), size)
                        for event in fetch_book_batch(event_ids, books[start : start + size])
                    ]
                invalid = re.search(r'["\']([^"\']+) is not a valid bookmaker', str(exc))
                if invalid:
                    bad_book = invalid.group(1).casefold()
                    remaining = [book for book in books if book.casefold() != bad_book]
                    if remaining and len(remaining) < len(books):
                        return fetch_book_batch(event_ids, remaining)
                raise

        detailed: list[dict[str, Any]] = []
        for start in range(0, len(ids), 10):
            detailed.extend(fetch_book_batch(ids[start : start + 10], active_books))
        quotes = [quote for event in detailed for quote in parse_event(event, "NFL")]

        # The batch endpoint can omit one requested book. Retry only the missing
        # regulated brands on the provider's documented single-event route.
        returned_books = {_norm(quote.book) for quote in quotes}
        missing_books = [book for book in active_books if _norm(book) not in returned_books]
        for missing_book in missing_books:
            missing_key = _norm(missing_book)
            for event_id in ids:
                try:
                    response = self.client.get(
                        "/odds",
                        {
                            "apiKey": self.api_key,
                            "eventId": event_id,
                            "bookmakers": missing_book,
                        },
                    )
                except ProviderError:
                    continue
                events = response if isinstance(response, list) else [response]
                quotes.extend(
                    quote
                    for event in events
                    for quote in parse_event(event, "NFL")
                    if _norm(quote.book) == missing_key
                )
        unique: dict[tuple[Any, ...], PropQuote] = {}
        for quote in quotes:
            unique[
                (
                    quote.event_id,
                    quote.player.casefold(),
                    quote.market,
                    quote.side,
                    quote.line,
                    _norm(quote.book),
                )
            ] = quote
        return list(unique.values())
