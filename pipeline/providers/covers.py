from __future__ import annotations

import datetime as dt
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any, Iterable

from ..http import ProviderError
from ..schema import Projection, PropQuote, american_to_decimal


SOURCE_URL = "https://www.covers.com/sport/football/nfl/player-props"
MAX_RESPONSE_BYTES = 4_000_000

MARKETS = {
    "PASSING YARDS": "Passing yards",
    "PASSING TOUCHDOWNS": "Passing touchdowns",
    "PASSING TDS": "Passing touchdowns",
    "PASSING ATTEMPTS": "Pass attempts",
    "PASS ATTEMPTS": "Pass attempts",
    "PASSING COMPLETIONS": "Pass completions",
    "COMPLETIONS": "Pass completions",
    "INTERCEPTIONS THROWN": "Pass interceptions",
    "PASSING INTERCEPTIONS": "Pass interceptions",
    "RUSHING YARDS": "Rushing yards",
    "RUSHING ATTEMPTS": "Rush attempts",
    "RUSH ATTEMPTS": "Rush attempts",
    "RECEIVING YARDS": "Receiving yards",
    "RECEPTIONS": "Receptions",
    "RECEIVING TARGETS": "Targets",
    "TARGETS": "Targets",
    "ANYTIME TOUCHDOWN": "Anytime touchdown",
    "ANYTIME TOUCHDOWN SCORER": "Anytime touchdown",
    "TOUCHDOWNS": "Touchdowns scored",
    "LONGEST PASS": "Longest pass",
    "LONGEST COMPLETION": "Longest pass",
    "LONGEST RUSH": "Longest rush",
    "LONGEST RECEPTION": "Longest reception",
    "FIELD GOALS MADE": "Field goals made",
    "KICKING POINTS": "Kicking points",
    "SOLO TACKLES": "Solo tackles",
    "TACKLES + ASSISTS": "Tackles + assists",
    "TACKLES AND ASSISTS": "Tackles + assists",
    "SACKS": "Sacks",
}

BOOKS = {
    "bet365": "bet365",
    "bet99": "BET99",
    "betmgm": "BetMGM",
    "betrivers": "BetRivers",
    "caesars": "Caesars",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "fanaticssportsbook": "Fanatics Sportsbook",
    "hardrockbet": "Hard Rock Bet",
    "pointsbet": "PointsBet",
    "sportsinteraction": "Sports Interaction",
    "thescorebet": "theScore Bet",
    "tonybet": "TonyBet",
    "tooniebet": "ToonieBet",
}

TEAM_ABBREVIATIONS = {
    "arizonacardinals": "ARI",
    "atlantafalcons": "ATL",
    "baltimoreravens": "BAL",
    "buffalobills": "BUF",
    "carolinapanthers": "CAR",
    "chicagobears": "CHI",
    "cincinnatibengals": "CIN",
    "clevelandbrowns": "CLE",
    "dallascowboys": "DAL",
    "denverbroncos": "DEN",
    "detroitlions": "DET",
    "greenbaypackers": "GB",
    "houstontexans": "HOU",
    "indianapoliscolts": "IND",
    "jacksonvillejaguars": "JAC",
    "kansascitychiefs": "KC",
    "lasvegasraiders": "LV",
    "losangeleschargers": "LAC",
    "losangelesrams": "LA",
    "miamidolphins": "MIA",
    "minnesotavikings": "MIN",
    "newenglandpatriots": "NE",
    "neworleanssaints": "NO",
    "newyorkgiants": "NYG",
    "newyorkjets": "NYJ",
    "philadelphiaeagles": "PHI",
    "pittsburghsteelers": "PIT",
    "sanfrancisco49ers": "SF",
    "seattleseahawks": "SEA",
    "tampabaybuccaneers": "TB",
    "tennesseetitans": "TEN",
    "washingtoncommanders": "WAS",
}


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _market_key(value: str) -> str:
    aliases = {
        "pass": "passing",
        "rush": "rushing",
        "rec": "receptions",
        "yd": "yards",
        "yds": "yards",
        "td": "touchdowns",
        "tds": "touchdowns",
    }
    ignored = {"player", "players", "prop", "props", "total", "alternate", "alternative"}
    tokens = [
        aliases.get(token, token)
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in ignored
    ]
    return (
        "".join(tokens)
        .replace("receptionyards", "receivingyards")
        .replace("anytimetouchdowns", "anytimetouchdown")
    )


