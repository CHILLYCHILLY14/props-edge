from __future__ import annotations

import datetime as dt
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..http import JsonClient
from ..schema import Projection, as_float


ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _date_range(start: dt.date, end: dt.date) -> str:
    return f"{start:%Y%m%d}-{end:%Y%m%d}"


def _nfl_season_year(day: dt.date) -> int:
    """NFL season labels follow the fall year, including January Week 18 games."""
    return day.year if day.month >= 7 else day.year - 1


def _season_type(event: dict[str, Any]) -> int | None:
    raw = (event.get("season") or {}).get("type")
    if raw is None:
        competition = (event.get("competitions") or [{}])[0]
        raw = (competition.get("type") or {}).get("id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _event_team_keys(event: dict[str, Any]) -> list[str]:
    competition = (event.get("competitions") or [{}])[0]
    return [
        _norm(str((row.get("team") or {}).get("displayName") or ""))
        for row in competition.get("competitors") or []
        if (row.get("team") or {}).get("displayName")
    ]


def _team_and_matchups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    answer: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        home = next((row for row in competitors if row.get("homeAway") == "home"), {})
        away = next((row for row in competitors if row.get("homeAway") == "away"), {})
        home_name = str((home.get("team") or {}).get("displayName") or "Home")
        away_name = str((away.get("team") or {}).get("displayName") or "Away")
        shared = {
            "event_id": str(event.get("id") or competition.get("id") or ""),
            "matchup": f"{away_name} @ {home_name}",
            "start_time": str(event.get("date") or competition.get("date") or ""),
        }
        for team_name, opponent, venue in (
            (home_name, away_name, "Home"),
            (away_name, home_name, "Away"),
        ):
            team_key = _norm(team_name)
            event_info = {**shared, "opponent": opponent, "venue": venue}
            if event_info not in answer[team_key]:
                answer[team_key].append(event_info)
    return dict(answer)


def _upcoming_teams(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    teams: dict[str, str] = {}
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        for competitor in competition.get("competitors") or []:
            team = competitor.get("team") or {}
            team_id = str(team.get("id") or "")
            team_name = str(team.get("displayName") or "")
            if team_id and team_name:
                teams[team_id] = team_name
    return sorted(teams.items(), key=lambda item: item[1])


def _position_group(value: str, stat_group: str = "") -> str:
    key = _norm(value)
    groups = {
        "QB": {"qb", "quarterback"},
        "RB": {"rb", "fb", "runningback", "fullback"},
        "WR": {"wr", "wide receiver", "widereceiver"},
        "TE": {"te", "tightend"},
        "K": {"k", "pk", "kicker", "placekicker"},
        "DL": {"de", "dt", "nt", "dl", "defensiveend", "defensivetackle"},
        "LB": {"lb", "ilb", "olb", "linebacker"},
        "DB": {"cb", "s", "ss", "fs", "db", "cornerback", "safety"},
    }
    for label, aliases in groups.items():
        if key in {_norm(alias) for alias in aliases}:
            return label
    group_key = _norm(stat_group)
    if "passing" in group_key:
        return "QB"
    if "kicking" in group_key:
        return "K"
    if "defensive" in group_key:
        return "DEF"
    return "SKILL"


def _weighted_mean(values: list[float], season_years: list[int], current_season_year: int | None, prior_weight: float) -> float:
    weights = list(range(1, len(values) + 1))
    if current_season_year is not None:
        weights = [
            weight * (1.0 if year == current_season_year else prior_weight)
            for weight, year in zip(weights, season_years)
        ]
    total = sum(weights)
    return sum(value * weight for value, weight in zip(values, weights)) / total if total else 0.0


def _defense_label(rank: int | None, teams: int) -> str:
    if rank is None or teams < 8:
        return "Unknown"
    percentile = (rank - 1) / max(1, teams - 1)
    if percentile <= 0.25:
        return "Tough"
    if percentile >= 0.75:
        return "Favorable"
    return "Neutral"


def _roster_info(payloads: list[tuple[str, dict[str, Any]]]) -> dict[tuple[str, str], dict[str, str]]:
    answer: dict[tuple[str, str], dict[str, str]] = {}
    for fallback_team, payload in payloads:
        team = str((payload.get("team") or {}).get("displayName") or fallback_team)
        team_key = _norm(team)
        for group in payload.get("athletes") or []:
            for athlete in group.get("items") or []:
                player = str(athlete.get("displayName") or athlete.get("fullName") or "")
                if not player:
                    continue
                position = _position_group(
                    str(
                        (athlete.get("position") or {}).get("abbreviation")
                        or (athlete.get("position") or {}).get("displayName")
                        or ""
                    )
                )
                injury = (athlete.get("injuries") or [{}])[0]
                injury_status = str(
                    injury.get("status")
                    or (injury.get("type") or {}).get("description")
                    or (injury.get("type") or {}).get("name")
                    or ""
                )
                answer[(team_key, _norm(player))] = {
                    "player": player,
                    "team": team,
                    "position": position,
                    "injury_status": injury_status,
                }
    return answer


def _canonical_market(group: str, name: str) -> str | None:
    group_key, stat = _norm(group), _norm(name)
    maps = {
        "passing": {
            "passingyards": "Passing yards",
            "yards": "Passing yards",
            "completions": "Pass completions",
            "passingcompletions": "Pass completions",
            "attempts": "Pass attempts",
            "passingattempts": "Pass attempts",
            "passingtouchdowns": "Passing touchdowns",
            "touchdowns": "Passing touchdowns",
            "interceptions": "Pass interceptions",
            "long": "Longest pass",
            "longestpass": "Longest pass",
        },
        "rushing": {
            "rushingattempts": "Rush attempts",
            "carries": "Rush attempts",
            "attempts": "Rush attempts",
            "rushingyards": "Rushing yards",
            "yards": "Rushing yards",
            "rushingtouchdowns": "Rushing touchdowns",
            "touchdowns": "Rushing touchdowns",
            "long": "Longest rush",
            "longestrush": "Longest rush",
        },
        "receiving": {
            "receptions": "Receptions",
            "receivingyards": "Receiving yards",
            "yards": "Receiving yards",
            "receivingtouchdowns": "Receiving touchdowns",
            "touchdowns": "Receiving touchdowns",
            "targets": "Targets",
            "receivingtargets": "Targets",
            "long": "Longest reception",
            "longestreception": "Longest reception",
        },
        "kicking": {
            "fieldgoalsmade": "Field goals made",
            "fieldgoalattempts": "Field goal attempts",
            "fieldgoalsattempted": "Field goal attempts",
            "fg": "Field goals made",
            "extrapointsmade": "Extra points made",
            "extrapointattempts": "Extra point attempts",
            "extrapointsattempted": "Extra point attempts",
            "xp": "Extra points made",
            "points": "Kicking points",
            "kickingpoints": "Kicking points",
            "totalkickingpoints": "Kicking points",
        },
        "defensive": {
            "totaltackles": "Tackles + assists",
            "tackles": "Tackles + assists",
            "solotackles": "Solo tackles",
            "solo": "Solo tackles",
            "sacks": "Sacks",
        },
    }
    for key, mapping in maps.items():
        if key in group_key and stat in mapping:
            return mapping[stat]
    return None


def parse_summaries(
    summaries: list[dict[str, Any]],
    sport: str,
    upcoming_matchups: dict[str, Any],
    current_season_year: int | None = None,
    defense_weight: float = 0.40,
    defense_max_adjustment: float = 0.12,
    defense_prior_season_weight: float = 0.55,
    defense_current_season_full_weight_games: int = 4,
    current_roster: dict[tuple[str, str], dict[str, str]] | None = None,
    verified_roster_teams: set[str] | None = None,
) -> list[Projection]:
    """Build player form plus a conservative opponent-allowance adjustment.

    Defensive allowance is measured from the same completed regular-season ESPN
    box scores as player form. Production is totaled by opponent, position group,
    and market for each game, then compared with the league median. The matchup
    adjustment is reliability-shrunk and capped so it informs the projection
    without overwhelming the player's own history.
    """
    if sport != "NFL":
        return []
    history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    history_seasons: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    display: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    defense_history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    defense_seasons: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for summary in summaries:
        try:
            summary_season = int(summary.get("_props_edge_season_year") or 0)
        except (TypeError, ValueError):
            summary_season = 0
        team_blocks = ((summary.get("boxscore") or {}).get("players") or [])
        team_names = [
            str((team_block.get("team") or {}).get("displayName") or "")
            for team_block in team_blocks
            if (team_block.get("team") or {}).get("displayName")
        ]
        opponent_by_team = {
            _norm(team): _norm(next((other for other in team_names if _norm(other) != _norm(team)), ""))
            for team in team_names
        }
        observed_by_player: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"player": "", "team": "", "position": "SKILL", "markets": {}}
        )
        for team_block in team_blocks:
            team = str((team_block.get("team") or {}).get("displayName") or "")
            for stat_group in team_block.get("statistics") or []:
                group = str(
                    stat_group.get("type")
                    or stat_group.get("name")
                    or stat_group.get("displayName")
                    or ""
                )
                names = (
                    stat_group.get("keys")
                    or stat_group.get("names")
                    or stat_group.get("labels")
                    or []
                )
                for athlete_row in stat_group.get("athletes") or []:
                    athlete = athlete_row.get("athlete") or {}
                    player = str(athlete.get("displayName") or "")
                    if not player:
                        continue
                    game_key = (player.casefold(), _norm(team))
                    raw_position = str(
                        (athlete.get("position") or {}).get("abbreviation")
                        or (athlete.get("position") or {}).get("displayName")
                        or ""
                    )
                    position = _position_group(raw_position, group)
                    roster_entry = (current_roster or {}).get((_norm(team), _norm(player)))
                    if roster_entry and roster_entry.get("position"):
                        position = str(roster_entry["position"])
                    if observed_by_player[game_key]["position"] in ("SKILL", "DEF") or position not in ("SKILL", "DEF"):
                        observed_by_player[game_key]["position"] = position
                    for name, raw in zip(names, athlete_row.get("stats") or []):
                        stat_key, group_key = _norm(str(name)), _norm(group)
                        combined = re.match(
                            r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$",
                            str(raw),
                        )
                        if combined and "passing" in group_key and "completion" in stat_key and "attempt" in stat_key:
                            values = (("Pass completions", float(combined.group(1))), ("Pass attempts", float(combined.group(2))))
                        elif combined and "kicking" in group_key and "fieldgoal" in stat_key:
                            values = (("Field goals made", float(combined.group(1))), ("Field goal attempts", float(combined.group(2))))
                        elif combined and "kicking" in group_key and ("extrapoint" in stat_key or stat_key.startswith("xp")):
                            values = (("Extra points made", float(combined.group(1))), ("Extra point attempts", float(combined.group(2))))
                        else:
                            market = _canonical_market(group, str(name))
                            value = as_float(raw)
                            values = () if market is None or value is None or value < 0 else ((market, value),)
                        for market, value in values:
                            observed_by_player[game_key]["player"] = player
                            observed_by_player[game_key]["team"] = team
                            observed_by_player[game_key]["markets"][market] = value
                            key = (player.casefold(), _norm(team), market)
                            history[key].append(value)
                            history_seasons[key].append(summary_season)
                            display[key] = (player, team, observed_by_player[game_key]["position"])
        for (player_key, team_key), info in observed_by_player.items():
            markets = info["markets"]
            derived: list[tuple[str, float]] = []
            rush_receiving_tds = sum(markets.get(name, 0.0) for name in ("Rushing touchdowns", "Receiving touchdowns"))
            if any(name in markets for name in ("Rushing touchdowns", "Receiving touchdowns")):
                derived.extend([
                    ("Anytime touchdown", rush_receiving_tds),
                    ("Rush + receiving touchdowns", rush_receiving_tds),
                    ("Touchdowns scored", rush_receiving_tds),
                ])
            if "Passing touchdowns" in markets:
                derived.append(("Pass + rush + receiving touchdowns", markets["Passing touchdowns"] + rush_receiving_tds))
            if "Passing yards" in markets:
                derived.extend([
                    ("Pass + rush yards", markets["Passing yards"] + markets.get("Rushing yards", 0.0)),
                    ("Pass + rush + receiving yards", markets["Passing yards"] + markets.get("Rushing yards", 0.0) + markets.get("Receiving yards", 0.0)),
                ])
            if any(name in markets for name in ("Rushing yards", "Receiving yards")):
                derived.append(("Rush + receiving yards", markets.get("Rushing yards", 0.0) + markets.get("Receiving yards", 0.0)))
            if "Kicking points" not in markets and any(name in markets for name in ("Field goals made", "Extra points made")):
                derived.append(("Kicking points", markets.get("Field goals made", 0.0) * 3 + markets.get("Extra points made", 0.0)))
            for market, value in derived:
                markets[market] = value
                combined_key = (player_key, team_key, market)
                history[combined_key].append(value)
                history_seasons[combined_key].append(summary_season)
                display[combined_key] = (info["player"], info["team"], info["position"])

        game_allowances: dict[tuple[str, str, str], float] = defaultdict(float)
        for (_, team_key), info in observed_by_player.items():
            opponent_key = opponent_by_team.get(team_key, "")
            if not opponent_key:
                continue
            position = str(info.get("position") or "SKILL")
            for market, value in info["markets"].items():
                game_allowances[(opponent_key, position, market)] += float(value)
                game_allowances[(opponent_key, "ALL", market)] += float(value)
        for allowance_key, value in game_allowances.items():
            defense_history[allowance_key].append(value)
            defense_seasons[allowance_key].append(summary_season)

    defense_profiles: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_position_market: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for defense_key, values in defense_history.items():
        recent = values[-8:]
        seasons = defense_seasons[defense_key][-8:]
        if len(recent) < 2:
            continue
        average = _weighted_mean(
            recent,
            seasons,
            current_season_year,
            defense_prior_season_weight,
        )
        current_samples = (
            len(recent)
            if current_season_year is None
            else sum(year == current_season_year for year in seasons)
        )
        defense_profiles[defense_key] = {
            "average": average,
            "samples": len(recent),
            "current_samples": current_samples,
        }
        by_position_market[(defense_key[1], defense_key[2])].append((defense_key[0], average))

    league_medians: dict[tuple[str, str], float] = {}
    defense_ranks: dict[tuple[str, str, str], tuple[int, int]] = {}
    for profile_key, teams in by_position_market.items():
        if not teams:
            continue
        league_medians[profile_key] = statistics.median(value for _, value in teams)
        ordered = sorted(teams, key=lambda item: (item[1], item[0]))
        for rank, (team_key, _) in enumerate(ordered, start=1):
            defense_ranks[(team_key, *profile_key)] = (rank, len(ordered))

    # ESPN's completed-game box scores identify the team a player represented
    # in that game. During the offseason that leaves traded/free-agent players
    # keyed to their former team, so their valid history never reaches the new
    # team's upcoming market. Remap only names that occur exactly once on an
    # upcoming, current roster; ambiguous names continue to fail closed.
    if current_roster:
        roster_destinations: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        for (team_key, player_key), roster_entry in current_roster.items():
            if upcoming_matchups and team_key not in upcoming_matchups:
                continue
            roster_destinations[player_key].append((team_key, roster_entry))

        remapped_history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        remapped_seasons: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        remapped_display: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        for old_key, values in history.items():
            old_player, old_team, old_position = display[old_key]
            destinations = roster_destinations.get(_norm(old_player), [])
            if len(destinations) == 1:
                team_key, roster_entry = destinations[0]
                player = str(roster_entry.get("player") or old_player)
                team = str(roster_entry.get("team") or (old_team if team_key == old_key[1] else team_key))
                position = str(roster_entry.get("position") or old_position)
                new_key = (player.casefold(), team_key, old_key[2])
                remapped_display[new_key] = (player, team, position)
            else:
                new_key = old_key
                remapped_display[new_key] = display[old_key]
            remapped_history[new_key].extend(values)
            remapped_seasons[new_key].extend(history_seasons[old_key])

        # Keep prior-season observations ahead of current-season observations
        # when old- and new-team keys were merged.
        for key, values in remapped_history.items():
            ordered = sorted(zip(remapped_seasons[key], values), key=lambda item: item[0])
            remapped_seasons[key] = [season for season, _ in ordered]
            remapped_history[key] = [value for _, value in ordered]
        history = remapped_history
        history_seasons = remapped_seasons
        display = remapped_display

    projections: list[Projection] = []
    for key, values in history.items():
        player, team, position = display[key]
        if upcoming_matchups and key[1] not in upcoming_matchups:
            continue
        roster_entry = (current_roster or {}).get((key[1], _norm(player)))
        roster_verified = verified_roster_teams is None or key[1] in verified_roster_teams
        if roster_verified and current_roster is not None and roster_entry is None:
            continue
        recent = values[-8:]
        recent_seasons = history_seasons[key][-8:]
        if len(recent) < 2:
            continue
        weights = list(range(1, len(recent) + 1))
        weighted_mean = sum(value * weight for value, weight in zip(recent, weights)) / sum(weights)
        median = statistics.median(recent)
        base_projection = 0.65 * weighted_mean + 0.35 * median
        deviation = statistics.stdev(recent) if len(recent) > 1 else 0.0
        average = statistics.mean(recent)
        stability = 1 / (1 + deviation / max(1.0, abs(average)))
        confidence = min(0.70, 0.18 + 0.06 * len(recent) + 0.16 * stability)
        raw_matchups = upcoming_matchups.get(key[1]) if upcoming_matchups else None
        if isinstance(raw_matchups, str):
            matchup_rows = [{"matchup": raw_matchups, "start_time": ""}]
        elif isinstance(raw_matchups, dict):
            matchup_rows = [raw_matchups]
        elif isinstance(raw_matchups, list):
            matchup_rows = raw_matchups
        else:
            matchup_rows = [{"matchup": "Next matchup not posted", "start_time": ""}]
        for matchup in matchup_rows:
            opponent = str(matchup.get("opponent") or "")
            opponent_key = _norm(opponent)
            selected_position = position
            profile = defense_profiles.get((opponent_key, selected_position, key[2]))
            profile_key = (selected_position, key[2])
            if profile is None or profile["samples"] < 4 or len(by_position_market.get(profile_key, [])) < 8:
                selected_position = "ALL"
                profile = defense_profiles.get((opponent_key, selected_position, key[2]))
                profile_key = (selected_position, key[2])

            defense_average: float | None = None
            league_average: float | None = None
            defense_rank: int | None = None
            defense_teams = 0
            defense_samples = 0
            defense_current_samples = 0
            adjustment = 0.0
            matchup_quality = "Unknown"
            if profile is not None:
                defense_average = float(profile["average"])
                league_average = league_medians.get(profile_key)
                defense_samples = int(profile["samples"])
                defense_current_samples = int(profile["current_samples"])
                defense_rank, defense_teams = defense_ranks.get(
                    (opponent_key, *profile_key),
                    (None, 0),
                )
                matchup_quality = _defense_label(defense_rank, defense_teams)
                if league_average is not None and league_average > 0:
                    raw_factor = max(0.65, min(1.35, defense_average / league_average))
                    sample_maturity = min(1.0, defense_samples / 8)
                    current_maturity = (
                        1.0
                        if current_season_year is None
                        else defense_prior_season_weight
                        + (1 - defense_prior_season_weight)
                        * min(
                            1.0,
                            defense_current_samples
                            / max(1, defense_current_season_full_weight_games),
                        )
                    )
                    adjustment = (raw_factor - 1) * defense_weight * sample_maturity * current_maturity
                    adjustment = max(-defense_max_adjustment, min(defense_max_adjustment, adjustment))
            projection = max(0.0, base_projection * (1 + adjustment))
            projections.append(
                Projection(
                    sport="NFL",
                    player=player,
                    team=team,
                    matchup=str(matchup.get("matchup") or "Next matchup not posted"),
                    market=key[2],
                    projection=round(projection, 2),
                    samples=len(recent),
                    confidence=round(confidence, 2),
                    standard_deviation=round(deviation, 2),
                    recent=[round(value, 2) for value in recent],
                    trend=round(projection - average, 2),
                    start_time=str(matchup.get("start_time") or ""),
                    source=(
                        "ESPN regular-season form + opponent allowance"
                        if profile is not None
                        else "ESPN regular-season form"
                    ),
                    current_season_samples=(
                        len(recent)
                        if current_season_year is None
                        else sum(year == current_season_year for year in recent_seasons)
                    ),
                    position=position,
                    opponent=opponent,
                    venue=str(matchup.get("venue") or ""),
                    event_id=str(matchup.get("event_id") or ""),
                    base_projection=round(base_projection, 2),
                    opponent_defense_average=(
                        None if defense_average is None else round(defense_average, 2)
                    ),
                    league_defense_average=(
                        None if league_average is None else round(league_average, 2)
                    ),
                    opponent_defense_rank=defense_rank,
                    opponent_defense_teams=defense_teams,
                    opponent_defense_samples=defense_samples,
                    opponent_defense_current_samples=defense_current_samples,
                    defense_adjustment=round(adjustment, 5),
                    matchup_quality=matchup_quality,
                    injury_status=str((roster_entry or {}).get("injury_status") or ""),
                    roster_verified=roster_verified,
                )
            )
    return sorted(
        projections,
        key=lambda row: (row.start_time, -row.confidence, -row.samples, row.player, row.market),
    )


