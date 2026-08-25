from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from typing import Any

from .schema import Projection, PropQuote


OPPOSITE = {"over": "under", "under": "over", "yes": "no", "no": "yes"}


def _book_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _selection(quote: PropQuote) -> str:
    line = "" if quote.line is None else f" {quote.line:g}"
    return f"{quote.player} — {quote.side.title()}{line} {quote.market}"


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _market_key(value: str) -> str:
    aliases = {
        "pass": "passing",
        "rush": "rushing",
        "rec": "receptions",
        "yd": "yards",
        "yds": "yards",
        "td": "touchdowns",
        "tds": "touchdowns",
        "reb": "rebounds",
        "rebs": "rebounds",
        "ast": "assists",
        "asts": "assists",
    }
    ignored = {"player", "players", "prop", "props", "total", "alternate", "alternative"}
    tokens = [
        aliases.get(token, token)
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in ignored
    ]
    result = "".join(tokens)
    return result.replace("receptionyards", "receivingyards")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _recommended_stake(model_prob: float | None, decimal_price: float, cfg: dict[str, Any]) -> tuple[float, float]:
    if model_prob is None or decimal_price <= 1:
        return 0.0, 0.0
    full_kelly = max(0.0, (model_prob * decimal_price - 1) / (decimal_price - 1))
    fraction = float(cfg.get("kelly_fraction", 0.25))
    bankroll = float(cfg.get("bankroll", 500))
    max_stake = float(cfg.get("max_stake", 50))
    stake = min(max_stake, bankroll * full_kelly * fraction)
    return round(full_kelly, 5), round(stake, 2)


def _allowed_price(quote: PropQuote, cfg: dict[str, Any]) -> bool:
    maximum = float(cfg.get("max_price", 500))
    if quote.sport == "NFL" and _market_key(quote.market) == "anytimetouchdown":
        maximum = float(cfg.get("nfl_touchdown_max_price", maximum))
    return float(cfg["min_price"]) <= quote.price_american <= maximum


def _board_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("event_id"),
        _name_key(str(row.get("player") or "")),
        _market_key(str(row.get("market") or "")),
        row.get("side"),
        row.get("line"),
    )


