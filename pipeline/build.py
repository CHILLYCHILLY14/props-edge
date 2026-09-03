from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .http import ProviderError
from .model import (
    evaluate_quotes,
    evaluate_quotes_against_projections,
    merge_boards,
    select_portfolio,
)
from .providers.espn import EspnProjectionProvider
from .providers.odds_api_io import OddsApiIoProvider
from .providers.the_odds_api import TheOddsApiProvider


ROOT = Path(__file__).resolve().parents[1]


def load_settings() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "settings.json").read_text())


def _write_json(name: str, value: Any) -> None:
    destination = ROOT / "site" / "data" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n")


def build() -> dict[str, Any]:
    settings = load_settings()
    primary_key = os.getenv("ODDS_API_IO_KEY", "").strip()
    secondary_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    target_book_key = "".join(
        character
        for character in str(settings["bookmakers"]["target"]).casefold()
        if character.isalnum()
    )
    is_target = lambda quote: "".join(
        character for character in quote.book.casefold() if character.isalnum()
    ) == target_book_key
    errors: list[str] = []
    quotes = []
    primary_quotes = []
    secondary_quotes = []
    odds_source = "No live props source available"
    if primary_key:
        try:
            primary_quotes = OddsApiIoProvider(primary_key, settings).fetch("NFL")
            quotes = primary_quotes
            if primary_quotes:
                odds_source = "Odds-API.io"
        except ProviderError as exc:
            errors.append(str(exc))
    # A non-target consensus pull cannot populate the board. If the primary
    # source has no DraftKings rows, give the configured backup source a chance
    # instead of treating any unrelated price as a successful primary fetch.
    if secondary_key and not any(is_target(quote) for quote in quotes):
        try:
            secondary_quotes = TheOddsApiProvider(secondary_key, settings).fetch("NFL")
            if any(is_target(quote) for quote in secondary_quotes):
                quotes = secondary_quotes
                odds_source = "The Odds API"
            elif not quotes and secondary_quotes:
                quotes = secondary_quotes
                odds_source = "The Odds API"
        except ProviderError as exc:
            errors.append(str(exc))

    projections = []
    try:
        projections = EspnProjectionProvider(settings).fetch("NFL")
    except ProviderError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"ESPN regular-season statistics failed: {exc}")

    market_watch = evaluate_quotes(quotes, settings)
    evaluated = evaluate_quotes_against_projections(quotes, projections, settings)
    board = select_portfolio(merge_boards(market_watch, evaluated), settings)
    maximum_rows = int(settings["projection_model"]["maximum_rows"])
    board = board[:maximum_rows]
    maximum_projection_rows = int(
        settings["projection_model"].get("maximum_projection_rows", maximum_rows)
    )
    projection_rows = [row.to_dict() for row in projections[:maximum_projection_rows]]
    target_quotes = [
        quote
        for quote in quotes
        if is_target(quote)
    ]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    actionable = [row for row in board if row["tier"] != "PASS"]
    lookahead_days = int(settings["fetch"]["lookahead_days"])
    scheduled_starts = sorted(
        {str(row.get("start_time") or "") for row in projection_rows if row.get("start_time")}
    )
    suggested_exposure = round(
        sum(float(row.get("recommended_stake") or 0) for row in actionable),
        2,
    )
    meta = {
        "league": "NFL",
        "generated_at": now,
        "provider_priority": ["Odds-API.io", "The Odds API", "ESPN regular-season statistics and current rosters"],
        "target_book": settings["bookmakers"]["target"],
        "configured": {
            "odds_api_io": bool(primary_key),
            "the_odds_api": bool(secondary_key),
            "espn_keyless": True,
        },
        "counts": {
            "priced_quotes": len(quotes),
            "target_priced_quotes": len(target_quotes),
            "priced_events": len({quote.event_id for quote in quotes}),
            "target_priced_events": len({quote.event_id for quote in target_quotes}),
            "priced_markets": len({quote.market for quote in quotes}),
            "board": len(board),
            "actionable": len(actionable),
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
            "roster_verified_teams": len({row["team"] for row in projection_rows if row.get("roster_verified")}),
            "suggested_exposure": suggested_exposure,
        },
        "source_by_sport": {
            "NFL": {
                "source": (
                    f"{odds_source} + ESPN regular-season form"
                    if quotes and projections
                    else odds_source if quotes else "ESPN regular-season form" if projections else "No source available"
                ),
                "priced_quotes": len(quotes),
                "target_priced_quotes": len(target_quotes),
                "projections": len(projection_rows),
                "errors": errors,
            }
        },
        "source_by_provider": {
            "odds_api_io": {
                "priced_quotes": len(primary_quotes),
                "target_priced_quotes": sum(is_target(quote) for quote in primary_quotes),
            },
            "the_odds_api": {
                "priced_quotes": len(secondary_quotes),
                "target_priced_quotes": sum(is_target(quote) for quote in secondary_quotes),
            },
        },
        "lookahead_days": lookahead_days,
        "next_scheduled_game": scheduled_starts[0] if scheduled_starts else "",
        "model_status": (
            "Live target-book NFL prices, regular-season player samples, and opponent matchup data are available."
            if target_quotes and projections
            else (
                f"The next {lookahead_days} days of regular-season schedule and form are ready, "
                f"but {settings['bookmakers']['target']} player-prop prices have not been returned yet."
                if projections and scheduled_starts
                else (
                    f"Regular-season form is available, but no game is scheduled inside the next {lookahead_days} days."
                    if projections
                    else "NFL data is still too thin. The model will not force a wager."
                )
            )
        ),
        "ledger_mode": "manual-browser",
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
            "Prior-season form is automatically reduced until four current-season games are available.",
            "Opponent adjustments compare position-level production allowed with the league median, then shrink and cap the result at 12%.",
            "The 10,000-run matchup simulator refreshes from the same ESPN form and defense data as the betting model.",
            "Touchdowns, field goals, interceptions and sacks use count-stat probability handling and stricter reliability gates.",
            "Sportsbook consensus is never treated as an independent model by itself.",
            "A wager enters My Ledger only after the user reviews the live price and clicks Add.",
            "API credentials remain GitHub Actions secrets and are never written to site data.",
        ],
    }
    _write_json("board.json", board)
    _write_json("projections.json", projection_rows)
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
    print("My Ledger is manual browser storage; 0 automatic wager entries")


if __name__ == "__main__":
    main()
