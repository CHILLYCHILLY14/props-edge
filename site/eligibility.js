/* Recheck quote eligibility when a saved page is opened or a wager is added. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.QuoteEligibility = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  function timestamp(value) {
    if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return NaN;
    return Date.parse(value);
  }
  function blockReason(row, maxAgeHours = 12, now = Date.now()) {
    const start = timestamp(row.start_time || row.tipoff);
    const updated = timestamp(row.updated_at || row.odds_fetched_at);
    if (!Number.isFinite(start) || !Number.isFinite(updated)) return 'Verified start time and odds timestamp required';
    if (start <= now) return 'Game has already started';
    if (updated > now + 5 * 60000) return 'Odds timestamp is in the future';
    if (now - updated >= Number(maxAgeHours) * 3600000) return 'Odds are stale; waiting for a live price refresh';
    return null;
  }
  return {blockReason};
});
