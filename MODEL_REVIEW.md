# NFL Props Edge — calculation review

Reviewed August 26, 2026.

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
- Up to eight games per team are selected across the league.
- Market-only rows are watches and cannot qualify.
- A matched player and market projection is required.
- Complete target-book Over/Under prices are required.
- External complete markets are power-de-vigged and preferred.
- Projection probability and market probability remain visible separately.
- Model edge and offered-price expected return must both pass.
- Raw projection/market disagreement above 18 percentage points fails closed.
- Sample size, confidence, price, alternate-line, player-correlation, slate,
  minimum-stake, and total-exposure rules are enforced.
- PASS always means a C$0 suggested stake.
- My Ledger accepts an entry only after a browser click and a positive stake.

## Honest interpretation

This is a conservative public-data model. It does not claim to know a late
inactive announcement, a coach's snap restriction, or a line move after the
latest refresh. Early in the season, players without enough regular-season
history should produce fewer qualified plays. That is intended behavior.
