"""Freeze NFL player forecasts and settle them from final ESPN box scores."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re

from . import model_accuracy as A
from .http import JsonClient, ProviderError
from .model import _market_key, _name_key
from .providers.espn import _canonical_market, _norm, ESPN_URL


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


def update(root, board, projections, errors, client=None):
    path = root / "state" / "model_accuracy.json"
    log = A.load(path)
    rows = []
    for p in projections:
        if not p.event_id or not p.start_time:
            continue
        rows.append({"kind": "prop", "league": "NFL", "event_id": p.event_id,
                     "start": p.start_time, "matchup": p.matchup, "player": p.player,
                     "market": p.market, "projection": p.projection,
                     "stat_key": stat_key(p.player, p.team, p.market)})
    for b in board:
        if not b.get("result_event_id") or b.get("model_prob_no_push") is None:
            continue
        rows.append({"kind": "call", "league": "NFL", "event_id": b["result_event_id"],
                     "start": b["start_time"], "matchup": b["matchup"], "player": b["player"],
                     "market": b["market"], "side": b["side"], "line": b.get("line"),
                     "price": b["price_american"], "probability": b["model_prob_no_push"],
                     "tier": b.get("model_tier", b["tier"]), "edge": b.get("edge_real"),
                     "pick": b["pick"], "book": b["book"],
                     "stat_key": stat_key(b["player"], b["result_team"], b["market"])})
    A.record(log, rows)
    now = datetime.now(timezone.utc)
    ids = sorted({r["event_id"] for r in log["records"].values()
                  if r["result"] == "Pending" and A.instant(r["start"]) <= now})
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
    A.save(root / "site/data/accuracy.json", A.report(log, "NFL prop prediction accuracy"))
