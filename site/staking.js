/* Browser-local bankroll and staking controls. Model probabilities never change here. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PropsEdgeStaking = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SCHEMA = 1;
  const SYSTEMS = new Set(["kelly", "flat", "percent"]);
  const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, finite(value, low)));

  function normalise(input, defaults) {
    const base = Object.assign({
      bankroll: 500,
      system: "kelly",
      kelly_fraction: 0.15,
      bankroll_pct: 0.01,
      flat_stake: 5,
      max_stake_pct: 0.10,
      max_slate_exposure_pct: 0.35,
      min_stake: 1,
      round_to: 0.5,
    }, defaults || {});
    const settings = Object.assign({}, base, input || {});
    return {
      bankroll: clamp(settings.bankroll, 1, 1000000000),
      system: SYSTEMS.has(settings.system) ? settings.system : base.system,
      kelly_fraction: clamp(settings.kelly_fraction, 0, 1),
      bankroll_pct: clamp(settings.bankroll_pct, 0, 1),
      flat_stake: clamp(settings.flat_stake, 0, 1000000000),
      max_stake_pct: clamp(settings.max_stake_pct, 0, 1),
      max_slate_exposure_pct: clamp(settings.max_slate_exposure_pct, 0, 1),
      min_stake: clamp(settings.min_stake, 0, 1000000000),
      round_to: clamp(settings.round_to, 0.01, 1000000),
    };
  }

  function load(key, defaults) {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "null");
      // Migrate the earlier {bankroll} setting without losing the user's value.
      return normalise(saved && (saved.settings || saved), defaults);
    } catch (_) {
      return normalise(null, defaults);
    }
  }

  function save(key, settings) {
    try {
      localStorage.setItem(key, JSON.stringify({
        schema: SCHEMA,
        saved_at: new Date().toISOString(),
        settings: normalise(settings),
      }));
      return true;
    } catch (_) {
      return false;
    }
  }

  function clear(key) {
    try { localStorage.removeItem(key); return true; } catch (_) { return false; }
  }

  function decimal(american) {
    const price = Number(american);
    if (!Number.isFinite(price) || price === 0) return null;
    return 1 + (price > 0 ? price / 100 : 100 / Math.abs(price));
  }

  function suggestedStake(row, settings, bankroll, maxModelProbability) {
    const safe = normalise(settings);
    const roll = Math.max(0, finite(bankroll, safe.bankroll));
    if (!row || row.tier === "PASS" || row.held || roll <= 0) return 0;

    let raw = 0;
    if (safe.system === "flat") {
      raw = safe.flat_stake;
    } else if (safe.system === "percent") {
      raw = roll * safe.bankroll_pct;
    } else {
      const price = decimal(row.price_american ?? row.price);
      if (!price) return 0;
      const active = 1 - clamp(row.push_prob || 0, 0, 1);
      if (active <= 0) return 0;
      const actionEdge = row.action_edge != null
        ? finite(row.action_edge, 0)
        : Math.min(finite(row.edge, 0), finite(row.edge_real ?? row.ev, 0));
      const probability = clamp((1 + actionEdge / active) / price, 0, finite(maxModelProbability, 0.85));
      const fullKelly = Math.max(0, ((probability * (price - 1)) - (1 - probability)) / (price - 1));
      const sampleMaturity = Math.min(1, Math.max(0, finite(row.projection_samples, 0)) / 8);
      const reliability = Math.max(0.25,
        clamp(row.confidence, 0, 1)
        * clamp(row.season_maturity == null ? 1 : row.season_maturity, 0, 1)
        * clamp(row.market_reliability == null ? 1 : row.market_reliability, 0, 1));
      raw = roll * fullKelly * safe.kelly_fraction * reliability * sampleMaturity * sampleMaturity;
    }

    raw = Math.min(raw, roll * safe.max_stake_pct);
    const capSteps = Math.floor((roll * safe.max_stake_pct + 1e-9) / safe.round_to);
    const rounded = Math.min(Math.round(raw / safe.round_to), capSteps) * safe.round_to;
    return rounded + 1e-9 < safe.min_stake ? 0 : Math.round(rounded * 100) / 100;
  }

  function plan(rows, settings, bankroll, maxModelProbability) {
    const safe = normalise(settings);
    const roll = Math.max(0, finite(bankroll, safe.bankroll));
    const stakes = (rows || []).map(row => suggestedStake(
      row, safe, roll, maxModelProbability,
    ));
    const total = stakes.reduce((sum, value) => sum + value, 0);
    const cap = roll * safe.max_slate_exposure_pct;
    if (total <= cap + 1e-9) return stakes;
    const scale = total ? cap / total : 0;
    return stakes.map(value => {
      const adjusted = Math.floor((value * scale + 1e-9) / safe.round_to) * safe.round_to;
      return adjusted + 1e-9 < safe.min_stake ? 0 : Math.round(adjusted * 100) / 100;
    });
  }

  function description(settings) {
    const safe = normalise(settings);
    if (safe.system === "flat") return `Flat C$${safe.flat_stake.toFixed(2)}`;
    if (safe.system === "percent") return `${(safe.bankroll_pct * 100).toFixed(1)}% bankroll`;
    return `${(safe.kelly_fraction * 100).toFixed(0)}% Kelly`;
  }

  return { SCHEMA, normalise, load, save, clear, decimal, suggestedStake, plan, description };
});
