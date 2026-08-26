import assert from "node:assert/strict";
import ledger from "../site/ledger.js";

const storage = {
  values: new Map(),
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; },
  setItem(key, value) { this.values.set(key, String(value)); },
};

const row = {
  event_id: "401000001",
  start_time: "2026-09-13T17:00:00Z",
  player: "Jordan Example",
  market: "Passing yards",
  side: "over",
  line: 275.5,
  book: "DraftKings",
  price_american: 110,
  tier: "GOOD",
  pick: "Jordan Example — Over 275.5 Passing yards",
  matchup: "Buffalo @ Kansas City",
  edge: 0.052,
  edge_real: 0.041,
  confidence: 0.61,
  bet_type: "PASSING",
};

assert.deepEqual(ledger.load(storage), [], "a new ledger must be empty");
let result = ledger.add([], row, 12.5);
assert.equal(result.added, true, "the explicit add action should create one wager");
assert.equal(result.rows.length, 1);
assert.equal(result.rows[0].stake, 12.5);
assert.equal(result.rows[0].status, "Pending");

const duplicate = ledger.add(result.rows, row, 12.5);
assert.equal(duplicate.added, false, "the same market cannot be added twice");
assert.equal(duplicate.reason, "duplicate");
assert.equal(ledger.add([], row, 0).added, false, "a zero stake cannot be added");

ledger.save(storage, result.rows);
assert.equal(ledger.load(storage).length, 1, "saved entries must persist locally");

let rows = ledger.update(result.rows, result.rows[0].id, { status: "Win" });
assert.equal(ledger.profit(rows[0]), 13.75, "+110 win profit should be stake × 1.10");
let summary = ledger.summary(rows, 500);
assert.equal(summary.pnl, 13.75);
assert.equal(summary.bankroll, 513.75);
assert.equal(summary.pending, 0);
assert.equal(summary.wins, 1);

rows = ledger.update(rows, rows[0].id, { status: "Pending", closing_odds: -110 });
assert.ok(ledger.clv(rows[0]) > 0, "a +110 ticket closing -110 should have positive CLV");
summary = ledger.summary(rows, 500);
assert.equal(summary.exposure, 12.5);
assert.equal(summary.available, 487.5);

const json = ledger.toJson(rows);
assert.equal(ledger.fromJson(json).length, 1, "JSON backup must round-trip");
assert.match(ledger.toCsv(rows), /Jordan Example/);
assert.equal(ledger.remove(rows, rows[0].id).length, 0);

storage.setItem("props-edge-ledger-v1", JSON.stringify([
  { ...row, id: "old-nfl", sport: "NFL", stake: 10 },
  { ...row, id: "old-other", sport: "OTHER", stake: 10 },
]));
storage.values.delete(ledger.STORAGE_KEY);
const migrated = ledger.load(storage);
assert.deepEqual(migrated.map((item) => item.id), ["old-nfl"], "legacy migration must keep NFL wagers only");

console.log("NFL Props manual ledger tests passed");
