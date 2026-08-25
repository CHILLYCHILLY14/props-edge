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


def _team_and_matchups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    answer: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        home = next((row for row in competitors if row.get("homeAway") == "home"), {})
        away = next((row for row in competitors if row.get("homeAway") == "away"), {})
        home_name = str((home.get("team") or {}).get("displayName") or "Home")
        away_name = str((away.get("team") or {}).get("displayName") or "Away")
        matchup = f"{away_name} @ {home_name}"
        event_info = {
            "matchup": matchup,
            "start_time": str(event.get("date") or competition.get("date") or ""),
        }
        for team_name in (home_name, away_name):
            team_key = _norm(team_name)
            if event_info not in answer[team_key]:
                answer[team_key].append(event_info)
    return dict(answer)


def _canonical_market(sport: str, group: str, name: str) -> str | None:
    group_key = _norm(group)
    stat = _norm(name)
    if sport in ("NFL", "NCAAF"):
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
    elif sport == "MLB":
        if "bat" in group_key:
            mapping = {
                "hits": "Batter hits",
                "runs": "Batter runs",
                "runsbattedin": "Batter RBIs",
                "rbis": "Batter RBIs",
                "walks": "Batter walks",
                "baseonballs": "Batter walks",
                "strikeouts": "Batter strikeouts",
                "totalbases": "Batter total bases",
                "homeruns": "Batter home runs",
                "doubles": "Batter doubles",
                "triples": "Batter triples",
                "stolenbases": "Batter stolen bases",
            }
            return mapping.get(stat)
        if "pitch" in group_key:
            mapping = {
                "strikeouts": "Pitcher strikeouts",
                "walks": "Pitcher walks",
                "baseonballs": "Pitcher walks",
                "earnedruns": "Pitcher earned runs",
                "hits": "Pitcher hits allowed",
                "outs": "Pitcher outs",
            }
            return mapping.get(stat)
    elif sport == "WNBA":
        mapping = {
            "points": "Points",
            "totalrebounds": "Rebounds",
            "rebounds": "Rebounds",
            "assists": "Assists",
            "threepointfieldgoalsmade": "Threes",
            "threepointsmade": "Threes",
            "steals": "Steals",
            "blocks": "Blocks",
            "turnovers": "Turnovers",
        }
        return mapping.get(stat)
    return None


