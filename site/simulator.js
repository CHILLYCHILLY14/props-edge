(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NFLPropsSimulator = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const COUNT_MARKETS = [
    "touchdown", "interception", "field goal", "extra point", "sack",
    "reception", "target", "attempt", "completion", "tackle",
  ];

  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  function isCountMarket(market) {
    const value = String(market || "").toLowerCase();
    return COUNT_MARKETS.some((token) => value.includes(token)) && !value.includes("yards") && !value.includes("longest");
  }

  function isAnytimeTouchdown(market) {
    return String(market || "").toLowerCase().includes("anytime touchdown");
  }

  function defaultLine(row) {
    const mean = Math.max(0, number(row?.projection));
    if (isAnytimeTouchdown(row?.market)) return 0.5;
    return Math.max(0.5, Math.floor(mean) + 0.5);
  }

  function seedFrom(value) {
    let seed = 2166136261;
    for (const char of String(value)) {
      seed ^= char.charCodeAt(0);
      seed = Math.imul(seed, 16777619);
    }
    return seed >>> 0 || 1;
  }

  function randomFactory(seed) {
    let value = seed >>> 0;
    return function () {
      value += 0x6D2B79F5;
      let result = value;
      result = Math.imul(result ^ (result >>> 15), result | 1);
      result ^= result + Math.imul(result ^ (result >>> 7), result | 61);
      return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
    };
  }

  function normal(random) {
    const first = Math.max(1e-12, random());
    const second = random();
    return Math.sqrt(-2 * Math.log(first)) * Math.cos(2 * Math.PI * second);
  }

  function poisson(mean, random) {
    const lambda = Math.max(0.0001, mean);
    if (lambda > 30) return Math.max(0, Math.round(lambda + Math.sqrt(lambda) * normal(random)));
    const limit = Math.exp(-lambda);
    let product = 1;
    let count = 0;
    do {
      count += 1;
      product *= random();
    } while (product > limit && count < 100);
    return count - 1;
  }

  function deviationFloor(row, mean) {
    const market = String(row?.market || "").toLowerCase();
    if (market.includes("yards") || market.includes("longest")) return Math.max(5, mean * 0.12);
    if (isCountMarket(market)) return 0.65;
    return 0.75;
  }

  function quantile(sorted, percentile) {
    if (!sorted.length) return 0;
    const index = (sorted.length - 1) * percentile;
    const low = Math.floor(index);
    const high = Math.ceil(index);
    if (low === high) return sorted[low];
    return sorted[low] + (sorted[high] - sorted[low]) * (index - low);
  }

  function fairAmerican(probability) {
    const probabilitySafe = clamp(number(probability, 0.5), 0.01, 0.99);
    if (probabilitySafe >= 0.5) return Math.round(-100 * probabilitySafe / (1 - probabilitySafe));
    return Math.round(100 * (1 - probabilitySafe) / probabilitySafe);
  }

  function riskFlags(row) {
    const flags = [];
    if (number(row?.current_season_samples) === 0) flags.push("Prior-season player form");
    if (number(row?.opponent_defense_current_samples) === 0) flags.push("Prior-season defense data");
    if (number(row?.opponent_defense_samples) < 6) flags.push("Thin opponent sample");
    if (number(row?.confidence) < 0.52) flags.push("Lower projection confidence");
    if (String(row?.injury_status || "").trim()) flags.push(`Roster status: ${row.injury_status}`);
    if (row?.roster_verified === false) flags.push("Current roster verification unavailable");
    const market = String(row?.market || "").toLowerCase();
    if (["touchdown", "interception", "field goal", "sack", "longest"].some((token) => market.includes(token))) {
      flags.push("High-variance market");
    }
    return [...new Set(flags)];
  }

  function run(row, requestedLine, iterations = 10000) {
    if (!row || !Number.isFinite(Number(row.projection))) throw new Error("A valid projection is required");
    const count = clamp(Math.round(number(iterations, 10000)), 1000, 50000);
    const mean = Math.max(0, number(row.projection));
    const base = Math.max(0.0001, number(row.base_projection, mean || 0.0001));
    const matchupMultiplier = mean / base;
    const line = Math.max(0, number(requestedLine, defaultLine(row)));
    const recent = (Array.isArray(row.recent) ? row.recent : [])
      .map((value) => Math.max(0, number(value)))
      .filter(Number.isFinite);
    const deviation = Math.max(number(row.standard_deviation), deviationFloor(row, mean));
    const random = randomFactory(seedFrom(`${row.player}|${row.market}|${row.matchup}|${line}|${count}`));
    const results = [];
    let overs = 0;
    let unders = 0;
    let pushes = 0;

    let touchdownProbability = 0;
    if (isAnytimeTouchdown(row.market)) {
      const hits = recent.filter((value) => value >= 1).length;
      const empirical = (hits + 0.75) / (recent.length + 1.5);
      const poissonProbability = 1 - Math.exp(-mean);
      const raw = 0.45 * empirical + 0.55 * poissonProbability;
      const confidence = clamp(number(row.confidence), 0, 0.75);
      const reliability = Math.min(0.8, confidence * (0.6 + 0.4 * Math.min(1, recent.length / 8)));
      touchdownProbability = clamp(0.18 + (raw - 0.18) * reliability, 0.04, 0.75);
    }

    for (let index = 0; index < count; index += 1) {
      let outcome;
      if (isAnytimeTouchdown(row.market)) {
        outcome = random() < touchdownProbability ? 1 : 0;
      } else if (isCountMarket(row.market)) {
        if (recent.length && random() < 0.35) {
          const empirical = recent[Math.floor(random() * recent.length)] * matchupMultiplier;
          const floor = Math.floor(empirical);
          outcome = floor + (random() < empirical - floor ? 1 : 0);
        } else {
          outcome = poisson(mean, random);
        }
      } else if (recent.length && random() < 0.38) {
        const empirical = recent[Math.floor(random() * recent.length)] * matchupMultiplier;
        outcome = Math.max(0, empirical + normal(random) * deviation * 0.08);
      } else {
        outcome = Math.max(0, mean + normal(random) * deviation);
      }
      results.push(outcome);
      if (outcome > line) overs += 1;
      else if (outcome < line) unders += 1;
      else pushes += 1;
    }

    results.sort((a, b) => a - b);
    const average = results.reduce((sum, value) => sum + value, 0) / count;
    return {
      iterations: count,
      line,
      average,
      median: quantile(results, 0.5),
      floor: quantile(results, 0.10),
      ceiling: quantile(results, 0.90),
      overProbability: overs / count,
      underProbability: unders / count,
      pushProbability: pushes / count,
      overFairAmerican: fairAmerican(overs / Math.max(1, overs + unders)),
      underFairAmerican: fairAmerican(unders / Math.max(1, overs + unders)),
      risks: riskFlags(row),
    };
  }

  return { defaultLine, fairAmerican, isCountMarket, riskFlags, run };
});
