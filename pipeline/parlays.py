"""Conservative, same-book daily parlay suggestions.

The straight-bet board and this feed answer different questions. A parlay may
target a large payout, but every leg still needs a fresh real price, a matched
NFL projection, enough regular-season samples, and a tolerable relationship
between model probability and sportsbook break-even probability. Nothing here
is auto-staked and no synthetic odds are invented. The combinations may mix
passing, rushing, receiving, touchdown, defense, and kicking markets.
"""
from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TARGETS = (
    {"key": "smart_shot", "name": "Smart Shot",
     "target_american": 1000, "minimum_decimal": 8.0, "maximum_decimal": 18.0},
    {"key": "home_run", "name": "Home Run",
     "target_american": 5000, "minimum_decimal": 35.0, "maximum_decimal": 75.0},
    {"key": "moonshot", "name": "Moonshot",
     "target_american": 10000, "minimum_decimal": 70.0, "maximum_decimal": 145.0},
)

TOUCHDOWN_TARGET = {
    "key": "touchdown_ticket",
    "name": "Touchdown Ticket",
    "target_american": 5000,
    "minimum_decimal": 15.0,
    "maximum_decimal": 125.0,
    "touchdown_only": True,
}

HARD_BLOCKS = (
    "already started", "odds are stale", "timestamp", "roster verification",
    "roster status", "only ", "raw projection/market disagreement",
    "no independent nfl player projection", "complete two-sided",
    "price outside the allowable range",
)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _local_day(value: str, timezone_name: str) -> str | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return None
        return moment.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except (TypeError, ValueError, KeyError):
        return None


def _american(decimal_price: float) -> int:
    return (round((decimal_price - 1) * 100) if decimal_price >= 2
            else -round(100 / (decimal_price - 1)))


def _market_group(market: str) -> str:
    value = str(market or "").casefold()
    if "touchdown" in value or " td" in value or value.startswith("td "):
        return "Touchdowns"
    if "target" in value:
        return "Targets"
    if "pass" in value or "interception" in value:
        return "Passing"
    if "rush" in value or "carr" in value:
        return "Rushing"
    if "receiv" in value or "reception" in value:
        return "Receiving"
    if "tackle" in value or "sack" in value or "defens" in value:
        return "Defense"
    if "kick" in value or "field goal" in value or "extra point" in value:
        return "Kicking"
    return "Other"


def _is_supported_selection(row: dict) -> bool:
    """Accept a modeled direction, not just touchdown overs.

    The upstream model owns market support and maps Yes/No outcomes to its
    canonical over/under directions. Requiring one of those four directions
    keeps novelty or unmodeled sportsbook outcomes out of this feed.
    """
    return (
        bool(str(row.get("market") or "").strip())
        and str(row.get("side") or "").casefold() in {"over", "under", "yes", "no"}
        and _market_group(row.get("market")) != "Other"
    )


def _is_anytime_touchdown(row: dict) -> bool:
    market = str(row.get("market") or "").casefold()
    side = str(row.get("side") or "").casefold()
    return (
        ("anytime td" in market or "anytime touchdown" in market)
        and side in {"over", "yes"}
        and (_number(row.get("line")) in {None, 0.0, 0.5})
    )


def _selection_label(row: dict) -> str:
    if _is_anytime_touchdown(row):
        return "Any Time Touchdown Scorer"
    pick = str(row.get("pick") or "").strip()
    prefix = f'{str(row.get("player") or "").strip()} — '
    return pick[len(prefix):] if prefix and pick.startswith(prefix) else pick