def evaluate_quotes(quotes: list[PropQuote], settings: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = settings["projection_model"]
    target = _book_key(settings["bookmakers"]["target"])
    groups: dict[tuple[str, str, str, float | None], list[PropQuote]] = defaultdict(list)
    for quote in quotes:
        groups[(quote.event_id, quote.player.casefold(), quote.market.casefold(), quote.line)].append(quote)
    board: list[dict[str, Any]] = []
    for group in groups.values():
        by_book: dict[str, dict[str, PropQuote]] = defaultdict(dict)
        for quote in group:
            by_book[_book_key(quote.book)][quote.side] = quote
        for quote in [row for row in group if _book_key(row.book) == target]:
            fair_samples = []
            opposite = OPPOSITE.get(quote.side)
            for sides in by_book.values():
                if quote.side not in sides or opposite not in sides:
                    continue
                p_side = 1 / sides[quote.side].price_decimal
                p_other = 1 / sides[opposite].price_decimal
                fair_samples.append(p_side / (p_side + p_other))
            fair = statistics.median(fair_samples) if fair_samples else None
            breakeven = 1 / quote.price_decimal
            model_prob = None if fair is None else 0.5 + (fair - 0.5) * float(cfg["probability_shrink"])
            edge = None if model_prob is None else model_prob - breakeven
            ev = None if model_prob is None else model_prob * quote.price_decimal - 1
            full_kelly, recommended_stake = _recommended_stake(model_prob, quote.price_decimal, cfg)
            tier = "PASS"
            reason = ""
            if len(fair_samples) < int(cfg["min_consensus_books"]):
                reason = f"needs {cfg['min_consensus_books']} complete two-sided books"
            elif not _allowed_price(quote, cfg):
                reason = "price outside allowed range"
            elif edge is None or edge < float(cfg["lean_edge"]) or (ev or 0) <= 0:
                reason = "edge below Lean threshold"
            elif edge >= float(cfg["best_edge"]):
                tier = "BEST"
            elif edge >= float(cfg["good_edge"]):
                tier = "GOOD"
            else:
                tier = "LEAN"
            board.append(
                {
                    **quote.to_dict(),
                    "pick": _selection(quote),
                    "breakeven": round(breakeven, 5),
                    "market_fair_prob": None if fair is None else round(fair, 5),
                    "model_prob": None if model_prob is None else round(model_prob, 5),
                    "edge": None if edge is None else round(edge, 5),
                    "ev": None if ev is None else round(ev, 5),
                    "full_kelly": full_kelly,
                    "recommended_stake": recommended_stake,
                    "consensus_books": len(fair_samples),
                    "confidence": round(min(0.85, 0.42 + 0.09 * len(fair_samples)), 2),
                    "tier": tier,
                    "reason": reason,
                    "mode": "priced",
                }
            )
    return sorted(board, key=lambda row: ({"BEST": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}[row["tier"]], -(row.get("edge") or -1)))


def evaluate_quotes_against_projections(
    quotes: list[PropQuote], projections: list[Projection], settings: dict[str, Any]
) -> list[dict[str, Any]]:
    """Price DraftKings props with a conservative ESPN-stat projection when consensus is absent."""
    cfg = settings["projection_model"]
    target = _book_key(settings["bookmakers"]["target"])
    projection_index = {
        (row.sport, _name_key(row.player), _market_key(row.market)): row for row in projections
    }
    board: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for quote in quotes:
        if _book_key(quote.book) != target or quote.side not in ("over", "under", "yes", "no"):
            continue
        market_key = _market_key(quote.market)
        is_touchdown_yes_no = quote.side in ("yes", "no") and market_key == "anytimetouchdown"
        if quote.side in ("over", "under") and quote.line is None:
            continue
        if quote.side in ("yes", "no") and not is_touchdown_yes_no:
            continue
        row_key = (
            quote.event_id,
            _name_key(quote.player),
            _market_key(quote.market),
            quote.side,
            quote.line,
        )
        if row_key in seen:
            continue
        seen.add(row_key)
        projection = projection_index.get((quote.sport, row_key[1], row_key[2]))
        if projection is None:
            continue
        recent = projection.recent or [projection.projection]
        confidence = max(0.2, min(0.72, float(projection.confidence)))
        if is_touchdown_yes_no:
            touchdown_hits = sum(value >= 1 for value in recent)
            empirical_yes = (touchdown_hits + 0.5) / (len(recent) + 2)
            poisson_yes = 1 - math.exp(-max(0.0, float(projection.projection)))
            raw_yes = (empirical_yes + poisson_yes) / 2
            conservative_yes = max(0.04, min(0.85, 0.2 + (raw_yes - 0.2) * confidence))
            model_prob = conservative_yes if quote.side == "yes" else 1 - conservative_yes
        else:
            deviation = max(float(projection.standard_deviation), 0.75)
            normal_over = 1 - _normal_cdf((quote.line - projection.projection) / deviation)
            over_wins = sum(value > quote.line for value in recent)
            empirical_over = (over_wins + 1) / (len(recent) + 2)
            raw_over = (normal_over + empirical_over) / 2
            raw_probability = raw_over if quote.side == "over" else 1 - raw_over
            model_prob = 0.5 + (raw_probability - 0.5) * confidence
        breakeven = 1 / quote.price_decimal
        edge = model_prob - breakeven
        ev = model_prob * quote.price_decimal - 1
        full_kelly, recommended_stake = _recommended_stake(model_prob, quote.price_decimal, cfg)
        tier = "PASS"
        reason = ""
        if not _allowed_price(quote, cfg):
            reason = "price outside allowed range"
        elif edge < float(cfg["lean_edge"]) or ev <= 0:
            reason = "ESPN projection edge below Lean threshold"
        elif edge >= float(cfg["best_edge"]):
            tier = "BEST"
        elif edge >= float(cfg["good_edge"]):
            tier = "GOOD"
        else:
            tier = "LEAN"
        board.append(
            {
                **quote.to_dict(),
                "pick": _selection(quote),
                "breakeven": round(breakeven, 5),
                "market_fair_prob": None,
                "model_prob": round(model_prob, 5),
                "edge": round(edge, 5),
                "ev": round(ev, 5),
                "full_kelly": full_kelly,
                "recommended_stake": recommended_stake,
                "consensus_books": 0,
                "confidence": round(confidence, 2),
                "tier": tier,
                "reason": reason,
                "mode": "projection-priced",
                "model_label": f"DraftKings price vs {projection.source}",
                "projection": projection.projection,
                "projection_samples": projection.samples,
            }
        )
    return sorted(
        board,
        key=lambda row: (
            {"BEST": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}[row["tier"]],
            -(row.get("edge") or -1),
        ),
    )


def merge_boards(
    consensus: list[dict[str, Any]], projected: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Prefer consensus plays, replacing only consensus PASS rows with actionable projections."""
    combined = {_board_key(row): row for row in consensus}
    for row in projected:
        key = _board_key(row)
        existing = combined.get(key)
        if existing is None or (existing["tier"] == "PASS" and row["tier"] != "PASS"):
            combined[key] = row
    return sorted(
        combined.values(),
        key=lambda row: (
            {"BEST": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}[row["tier"]],
            -(row.get("edge") or -1),
        ),
    )
