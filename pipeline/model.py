from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from .schema import PropQuote


OPPOSITE = {"over": "under", "under": "over", "yes": "no", "no": "yes"}


def _book_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _selection(quote: PropQuote) -> str:
    line = "" if quote.line is None else f" {quote.line:g}"
    return f"{quote.player} — {quote.side.title()}{line} {quote.market}"


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
            tier = "PASS"
            reason = ""
            if len(fair_samples) < int(cfg["min_consensus_books"]):
                reason = f"needs {cfg['min_consensus_books']} complete two-sided books"
            elif not (float(cfg["min_price"]) <= quote.price_american <= float(cfg["max_price"])):
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
                    "consensus_books": len(fair_samples),
                    "confidence": round(min(0.85, 0.42 + 0.09 * len(fair_samples)), 2),
                    "tier": tier,
                    "reason": reason,
                    "mode": "priced",
                }
            )
    return sorted(board, key=lambda row: ({"BEST": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}[row["tier"]], -(row.get("edge") or -1)))

