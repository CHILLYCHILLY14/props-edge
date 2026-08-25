from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .http import ProviderError
from .model import evaluate_quotes, evaluate_quotes_against_projections, merge_boards
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
    primary = OddsApiIoProvider(primary_key, settings) if primary_key else None
    secondary = TheOddsApiProvider(secondary_key, settings) if secondary_key else None
    espn = EspnProjectionProvider(settings)
    quotes = []
    projections = []
    source_by_sport: dict[str, dict[str, Any]] = {}
    def fetch_espn(sport_name: str) -> tuple[list[Any], str | None]:
        try:
            return espn.fetch(sport_name), None
        except ProviderError as exc:
            return [], str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        espn_futures = {
            sport_name: pool.submit(fetch_espn, sport_name) for sport_name in settings["sports"]
        }
        for sport in settings["sports"]:
            errors: list[str] = []
            sport_quotes = []
            source = None
            if primary:
                try:
                    sport_quotes = primary.fetch(sport)
                    if sport_quotes:
                        source = primary.name
                except ProviderError as exc:
                    errors.append(str(exc))
            if not sport_quotes and secondary:
                try:
                    sport_quotes = secondary.fetch(sport)
                    if sport_quotes:
                        source = secondary.name
                except ProviderError as exc:
                    errors.append(str(exc))
            sport_projections, espn_error = espn_futures[sport].result()
            if espn_error:
                errors.append(espn_error)
            if sport_quotes and sport_projections:
                source = f"{source} + {espn.name}"
            elif not sport_quotes and sport_projections:
                source = espn.name
            elif not sport_quotes:
                source = "No source available"
            quotes.extend(sport_quotes)
            projections.extend(sport_projections)
            source_by_sport[sport] = {
                "source": source,
                "priced_quotes": len(sport_quotes),
                "projections": len(sport_projections),
                "errors": errors,
            }

    consensus_board = evaluate_quotes(quotes, settings)
    projection_board = evaluate_quotes_against_projections(quotes, projections, settings)
    board = merge_boards(consensus_board, projection_board)
    projection_rows = [row.to_dict() for row in projections]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    meta = {
        "generated_at": now,
        "provider_priority": ["Odds-API.io", "The Odds API", "ESPN public statistics"],
        "target_book": settings["bookmakers"]["target"],
        "keyless_fallback": True,
        "configured": {
            "odds_api_io": bool(primary_key),
            "the_odds_api": bool(secondary_key),
            "espn_keyless": True
        },
        "counts": {
            "priced_quotes": len(quotes),
            "board": len(board),
            "actionable": sum(row["tier"] != "PASS" for row in board),
            "best": sum(row["tier"] == "BEST" for row in board),
            "good": sum(row["tier"] == "GOOD" for row in board),
            "leans": sum(row["tier"] == "LEAN" for row in board),
            "projections": len(projection_rows),
        },
        "source_by_sport": source_by_sport,
        "workflow_url": (
            f"https://github.com/{repository}/actions/workflows/refresh.yml" if repository else ""
        ),
        "notes": [
            "API credentials are optional and are read only from GitHub Actions secrets.",
            "If both odds providers are unavailable, ESPN schedules and box-score statistics produce projection-only rows.",
            "When DraftKings prices exist without enough consensus books, ESPN projections can price the line conservatively.",
            "Action Network and OddsShark are not scraped because automated extraction is blocked or prohibited.",
        ],
    }
    _write_json("board.json", board)
    _write_json("projections.json", projection_rows)
    _write_json("meta.json", meta)
    return meta


def main() -> None:
    argparse.ArgumentParser(description="Build the Props Edge data files").parse_args()
    meta = build()
    counts = meta["counts"]
    print(
        f"Props Edge refreshed: {counts['actionable']} priced plays, "
        f"{counts['projections']} ESPN projections"
    )


if __name__ == "__main__":
    main()
