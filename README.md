# Props Edge

Props Edge is a static GitHub Pages dashboard for NFL, NCAAF, MLB, and WNBA player props. It follows a secure hybrid data model:

1. Odds-API.io, when its GitHub Actions secret is configured.
2. The Odds API, when its GitHub Actions secret is configured and the primary feed has no props.
3. ESPN public schedules and box scores as a keyless projection-only fallback.

Sportsbook odds produce model-ranked **Best**, **Good**, and **Lean** rows. ESPN has player statistics but no sportsbook prices, so its fallback rows are clearly labeled **Projections**, not bets. Current sportsbook lines can also be imported as a CSV and evaluated locally in the browser.

## Fast GitHub setup

1. Create a new public GitHub repository named `props-edge`.
2. Unzip this project and upload the files and folders inside it to the repository root.
3. Commit the upload to the `main` branch.
4. Open **Settings → Pages** and choose **GitHub Actions** as the source.
5. Open **Actions → Refresh Props Edge → Run workflow**.

The workflow refreshes automatically five times per day and redeploys the page. It can also be run at any time from GitHub Actions or through the dashboard's **Run GitHub update** link. The **Game date** selector lets you isolate today, tomorrow, or any other available scheduled day in the seven-day window.

## Add private odds-provider keys (optional)

Any key previously pasted into chat, source code, or another public location must be revoked and replaced at the provider before use.

For each replacement key:

1. In the new repository, open **Settings → Secrets and variables → Actions**.
2. Choose **New repository secret**.
3. Create `ODDS_API_IO_KEY` for Odds-API.io.
4. Create `THE_ODDS_API_KEY` for The Odds API.
5. Paste each replacement value only into its matching secret and save it.
6. Run **Actions → Refresh Props Edge → Run workflow**.

Do not put a key in an Actions variable, `.env` file, JSON file, JavaScript, HTML, issue, commit message, or workflow text. The workflow reads the two repository secrets only during the private data-build step. It publishes player, market, price, model, and source fields—not credentials or provider request URLs. Provider errors are sanitized before they reach public JSON.

The `.gitignore` excludes `.env` files. The automated tests also reject long secret-shaped hexadecimal tokens before each refresh.

## Run without any API keys

No setup is required. The workflow automatically uses ESPN's public schedules and recent box scores. When enough recent player samples and an upcoming matchup exist, the site displays projection cards. ESPN does not publish sportsbook prop odds, so it cannot calculate price-based expected value by itself.

This mode may legitimately show no rows during an offseason, before schedules are posted, or before players have enough recent games.

## Import current sportsbook lines

On the dashboard, choose **Download CSV template**, enter current lines, then choose **Import sportsbook lines**. Required columns are:

```csv
sport,player,market,line,over_odds,under_odds,book,matchup
WNBA,Player Name,Points,20.5,-110,-110,theScore Bet,Away @ Home
```

The CSV is read only by that browser tab and is not uploaded, committed, or retained after the page closes. Player and market names must match a displayed ESPN projection.

## Data and model rules

- DraftKings is the target book for automatically priced plays. FanDuel is the second primary comparison book so the free Odds-API.io account stays within its two-book maximum.
- Consensus probabilities use complete two-sided prices from at least two books and remove the two-way market margin before comparison.
- If DraftKings has a current price but fewer than two consensus books are available, the model can conservatively compare that price with the matching ESPN player projection. These cards are explicitly labeled `DraftKings price vs ESPN projection`.
- Model probabilities are shrunk toward 50% to reduce overconfidence.
- A positive expected value and at least a 2% model edge are required for a Lean; 4% is Good and 6% is Best.
- Prices outside -250 to +500 are rejected.
- ESPN projections use up to eight recent player games, weight recent games more heavily, and show the sample count and confidence.
- Action Network and OddsShark are not scraped because automated extraction is prohibited or blocked. Sportsbook sites can also be geo-, age-, and bot-gated, so they are not a reliable unattended GitHub Actions feed.

This is an informational model, not a promise of profit. Verify the player, market, line, price, and availability at the sportsbook before betting, and use legal age and responsible-gambling limits.

## Local checks

```bash
python -m unittest tests.test_offline -v
python -m pipeline.build
python -m http.server 8000 --directory site
```

Then open `http://localhost:8000`.
