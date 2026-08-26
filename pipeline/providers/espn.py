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
        event_info = {
            "matchup": f"{away_name} @ {home_name}",
            "start_time": str(event.get("date") or competition.get("date") or ""),
        }
        for team_name in (home_name, away_name):
            team_key = _norm(team_name)
            if event_info not in answer[team_key]:
                answer[team_key].append(event_info)
    return dict(answer)


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
            "fg": "Field goals made",
            "extrapointsmade": "Extra points made",
            "xp": "Extra points made",
            "points": "Kicking points",
            "kickingpoints": "Kicking points",
        },
        "defensive": {
            "totaltackles": "Tackles + assists",
            "tackles": "Tackles + assists",
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
) -> list[Projection]:
    """Parse completed regular-season NFL box scores into conservative form rows."""
    if sport != "NFL":
        return []
    history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    display: dict[tuple[str, str, str], tuple[str, str]] = {}
    for summary in summaries:
        observed_by_player: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"player": "", "team": "", "markets": {}}
        )
        for team_block in ((summary.get("boxscore") or {}).get("players") or []):
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
                    player = str(
                        (athlete_row.get("athlete") or {}).get("displayName") or ""
                    )
                    if not player:
                        continue
                    game_key = (player.casefold(), _norm(team))
                    for name, raw in zip(names, athlete_row.get("stats") or []):
                        stat_key, group_key = _norm(str(name)), _norm(group)
                        combined = re.match(
                            r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$",
                            str(raw),
                        )
                        if (
                            "passing" in group_key
                            and "completion" in stat_key
                            and "attempt" in stat_key
                            and combined
                        ):
                            values = (
                                ("Pass completions", float(combined.group(1))),
                                ("Pass attempts", float(combined.group(2))),
                            )
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
                            display[key] = (player, team)
        for (player_key, team_key), info in observed_by_player.items():
            markets = info["markets"]
            touchdown_types = ("Rushing touchdowns", "Receiving touchdowns")
            if not any(name in markets for name in touchdown_types):
                continue
            combined_key = (player_key, team_key, "Anytime touchdown")
            history[combined_key].append(sum(markets.get(name, 0.0) for name in touchdown_types))
            display[combined_key] = (info["player"], info["team"])

    projections: list[Projection] = []
    for key, values in history.items():
        player, team = display[key]
        if upcoming_matchups and key[1] not in upcoming_matchups:
            continue
        recent = values[-8:]
        if len(recent) < 2:
            continue
        weights = list(range(1, len(recent) + 1))
        weighted_mean = sum(value * weight for value, weight in zip(recent, weights)) / sum(weights)
        median = statistics.median(recent)
        projection = 0.65 * weighted_mean + 0.35 * median
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
                    source="ESPN regular-season form",
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
        recent = self.client.get(
            f"/{path}/scoreboard",
            {
                "dates": _date_range(
                    today - dt.timedelta(days=int(fetch["espn_lookback_days"])),
                    today,
                ),
                "limit": 1000,
            },
        )
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
        completed = [
            event
            for event in recent.get("events") or []
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
        event_ids = [
            str(event["id"])
            for event in reversed(selected)
            if event.get("id")
        ]

        def fetch_summary(event_id: str) -> dict[str, Any] | None:
            try:
                return self.client.get(f"/{path}/summary", {"event": event_id}, retries=1)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            summaries = [summary for summary in pool.map(fetch_summary, event_ids) if summary]
        upcoming_events = [
            event
            for event in upcoming.get("events") or []
            if not ((event.get("status") or {}).get("type") or {}).get("completed")
            and _season_type(event) == 2
        ]
        return parse_summaries(summaries, "NFL", _team_and_matchups(upcoming_events))
