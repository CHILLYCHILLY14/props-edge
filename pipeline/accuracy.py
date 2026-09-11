"""Freeze NFL player forecasts and settle them from final ESPN box scores."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re

from . import model_accuracy as A
from .http import JsonClient, ProviderError
from .model import _market_key, _name_key
from .providers.espn import _canonical_market, _nfl_season_year, _norm, ESPN_URL


PROJECTION_HISTORY_FIELDS = {
    "actual",
    "captured_at",
    "event_id",
    "graded_at",
    "id",
    "kind",
    "league",
    "market",
    "matchup",
    "player",
    "projection",
    "result",
    "season",
    "season_type",
    "start",
    "stat_key",
    "version",
}


def stat_key(player, team, market):
    return "|".join((_name_key(player), _norm(team), _market_key(market)))


def final_stats(summary):
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    status = comp.get("status") or {}
    if "CANCEL" in str((status.get("type") or {}).get("name") or "").upper():
        return {"canceled": True}
    if not (status.get("type") or {}).get("completed"):
        return {}
    observed = {}
    for team in (summary.get("boxscore") or {}).get("players") or []:
        team_name = (team.get("team") or {}).get("displayName") or ""
        for group in team.get("statistics") or []:
            group_name = group.get("type") or group.get("name") or group.get("displayName") or ""
            names = group.get("keys") or group.get("names") or group.get("labels") or []
            for athlete in group.get("athletes") or []:
                player = (athlete.get("athlete") or {}).get("displayName")
                if not player or athlete.get("didNotPlay") or athlete.get("inactive"):
                    continue
                values = observed.setdefault((player, team_name), {})
                for name, raw in zip(names, athlete.get("stats") or []):
                    g, n = _norm(group_name), _norm(str(name))
                    pair = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*", str(raw))
                    if pair and "passing" in g and "completion" in n and "attempt" in n:
                        values.update({"Pass completions": float(pair[1]), "Pass attempts": float(pair[2])})
                    elif pair and "kicking" in g and ("fieldgoal" in n or n == "fg"):
                        values.update({"Field goals made": float(pair[1]), "Field goal attempts": float(pair[2])})
                    elif pair and "kicking" in g and ("extrapoint" in n or n.startswith("xp")):
                        values.update({"Extra points made": float(pair[1]), "Extra point attempts": float(pair[2])})
                    else:
                        market = _canonical_market(group_name, str(name))
                        value = A.number(str(raw).replace(",", ""))
                        if market and value is not None:
                            values[market] = value  # negative yardage is a real result
                        if "passing" not in g and ("touchdown" in n or n == "td") and value is not None:
                            values["_td_"+g] = value
    stats = {}
    for (player, team), values in observed.items():
        if not values:
            continue
        rt = values.get("Rushing touchdowns", 0)+values.get("Receiving touchdowns", 0)
        if any(k.startswith("_td_") for k in values):
            td = sum(v for k,v in values.items() if k.startswith("_td_"))
            values.update({"Anytime touchdown": td, "Touchdowns scored": td})
        if "Rushing touchdowns" in values or "Receiving touchdowns" in values:
            values["Rush + receiving touchdowns"] = rt
        if "Passing touchdowns" in values:
            values["Pass + rush + receiving touchdowns"] = values["Passing touchdowns"]+rt
        if "Passing yards" in values:
            values["Pass + rush yards"] = values["Passing yards"]+values.get("Rushing yards", 0)
            values["Pass + rush + receiving yards"] = values["Pass + rush yards"]+values.get("Receiving yards", 0)
        if "Rushing yards" in values or "Receiving yards" in values:
            values["Rush + receiving yards"] = values.get("Rushing yards", 0)+values.get("Receiving yards", 0)
        if "Kicking points" not in values and ("Field goals made" in values or "Extra points made" in values):
            values["Kicking points"] = 3*values.get("Field goals made", 0)+values.get("Extra points made", 0)
        stats.update({stat_key(player, team, k): v for k,v in values.items() if not k.startswith("_")})
    return {"completed": True, "stats": stats}


def current_projection_log(log, season, season_type=2):
    """Keep only the current-season player-forecast audit.

    Sportsbook calls, prices, stake units, and ledger-like fields do not belong
    in the durable/public accuracy history. Rebuilding each retained row from an
    allowlist also keeps that boundary intact if an older file contains extras.
    """
    records = {}
    for source in log.get("records", {}).values():
        if source.get("kind") != "prop":
            continue
        if str(source.get("season")) != str(season):
            continue
        if str(source.get("season_type")) != str(season_type):
            continue
        row = {key: value for key, value in source.items()
               if key in PROJECTION_HISTORY_FIELDS}
        row.update(kind="prop", league="NFL", season=season,
                   season_type=season_type)
        row["id"] = A.key(row)
        records[row["id"]] = row
    return {"schema": 1, "projection_only": True, "records": records}


def migrate_history(root):
    """Upgrade an existing cached history without fetching or changing forecasts."""
    path = root / "state" / "model_accuracy.json"
    log = A.load(path)
    now = datetime.now(timezone.utc)
    season = _nfl_season_year(now.date())
    # Props Edge deliberately excludes preseason. Older frozen rows predate the
    # scope fields, so add only schedule metadata; never rewrite their forecast,
    # line, price, or result.
    for saved in log.get("records", {}).values():
        start = A.instant(saved.get("start"))
        if saved.get("season") is None and start is not None:
            saved["season"] = _nfl_season_year(start.date())
        if saved.get("season_type") is None:
            saved["season_type"] = 2
    log = current_projection_log(log, season)
    A.save(path, log)
    A.save(root / "site/data/accuracy.json",
           A.report(log, "NFL player projection accuracy", season=season,
                    season_type=2, projection_only=True, record_limit=1000))
    return log, season


def update(root, board, projections, errors, client=None):
    path = root / "state" / "model_accuracy.json"
    log, season = migrate_history(root)
    now = datetime.now(timezone.utc)
    rows = []
    for p in projections:
        if not p.event_id or not p.start_time:
            continue
        rows.append({"kind": "prop", "league": "NFL", "event_id": p.event_id,
                     "start": p.start_time, "matchup": p.matchup, "player": p.player,
                     "season": season, "season_type": 2,
                     "market": p.market, "projection": p.projection,
                     "stat_key": stat_key(p.player, p.team, p.market)})
    A.record(log, rows)
    ids = sorted({r["event_id"] for r in log["records"].values()
                  if r["result"] == "Pending"
                  and A.instant(r["start"]) is not None
                  and A.instant(r["start"]) <= now})
    client = client or JsonClient("ESPN results", ESPN_URL, timeout=18)
    def fetch(event):
        try:
            return event, final_stats(client.get("/football/nfl/summary", {"event": event}, retries=1))
        except ProviderError:
            return event, None
    with ThreadPoolExecutor(max_workers=6) as pool:
        result_rows = list(pool.map(fetch, ids))
    failed = sum(value is None for _, value in result_rows)
    if failed:
        errors.append(f"Accuracy: {failed} final box-score requests unavailable; saved predictions retained")
    A.settle(log, {event: value for event,value in result_rows if value})
    A.save(path, log)
    A.save(root / "site/data/accuracy.json",
           A.report(log, "NFL player projection accuracy", season=season,
                    season_type=2, projection_only=True, record_limit=1000))


if __name__ == "__main__":
    from pathlib import Path
    saved, season = migrate_history(Path(__file__).resolve().parents[1])
    print(f"Prepared {len(saved['records'])} frozen {season} player projections")