def _instant(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment if moment.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _win_probability(row):
    conditional = _number(row.get("model_prob_no_push"))
    push = _number(row.get("push_prob", 0))
    if conditional is None or push is None or not 0 <= conditional <= 1 or not 0 <= push < 1:
        return None
    return conditional * (1 - push)


def _candidate(row: dict, cfg: dict, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    start, updated = _instant(row.get("start_time")), _instant(row.get("updated_at"))
    if start is None or updated is None or start <= now:
        return False
    age = (now - updated).total_seconds() / 3600
    if age < -5 / 60 or age >= float(cfg.get("max_odds_age_hours", 12)):
        return False
    if not row.get("player") or not (row.get("event_id") or row.get("result_event_id")):
        return False
    probability = _number(row.get("model_prob_no_push"))
    decimal_price = _number(row.get("price_decimal"))
    breakeven = _number(row.get("breakeven"))
    confidence = _number(row.get("confidence"))
    samples = _number(row.get("projection_samples"))
    gap = _number(row.get("raw_projection_market_gap"))
    reason = str(row.get("reason") or "").casefold()
    if not _is_supported_selection(row) or not row.get("roster_verified"):
        return False
    hard_blocked = any(
        block in reason
        for block in HARD_BLOCKS
        if block != "complete two-sided"
    )
    if "complete two-sided" in reason and not _is_anytime_touchdown(row):
        hard_blocked = True
    if hard_blocked:
        return False
    if None in (probability, decimal_price, breakeven, confidence, samples, _win_probability(row)):
        return False
    if not 0 < probability <= 1 or not 0 < breakeven < 1 or not 0 <= confidence <= 1:
        return False
    if probability < float(cfg.get("minimum_model_probability", 0.22)):
        return False
    if confidence < float(cfg.get("minimum_confidence", 0.52)):
        return False
    if samples < int(cfg.get("minimum_samples", 6)):
        return False
    if decimal_price <= 1 or not row.get("book"):
        return False
    price = _number(row.get("price_american"))
    ceiling = cfg.get("nfl_touchdown_max_price", 450) if _market_group(row.get("market")) == "Touchdowns" else cfg.get("max_price", 300)
    if price is None or not float(cfg.get("min_price", -175)) <= price <= float(ceiling):
        return False
    if abs(price - _american(decimal_price)) > 1 or abs(breakeven - 1 / decimal_price) > .001:
        return False
    if gap is None or abs(gap) > float(cfg.get("maximum_projection_gap", 0.25)):
        return False
    return probability / max(0.0001, breakeven) >= float(cfg.get("minimum_value_ratio", 0.90))


def _dedupe(rows: list[dict]) -> list[dict]:
    """Keep the stronger direction for each exact player market at a book."""
    best = {}
    for row in rows:
        key = (str(row.get("event_id") or row.get("result_event_id") or ""),
               str(row.get("player") or "").casefold(),
               str(row.get("market") or "").casefold())
        probability = float(row["model_prob_no_push"])
        breakeven = float(row["breakeven"])
        quality = probability / max(0.0001, breakeven)
        current = best.get(key)
        if current is None or quality > current[0]:
            best[key] = (quality, row)
    return [item[1] for item in best.values()]


def _valid_combo(combo: tuple[dict, ...], maximum_per_game: int,
                 minimum_market_groups: int, maximum_per_market_group: int) -> bool:
    players = {(str(row.get("event_id") or row.get("result_event_id") or ""),
                str(row.get("player") or "").casefold()) for row in combo}
    if len(players) != len(combo):
        return False
    events = Counter(str(row.get("event_id") or row.get("result_event_id") or "") for row in combo)
    if max(events.values(), default=0) > maximum_per_game:
        return False
    groups = Counter(_market_group(row.get("market")) for row in combo)
    return (
        len(groups) >= minimum_market_groups
        and max(groups.values(), default=0) <= maximum_per_market_group
    )


def _best_card(rows: list[dict], target: dict, cfg: dict) -> dict | None:
    target_decimal = 1 + float(target["target_american"]) / 100
    touchdown_only = bool(target.get("touchdown_only"))
    if touchdown_only:
        rows = [row for row in rows if _is_anytime_touchdown(row)]
    ideal_prices = (target_decimal ** (1 / 3), target_decimal ** (1 / 4))
    candidates = sorted(
        _dedupe(rows),
        key=lambda row: (
            min(
                abs(math.log(float(row["price_decimal"]) / ideal))
                for ideal in ideal_prices
            ) - 0.18 * math.log(
                float(row["model_prob_no_push"])
                / max(0.0001, float(row["breakeven"]))
            ),
            -float(row["confidence"]),
        ),
    )[:int(cfg.get("maximum_candidate_legs", 28))]
    maximum_per_game = int(cfg.get("maximum_legs_per_game", 4))
    minimum_market_groups = 1 if touchdown_only else int(cfg.get("minimum_market_groups", 2))
    maximum_per_market_group = 4 if touchdown_only else int(cfg.get("maximum_legs_per_market_group", 2))
    minimum_parlay_value_ratio = float(cfg.get("minimum_parlay_value_ratio", 0.75))
    best = None
    for leg_count in (3, 4):
        for combo in itertools.combinations(candidates, leg_count):
            if not _valid_combo(
                combo, maximum_per_game, minimum_market_groups,
                maximum_per_market_group,
            ):
                continue
            decimal_price = math.prod(float(row["price_decimal"]) for row in combo)
            if not target["minimum_decimal"] <= decimal_price <= target["maximum_decimal"]:
                continue
            event_counts = Counter(
                str(row.get("event_id") or row.get("result_event_id") or "") for row in combo
            )
            same_game_pairs = sum(count * (count - 1) // 2 for count in event_counts.values())
            correlation_factor = float(cfg.get("same_game_pair_haircut", 0.90)) ** same_game_pairs
            # Full advertised payout requires every leg to win, without pushes.
            raw_probability = math.prod(_win_probability(row) for row in combo)
            model_probability = raw_probability * correlation_factor
            implied_probability = 1 / decimal_price
            parlay_value_ratio = model_probability / implied_probability
            if parlay_value_ratio < minimum_parlay_value_ratio:
                continue
            value_ratios = [
                float(row["model_prob_no_push"]) / max(0.0001, float(row["breakeven"]))
                for row in combo
            ]
            distance = abs(math.log(decimal_price / target_decimal))
            score = (
                distance,
                -sum(math.log(max(0.01, ratio)) for ratio in value_ratios),
                -model_probability,
                -sum(float(row["confidence"]) for row in combo),
            )
            if best is None or score < best[0]:
                best = (score, combo, decimal_price, raw_probability,
                        model_probability, correlation_factor)
    if best is None:
        return None
    _, combo, decimal_price, raw_probability, model_probability, correlation_factor = best
    return {
        "status": "ready",
        "key": target["key"],
        "name": target["name"],
        "target_american": target["target_american"],
        "book": combo[0]["book"],
        "estimated_decimal": round(decimal_price, 2),
        "estimated_american": _american(decimal_price),
        "raw_independent_probability": round(raw_probability, 5),
        "correlation_factor": round(correlation_factor, 5),
        "same_game": correlation_factor < 1,
        "model_probability": round(model_probability, 5),
        "book_implied_probability": round(1 / decimal_price, 5),
        "parlay_value_ratio": round(model_probability * decimal_price, 4),
        "fair_american": _american(1 / max(0.0001, model_probability)),
        "leg_count": len(combo),
        "legs": [{
            "event_id": row.get("event_id") or row.get("result_event_id"),
            "start_time": row.get("start_time"),
            "updated_at": row.get("updated_at"),
            "matchup": row.get("matchup"),
            "player": row.get("player"),
            "market": row.get("market"),
            "market_group": _market_group(row.get("market")),
            "side": row.get("side"),
            "line": row.get("line"),
            "pick": row.get("pick"),
            "selection": _selection_label(row),
            "price_american": row.get("price_american"),
            "model_probability": round(_win_probability(row), 5),
            "push_probability": row.get("push_prob", 0),
            "breakeven": row.get("breakeven"),
            "confidence": row.get("confidence"),
            "projection": row.get("projection"),
            "opponent_defense_rank": row.get("opponent_defense_rank"),
            "opponent_defense_teams": row.get("opponent_defense_teams"),
            "samples": row.get("projection_samples"),
            "current_season_samples": row.get("current_season_samples"),
            "matchup_quality": row.get("matchup_quality"),
        } for row in combo],
    }


def build(rows: list[dict], projections: list[dict], settings: dict,
          generated_at: str | None = None) -> dict:
    cfg = {**(settings.get("projection_model") or {}),
           **(settings.get("daily_parlays") or {})}
    now = _instant(generated_at) or datetime.now(timezone.utc)
    timezone_name = str(cfg.get("timezone") or "America/Toronto")
    games_by_day: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in projections:
        day = _local_day(row.get("start_time"), timezone_name)
        if day and row.get("matchup"):
            games_by_day[day].add((str(row.get("event_id") or ""), str(row["matchup"])))

    eligible_by_day_book: dict[tuple[str, str], list[dict]] = defaultdict(list)
    considered_by_day: Counter[str] = Counter()
    for row in rows:
        day = _local_day(row.get("start_time"), timezone_name)
        if not day:
            continue
        if _is_supported_selection(row):
            considered_by_day[day] += 1
        if _candidate(row, cfg, now):
            eligible_by_day_book[(day, str(row["book"]))].append(row)

    dates = []
    for day in sorted(games_by_day):
        books = sorted(book for date, book in eligible_by_day_book if date == day)
        cards = []
        for target in (TOUCHDOWN_TARGET, *TARGETS):
            possibilities = [
                card for book in books
                if (card := _best_card(eligible_by_day_book[(day, book)], target, cfg))
            ]
            if possibilities:
                target_decimal = 1 + target["target_american"] / 100
                card = min(possibilities,
                           key=lambda value: abs(math.log(value["estimated_decimal"] / target_decimal)))
            else:
                eligible = sum(len(eligible_by_day_book[(day, book)]) for book in books)
                card = {
                    "status": "waiting",
                    "key": target["key"],
                    "name": target["name"],
                    "target_american": target["target_american"],
                    "leg_count": 0,
                    "legs": [],
                    "reason": (
                        "Waiting for at least three fresh, supported anytime-touchdown prices at one eligible book."
                        if target.get("touchdown_only") else
                        "Waiting for at least three fresh, statistically supported prop prices at one eligible book."
                        if eligible < 3 else
                        "Supported legs are available, but no 3–4 leg combination lands inside this payout band."
                    ),
                }
            cards.append(card)
        dates.append({
            "date": day,
            "games": [{"event_id": event, "matchup": matchup}
                      for event, matchup in sorted(games_by_day[day])],
            "prop_prices_considered": considered_by_day[day],
            "eligible_legs": sum(len(eligible_by_day_book[(day, book)]) for book in books),
            "cards": cards,
        })
    return {
        "schema": 1,
        "generated_at": generated_at,
        "timezone": timezone_name,
        "method": (
            "One dedicated anytime-touchdown ticket plus three mixed-market payout targets, "
            "each using three or four statistically supported NFL prop outcomes at one eligible sportsbook. "
            "Every leg has a fresh posted price, a matched NFL projection, at least "
            "the configured sample and confidence floors, and no hard safety flag. "
            "Mixed tickets use at least two market groups; the touchdown ticket uses only anytime scorers. Every ticket limits repeated player and "
            "market exposure, and gives same-game pairs an additional probability haircut."
        ),
        "notes": [
            "Displayed parlay odds are straight multiplication of the posted leg prices; the sportsbook may reprice or reject correlated same-game legs.",
            "Win chances estimate all legs winning outright. A push changes the ticket and payout under the book's rules. Same-game probability haircuts are uncalibrated assumptions, not a fitted joint-outcome model.",
            "Anytime-touchdown scorer markets may be one-sided. Those legs require an independent player projection and pass the parlay safeguards, but remain ineligible for the stricter straight-bet board when no two-sided no-vig price exists.",
            "These are high-variance planning cards, not Kelly-sized model bets, and nothing is added to My Ledger automatically.",
            "Verify every leg, price, player status, and the final parlay payout at the named sportsbook before considering a wager.",
        ],
        "dates": dates,
    }
