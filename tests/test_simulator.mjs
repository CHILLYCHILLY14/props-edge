import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Simulator = require("../site/simulator.js");

const projection = {
  player: "Jordan Example",
  market: "Passing yards",
  matchup: "Buffalo @ Kansas City",
  projection: 282.4,
  base_projection: 276.8,
  standard_deviation: 24.2,
  recent: [250, 270, 281, 295, 260, 300, 289, 285],
  samples: 8,
  current_season_samples: 4,
  opponent_defense_samples: 8,
  opponent_defense_current_samples: 4,
  confidence: 0.65,
};

assert.equal(Simulator.defaultLine(projection), 282.5);
const first = Simulator.run(projection, 275.5, 10000);
const second = Simulator.run(projection, 275.5, 10000);
assert.deepEqual(first, second, "simulations should be deterministic for the same scenario");
assert.equal(first.iterations, 10000);
assert.ok(Math.abs(first.overProbability + first.underProbability + first.pushProbability - 1) < 1e-9);
assert.ok(first.average > 260 && first.average < 300);
assert.ok(first.floor < first.median && first.median < first.ceiling);

const touchdown = Simulator.run({
  ...projection,
  market: "Anytime touchdown",
  projection: 0.42,
  base_projection: 0.4,
  standard_deviation: 0.5,
  recent: [0, 1, 0, 0, 1, 0, 1, 0],
}, 0.5, 10000);
assert.equal(touchdown.pushProbability, 0);
assert.ok(touchdown.overProbability > 0.1 && touchdown.overProbability < 0.6);
assert.ok(Simulator.riskFlags({ ...projection, market: "Anytime touchdown" }).includes("High-variance market"));

console.log("simulator tests passed");
