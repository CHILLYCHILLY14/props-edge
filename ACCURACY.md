# Prediction accuracy

The Accuracy view records model predictions independently of the wager ledger.
No wager is created by this tracker. Refreshes preserve `state/model_accuracy.json`
and publish the model-only report in `data/accuracy.json`.

* Only current NFL regular-season player projections appear in this tracker.
  Earlier seasons and preseason do not contribute to any displayed totals.
* The first player/statistic forecast is frozen before kickoff. Refreshes and
  model changes never rewrite that forecast after the result is known.
* Unpriced team/player forecasts are tracked. They report final-score or player
  statistic error even if there was never a wager or a qualifying betting edge.
* Final results grade projection error. A missing player, missing score,
  incomplete box score or failed feed remains pending; missing data is never zero.
* The accuracy history contains no wager prices, stakes, ledger entries or ROI.
  My Ledger remains a separate, manual record stored in the user's browser.
* Legacy records are imported only with a documented pre-event timestamp. Games
  with no saved pre-event forecast cannot be backfilled honestly.
* Model/tier versions remain separate. Changing a threshold never relabels old
  predictions or proves that a revised model will be profitable.

Run `python -m unittest tests.test_accuracy` to verify freezing, grading,
missing results, pushes, voids, calibration and history persistence.

## Props

Player projections are matched to the ESPN event ID, player, team and statistic.
Final ESPN box scores grade saved forecasts, including negative yardage.
A player absent from the box score stays pending. All saved forecasts contribute
to the summary; the public detail list displays the newest 1,000 and states when
older rows are omitted from that list.
Qualified alternatives keep their model grade when the portfolio or stake limit
excludes them. They appear under Other qualified options with zero suggested
exposure. Kelly sizing uses the conservative return, not the uncompressed EV.
