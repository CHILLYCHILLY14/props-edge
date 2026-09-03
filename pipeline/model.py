from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from typing import Any

from .schema import Projection, PropQuote


OPPOSITE = {"over": "under", "under": "over", "yes": "no", "no": "yes"}
TIER_ORDER = {"BEST": 0, "GOOD": 1, "LEAN": 2, "PASS": 3}


def _book_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _book_aliases(settings: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map provider-specific book labels to canonical Ontario book identities."""
    configured = settings.get("bookmakers", {}).get("ontario_regulated", {})
    aliases: dict[str, tuple[str, str]] = {}
    if isinstance(configured, dict):
        for canonical, values in configured.items():
            canonical_key = _book_key(str(canonical))
            for value in [canonical, *(values if isinstance(values, list) else [])]:
                aliases[_book_key(str(value))] = (canonical_key, str(canonical))
    return aliases


def eligible_book_key(book: str, settings: dict[str, Any]) -> str | None:
    matched = _book_aliases(settings).get(_book_key(book))
    return matched[0] if matched else None


def eligible_book_name(book: str, settings: dict[str, Any]) -> str | None:
    matched = _book_aliases(settings).get(_book_key(book))
    return matched[1] if matched else None


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
    }
    ignored = {"player", "players", "prop", "props", "total", "alternate", "alternative"}
    tokens = [
        aliases.get(token, token)
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in ignored
    ]
    return "".join(tokens).replace("receptionyards", "receivingyards")


def _selection(quote: PropQuote) -> str:
    line = "" if quote.line is None else f" {quote.line:g}"
    return f"{quote.player} — {quote.side.title()}{line} {quote.market}"


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _poisson_pmf(mean: float, value: int) -> float:
    if value < 0:
        return 0.0
    safe_mean = max(0.0001, float(mean))
    return math.exp(-safe_mean + value * math.log(safe_mean) - math.lgamma(value + 1))


def _poisson_cdf(mean: float, value: int) -> float:
    if value < 0:
        return 0.0
    return min(1.0, sum(_poisson_pmf(mean, index) for index in range(value + 1)))


def _poisson_over_under(mean: float, line: float) -> tuple[float, float, float]:
    """Return over, under and push probabilities for a count-stat line."""
    integer_line = abs(line - round(line)) < 1e-9
    floor_line = math.floor(line)
    if integer_line:
        point = int(round(line))
        over = 1 - _poisson_cdf(mean, point)
        under = _poisson_cdf(mean, point - 1)
        push = _poisson_pmf(mean, point)
        return over, under, push
    over = 1 - _poisson_cdf(mean, floor_line)
    return over, 1 - over, 0.0


def _compress(value: float, ceiling: float) -> float:
    if not value or ceiling <= 0:
        return value
    return math.copysign(ceiling * (1 - math.exp(-abs(value) / ceiling)), value)


def _power_devig(decimal_a: float, decimal_b: float) -> tuple[float, float] | None:
    """Return power-method fair probabilities for a complete two-way market."""
    if decimal_a <= 1 or decimal_b <= 1:
        return None
    implied_a, implied_b = 1 / decimal_a, 1 / decimal_b
    low, high = 0.01, 20.0
    for _ in range(80):
        exponent = (low + high) / 2
        total = implied_a**exponent + implied_b**exponent
        if total > 1:
            low = exponent
        else:
            high = exponent
    exponent = (low + high) / 2
    fair_a, fair_b = implied_a**exponent, implied_b**exponent
    total = fair_a + fair_b
    if total <= 0:
        return None
    return fair_a / total, fair_b / total


def _allowed_price(quote: PropQuote, cfg: dict[str, Any]) -> bool:
    maximum = float(cfg.get("max_price", 300))
    if _market_key(quote.market) == "anytimetouchdown":
        maximum = float(cfg.get("nfl_touchdown_max_price", maximum))
    return float(cfg.get("min_price", -175)) <= quote.price_american <= maximum


def _board_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("event_id"),
        _name_key(str(row.get("player") or "")),
        _market_key(str(row.get("market") or "")),
        row.get("side"),
        row.get("line"),
    )


def _groups(quotes: list[PropQuote]) -> list[list[PropQuote]]:
    grouped: dict[tuple[str, str, str, float | None], list[PropQuote]] = defaultdict(list)
    for quote in quotes:
        if quote.sport == "NFL":
            grouped[
                (quote.event_id, _name_key(quote.player), _market_key(quote.market), quote.line)
            ].append(quote)
    return list(grouped.values())


def _market_context(
    group: list[PropQuote], quote: PropQuote, settings: dict[str, Any]
) -> tuple[float | None, float | None, int, str]:
    by_book: dict[str, dict[str, PropQuote]] = defaultdict(dict)
    for row in group:
        book = eligible_book_key(row.book, settings)
        if book:
            current = by_book[book].get(row.side)
            if current is None or row.price_decimal > current.price_decimal:
                by_book[book][row.side] = row
    opposite = OPPOSITE.get(quote.side)
    if opposite is None:
        return None, None, 0, "incomplete market"
    offered_book = eligible_book_key(quote.book, settings)
    offered_fair: float | None = None
    external: list[float] = []
    for book, sides in by_book.items():
        if quote.side not in sides or opposite not in sides:
            continue
        pair = _power_devig(
            sides[quote.side].price_decimal,
            sides[opposite].price_decimal,
        )
        if pair is None:
            continue
        if book == offered_book:
            offered_fair = pair[0]
        else:
            external.append(pair[0])
    if external:
        return statistics.median(external), offered_fair, len(external), "other Ontario books' no-vig median"
    if offered_fair is not None:
        return offered_fair, offered_fair, 0, "offered-book no-vig"
    return None, None, 0, "offered-price break-even"


def _best_eligible_offers(
    group: list[PropQuote], settings: dict[str, Any]
) -> list[PropQuote]:
    """Return the best qualifying price per side from regulated books.

    A complete two-sided offer is preferred so an isolated stale/high quote
    cannot hide a slightly lower price whose own book can be de-vigged.
    """
    available_sides: dict[str, set[str]] = defaultdict(set)
    for quote in group:
        book = eligible_book_key(quote.book, settings)
        if book:
            available_sides[book].add(quote.side)
    best: dict[str, PropQuote] = {}
    best_complete: dict[str, PropQuote] = {}
    for quote in group:
        book = eligible_book_key(quote.book, settings)
        if book is None:
            continue
        current = best.get(quote.side)
        if current is None or quote.price_decimal > current.price_decimal:
            best[quote.side] = quote
        opposite = OPPOSITE.get(quote.side)
        if opposite and opposite in available_sides[book]:
            current_complete = best_complete.get(quote.side)
            if current_complete is None or quote.price_decimal > current_complete.price_decimal:
                best_complete[quote.side] = quote
    return [best_complete.get(side, quote) for side, quote in best.items()]


def _market_watch_row(
    quote: PropQuote,
    market_fair: float | None,
    offered_fair: float | None,
    external_books: int,
    market_basis: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    return {
        **quote.to_dict(),
        "book": eligible_book_name(quote.book, settings) or quote.book,
        "pick": _selection(quote),
        "breakeven": round(1 / quote.price_decimal, 5),
        "market_fair_prob": None if market_fair is None else round(market_fair, 5),
        "offered_fair_prob": None if offered_fair is None else round(offered_fair, 5),
        "target_fair_prob": None if offered_fair is None else round(offered_fair, 5),
        "market_basis": market_basis,
        "model_prob": None,
        "model_prob_no_push": None,
        "projection_prob": None,
        "push_prob": 0.0,
        "edge": None,
        "edge_raw": None,
        "edge_real": None,
        "edge_price": None,
        "ev": None,
        "full_kelly": 0.0,
        "recommended_stake": 0.0,
        "consensus_books": external_books,
        "confidence": 0.0,
        "tier": "PASS",
        "reason": "No independent NFL player projection for this player and market yet",
        "mode": "market-watch",
        "model_label": "Live market watch — not a model bet",
    }


def evaluate_quotes(quotes: list[PropQuote], settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a non-actionable market watch board.

    Sportsbook consensus is useful context, but it is not an independent model.
    These rows cannot qualify until an NFL player projection is matched.
    """
    board: list[dict[str, Any]] = []
    for group in _groups(quotes):
        for quote in _best_eligible_offers(group, settings):
            fair, offered_fair, external_books, basis = _market_context(group, quote, settings)
            board.append(
                _market_watch_row(
                    quote, fair, offered_fair, external_books, basis, settings
                )
            )
    return sorted(board, key=lambda row: (row["start_time"], row["player"], row["market"]))


def _sd_floor(market: str, projection: float) -> float:
    key = _market_key(market)
    if "yards" in key or "longest" in key:
        return max(5.0, abs(projection) * 0.12)
    if any(token in key for token in ("attempts", "completions", "receptions", "targets")):
        return 1.25
    if any(token in key for token in ("touchdowns", "interceptions", "fieldgoals", "sacks")):
        return 0.65
    return 0.75


def _is_volatile_market(market: str) -> bool:
    key = _market_key(market)
    return any(
        token in key
        for token in ("touchdowns", "interceptions", "fieldgoals", "extrapoints", "sacks", "longest")
    )


def _market_reliability(market: str, cfg: dict[str, Any]) -> float:
    key = _market_key(market)
    if "longest" in key:
        return float(cfg.get("longest_market_weight", 0.70))
    if _is_volatile_market(market):
        return float(cfg.get("volatile_market_weight", 0.78))
    return 1.0


def _season_maturity(projection: Projection, cfg: dict[str, Any]) -> float:
    prior_weight = max(0.0, min(1.0, float(cfg.get("prior_season_weight", 1.0))))
    current_samples = max(0, int(getattr(projection, "current_season_samples", 0)))
    full_weight_games = max(1, int(cfg.get("current_season_full_weight_games", 4)))
    return prior_weight + (1 - prior_weight) * min(1.0, current_samples / full_weight_games)


def _projection_probabilities(
    quote: PropQuote, projection: Projection
) -> tuple[float, float, float] | None:
    """Return unconditional (win, push, loss) from the NFL form projection."""
    recent = [float(value) for value in (projection.recent or [projection.projection])]
    samples = max(1, len(recent))
    confidence = max(0.0, min(0.75, float(projection.confidence)))
    market_key = _market_key(quote.market)
    if quote.side in ("yes", "no"):
        if market_key != "anytimetouchdown":
            return None
        hits = sum(value >= 1 for value in recent)
        empirical_yes = (hits + 0.75) / (samples + 1.5)
        poisson_yes = 1 - math.exp(-max(0.0, float(projection.projection)))
        raw_yes = 0.45 * empirical_yes + 0.55 * poisson_yes
        reliability = min(0.8, confidence * (0.6 + 0.4 * min(1.0, samples / 8)))
        yes_probability = max(0.04, min(0.75, 0.18 + (raw_yes - 0.18) * reliability))
        win = yes_probability if quote.side == "yes" else 1 - yes_probability
        return win, 0.0, 1 - win

    if quote.side not in ("over", "under") or quote.line is None:
        return None
    line = float(quote.line)
    mean = float(projection.projection)
    deviation = max(
        float(projection.standard_deviation),
        _sd_floor(quote.market, mean),
    )
    integer_line = abs(line - round(line)) < 1e-9
    low_count = any(
        token in market_key
        for token in ("touchdowns", "interceptions", "fieldgoals", "extrapoints", "sacks")
    )
    if low_count:
        distribution_over, distribution_under, distribution_push = _poisson_over_under(mean, line)
    elif integer_line:
        distribution_over = 1 - _normal_cdf((line + 0.5 - mean) / deviation)
        distribution_under = _normal_cdf((line - 0.5 - mean) / deviation)
        distribution_push = max(0.0, 1 - distribution_over - distribution_under)
    else:
        distribution_over = 1 - _normal_cdf((line - mean) / deviation)
        distribution_under = 1 - distribution_over
        distribution_push = 0.0
    if integer_line:
        over_count = sum(value > line for value in recent)
        under_count = sum(value < line for value in recent)
        push_count = samples - over_count - under_count
        empirical_over = (over_count + 0.5) / (samples + 1.5)
        empirical_under = (under_count + 0.5) / (samples + 1.5)
        empirical_push = (push_count + 0.5) / (samples + 1.5)
    else:
        over_count = sum(value > line for value in recent)
        empirical_over = (over_count + 1) / (samples + 2)
        empirical_under = 1 - empirical_over
        empirical_push = 0.0
    distribution_weight = 0.58 if low_count else 0.62
    over = distribution_weight * distribution_over + (1 - distribution_weight) * empirical_over
    under = distribution_weight * distribution_under + (1 - distribution_weight) * empirical_under
    push = distribution_weight * distribution_push + (1 - distribution_weight) * empirical_push
    total = over + under + push
    if total <= 0:
        return None
    over, under, push = over / total, under / total, push / total
    if quote.side == "over":
        return over, push, under
    return under, push, over


def _projection_index(projections: list[Projection]) -> dict[tuple[str, str], list[Projection]]:
    result: dict[tuple[str, str], list[Projection]] = defaultdict(list)
    for projection in projections:
        if projection.sport == "NFL":
            result[
                (_name_key(projection.player), _market_key(projection.market))
            ].append(projection)
    return result


def _matching_projection(
    quote: PropQuote,
    index: dict[tuple[str, str], list[Projection]],
) -> Projection | None:
    candidates = index.get((_name_key(quote.player), _market_key(quote.market)), [])
    if not candidates:
        return None
    quote_date = str(quote.start_time)[:10]
    exact = [row for row in candidates if str(row.start_time)[:10] == quote_date]
    return (exact or candidates)[0]


def _tier_and_reason(
    quote: PropQuote,
    projection: Projection,
    offered_fair: float | None,
    external_books: int,
    raw_projection_gap: float,
    edge: float,
    price_ev: float,
    cfg: dict[str, Any],
) -> tuple[str, str]:
    samples = int(projection.samples)
    confidence = float(projection.confidence)
    if not bool(getattr(projection, "roster_verified", True)):
        return "PASS", "Current roster verification is unavailable for this team"
    injury_status = str(getattr(projection, "injury_status", "") or "").casefold()
    if any(
        blocked in injury_status
        for blocked in ("out", "injured reserve", "doubtful", "suspended", "physically unable")
    ):
        return "PASS", f"Player roster status is {projection.injury_status}"
    if quote.side in ("over", "under") and quote.line is None:
        return "PASS", "A real prop line is required"
    if quote.side in ("over", "under") and offered_fair is None:
        return "PASS", "Complete two-sided offered-book prices are required"
    required_samples = int(cfg["minimum_samples"])
    if _is_volatile_market(quote.market):
        required_samples = max(required_samples, int(cfg.get("volatile_minimum_samples", required_samples)))
    if samples < required_samples:
        return "PASS", f"Only {samples} regular-season samples; {required_samples} required for this market"
    if confidence < float(cfg["minimum_confidence"]):
        return "PASS", "Projection confidence is below the Lean gate"
    if not _allowed_price(quote, cfg):
        return "PASS", "Price outside the allowable range"
    if raw_projection_gap > float(cfg["max_raw_market_gap"]):
        return "PASS", "Raw projection/market disagreement exceeds the safety limit"
    if edge < float(cfg["lean_edge"]) or price_ev < float(cfg["lean_price_ev"]):
        return "PASS", "Does not clear both model-edge and offered-price value gates"

    tier = "LEAN"
    if (
        edge >= float(cfg["best_edge"])
        and price_ev >= float(cfg["best_price_ev"])
        and samples >= int(cfg["best_minimum_samples"])
        and confidence >= float(cfg["best_minimum_confidence"])
        and external_books >= int(cfg["min_external_books"])
        and float(cfg["best_min_price"]) <= quote.price_american <= float(cfg["best_max_price"])
    ):
        tier = "BEST"
    elif (
        edge >= float(cfg["good_edge"])
        and price_ev >= float(cfg["good_price_ev"])
        and samples >= int(cfg["good_minimum_samples"])
        and confidence >= float(cfg["good_minimum_confidence"])
    ):
        tier = "GOOD"
    if external_books == 0 and offered_fair is None and tier == "BEST":
        tier = "GOOD"
    return tier, ""


def _recommended_stake(
    win_probability: float,
    loss_probability: float,
    decimal_price: float,
    confidence: float,
    samples: int,
    cfg: dict[str, Any],
) -> tuple[float, float]:
    if decimal_price <= 1:
        return 0.0, 0.0
    profit_multiple = decimal_price - 1
    full_kelly = max(
        0.0,
        (profit_multiple * win_probability - loss_probability) / profit_multiple,
    )
    maturity = min(1.0, samples / max(1, int(cfg["full_sample_size"])))
    stake = (
        float(cfg["bankroll"])
        * full_kelly
        * float(cfg["kelly_fraction"])
        * max(0.25, confidence)
        * maturity
    )
    stake = min(float(cfg["max_stake"]), stake)
    stake = round(stake * 2) / 2
    return round(full_kelly, 5), round(stake, 2)


def evaluate_quotes_against_projections(
    quotes: list[PropQuote],
    projections: list[Projection],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Combine independent NFL form with live no-vig market information."""
    cfg = settings["projection_model"]
    index = _projection_index(projections)
    board: list[dict[str, Any]] = []
    for group in _groups(quotes):
        for quote in _best_eligible_offers(group, settings):
            projection = _matching_projection(quote, index)
            if projection is None:
                continue
            probabilities = _projection_probabilities(quote, projection)
            fair, offered_fair, external_books, market_basis = _market_context(
                group, quote, settings
            )
            breakeven = 1 / quote.price_decimal
            market_conditional = fair if fair is not None else breakeven
            if probabilities is None:
                projection_win, projection_push, projection_loss = 0.0, 0.0, 1.0
                projection_conditional = 0.0
            else:
                projection_win, projection_push, projection_loss = probabilities
                non_push = projection_win + projection_loss
                projection_conditional = projection_win / non_push if non_push else 0.0
            samples = int(projection.samples)
            confidence = max(0.0, min(0.75, float(projection.confidence)))
            maturity = min(1.0, samples / max(1, int(cfg["full_sample_size"])))
            season_maturity = _season_maturity(projection, cfg)
            market_reliability = _market_reliability(quote.market, cfg)
            projection_weight = min(
                float(cfg["maximum_projection_weight"]),
                confidence * maturity,
            ) * season_maturity * market_reliability
            model_conditional = (
                market_conditional
                + (projection_conditional - market_conditional) * projection_weight
            )
            push_probability = projection_push
            model_win = model_conditional * (1 - push_probability)
            model_loss = (1 - model_conditional) * (1 - push_probability)
            raw_edge = model_conditional / max(0.0001, market_conditional) - 1
            raw_price_ev = model_win * quote.price_decimal + push_probability - 1
            edge = _compress(raw_edge, float(cfg["edge_ceiling"]))
            price_ev = _compress(raw_price_ev, float(cfg["price_ev_ceiling"]))
            raw_projection_gap = abs(projection_conditional - market_conditional)
            tier, reason = _tier_and_reason(
                quote,
                projection,
                offered_fair,
                external_books,
                raw_projection_gap,
                edge,
                price_ev,
                cfg,
            )
            full_kelly, stake = _recommended_stake(
                model_win,
                model_loss,
                quote.price_decimal,
                confidence * season_maturity * market_reliability,
                samples,
                cfg,
            )
            if tier == "PASS":
                stake = 0.0
            elif stake < float(cfg["minimum_stake"]):
                tier = "PASS"
                reason = "Calculated stake is below the minimum wager"
                stake = 0.0
            form_label = (
                "Matchup-adjusted NFL form"
                if int(getattr(projection, "opponent_defense_samples", 0)) >= 2
                else "NFL form"
            )
            model_label = (
                f"{form_label} + external no-vig market"
                if external_books
                else f"{form_label} + offered-book price"
            )
            if season_maturity < 0.999:
                model_label = model_label.replace("NFL form", "prior-season-weighted NFL form")
            board.append(
                {
                    **quote.to_dict(),
                    "book": eligible_book_name(quote.book, settings) or quote.book,
                    "pick": _selection(quote),
                    "breakeven": round(breakeven, 5),
                    "market_fair_prob": round(market_conditional, 5),
                    "offered_fair_prob": None if offered_fair is None else round(offered_fair, 5),
                    "target_fair_prob": None if offered_fair is None else round(offered_fair, 5),
                    "market_basis": market_basis,
                    "projection_prob": round(projection_conditional, 5),
                    "model_prob": round(model_win, 5),
                    "model_prob_no_push": round(model_conditional, 5),
                    "push_prob": round(push_probability, 5),
                    "edge": round(edge, 5),
                    "edge_raw": round(raw_edge, 5),
                    "edge_real": round(price_ev, 5),
                    "edge_price": round(raw_price_ev, 5),
                    "ev": round(raw_price_ev, 5),
                    "full_kelly": full_kelly,
                    "recommended_stake": stake,
                    "consensus_books": external_books,
                    "confidence": round(confidence, 2),
                    "tier": tier,
                    "reason": reason,
                    "mode": "projection-and-market",
                    "model_label": model_label,
                    "projection": projection.projection,
                    "base_projection": projection.base_projection,
                    "projection_samples": samples,
                    "projection_standard_deviation": projection.standard_deviation,
                    "current_season_samples": int(getattr(projection, "current_season_samples", 0)),
                    "position": projection.position,
                    "opponent": projection.opponent,
                    "venue": projection.venue,
                    "opponent_defense_average": projection.opponent_defense_average,
                    "league_defense_average": projection.league_defense_average,
                    "opponent_defense_rank": projection.opponent_defense_rank,
                    "opponent_defense_teams": projection.opponent_defense_teams,
                    "opponent_defense_samples": projection.opponent_defense_samples,
                    "opponent_defense_current_samples": projection.opponent_defense_current_samples,
                    "defense_adjustment": projection.defense_adjustment,
                    "matchup_quality": projection.matchup_quality,
                    "injury_status": projection.injury_status,
                    "roster_verified": projection.roster_verified,
                    "projection_weight": round(projection_weight, 5),
                    "season_maturity": round(season_maturity, 5),
                    "market_reliability": round(market_reliability, 5),
                    "raw_projection_market_gap": round(raw_projection_gap, 5),
                }
            )
    return sorted(
        board,
        key=lambda row: (
            TIER_ORDER[row["tier"]],
            -(row.get("edge_real") or -1),
            -(row.get("edge") or -1),
        ),
    )


def merge_boards(
    market_watch: list[dict[str, Any]],
    projected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer every projection-evaluated row over its market-watch counterpart."""
    combined = {_board_key(row): row for row in market_watch}
    for row in projected:
        combined[_board_key(row)] = row
    return sorted(
        combined.values(),
        key=lambda row: (
            TIER_ORDER[row["tier"]],
            -(row.get("edge_real") or -1),
            row.get("start_time") or "",
        ),
    )


def select_portfolio(
    board: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply alternate-line, player-correlation, slate and exposure limits."""
    cfg = settings["projection_model"]
    rows = [dict(row) for row in board]
    candidates = sorted(
        (row for row in rows if row["tier"] != "PASS"),
        key=lambda row: (
            TIER_ORDER[row["tier"]],
            -(row.get("edge_real") or -1),
            -(row.get("edge") or -1),
        ),
    )
    seen_market: set[tuple[str, str, str]] = set()
    player_counts: dict[tuple[str, str], int] = defaultdict(int)
    selected = 0
    exposure = 0.0
    max_exposure = float(cfg["bankroll"]) * float(cfg["max_open_exposure"])
    for row in candidates:
        player_key = (str(row["event_id"]), _name_key(str(row["player"])))
        market_key = (*player_key, _market_key(str(row["market"])))
        reject_reason = ""
        if market_key in seen_market:
            reject_reason = "A stronger side or alternate line was selected for this player market"
        elif player_counts[player_key] >= int(cfg["max_props_per_player"]):
            reject_reason = "Player correlation limit reached"
        elif selected >= int(cfg["max_plays_per_slate"]):
            reject_reason = "Outside the top plays for this slate"
        else:
            remaining = max(0.0, max_exposure - exposure)
            stake = min(float(row["recommended_stake"]), remaining)
            stake = round(stake * 2) / 2
            if stake < float(cfg["minimum_stake"]):
                reject_reason = "Slate exposure limit leaves less than the minimum wager"
            else:
                row["recommended_stake"] = stake
                seen_market.add(market_key)
                player_counts[player_key] += 1
                selected += 1
                exposure += stake
        if reject_reason:
            row["model_tier"] = row["tier"]
            row["tier"] = "PASS"
            row["reason"] = reject_reason
            row["recommended_stake"] = 0.0
    return sorted(
        rows,
        key=lambda row: (
            TIER_ORDER[row["tier"]],
            -(row.get("edge_real") or -1),
            row.get("start_time") or "",
        ),
    )
