# NFL Props Edge — calculation review

Reviewed September 3, 2026.

## Problems corrected

The prior board could shrink a sportsbook consensus toward 50%, label that
result a model probability, and compare it with the target-book break-even
probability. Because the target book was also included in the consensus, a
price difference could appear to be an independent model edge even when no
player-stat projection existed.

The prior ESPN history query also used only the league's most recent completed
games. That did not reliably cover every team, and preseason games could enter
the player sample. PASS rows could retain a positive Kelly stake, integer lines
did not reserve push probability, and alternate lines for the same player and
market were not portfolio-deduplicated.

## Current decision rules

- Only NFL regular-season player history is used.
- The schedule and market search spans 21 days; a wager still requires a
  currently posted price from the Ontario-regulated book allowlist.
- The Ontario-key provider is preferred, with the existing regulated-brand
  provider retained as a continuity fallback.
- The best returned price is selected for each exact player, market, side, and
  line before model tiering.
- Up to eight games per team are selected across the league.
- Opponent strength is calculated from position-level production allowed in
  the same completed regular-season box scores as player form.
- Defensive allowance is compared with the league median, reliability-shrunk,
  reduced while it is prior-season-only, and capped at plus or minus 12%.
- Prior-season form receives 65% of normal projection influence, increasing to
  full weight after four current-season games.
- Current ESPN rosters filter stale team/player combinations and supply
  position and injury context; Out, Injured Reserve, Doubtful, Suspended, and
  Physically Unable statuses cannot qualify.
- Low-count props use Poisson plus empirical probabilities; integer lines retain
  explicit push probability.
- Touchdowns, field goals, interceptions, sacks, and longest-play props require
  at least six regular-season samples and receive a volatility reduction.
- Field goals, extra points, kicking points, and supported combined yardage and
  touchdown markets are built from actual game-level box scores.
- Market-only rows are watches and cannot qualify.
- A matched player and market projection is required.
- Complete offered-book Over/Under prices are required.
- External complete markets are power-de-vigged and preferred.
- Projection probability and market probability remain visible separately.
- Model edge and offered-price expected return must both pass.
- Raw projection/market disagreement above 18 percentage points is LEAN-only
  with a half-sized stake; above 25 percentage points still fails closed.
- Sample size, confidence, price, alternate-line, player-correlation, slate,
  minimum-stake, and total-exposure rules are enforced.
- PASS always means a C$0 suggested stake.
- The minimum model-sized wager is C$1; the model never rounds a smaller Kelly
  recommendation up to a larger risk amount.
- My Ledger accepts an entry only after a browser click and a positive stake.
- The 10,000-run Matchup Lab is scenario analysis only and cannot qualify or
  add a wager by itself.

## Honest interpretation

This is a conservative public-data model. It does not claim to know a late
inactive announcement, a coach's snap restriction, or a line move after the
latest refresh. Early in the season, players without enough regular-season
history should produce fewer qualified plays. That is intended behavior.
