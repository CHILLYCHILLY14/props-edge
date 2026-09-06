# Prediction accuracy

The Accuracy view records model predictions independently of the wager ledger.
No wager is created by this tracker. Refreshes preserve `state/model_accuracy.json`
and publish the model-only report in `data/accuracy.json`.

* Forecasts and quoted selections are frozen before the event starts. Every
  original price, line, probability and tier remains available for review.
* Preferred-pick statistics use one first-selected side per event and market,
  including PASS/AVOID. The full audit includes the opposite sides and, for
  props, alternative lines; do not interpret its combined win rate as pick quality.
* Unpriced team/player forecasts are tracked. They report final-score or player
  statistic error even if there was never a wager or a qualifying betting edge.
* Final results grade wins, losses and pushes. A missing player, missing score,
  incomplete box score or failed feed remains pending; missing data is never zero.
* ROI is hypothetical flat-one-unit return. Push stakes are included in the ROI
  denominator; voids are excluded. Win rate and Brier exclude pushes and voids.
* Legacy records are imported only with a documented pre-event timestamp. Games
  with no saved pre-event forecast cannot be backfilled honestly.
* Model/tier versions remain separate. Changing a threshold never relabels old
  predictions or proves that a revised model will be profitable.

Run `python -m unittest tests.test_accuracy` to verify freezing, grading,
missing results, pushes, voids, calibration and history persistence.

## Props

Player projections are matched to the ESPN event ID, player, team and statistic.
Final ESPN box scores settle exact saved lines, including negative yardage and
integer-line pushes. A player absent from the box score stays pending.
Qualified alternatives keep their model grade when the portfolio or stake limit
excludes them. They appear under Other qualified options with zero suggested
exposure. Kelly sizing uses the conservative return, not the uncompressed EV.
