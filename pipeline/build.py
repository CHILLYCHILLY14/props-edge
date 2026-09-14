from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .http import ProviderError
from .model import (
    eligible_book_key,
    eligible_book_name,
    evaluate_quotes,
    evaluate_quotes_against_projections,
    merge_boards,
    quote_block_reason,
    select_portfolio,
)
from . import parlays
from .providers.covers import CoversProvider, SOURCE_URL as COVERS_SOURCE_URL
from .providers.espn import EspnProjectionProvider, _nfl_season_year
from .providers.odds_api_io import OddsApiIoProvider  # retained for disabled-provider regression tests
from .providers.the_odds_api import TheOddsApiProvider  # retained for disabled-provider regression tests
from .schema import PropQuote, decimal_to_american


ROOT = Path(__file__).resolve().parents[1]
QUOTE_CACHE = ROOT / "state" / "last_good_quotes.json"


def _quote_key(quote: PropQuote) -> tuple[Any, ...]:
    return (
        quote.event_id,
        "".join(ch for ch in quote.player.casefold() if ch.isalnum()),
        "".join(ch for ch in quote.market.casefold() if ch.isalnum()),
        quote.side.casefold(),
        None if quote.line is None else round(float(quote.line), 4),
        "".join(ch for ch in quote.book.casefold() if ch.isalnum()),
    )


