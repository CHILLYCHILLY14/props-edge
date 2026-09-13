/*
 * sync-adapter.js — Props Edge's half of the shared ledger.
 *
 * Player props already carry the status the sheet wants (Pending / Win / Loss /
 * Push / Void), so the translation here is mostly a rename. Profit is left for
 * the sheet to derive from stake and price, which keeps one formula in one place
 * rather than five that can drift.
 */
(function () {
  "use strict";
  if (!window.BetSync) return;
  var BS = window.BetSync;
  var APP = "props", SPORT = "NFL";
  var L = window.NFLPropsLedger;
  var STK = window.PropsEdgeStaking;
  var SETTINGS_KEY = "nfl-props-edge-settings-v2";
  if (!L) return;

  function toCanonical(item) {
    return {
      id: APP + ":" + item.id,
      app: APP, sport: SPORT,
      placed_at: item.added_at,
      event_date: String(item.start_time || "").slice(0, 10),
      event: item.matchup,
      market: item.market,
      selection: item.pick,
      side: item.bet_type,
      line: null,
      price: item.price_american,
      book: item.book,
      stake: item.stake,
      tier: item.tier,
      edge: item.edge,
      model_prob: null,
      status: item.status || "Pending",
      pnl: null,
      closing_price: item.closing_odds === "" ? null : item.closing_odds,
      score: "",
      notes: item.notes,
      native: item
    };
  }

  function fromCanonical(row) {
    var item = row.native ? JSON.parse(JSON.stringify(row.native)) : {
      id: String(row.id).split(":").slice(1).join(":"),
      added_at: row.placed_at, start_time: row.event_date,
      matchup: row.event, market: row.market, pick: row.selection,
      bet_type: row.side, book: row.book, tier: row.tier, edge: row.edge
    };
    item.price_american = row.price;
    if (row.stake != null) item.stake = row.stake;
    item.status = row.status || "Pending";
    item.closing_odds = row.closing_price == null ? "" : row.closing_price;
    item.notes = row.notes || "";
    return item;
  }

  function fingerprint(rows) {
    return JSON.stringify((rows || []).map(function (r) {
      return [r.id, r.stake, r.status, r.closing_odds, r.notes];
    }).sort());
  }

  function rerender() {
    try { if (typeof state !== "undefined") state.ledger = L.load(localStorage); } catch (_) {}
    if (typeof window.render === "function") try { window.render(); } catch (_) {}
  }

  BS.register({
    app: APP,
    sport: SPORT,
    storageKey: L.STORAGE_KEY,

    readLocal: function () {
      return (L.load(localStorage) || []).map(toCanonical);
    },

    writeLocal: function (rows) {
      var next = rows.map(fromCanonical);
      var before = fingerprint(L.load(localStorage));
      L.save(localStorage, next);
      if (before !== fingerprint(next)) rerender();
    },

    applyBankroll: function (bank) {
      if (!STK || !bank || bank.current == null) return;
      try {
        var settings = STK.load(SETTINGS_KEY);
        if (Math.abs(Number(settings.bankroll) - bank.current) < 0.005) return;
        settings.bankroll = bank.current;
        STK.save(SETTINGS_KEY, settings);
        if (typeof state !== "undefined") {
          state.staking = settings;
          state.bankroll = Math.max(1, Number(settings.bankroll) || 500);
          var input = document.getElementById("bankrollInput");
          if (input) input.value = state.bankroll;
        }
        rerender();
      } catch (_) {}
    }
  });

  BS.start();
})();
