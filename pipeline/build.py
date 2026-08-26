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
    errors: list[str] = []
    quotes = []
    odds_source = "No live props source available"
    if primary_key:
        try:
            quotes = OddsApiIoProvider(primary_key, settings).fetch("NFL")
            if quotes:
                odds_source = "Odds-API.io"
        except ProviderError as exc:
            errors.append(str(exc))
    if not quotes and secondary_key:
        try:
            quotes = TheOddsApiProvider(secondary_key, settings).fetch("NFL")
            if quotes:
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
    projection_rows = [row.to_dict() for row in projections[:maximum_rows]]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    actionable = [row for row in board if row["tier"] != "PASS"]
    suggested_exposure = round(
        sum(float(row.get("recommended_stake") or 0) for row in actionable),
        2,
    )
    meta = {
        "league": "NFL",
        "generated_at": now,
        "provider_priority": ["Odds-API.io", "The Odds API", "ESPN regular-season statistics"],
        "target_book": settings["bookmakers"]["target"],
        "configured": {
            "odds_api_io": bool(primary_key),
            "the_odds_api": bool(secondary_key),
            "espn_keyless": True,
        },
        "counts": {
            "priced_quotes": len(quotes),
            "board": len(board),
            "actionable": len(actionable),
            "best": sum(row["tier"] == "BEST" for row in board),
            "good": sum(row["tier"] == "GOOD" for row in board),
            "leans": sum(row["tier"] == "LEAN" for row in board),
            "watch": sum(row["tier"] == "PASS" for row in board),
            "projections": len(projection_rows),
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
                "projections": len(projection_rows),
                "errors": errors,
            }
        },
        "workflow_url": (
            f"https://github.com/{repository}/actions/workflows/refresh.yml"
            if repository
            else ""
        ),
        "model_status": (
            "Live NFL prices and regular-season player samples are available."
            if quotes and projections
            else "NFL data is still thin. The model will not force a wager."
        ),
        "ledger_mode": "manual-browser",
        "notes": [
            "Only NFL player props are collected and published.",
            "Preseason box scores are excluded from every projection and betting decision.",
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
