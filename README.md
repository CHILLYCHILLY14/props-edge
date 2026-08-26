# NFL Props Edge

NFL Props Edge is an NFL-only player-prop model and GitHub Pages dashboard. It
combines current target-book prices, complete no-vig two-way markets,
conservative ESPN regular-season player form, and opponent-defense matchup
adjustments. It never creates a wager merely
because another sportsbook has a different number.

The public site contains no demo slate, sample recommendation, or fabricated
price. If live prices or mature regular-season samples are unavailable, the
correct output is a watch row or an empty qualified board.

## What changed in the NFL-only review

- The pipeline fetches and publishes only NFL player props.
- Preseason results are excluded from projections and betting decisions.
- The schedule and price search now reaches 21 days ahead so the opening slate
  appears before it enters the old eight-day window.
- Historical selection covers up to eight regular-season games per team instead
  of taking only the league's most recent handful of games.
- Every upcoming projection compares the opponent's position-level production
  allowed with the league median from the same audited box scores.
- Matchup adjustments are reliability-shrunk, discounted when they rely on the
  prior season, and capped at plus or minus 12%.
- A selectable 10,000-run Matchup Lab reports scenario hit rates, fair odds,
  outcome ranges, defensive rank, recent results, and model risk flags.
- Prior-season form is deliberately reduced until a player has four games in
  the current regular season.
- Current ESPN rosters filter out players who are no longer on the upcoming
  team and add position and injury context to each projection.
- Official provider coverage now includes core passing, rushing, receiving,
  touchdown, combined-yardage, kicking, and defensive player props.
- ESPN field-goal and extra-point made/attempted pairs are parsed correctly;
  kicking points and supported combined markets are derived game by game.
- Sportsbook consensus alone is never called a model or allowed to qualify.
- A player-form projection must match the live player and market.
- Complete two-way prices are de-vigged with the power method. External books
  are preferred; DraftKings is the target price.
- Integer lines explicitly include Win, Push, and Loss probability.
- Model edge and offered-price value are calculated and gated separately.
- Thin samples, low confidence, incomplete prices, excessive disagreement,
  extreme odds, duplicate alternate lines, player correlation, slate size, and
  exposure all fail closed.
- PASS rows always receive a C$0 suggested stake.
- My Ledger is manual browser storage. A model card enters it only after the
  user clicks **Add to My Ledger**.

## Calculation flow

1. ESPN regular-season box scores create up to eight recent observations for
   each player and market.
2. The stat projection blends a recency-weighted mean with the sample median,
   then measures sample variability and stability.
3. Opponent allowance is measured by team, position group, and market. The
   player projection receives a capped matchup adjustment only when a matched
   defensive sample is available.
4. Yardage and volume markets blend a continuous distribution with empirical
   hit rates. Low-count markets such as touchdowns, field goals, interceptions,
   and sacks blend a Poisson count model with the observed game sample. Integer
   lines reserve probability for a push.
5. Complete sportsbook pairs are de-vigged. The independent player-form
   probability is blended toward the market according to sample maturity and
   confidence, with a hard maximum projection weight. Prior-season and volatile
   markets receive additional reliability reductions.
6. Model edge is the relative difference between the blended no-push model
   probability and the no-vig market probability.
7. Price value is the expected return at the offered decimal odds, including
   push refunds.
8. Positive edge and price value are compressed separately before tiering.
9. Suggested stakes use 0.15 Kelly and are scaled again for confidence and
   sample maturity.

Current compressed gates:

| Tier | Model edge | Price value | Samples | Confidence |
|---|---:|---:|---:|---:|
| Lean | 2.5% | 1.5% | 4+ | 45%+ |
| Good | 5.0% | 3.0% | 5+ | 52%+ |
| Best | 8.0% | 5.5% | 7+ | 62%+ |

A Best Bet also requires an external no-vig comparison and a price from -160
through +200. All qualified prices must remain within the broader configured
range. Touchdowns, field goals, interceptions, sacks, and longest-play markets
require at least six samples even at the Lean tier. These are model-selection
thresholds, not promises of profit.

Supported projection families include passing yards/touchdowns/attempts/
completions/interceptions, rushing yards/attempts/touchdowns, receptions/
receiving yards/targets/touchdowns, longest plays, anytime and total touchdowns,
combined passing-rushing-receiving markets, field goals, extra points, kicking
points, sacks, solo tackles, and tackles plus assists. A market is shown as a
watch—not a bet—when its provider label cannot be matched to a real projection.

## My Ledger

My Ledger is deliberately separate from the scheduled model:

1. Review the player, market, line, price, injury status, and current role.
2. Edit the prefilled stake to the amount actually placed.
3. Click **Add to My Ledger**.
4. Set Win, Loss, Push, or Void manually after the prop settles.
5. Optionally enter closing odds to calculate CLV.

The ledger is stored only in that browser and device. Use **Backup JSON** for a
restorable copy or **Export CSV** for a spreadsheet record. The scheduled
workflow cannot read or modify it.

## Matchup Lab

The Matchup Lab is fed by the same scheduled pipeline as the betting board. It
lets you select an upcoming game, player, prop market, side, and scenario line.
Each run uses 10,000 deterministic trials that blend the matchup-adjusted
distribution with the player's recent empirical results. Low-count props use
count-stat simulation, while yardage and longest-play props use a continuous
distribution.

The simulator is an analysis tool, not a back door around qualification. Its
result cannot enter My Ledger and is never called a wager unless a complete
current DraftKings market passes every model, price, confidence, and exposure
gate.

## GitHub setup and updates

1. In **Settings → Pages**, use **GitHub Actions** as the Pages source.
2. In **Settings → Secrets and variables → Actions**, store provider keys only
   as repository secrets:
   - **ODDS_API_IO_KEY**
   - **THE_ODDS_API_KEY**
3. Open **Actions → Refresh NFL Props Edge → Run workflow**.

The workflow also runs five times daily. It tests the model and the manual
ledger before it refreshes real NFL prices, commits only generated site data,
and deploys GitHub Pages.

Do not put keys in JavaScript, JSON, an Actions variable, a commit, an issue, or
an environment file. The dashboard publishes calculated rows and sanitized
source health only.

## Honest limits

Public data cannot fully capture late inactive announcements, snap-count
restrictions, unexpected depth-chart changes, weather changes, or a sportsbook
line move after the last refresh. A rookie or a player without enough recent
regular-season games will correctly have no qualified projection. Always verify
the current number and player availability before wagering.

## Local verification

    python -m unittest tests.test_offline -v
    node tests/test_ledger.mjs
    node tests/test_simulator.mjs
    python -m pipeline.build
    python -m http.server 8000 --directory site

Then open http://localhost:8000.