def parse_summaries(
    summaries: list[dict[str, Any]], sport: str, upcoming_matchups: dict[str, Any]
) -> list[Projection]:
    history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    display: dict[tuple[str, str, str], tuple[str, str]] = {}
    for summary in summaries:
        game_observed: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"player": "", "team": "", "markets": {}}
        )
        for team_block in ((summary.get("boxscore") or {}).get("players") or []):
            team = str((team_block.get("team") or {}).get("displayName") or "")
            for stat_group in team_block.get("statistics") or []:
                group = str(stat_group.get("type") or stat_group.get("name") or stat_group.get("displayName") or "")
                names = stat_group.get("keys") or stat_group.get("names") or stat_group.get("labels") or []
                for athlete_row in stat_group.get("athletes") or []:
                    player = str((athlete_row.get("athlete") or {}).get("displayName") or "")
                    if not player:
                        continue
                    observed: dict[str, float] = {}
                    for name, raw in zip(names, athlete_row.get("stats") or []):
                        stat_key = _norm(str(name))
                        group_key = _norm(group)
                        combined_match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$", str(raw))
                        if (
                            sport in ("NFL", "NCAAF")
                            and "passing" in group_key
                            and "completion" in stat_key
                            and "attempt" in stat_key
                            and combined_match
                        ):
                            game_key = (player.casefold(), _norm(team))
                            for market, value in (
                                ("Pass completions", float(combined_match.group(1))),
                                ("Pass attempts", float(combined_match.group(2))),
                            ):
                                observed[market] = value
                                game_observed[game_key]["player"] = player
                                game_observed[game_key]["team"] = team
                                game_observed[game_key]["markets"][market] = value
                                key = (player.casefold(), _norm(team), market)
                                history[key].append(value)
                                display[key] = (player, team)
                            continue
                        market = _canonical_market(sport, group, str(name))
                        value = as_float(raw)
                        if market is None or value is None or value < 0:
                            continue
                        observed[market] = value
                        game_key = (player.casefold(), _norm(team))
                        game_observed[game_key]["player"] = player
                        game_observed[game_key]["team"] = team
                        game_observed[game_key]["markets"][market] = value
                        key = (player.casefold(), _norm(team), market)
                        history[key].append(value)
                        display[key] = (player, team)
                    combinations = []
                    if sport == "MLB":
                        if all(
                            component in observed
                            for component in ("Batter hits", "Batter doubles", "Batter triples", "Batter home runs")
                        ):
                            singles = max(
                                0.0,
                                observed["Batter hits"]
                                - observed["Batter doubles"]
                                - observed["Batter triples"]
                                - observed["Batter home runs"],
                            )
                            singles_key = (player.casefold(), _norm(team), "Batter singles")
                            history[singles_key].append(singles)
                            display[singles_key] = (player, team)
                        combinations = [
                            ("Batter hits + runs + RBIs", ("Batter hits", "Batter runs", "Batter RBIs")),
                        ]
                    elif sport == "WNBA":
                        combinations = [
                            ("Points + Rebounds", ("Points", "Rebounds")),
                            ("Points + Assists", ("Points", "Assists")),
                            ("Points + Rebounds + Assists", ("Points", "Rebounds", "Assists")),
                            ("Rebounds + Assists", ("Rebounds", "Assists")),
                        ]
                    for combined_market, components in combinations:
                        if not all(component in observed for component in components):
                            continue
                        combined_value = sum(observed[component] for component in components)
                        combined_key = (player.casefold(), _norm(team), combined_market)
                        history[combined_key].append(combined_value)
                        display[combined_key] = (player, team)

        if sport in ("NFL", "NCAAF"):
            for (player_key, team_key), info in game_observed.items():
                markets = info["markets"]
                touchdown_markets = ("Rushing touchdowns", "Receiving touchdowns")
                if not any(market in markets for market in touchdown_markets):
                    continue
                total_touchdowns = sum(markets.get(market, 0.0) for market in touchdown_markets)
                combined_key = (player_key, team_key, "Anytime touchdown")
                history[combined_key].append(total_touchdowns)
                display[combined_key] = (info["player"], info["team"])

    projections: list[Projection] = []
    for key, values in history.items():
        player, team = display[key]
        team_key = key[1]
        if upcoming_matchups and team_key not in upcoming_matchups:
            continue
        recent = values[-8:]
        if len(recent) < 2:
            continue
        weights = list(range(1, len(recent) + 1))
        projection = sum(value * weight for value, weight in zip(recent, weights)) / sum(weights)
        deviation = statistics.pstdev(recent) if len(recent) > 1 else 0.0
        simple_average = statistics.mean(recent)
        raw_matchups = upcoming_matchups.get(team_key) if upcoming_matchups else None
        if isinstance(raw_matchups, str):
            matchup_rows = [{"matchup": raw_matchups, "start_time": ""}]
        elif isinstance(raw_matchups, dict):
            matchup_rows = [raw_matchups]
        elif isinstance(raw_matchups, list):
            matchup_rows = raw_matchups
        else:
            matchup_rows = [{"matchup": "Next matchup not posted", "start_time": ""}]
        for matchup_info in matchup_rows:
            projections.append(
                Projection(
                    sport=sport,
                    player=player,
                    team=team,
                    matchup=str(matchup_info.get("matchup") or "Next matchup not posted"),
                    market=key[2],
                    projection=round(projection, 2),
                    samples=len(recent),
                    confidence=round(min(0.72, 0.28 + 0.07 * len(recent)), 2),
                    standard_deviation=round(deviation, 2),
                    recent=[round(value, 2) for value in recent],
                    trend=round(projection - simple_average, 2),
                    start_time=str(matchup_info.get("start_time") or ""),
                )
            )
    return sorted(
        projections,
        key=lambda row: (row.start_time, -row.confidence, -row.samples, row.player, row.market),
    )


class EspnProjectionProvider:
    name = "ESPN public statistics"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.client = JsonClient(self.name, ESPN_URL, timeout=18)

    def fetch(self, sport: str) -> list[Projection]:
        cfg = self.settings["sports"][sport]
        path = cfg["espn_path"]
        today = dt.datetime.now(dt.timezone.utc).date()
        lookback = int(self.settings["fetch"]["espn_lookback_days"])
        lookahead = int(self.settings["fetch"]["lookahead_days"])
        recent = self.client.get(
            f"/{path}/scoreboard",
            {"dates": _date_range(today - dt.timedelta(days=lookback), today), "limit": 500},
        )
        upcoming = self.client.get(
            f"/{path}/scoreboard",
            {"dates": _date_range(today, today + dt.timedelta(days=lookahead)), "limit": 500},
        )
        completed = [
            event
            for event in recent.get("events") or []
            if ((event.get("status") or {}).get("type") or {}).get("completed")
        ]
        completed.sort(key=lambda event: str(event.get("date") or ""), reverse=True)
        max_games = int(self.settings["fetch"]["espn_max_completed_games"])
        event_ids = [event["id"] for event in completed[:max_games] if event.get("id")]
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
        ]
        matchup_map = _team_and_matchups(upcoming_events)
        return parse_summaries(summaries, sport, matchup_map)
