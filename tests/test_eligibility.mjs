import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const E = createRequire(import.meta.url)('../site/eligibility.js');
const now = Date.parse('2026-09-12T19:00:00Z');
const row = {start_time:'2026-09-13T17:00:00Z',updated_at:'2026-09-12T18:00:00Z'};
assert.equal(E.blockReason(row,12,now),null);
for (const updated_at of [null,'bad','2026-08-01T00:00:00Z','2026-09-12T20:00:00Z'])
  assert.ok(E.blockReason({...row,updated_at},12,now));
assert.match(E.blockReason(row,12,Date.parse(row.start_time)),/started/);
assert.match(E.blockReason(row,12,Date.parse('2026-09-13T06:00:00Z')),/stale/);
assert.equal(E.blockReason({tipoff:row.start_time,odds_fetched_at:row.updated_at},12,now),null);
console.log('quote freshness and start-time gates passed');
