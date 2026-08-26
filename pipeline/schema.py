from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PropQuote:
    sport: str
    event_id: str
    start_time: str
    matchup: str
    player: str
    market: str
    side: str
    line: float | None
    price_decimal: float
    price_american: int
    book: str
    provider: str
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Projection:
    sport: str
    player: str
    team: str
    matchup: str
    market: str
    projection: float
    samples: int
    confidence: float
    standard_deviation: float
    recent: list[float]
    trend: float
    start_time: str = ""
    source: str = "ESPN public statistics"
    current_season_samples: int = 0
    position: str = ""
    opponent: str = ""
    venue: str = ""
    event_id: str = ""
    base_projection: float | None = None
    opponent_defense_average: float | None = None
    league_defense_average: float | None = None
    opponent_defense_rank: int | None = None
    opponent_defense_teams: int = 0
    opponent_defense_samples: int = 0
    opponent_defense_current_samples: int = 0
    defense_adjustment: float = 0.0
    matchup_quality: str = "Unknown"
    injury_status: str = ""
    roster_verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decimal_to_american(decimal_price: float) -> int:
    if decimal_price <= 1:
        raise ValueError("decimal odds must be greater than 1")
    if decimal_price >= 2:
        return round((decimal_price - 1) * 100)
    return round(-100 / (decimal_price - 1))


def american_to_decimal(american_price: float | int) -> float:
    price = float(american_price)
    if price == 0:
        raise ValueError("American odds cannot be zero")
    if price > 0:
        return 1 + price / 100
    return 1 + 100 / abs(price)


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