class EspnProjectionProvider:
    name = "ESPN regular-season statistics"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.client = JsonClient(self.name, ESPN_URL, timeout=18)

    def fetch(self, sport: str) -> list[Projection]:
        if sport != "NFL":
            return []
        path = self.settings["sports"]["NFL"]["espn_path"]
        today = dt.datetime.now(dt.timezone.utc).date()
        fetch = self.settings["fetch"]
        # ESPN rejects very long date ranges (including a 400-day offseason
        # lookback). Query season identifiers instead so an August refresh can
        # still use the prior regular season while the new one has no games.
        recent_events: list[dict[str, Any]] = []
        for season_year in (today.year - 1, today.year):
            season = self.client.get(
                f"/{path}/scoreboard",
                {
                    "dates": str(season_year),
                    "seasontype": 2,
                    "limit": 1000,
                },
            )
            recent_events.extend(season.get("events") or [])
        upcoming = self.client.get(
            f"/{path}/scoreboard",
            {
                "dates": _date_range(
                    today,
                    today + dt.timedelta(days=int(fetch["lookahead_days"])),
                ),
                "limit": 500,
            },
        )
        upcoming_events = [
            event
            for event in upcoming.get("events") or []
            if not ((event.get("status") or {}).get("type") or {}).get("completed")
            and _season_type(event) == 2
        ]
        roster_targets = _upcoming_teams(upcoming_events)

        def fetch_roster(team_input: tuple[str, str]) -> tuple[str, dict[str, Any]] | None:
            team_id, team_name = team_input
            try:
                payload = self.client.get(
                    f"/{path}/teams/{team_id}/roster",
                    {"season": _nfl_season_year(today)},
                    retries=2,
                )
                return team_name, payload
            except Exception:
                return None

        # Fetch current rosters before the larger historical-summary sweep so
        # roster verification remains reliable if ESPN begins throttling the
        # runner later in the refresh.
        with ThreadPoolExecutor(max_workers=4) as pool:
            roster_payloads = [
                payload for payload in pool.map(fetch_roster, roster_targets) if payload
            ]
        current_roster = _roster_info(roster_payloads)
        verified_roster_teams = {
            _norm(team_name)
            for team_name, payload in roster_payloads
            if any(group.get("items") for group in payload.get("athletes") or [])
        }
        completed = [
            event
            for event in recent_events
            if ((event.get("status") or {}).get("type") or {}).get("completed")
            and _season_type(event) == 2
        ]
        completed.sort(key=lambda event: str(event.get("date") or ""), reverse=True)
        per_team_limit = int(fetch["espn_games_per_team"])
        hard_limit = int(fetch["espn_max_completed_games"])
        team_counts: dict[str, int] = defaultdict(int)
        selected: list[dict[str, Any]] = []
        for event in completed:
            teams = _event_team_keys(event)
            if not teams or not any(team_counts[team] < per_team_limit for team in teams):
                continue
            selected.append(event)
            for team in teams:
                team_counts[team] += 1
            if len(selected) >= hard_limit or (
                len(team_counts) >= 32 and all(count >= per_team_limit for count in team_counts.values())
            ):
                break
        # Parse oldest to newest so recency weighting is pointed in the right direction.
        event_inputs = [
            (
                str(event["id"]),
                int((event.get("season") or {}).get("year") or str(event.get("date") or "")[:4] or 0),
            )
            for event in reversed(selected)
            if event.get("id")
        ]

        def fetch_summary(event_input: tuple[str, int]) -> dict[str, Any] | None:
            event_id, season_year = event_input
            try:
                summary = self.client.get(f"/{path}/summary", {"event": event_id}, retries=1)
                summary["_props_edge_season_year"] = season_year
                return summary
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            summaries = [summary for summary in pool.map(fetch_summary, event_inputs) if summary]
        return parse_summaries(
            summaries,
            "NFL",
            _team_and_matchups(upcoming_events),
            current_season_year=_nfl_season_year(today),
            defense_weight=float(self.settings["projection_model"].get("defense_weight", 0.40)),
            defense_max_adjustment=float(
                self.settings["projection_model"].get("defense_max_adjustment", 0.12)
            ),
            defense_prior_season_weight=float(
                self.settings["projection_model"].get("defense_prior_season_weight", 0.55)
            ),
            defense_current_season_full_weight_games=int(
                self.settings["projection_model"].get(
                    "defense_current_season_full_weight_games", 4
                )
            ),
            current_roster=current_roster,
            verified_roster_teams=verified_roster_teams,
        )
