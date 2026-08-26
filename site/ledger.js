(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.NFLPropsLedger = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STORAGE_KEY = "nfl-props-edge-ledger-v2";
  const LEGACY_KEY = "props-edge-ledger-v1";
  const STATUS = new Set(["Pending", "Win", "Loss", "Push", "Void"]);

  function decimalOdds(price) {
    const value = Number(price);
    if (!Number.isFinite(value) || value === 0) return null;
    return value > 0 ? 1 + value / 100 : 1 + 100 / Math.abs(value);
  }

  function keyFor(row) {
    return [
      "NFL",
      row.event_id,
      row.book,
      row.player,
      row.market,
      row.side,
      row.line,
      row.start_time,
    ].map((value) => String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "")).join("-");
  }

  function normalize(item) {
    const price = Number(item.price_american);
    const stake = Math.max(0, Number(item.stake) || 0);
    return {
      id: String(item.id || keyFor(item)),
      added_at: String(item.added_at || new Date().toISOString()),
      start_time: String(item.start_time || ""),
      sport: "NFL",
      event_id: String(item.event_id || ""),
      tier: String(item.tier || "LEAN"),
      market: String(item.market || "Player prop"),
      bet_type: String(item.bet_type || "OTHER"),
      pick: String(item.pick || ""),
      matchup: String(item.matchup || ""),
      book: String(item.book || ""),
      price_american: Number.isFinite(price) ? price : 0,
      edge: Number(item.edge) || 0,
      edge_real: Number(item.edge_real ?? item.ev) || 0,
      confidence: Number(item.confidence) || 0,
      stake,
      status: STATUS.has(item.status) ? item.status : "Pending",
      closing_odds: item.closing_odds === "" ? "" : Number(item.closing_odds) || "",
      notes: String(item.notes || ""),
    };
  }

  function load(storage) {
    if (!storage) return [];
    try {
      const current = JSON.parse(storage.getItem(STORAGE_KEY) || "null");
      if (Array.isArray(current)) return current.map(normalize);
      const legacy = JSON.parse(storage.getItem(LEGACY_KEY) || "[]");
      const nflOnly = Array.isArray(legacy)
        ? legacy.filter((item) => String(item.sport || "").toUpperCase() === "NFL").map(normalize)
        : [];
      if (nflOnly.length) storage.setItem(STORAGE_KEY, JSON.stringify(nflOnly));
      return nflOnly;
    } catch {
      return [];
    }
  }

  function save(storage, rows) {
    if (!storage) return;
    storage.setItem(STORAGE_KEY, JSON.stringify(rows.map(normalize)));
  }

  function add(rows, row, stake) {
    const id = keyFor(row);
    if (rows.some((item) => item.id === id)) return { rows, added: false, reason: "duplicate" };
    const acceptedStake = Math.max(0, Number(stake) || 0);
    if (!acceptedStake) return { rows, added: false, reason: "stake" };
    const entry = normalize({
      ...row,
      id,
      added_at: new Date().toISOString(),
      stake: acceptedStake,
      status: "Pending",
      closing_odds: "",
      notes: "",
    });
    return { rows: [entry, ...rows], added: true, entry };
  }

  function update(rows, id, patch) {
    return rows.map((item) => item.id === id ? normalize({ ...item, ...patch }) : item);
  }

  function remove(rows, id) {
    return rows.filter((item) => item.id !== id);
  }

  function profit(item) {
    const stake = Math.max(0, Number(item.stake) || 0);
    if (item.status === "Loss") return -stake;
    if (item.status !== "Win") return 0;
    const decimal = decimalOdds(item.price_american);
    return decimal ? Math.round(stake * (decimal - 1) * 100) / 100 : 0;
  }

  function clv(item) {
    const placed = decimalOdds(item.price_american);
    const closing = decimalOdds(item.closing_odds);
    return placed && closing ? placed / closing - 1 : null;
  }

  function summary(rows, bankroll) {
    const starting = Math.max(0, Number(bankroll) || 0);
    const pnl = rows.reduce((total, item) => total + profit(item), 0);
    const pending = rows.filter((item) => item.status === "Pending");
    const decisions = rows.filter((item) => item.status === "Win" || item.status === "Loss");
    const exposure = pending.reduce((total, item) => total + Math.max(0, Number(item.stake) || 0), 0);
    const settledRisk = decisions.reduce((total, item) => total + Math.max(0, Number(item.stake) || 0), 0);
    return {
      bets: rows.length,
      pending: pending.length,
      settled: rows.length - pending.length,
      wins: decisions.filter((item) => item.status === "Win").length,
      losses: decisions.filter((item) => item.status === "Loss").length,
      exposure,
      pnl,
      bankroll: starting + pnl,
      available: starting + pnl - exposure,
      roi: settledRisk ? pnl / settledRisk : 0,
    };
  }

  function csvCell(value) {
    return `"${String(value ?? "").replaceAll('"', '""')}"`;
  }

  function toCsv(rows) {
    const headers = [
      "date", "tier", "market", "pick", "matchup", "book", "american_odds",
      "edge", "price_value", "confidence", "stake", "status", "closing_odds",
      "clv", "profit_loss", "notes",
    ];
    const body = rows.map((item) => [
      item.start_time, item.tier, item.market, item.pick, item.matchup, item.book,
      item.price_american, item.edge, item.edge_real, item.confidence, item.stake,
      item.status, item.closing_odds, clv(item), profit(item), item.notes,
    ]);
    return [headers, ...body].map((row) => row.map(csvCell).join(",")).join("\n");
  }

  function toJson(rows) {
    return JSON.stringify({ version: 2, league: "NFL", exported_at: new Date().toISOString(), bets: rows.map(normalize) }, null, 2);
  }

  function fromJson(text) {
    const parsed = JSON.parse(text);
    const rows = Array.isArray(parsed) ? parsed : parsed.bets;
    if (!Array.isArray(rows)) throw new Error("Ledger JSON must contain a bets array.");
    const unique = [];
    const seen = new Set();
    for (const item of rows.map(normalize)) {
      if (!item.id || seen.has(item.id)) continue;
      seen.add(item.id);
      unique.push(item);
    }
    return unique;
  }

  return {
    STORAGE_KEY,
    keyFor,
    load,
    save,
    add,
    update,
    remove,
    profit,
    clv,
    summary,
    toCsv,
    toJson,
    fromJson,
  };
});
