# NFL Props Edge

NFL Props Edge is an NFL-only player-prop model and GitHub Pages dashboard. It
combines current target-book prices, complete no-vig two-way markets, and
conservative ESPN regular-season player form. It never creates a wager merely
because another sportsbook has a different number.

The public site contains no demo slate, sample recommendation, or fabricated
price. If live prices or mature regular-season samples are unavailable, the
correct output is a watch row or an empty qualified board.

## What changed in the NFL-only review

- The pipeline fetches and publishes only NFL player props.
- Preseason results are excluded from projections and betting decisions.
- Historical selection covers up to eight regular-season games per team instead
  of taking only the league's most recent handful of games.
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
3. Normal-distribution and empirical hit-rate estimates are blended. Integer
   lines reserve probability for a push. Touchdown props use empirical scoring
   frequency plus a Poisson estimate and are regressed toward a conservative
   baseline.
4. Complete sportsbook pairs are de-vigged. The independent player-form
   probability is blended toward the market according to sample maturity and
   confidence, with a hard maximum projection weight.
5. Model edge is the relative difference between the blended no-push model
   probability and the no-vig market probability.
6. Price value is the expected return at the offered decimal odds, including
   push refunds.
7. Positive edge and price value are compressed separately before tiering.
8. Suggested stakes use 0.15 Kelly and are scaled again for confidence and
   sample maturity.

Current compressed gates:

| Tier | Model edge | Price value | Samples | Confidence |
|---|---:|---:|---:|---:|
| Lean | 2.5% | 1.5% | 4+ | 45%+ |
| Good | 5.0% | 3.0% | 5+ | 52%+ |
| Best | 8.0% | 5.5% | 7+ | 62%+ |

A Best Bet also requires an external no-vig comparison and a price from -160
through +200. All qualified prices must remain within the broader configured
range. These are model-selection thresholds, not promises of profit.

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
    python -m pipeline.build
    python -m http.server 8000 --directory site

Then open http://localhost:8000.