class _TokenParser(HTMLParser):
    """Extract visible text and image labels without executing page scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def _append(self, value: str | None) -> None:
        value = _clean(value or "")
        if value:
            self.tokens.append(value)

    def handle_data(self, data: str) -> None:
        self._append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "img":
            return
        values = dict(attrs)
        self._append(values.get("alt"))


def _heading(token: str) -> str | None:
    value = _clean(token)
    if value != value.upper() or not any(ch.isalpha() for ch in value):
        return None
    return MARKETS.get(value)


def _book(token: str) -> str | None:
    value = re.sub(r"\b(?:image|logo|sportsbook)\b", "", token, flags=re.I)
    return BOOKS.get(_compact(value))


def _projection_matchup_key(projection: Projection) -> str:
    sides = re.split(r"\s+(?:@|at|vs\.?|versus)\s+", projection.matchup, maxsplit=1, flags=re.I)
    if len(sides) != 2:
        return ""
    away = TEAM_ABBREVIATIONS.get(_compact(sides[0]), "")
    home = TEAM_ABBREVIATIONS.get(_compact(sides[1]), "")
    return f"{away}@{home}" if away and home else ""


def _segment_matchups(tokens: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for token in tokens:
        for away, home in re.findall(r"\b([A-Z]{2,3})\s*(?:@|AT|VS\.?)\s*([A-Z]{2,3})\b", token.upper()):
            result.add(f"{away}@{home}")
            result.add(f"{home}@{away}")
    return result


def _player_aliases(name: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+", name.casefold())
    if not parts:
        return set()
    aliases = {_compact(name)}
    if len(parts) >= 2:
        aliases.add(parts[0][0] + "".join(parts[1:]))
        aliases.add(parts[0][0] + parts[-1])
    return {alias for alias in aliases if len(alias) >= 4}


def _matching_projection(
    tokens: list[str],
    market: str,
    projections: list[Projection],
) -> Projection | None:
    text = _compact(" ".join(tokens))
    matchups = _segment_matchups(tokens)
    candidates = []
    for row in projections:
        if row.sport != "NFL" or _market_key(row.market) != _market_key(market):
            continue
        if not any(alias in text for alias in _player_aliases(row.player)):
            continue
        candidates.append(row)
    if len(candidates) == 1:
        return candidates[0]
    exact = [
        row for row in candidates
        if (key := _projection_matchup_key(row)) and key in matchups
    ]
    return exact[0] if len(exact) == 1 else None


_ODDS_WITH_LINE = re.compile(
    r"(?<![a-z0-9])([ou])\s*(\d+(?:\.\d+)?)\s*(?:at\s*)?([+-]\d+|even)\b",
    re.I,
)
_PLAIN_PRICE = re.compile(r"(?<![\d.])([+-]\d{3,4}|even)\b", re.I)


def _parse_offer(tokens: list[str], anytime: bool) -> tuple[str, float | None, int] | None:
    text = _clean(" ".join(tokens[:8]))
    match = _ODDS_WITH_LINE.search(text)
    if match:
        price = 100 if match.group(3).casefold() == "even" else int(match.group(3))
        if price == 0:
            return None
        return ("over" if match.group(1).casefold() == "o" else "under", float(match.group(2)), price)
    if anytime:
        match = _PLAIN_PRICE.search(text)
        if match:
            price = 100 if match.group(1).casefold() == "even" else int(match.group(1))
            if price:
                return "yes", None, price
    return None


def parse_html(
    html: str,
    projections: list[Projection],
    observed_at: str | None = None,
) -> list[PropQuote]:
    parser = _TokenParser()
    parser.feed(html)
    tokens = parser.tokens
    observed_at = observed_at or dt.datetime.now(dt.timezone.utc).isoformat()

    headings = [(index, market) for index, token in enumerate(tokens) if (market := _heading(token))]
    unique: dict[tuple[Any, ...], PropQuote] = {}
    for position, (start, market) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(tokens)
        segment = tokens[start + 1 : end]
        projection = _matching_projection(segment, market, projections)
        if projection is None or not projection.event_id or not projection.start_time:
            continue
        anytime = _market_key(market) == "anytimetouchdown"
        for index, token in enumerate(segment):
            book = _book(token)
            if book is None:
                continue
            tail: list[str] = []
            for candidate in segment[index + 1 : index + 10]:
                if _book(candidate):
                    break
                tail.append(candidate)
            offer = _parse_offer(tail, anytime)
            if offer is None:
                continue
            side, line, price = offer
            quote = PropQuote(
                sport="NFL",
                event_id=projection.event_id,
                start_time=projection.start_time,
                matchup=projection.matchup,
                player=projection.player,
                market=projection.market,
                side=side,
                line=line,
                price_decimal=american_to_decimal(price),
                price_american=price,
                book=book,
                provider="Covers public prop comparison",
                updated_at=observed_at,
            )
            key = (
                quote.event_id,
                _compact(quote.player),
                _market_key(quote.market),
                quote.side,
                quote.line,
                _compact(quote.book),
            )
            unique[key] = quote
    return list(unique.values())


class CoversProvider:
    """Keyless reader for the public, server-rendered NFL prop comparison page."""

    name = "Covers public prop comparison"
    source_url = SOURCE_URL

    def __init__(self, settings: dict[str, Any], timeout: int = 25) -> None:
        self.settings = settings
        self.timeout = timeout

    def fetch(self, sport: str, projections: list[Projection]) -> list[PropQuote]:
        if sport != "NFL":
            return []
        request = urllib.request.Request(
            SOURCE_URL,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (compatible; KEVBOTBETS/1.0; public-market-reader)",
            },
        )
        last_error = "unknown provider error"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content_type = str(response.headers.get("Content-Type") or "").casefold()
                    if "html" not in content_type:
                        raise ProviderError("Covers returned a non-HTML response")
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise ProviderError("Covers response exceeded the safe size limit")
                    html = payload.decode("utf-8", errors="replace")
                    observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
                    quotes = parse_html(html, projections, observed_at=observed_at)
                    if not quotes:
                        raise ProviderError("Covers returned no matchable NFL prop prices")
                    return quotes
            except ProviderError:
                raise
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = type(exc).__name__
            if attempt < 2:
                time.sleep(1.5 * (2**attempt))
        raise ProviderError(f"{self.name} request failed ({last_error})")
