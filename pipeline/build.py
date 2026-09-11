from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .http import ProviderError
from .model import (
    eligible_book_key,
    eligible_book_name,
    evaluate_quotes,
    evaluate_quotes_against_projections,
    merge_boards,
    select_portfolio,
)
from . import parlays
from .providers.espn import EspnProjectionProvider, _nfl_season_year
from .providers.odds_api_io import OddsApiIoProvider
from .providers.the_odds_api import TheOddsApiProvider


ROOT = Path(__file__).resolve().parents[1]


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
    primary_key = os.getenv("ODDS_API_IO_KEY", "").strip()
    secondary_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    is_eligible = lambda quote: eligible_book_key(quote.book, settings) is not None
    errors: list[str] = []
    quotes = []
    primary_quotes = []
    secondary_quotes = []
    odds_source = "No live props source available"
    if primary_key:
        try:
            primary_quotes = OddsApiIoProvider(primary_key, settings).fetch("NFL")
        except ProviderError as exc:
            errors.append(str(exc))
    # Prefer the jurisdiction-specific Canadian feed. The brand-level source is
    # a continuity fallback when that feed has no eligible Ontario price rows.
    if secondary_key:
        try:
            secondary_quotes = TheOddsApiProvider(secondary_key, settings).fetch("NFL")
        except ProviderError as exc:
            errors.append(str(exc))
    eligible_primary = [quote for quote in primary_quotes if is_eligible(quote)]
    eligible_secondary = [quote for quote in secondary_quotes if is_eligible(quote)]
    if eligible_secondary:
        quotes = eligible_secondary
        odds_source = "The Odds API (Ontario keys)"
    elif eligible_primary:
        quotes = eligible_primary
        odds_source = "Odds-API.io (regulated-brand fallback)"

    projections = _fetch_projections(settings, errors)

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
    parlay_feed = parlays.build(parlay_rows, projection_rows, settings, generated_at=now)
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
        "provider_priority": ["The Odds API Ontario keys", "Odds-API.io regulated-brand fallback", "ESPN regular-season statistics and current rosters"],
        "pricing_mode": "best-ontario-regulated",
        "price_scope": "Best available Ontario-regulated book",
        "eligible_books": eligible_books,
        "ontario_registry": settings["bookmakers"]["ontario_registry"],
        "ontario_verified_as_of": settings["bookmakers"]["ontario_verified_as_of"],
        "configured": {
            "odds_api_io": bool(primary_key),
            "the_odds_api": bool(secondary_key),
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
            "odds_api_io": {
                "priced_quotes": len(primary_quotes),
                "eligible_priced_quotes": len(eligible_primary),
            },
            "the_odds_api": {
                "priced_quotes": len(secondary_quotes),
                "eligible_priced_quotes": len(eligible_secondary),
            },
        },
        "lookahead_days": lookahead_days,
        "next_scheduled_game": scheduled_starts[0] if scheduled_starts else "",
        "model_status": (
            "Live Ontario-regulated NFL prices, regular-season player samples, and opponent matchup data are available."
            if quotes and projections
            else (
                f"The next {lookahead_days} days of regular-season schedule and form are ready, "
                "but no eligible Ontario-regulated player-prop prices have been returned yet."
                if projections and scheduled_starts
                else (
                    f"Regular-season form is available, but no game is scheduled inside the next {lookahead_days} days."
                    if projections
                    else "NFL data is still too thin. The model will not force a wager."
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
            "Each exact prop publishes the best returned price from the configured Ontario-regulated book allowlist.",
            "The Ontario-key feed is preferred; the regulated-brand feed is used only as a continuity fallback and every price must be verified in the Ontario sportsbook before wagering.",
            "A wager enters My Ledger only after the user reviews the live price and clicks Add.",
            "API credentials remain GitHub Actions secrets and are never written to site data.",
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