def _load_quote_cache(
    settings: dict[str, Any],
    path: Path = QUOTE_CACHE,
    now: dt.datetime | None = None,
) -> list[PropQuote]:
    """Load only still-fresh, internally consistent real offers.

    Cache reuse never changes updated_at. The normal quote gate therefore
    removes a retained offer at the same age limit as a directly fetched one.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("quotes", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    result: dict[tuple[Any, ...], PropQuote] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            quote = PropQuote(
                sport=str(row["sport"]),
                event_id=str(row["event_id"]),
                start_time=str(row["start_time"]),
                matchup=str(row["matchup"]),
                player=str(row["player"]),
                market=str(row["market"]),
                side=str(row["side"]),
                line=None if row.get("line") is None else float(row["line"]),
                price_decimal=float(row["price_decimal"]),
                price_american=int(row["price_american"]),
                book=str(row["book"]),
                provider=str(row["provider"]),
                updated_at=None if row.get("updated_at") is None else str(row["updated_at"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if (
            quote.sport != "NFL"
            or not all((quote.event_id, quote.start_time, quote.matchup, quote.player,
                        quote.market, quote.side, quote.book, quote.provider))
            or eligible_book_key(quote.book, settings) is None
            or quote.price_decimal <= 1
            or abs(decimal_to_american(quote.price_decimal) - quote.price_american) > 1
            or quote_block_reason(quote, settings["projection_model"], now) is not None
        ):
            continue
        result[_quote_key(quote)] = quote
    return list(result.values())


def _merge_quote_cache(
    cached: list[PropQuote], current: list[PropQuote]
) -> list[PropQuote]:
    """Keep non-overlapping fresh cache rows; a current observation always wins."""
    merged = {_quote_key(quote): quote for quote in cached}
    for quote in current:
        merged[_quote_key(quote)] = quote
    return list(merged.values())


def _write_quote_cache(
    quotes: list[PropQuote], path: Path = QUOTE_CACHE
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "note": (
            "updated_at is the original observation time. Cache reuse must never "
            "refresh it or extend the configured freshness window."
        ),
        "quotes": [quote.to_dict() for quote in quotes],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def load_settings() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "settings.json").read_text())


def _write_json(name: str, value: Any) -> None:
    destination = ROOT / "site" / "data" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n")


def _roster_coverage(projections: list[Any]) -> tuple[int, int]:
    """Return verified/scheduled team counts from projection rows.

    A transient ESPN roster failure should not silently remove one team's ability
    to qualify for the whole refresh. We still fail closed for that team, but a
    single clean retry is cheap compared with publishing an avoidable 31/32 slate.
    """
    scheduled = {
        str(row.team)
        for row in projections
        if getattr(row, "start_time", "") and getattr(row, "team", "")
    }
    verified = {
        str(row.team)
        for row in projections
        if getattr(row, "start_time", "")
        and getattr(row, "team", "")
        and bool(getattr(row, "roster_verified", False))
    }
    return len(verified), len(scheduled)


def _fetch_projections(settings: dict[str, Any], errors: list[str]) -> list[Any]:
    try:
        projections = EspnProjectionProvider(settings).fetch("NFL")
    except ProviderError as exc:
        errors.append(str(exc))
        return []
    except Exception as exc:
        errors.append(f"ESPN regular-season statistics failed: {exc}")
        return []

    verified, scheduled = _roster_coverage(projections)
    if projections and scheduled and verified < scheduled:
        # Retry the entire projection pull once. The provider fetches rosters
        # before the heavier summary sweep, so this mainly repairs a transient
        # one-team roster timeout without weakening roster verification.
        try:
            retry = EspnProjectionProvider(settings).fetch("NFL")
            retry_verified, retry_scheduled = _roster_coverage(retry)
            if retry_verified > verified or (
                retry_verified == verified and retry_scheduled > scheduled
            ):
                projections = retry
                verified, scheduled = retry_verified, retry_scheduled
        except Exception as exc:
            errors.append(f"ESPN roster verification retry failed: {exc}")

    if scheduled and verified < scheduled:
        errors.append(
            f"ESPN roster verification incomplete: {verified}/{scheduled} scheduled teams verified; "
            "unverified players remain WATCH-only."
        )
    return projections


def build() -> dict[str, Any]:
    settings = load_settings()
    # Keyless mode never reads or spends an odds API credential. Covers is a
    # public comparison page; every quote is still restricted to a brand on the
    # current Ontario registry allowlist and must be verified before placement.
    is_eligible = lambda quote: eligible_book_key(quote.book, settings) is not None
    errors: list[str] = []
    projections = _fetch_projections(settings, errors)
    public_quotes: list[Any] = []
    if projections:
        try:
            public_quotes = CoversProvider(settings).fetch("NFL", projections)
        except ProviderError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"Covers public prop comparison failed: {exc}")
    current_quotes = [quote for quote in public_quotes if is_eligible(quote)]
    cached_quotes = _load_quote_cache(settings)
    quotes = _merge_quote_cache(cached_quotes, current_quotes)
    current_keys = {_quote_key(quote) for quote in current_quotes}
    cached_only_quotes = sum(_quote_key(quote) not in current_keys for quote in quotes)
    if current_quotes:
        # Preserve fresh cache rows omitted by a partial page response, while a
        # current observation always replaces the same cached offer.
        _write_quote_cache(quotes)
    odds_source = (
        "Covers public prop comparison"
        if current_quotes and not cached_only_quotes
        else (
            f"Covers public prop comparison + {cached_only_quotes} retained fresh offer(s)"
            if current_quotes
            else (
                "Last verified Covers offers (original timestamps retained)"
                if quotes
                else "No verified keyless props source available"
            )
        )
    )

    market_watch = evaluate_quotes(quotes, settings)
    evaluated = evaluate_quotes_against_projections(quotes, projections, settings)
    board = select_portfolio(merge_boards(market_watch, evaluated), settings)
    maximum_rows = int(settings["projection_model"]["maximum_rows"])
    board = board[:maximum_rows]
    maximum_projection_rows = int(
        settings["projection_model"].get("maximum_projection_rows", maximum_rows)
    )
    projection_rows = [row.to_dict() for row in projections[:maximum_projection_rows]]
    eligible_books = sorted(
        {
            eligible_book_name(quote.book, settings) or quote.book
            for quote in quotes
        }
    )
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    # Re-evaluate per book so a parlay never multiplies best prices that came
    # from different sportsbooks. The main board still publishes the best
    # available straight-bet price; this separate pool preserves same-book legs.
    parlay_rows = []
    for book_key in sorted({eligible_book_key(quote.book, settings) for quote in quotes} - {None}):
        book_quotes = [quote for quote in quotes
                       if eligible_book_key(quote.book, settings) == book_key]
        book_watch = evaluate_quotes(book_quotes, settings)
        book_evaluated = evaluate_quotes_against_projections(
            book_quotes, projections, settings
        )
        parlay_rows.extend(merge_boards(book_watch, book_evaluated))
    # The displayed player table is capped, but the game-day calendar must span
    # the entire fetched schedule, including dates beyond that table's limit.
    parlay_schedule = [
        {"event_id": row.event_id, "start_time": row.start_time, "matchup": row.matchup}
        for row in projections
    ]
    parlay_feed = parlays.build(parlay_rows, parlay_schedule, settings, generated_at=now)
    ready_parlays = sum(card.get("status") == "ready"
                        for day in parlay_feed["dates"] for card in day["cards"])
    actionable = [row for row in board if row["tier"] != "PASS" and not row.get("held")]
    lookahead_days = int(settings["fetch"]["lookahead_days"])
    scheduled_starts = sorted(
        {str(row.get("start_time") or "") for row in projection_rows if row.get("start_time")}
    )
    scheduled_roster_teams = {
        str(row.get("team") or "")
        for row in projection_rows
        if row.get("start_time") and row.get("team")
    }
    verified_roster_teams = {
        str(row.get("team") or "")
        for row in projection_rows
        if row.get("start_time") and row.get("team") and row.get("roster_verified")
    }
    suggested_exposure = round(
        sum(float(row.get("recommended_stake") or 0) for row in actionable),
        2,
    )
    meta = {
        "league": "NFL",
        "season": _nfl_season_year(dt.datetime.now(dt.timezone.utc).date()),
        "generated_at": now,
        "provider_priority": [
            "Covers public NFL prop comparison (no key)",
            "ESPN regular-season statistics and current rosters (no key)",
        ],
        "odds_mode": "keyless",
        "price_source_status": (
            "available" if current_quotes else "cached" if quotes else "unavailable"
        ),
        "pricing_mode": "keyless-public-market-lines",
        "price_scope": (
            "Publicly observed sportsbook-brand prop prices; verify the Ontario line before wagering"
            if current_quotes
            else (
                "Last verified public offers retained with their original observation times; "
                "they expire at the normal freshness limit and must be rechecked before wagering"
                if quotes
                else "Verified keyless player-prop prices are not currently available"
            )
        ),
        "eligible_books": eligible_books,
        "ontario_registry": settings["bookmakers"]["ontario_registry"],
        "ontario_verified_as_of": settings["bookmakers"]["ontario_verified_as_of"],
        "configured": {
            "covers_keyless": True,
            "odds_api_io": False,
            "the_odds_api": False,
            "espn_keyless": True,
        },
        "counts": {
            "priced_quotes": len(quotes),
            "eligible_priced_quotes": len(quotes),
            "priced_events": len({quote.event_id for quote in quotes}),
            "eligible_priced_events": len({quote.event_id for quote in quotes}),
            "eligible_books": len(eligible_books),
            "priced_markets": len({quote.market for quote in quotes}),
            "board": len(board),
            "actionable": len(actionable),
            "qualified_options": sum(row["tier"] != "PASS" for row in board),
            "best": sum(row["tier"] == "BEST" for row in board),
            "good": sum(row["tier"] == "GOOD" for row in board),
            "leans": sum(row["tier"] == "LEAN" for row in board),
            "watch": sum(row["tier"] == "PASS" for row in board),
            "projections": len(projection_rows),
            "projected_markets": len({row["market"] for row in projection_rows}),
            "scheduled_projections": sum(bool(row.get("start_time")) for row in projection_rows),
            "matchup_adjusted": sum(int(row.get("opponent_defense_samples") or 0) >= 2 for row in projection_rows),
            "simulator_players": len({row["player"] for row in projection_rows}),
            "roster_verified": sum(bool(row.get("roster_verified")) for row in projection_rows),
            "roster_verified_teams": len(verified_roster_teams),
            "scheduled_roster_teams": len(scheduled_roster_teams),
            "suggested_exposure": suggested_exposure,
            "parlay_game_days": len(parlay_feed["dates"]),
            "parlays_ready": ready_parlays,
        },
        "source_by_sport": {
            "NFL": {
                "source": (
                    f"{odds_source} + ESPN regular-season form"
                    if quotes and projections
                    else odds_source if quotes else "ESPN regular-season form" if projections else "No source available"
                ),
                "priced_quotes": len(quotes),
                "eligible_priced_quotes": len(quotes),
                "projections": len(projection_rows),
                "errors": errors,
            }
        },
        "source_by_provider": {
            "covers": {
                "source_url": COVERS_SOURCE_URL,
                "timestamp_basis": "page_observed_at",
                "priced_quotes": len(public_quotes),
                "eligible_priced_quotes": len(current_quotes),
                "retained_fresh_quotes": cached_only_quotes,
                "published_quotes": len(quotes),
            },
            "odds_api_io": {
                "priced_quotes": 0,
                "eligible_priced_quotes": 0,
                "disabled": True,
            },
            "the_odds_api": {
                "priced_quotes": 0,
                "eligible_priced_quotes": 0,
                "disabled": True,
            },
        },
        "lookahead_days": lookahead_days,
        "next_scheduled_game": scheduled_starts[0] if scheduled_starts else "",
        "model_status": (
            "Observed NFL prop prices from Ontario-regulated sportsbook brands, regular-season player samples, and opponent matchup data are available. Verify the current Ontario price before wagering."
            if current_quotes and projections
            else (
                "The live comparison request returned no matchable prices, so the board retained "
                "the last verified offers without changing their observation times. They remain "
                "eligible only until the normal freshness limit and must be rechecked before wagering."
                if quotes and projections
                else (
                    f"The next {lookahead_days} days of regular-season schedule and form are ready, "
                    "but the public comparison source returned no matchable player-prop prices. Key-based requests are disabled."
                    if projections and scheduled_starts
                    else (
                        f"Regular-season form is available, but no game is scheduled inside the next {lookahead_days} days."
                        if projections
                        else "NFL data is still too thin. The model will not force a wager."
                    )
                )
            )
        ),
        "ledger_mode": "manual-browser",
        "max_odds_age_hours": settings["projection_model"].get("max_odds_age_hours", 12),
        "market_coverage": [
            "Passing yards, touchdowns, attempts, completions, interceptions and longest completion",
            "Rushing yards, attempts, touchdowns and longest rush",
            "Receptions, receiving yards, targets, touchdowns and longest reception",
            "Combined passing/rushing/receiving yards and touchdowns",
            "Anytime touchdowns and total touchdowns scored",
            "Field goals made, extra points and kicking points",
            "Sacks, solo tackles and tackles + assists",
        ],
        "notes": [
            "Only NFL player props are collected and published.",
            "Preseason box scores are excluded from every projection and betting decision.",
            "Current ESPN rosters remove players who are no longer on the upcoming team and supply position and injury context.",
            "A transient incomplete roster pull is retried once; any team still unverified remains WATCH-only rather than being allowed to qualify.",
            "Prior-season form is automatically reduced until four current-season games are available.",
            "Opponent adjustments compare position-level production allowed with the league median, then shrink and cap the result at 12%.",
            "The 10,000-run matchup simulator refreshes from the same ESPN form and defense data as the betting model.",
            "Daily Parlays mix 3–4 fresh same-book prop legs, retain the sample and confidence safeguards, require market variety, and apply an extra same-game correlation haircut.",
            "Touchdowns, field goals, interceptions and sacks use count-stat probability handling and stricter reliability gates.",
            "Sportsbook consensus is never treated as an independent model by itself.",
            "Each exact prop publishes the best observed price from the configured Ontario-regulated brand allowlist.",
            "Covers supplies public comparison-page market lines. The observation time is recorded; it is not represented as a sportsbook-originated update time.",
            "An exact one-sided offer can qualify only as a LEAN after an extra edge reserve; no opposite price or no-vig probability is invented.",
            "Key-based odds requests are disabled. If the public page is temporarily unavailable, last verified offers remain only with their original timestamps and expire at the normal freshness limit.",
            "A wager enters My Ledger only after the user reviews the live price and clicks Add.",
            "No odds API credentials are read or sent by the scheduled build.",
        ],
    }
    from . import accuracy
    accuracy.update(ROOT, board, projections, errors)
    _write_json("board.json", board)
    _write_json("projections.json", projection_rows)
    _write_json("parlays.json", parlay_feed)
    _write_json("meta.json", meta)
    return meta


def main() -> None:
    argparse.ArgumentParser(description="Build the NFL Props Edge data files").parse_args()
    meta = build()
    counts = meta["counts"]
    print(
        f"NFL Props Edge refreshed: {counts['actionable']} qualified plays, "
        f"{counts['projections']} regular-season projections, "
        f"{counts['priced_quotes']} live price rows"
    )
    if counts.get("scheduled_roster_teams"):
        print(
            f"Roster verification: {counts['roster_verified_teams']}/"
            f"{counts['scheduled_roster_teams']} scheduled teams"
        )
    print("My Ledger is manual browser storage; 0 automatic wager entries")


if __name__ == "__main__":
    main()
