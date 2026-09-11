const state = {
  meta: null,
  board: [],
  projections: [],
  parlays: { dates: [], notes: [] },
  staking: null,
  ledger: [],
  betIndex: {},
  view: "best",
  market: "ALL",
  date: "ALL",
  search: "",
  bankroll: 500,
  schemaReady: false,
  simulator: { game: "", player: "", market: "", line: null, side: "over" },
};

const L = window.NFLPropsLedger;
const S = window.NFLPropsSimulator;
const STAKING = window.PropsEdgeStaking;
const SETTINGS_KEY = "nfl-props-edge-settings-v2";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));
const pct = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
const american = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value) > 0 ? "+" : ""}${Math.round(Number(value))}`;
const money = (value, signed = false) => {
  const number = Number(value) || 0;
  return `${signed && number > 0 ? "+" : number < 0 ? "−" : ""}C$${Math.abs(number).toFixed(2)}`;
};
const normalized = (value) => String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");

function dateKey(value) {
  if (!value) return "";
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return "";
  const pad = (number) => String(number).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}`;
}

function dateLabel(key) {
  const [year, month, day] = key.split("-").map(Number);
  const target = new Date(year, month - 1, day);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const difference = Math.round((target - today) / 86400000);
  const prefix = difference === 0 ? "Today · " : difference === 1 ? "Tomorrow · " : "";
  return prefix + target.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function formatStart(value) {
  return value
    ? new Date(value).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "Time pending";
}

function marketGroup(market) {
  const value = String(market || "").toLowerCase();
  if (value.includes("touchdown") || /\btd\b/.test(value)) return "TOUCHDOWNS";
  if (value.includes("target")) return "TARGETS";
  if (value.includes("pass") || value.includes("interception")) return "PASSING";
  if (value.includes("rush") || value.includes("carr")) return "RUSHING";
  if (value.includes("receiv") || value.includes("reception")) return "RECEIVING";
  if (value.includes("tackle") || value.includes("sack") || value.includes("defens")) return "DEFENSE";
  if (value.includes("kick") || value.includes("field goal") || value.includes("extra point")) return "KICKING";
  return "OTHER";
}

function tierClass(tier) {
  return String(tier || "PASS").toLowerCase().replaceAll(" ", "-");
}

function loadSettings() {
  state.staking = STAKING ? STAKING.load(SETTINGS_KEY) : { bankroll: 500 };
  state.bankroll = Math.max(1, Number(state.staking.bankroll) || 500);
  $("#bankrollInput").value = state.bankroll;
}

function saveSettings() {
  state.staking = STAKING
    ? STAKING.normalise({ ...(state.staking || {}), bankroll: state.bankroll })
    : { bankroll: state.bankroll };
  if (STAKING) return STAKING.save(SETTINGS_KEY, state.staking);
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.staking)); return true; }
  catch { return false; }
}

async function loadData() {
  const stamp = Date.now();
  const [meta, board, projections, parlays] = await Promise.all([
    fetch(`data/meta.json?v=${stamp}`).then((response) => {
      if (!response.ok) throw new Error("meta feed unavailable");
      return response.json();
    }),
    fetch(`data/board.json?v=${stamp}`).then((response) => response.json()),
    fetch(`data/projections.json?v=${stamp}`).then((response) => response.json()),
    fetch(`data/parlays.json?v=${stamp}`)
      .then((response) => response.ok ? response.json() : ({ dates: [], notes: [] }))
      .catch(() => ({ dates: [], notes: [] })),
  ]);
  state.meta = meta;
  state.schemaReady = meta.league === "NFL" && meta.ledger_mode === "manual-browser";
  state.board = (Array.isArray(board) ? board : [])
    .filter((row) => row.sport === "NFL")
    .map((row) => state.schemaReady ? row : ({
      ...row,
      tier: "PASS",
      recommended_stake: 0,
      reason: "Waiting for the audited NFL-only model refresh",
    }));
  state.projections = state.schemaReady
    ? (Array.isArray(projections) ? projections : []).filter((row) => row.sport === "NFL")
    : [];
  state.parlays = state.schemaReady && parlays && Array.isArray(parlays.dates)
    ? parlays
    : { dates: [], notes: [] };
  populateDates();
  populateSimulator();
  render();
  renderSimulator();
}

function populateDates() {
  const dates = [...new Set(
    [
      ...[...state.board, ...state.projections].map((row) => dateKey(row.start_time)),
      ...state.parlays.dates.map((day) => day.date),
    ].filter(Boolean),
  )].sort();
  const select = $("#dateSelect");
  select.innerHTML = '<option value="ALL">All upcoming dates</option>' + dates
    .map((date) => `<option value="${date}">${escapeHtml(dateLabel(date))}</option>`)
    .join("");
  if (state.date !== "ALL" && dates.includes(state.date)) select.value = state.date;
  else state.date = "ALL";
}

function visible(row) {
  if (row.sport !== "NFL") return false;
  const dateMatches = state.date === "ALL" || dateKey(row.start_time) === state.date;
  const marketMatches = state.market === "ALL" || marketGroup(row.market) === state.market;
  const haystack = `${row.player} ${row.market} ${row.matchup || ""} ${row.pick || ""}`.toLowerCase();
  return dateMatches && marketMatches && (!state.search || haystack.includes(state.search));
}

function filteredBoard() {
  return state.board.filter(visible);
}

function filteredProjections() {
  return state.projections.filter(visible);
}

function expireQuotes() {
  state.board = state.board.map(row => {
    const reason = window.QuoteEligibility.blockReason(row, state.meta?.max_odds_age_hours ?? 12);
    return reason ? {...row, tier:"PASS", recommended_stake:0, reason} : row;
  });
}

function render() {
  expireQuotes();
  renderStatus();
  renderMetrics();
  renderCards();
  renderParlays();
  renderFullBoard();
  renderProjections();
  renderLedger();
  renderSettings();
  renderModel();
}

function renderStatus() {
  if (!state.meta) return;
  const banner = $("#statusBanner");
  const generated = new Date(state.meta.generated_at);
  const ageHours = (Date.now() - generated.getTime()) / 3600000;
  const counts = {...(state.meta.counts || {}), actionable:state.board.filter(row=>row.tier!=="PASS").length};
  const source = (state.meta.source_by_sport || {}).NFL || {};
  const lookahead = Number(state.meta.lookahead_days) || 21;
  const formReady = Number(source.projections) > 0;
  const eligiblePriceRows = Number(source.eligible_priced_quotes ?? source.target_priced_quotes ?? counts.eligible_priced_quotes ?? counts.target_priced_quotes ?? state.board.length) || 0;
  const pricesReady = eligiblePriceRows > 0;
  const qualified = Number(counts.actionable) > 0;
  const setStage = (selector, status, label) => {
    const stage = $(selector);
    stage.className = `readiness-step ${status}`;
    stage.querySelector("b").textContent = label;
  };
  setStage("#stageForm", formReady ? "done" : "waiting", formReady ? "READY" : "WAIT");
  setStage("#stagePrices", pricesReady ? "done" : "waiting", pricesReady ? "PRICED" : "WAIT");
  setStage(
    "#stageQualify",
    qualified ? "done" : pricesReady ? "hold" : "locked",
    qualified ? "QUALIFIED" : pricesReady ? "NO PLAY" : "LOCKED",
  );
  const readyCount = Number(formReady) + Number(pricesReady) + Number(qualified);
  $("#readinessState").textContent = qualified ? "BOARD LIVE" : `${readyCount}/3 READY`;
  $("#lastUpdated").textContent = Number.isNaN(generated.getTime())
    ? "Update time unavailable"
    : generated.toLocaleString();
  if (!state.schemaReady) {
    banner.className = "status-banner warn";
    banner.textContent = "The NFL-only audited refresh is still publishing. Old multi-sport recommendations are disabled.";
    $("#feedState").textContent = "REFRESHING";
  } else if (ageHours > 12) {
    banner.className = "status-banner warn";
    banner.textContent = `NFL data is ${Math.floor(ageHours)} hours old. Treat every displayed price as stale and verify it at the sportsbook.`;
    $("#feedState").textContent = "STALE";
  } else if (pricesReady) {
    banner.className = "status-banner ok";
    banner.textContent = `${counts.actionable || 0} qualified NFL props from ${eligiblePriceRows} live Ontario-regulated price rows. ${state.meta.model_status || ""}`;
    $("#feedState").textContent = "LIVE";
  } else if (Number(source.projections) > 0) {
    banner.className = "status-banner warn";
    banner.textContent = `Regular-season form is ready and the model checks ${lookahead} days ahead, but no current Ontario-regulated player-prop prices were returned. Books often post these closer to kickoff; no wager can qualify without a complete live price.`;
    $("#feedState").textContent = "NO PRICES";
  } else {
    banner.className = "status-banner warn";
    banner.textContent = "NFL data is still too thin for a qualified prop. The model is correctly refusing to force a wager.";
    $("#feedState").textContent = "THIN DATA";
  }
}

function renderMetrics() {
  const rows = filteredBoard();
  const qualified = rows.filter((row) => row.tier !== "PASS");
  const ledger = L.summary(state.ledger, state.bankroll);
  $("#metricQualified").textContent = qualified.length;
  $("#metricPriced").textContent = state.meta?.counts?.priced_quotes ?? rows.length;
  $("#metricProjection").textContent = filteredProjections().length;
  $("#metricBankroll").textContent = money(ledger.bankroll);
  $("#metricExposure").textContent = money(ledger.exposure);
  $("#metricExposurePct").textContent = `${pct(ledger.exposure / Math.max(1, ledger.bankroll))} of bankroll`;
  $("#metricPnl").textContent = money(ledger.pnl, true);
  $("#metricPnl").className = ledger.pnl > 0 ? "positive" : ledger.pnl < 0 ? "negative" : "";
  $("#metricRoi").textContent = `${pct(ledger.roi)} ROI`;
}

function betCard(row) {
  const key = L.keyFor(row);
  state.betIndex[key] = row;
  const saved = state.ledger.some((item) => item.id === key);
  const confidence = Number(row.confidence) || 0;
  const stake = row.held ? 0 : Number(state.stakeIndex?.[key] ?? row.recommended_stake) || 0;
  return `
    <article class="bet-card ${tierClass(row.tier)}">
      <div class="card-kicker"><span>${escapeHtml(marketGroup(row.market))}</span><span class="tier ${tierClass(row.tier)}">${escapeHtml(row.tier)}</span></div>
      <h3>${escapeHtml(row.pick)}</h3>
      <p class="game-line">${escapeHtml(formatStart(row.start_time))} · ${escapeHtml(row.matchup)}</p>
      <p class="model-label">${escapeHtml(row.model_label || "NFL form + market")}</p>
      <div class="card-numbers">
        <div><span>Price</span><strong>${american(row.price_american)}</strong></div>
        <div><span>Model</span><strong>${pct(row.model_prob_no_push ?? row.model_prob)}</strong></div>
        <div><span>No-vig</span><strong>${pct(row.market_fair_prob)}</strong></div>
        <div><span>No-vig value</span><strong>${pct(row.edge)}</strong></div>
        <div><span>Conservative return</span><strong>${pct(row.action_edge ?? row.edge_real ?? row.ev)}</strong></div>
        <div><span>Samples</span><strong>${Number(row.projection_samples) || 0}</strong></div>
      </div>
      <div class="confidence-row"><span>Confidence ${pct(confidence)}</span><span>Push ${pct(row.push_prob)}</span></div>
      <div class="confidence-track"><i style="width:${Math.max(3, confidence * 100)}%"></i></div>
      <div class="manual-add">
        <label>Stake <span>C$</span><input id="stake-${escapeHtml(key)}" type="number" min="1" step="0.5" value="${stake.toFixed(2)}" /></label>
        <button class="add-ledger" data-key="${escapeHtml(key)}" ${saved ? "disabled" : ""}>${saved ? "In My Ledger" : "Add to My Ledger"}</button>
      </div>
      <p class="manual-note">${row.held ? `Additional ${escapeHtml(row.tier)} option: ${escapeHtml(row.reason)}. It is excluded from suggested exposure. ` : ""}Review the current line first. This button is the only way a wager is added.</p>
    </article>`;
}

function renderCardGroup(selector, rows, message) {
  $(selector).innerHTML = rows.length
    ? rows.map(betCard).join("")
    : `<div class="empty-state"><strong>No qualifying plays</strong><span>${escapeHtml(message)}</span></div>`;
}

function renderCards() {
  state.betIndex = {};
  const actionable = filteredBoard().filter((row) => row.tier !== "PASS" && !row.held);
  const available = L.summary(state.ledger, state.bankroll).available;
  const planned = STAKING
    ? STAKING.plan(actionable, state.staking, available, 0.85)
    : actionable.map((row) => Number(row.recommended_stake) || 0);
  state.stakeIndex = Object.fromEntries(actionable.map((row, index) => [L.keyFor(row), planned[index]]));
  renderCardGroup("#bestBoard", actionable.filter((row) => row.tier === "BEST"), "Nothing clears every Best Bet gate for this filter.");
  renderCardGroup("#goodBoard", actionable.filter((row) => row.tier === "GOOD"), "No Good Plays clear the current data and price gates.");
  renderCardGroup("#leanBoard", actionable.filter((row) => row.tier === "LEAN"), "No Leans clear both model-edge and price-value gates.");
  renderCardGroup("#otherBoard", filteredBoard().filter(row=>row.tier!=="PASS" && row.held), "No additional qualified options for this filter.");
}

function parlayTicket(card, stale) {
  const blocked = card.status === "ready" && window.QuoteEligibility.parlayBlockReason(card, state.meta?.max_odds_age_hours ?? 12);
  if (card.status !== "ready" || stale || blocked) {
    const reason = blocked || (stale
      ? "The posted leg prices are stale. Waiting for the next live odds refresh."
      : card.reason || "No supported 3–4 leg combination reaches this payout band yet.");
    return `
      <article class="parlay-ticket waiting ${escapeHtml(card.key || "")}">
        <div class="parlay-head">
          <div><span>${escapeHtml(card.name || "Daily card")}</span><strong>${card.key === "touchdown_ticket" ? "TD-only" : `${american(card.target_american)} target`}</strong></div>
          <b>WAITING</b>
        </div>
        <div class="parlay-wait"><span>NO FORCED TICKET</span><p>${escapeHtml(reason)}</p></div>
      </article>`;
  }
  const legs = (card.legs || []).map((leg) => `
    <li class="parlay-leg">
      <span class="parlay-node" aria-hidden="true">${escapeHtml(String(leg.market_group || marketGroup(leg.market)).slice(0, 1))}</span>
      <div class="parlay-leg-copy">
        <div><strong>${escapeHtml(leg.player)}</strong><b>${american(leg.price_american)}</b></div>
        <span>${escapeHtml(leg.selection || leg.pick || `${leg.side || ""} ${leg.line ?? ""} ${leg.market || ""}`)}</span>
        <small>${escapeHtml(leg.matchup || "Matchup pending")} · ${escapeHtml(formatStart(leg.start_time))}</small>
        <em>${escapeHtml(leg.market_group || marketGroup(leg.market))} · ${pct(leg.model_probability)} model · ${Number(leg.samples) || 0} samples</em>
      </div>
    </li>`).join("");
  return `
    <article class="parlay-ticket ${escapeHtml(card.key || "")}">
      <div class="parlay-head">
        <div><span>${card.leg_count} leg parlay</span><strong>${american(card.estimated_american)}</strong></div>
        <b>${escapeHtml(card.name)}</b>
      </div>
      <div class="parlay-book"><span>${escapeHtml(card.book)}</span><small>${card.key === "touchdown_ticket" ? "Anytime scorers · best supported" : `${american(card.target_american)} target`} · estimated payout</small></div>
      <ol class="parlay-legs">${legs}</ol>
      <div class="parlay-foot">
        <div><span>Estimated all-win chance</span><strong>${pct(card.model_probability)}</strong></div>
        <div><span>Implied by estimated payout</span><strong>${pct(card.book_implied_probability)}</strong></div>
        <small>${Number(card.correlation_factor) < 1 ? "Same-game estimate uses an uncalibrated correlation assumption. Confirm the book’s combined price." : "Estimate assumes independent games."} Pushes change the payout.</small>
      </div>
    </article>`;
}

function renderParlays() {
  const board = $("#parlayBoard");
  if (!board) return;
  const generated = new Date(state.parlays.generated_at || state.meta?.generated_at);
  const stale = Number.isNaN(generated.getTime())
    || (Date.now() - generated.getTime()) / 3600000 > Number(state.meta?.max_odds_age_hours || 12);
  const dates = (state.parlays.dates || []).filter((day) => (
    state.date === "ALL" || day.date === state.date
  ));
  if (!dates.length) {
    board.innerHTML = '<div class="empty-state panel"><strong>No NFL parlay slate for this date</strong><span>The schedule remains visible here as soon as an NFL game day enters the live lookahead window.</span></div>';
    return;
  }
  board.innerHTML = dates.map((day) => `
    <section class="parlay-day">
      <div class="parlay-day-head">
        <div><span>GAME DAY</span><h3>${escapeHtml(dateLabel(day.date))}</h3></div>
        <p>${(day.games || []).length} game${(day.games || []).length === 1 ? "" : "s"} · ${Number(day.eligible_legs) || 0} supported same-book legs</p>
      </div>
      <div class="touchdown-feature">
        <div class="touchdown-feature-label"><span>DEDICATED CARD</span><strong>Anytime touchdown scorers only</strong></div>
        ${(day.cards || []).filter((card) => card.key === "touchdown_ticket").map((card) => parlayTicket(card, stale)).join("")}
      </div>
      <div class="mixed-parlay-label"><span>MIXED-MARKET CARDS</span><small>Passing · rushing · receiving · touchdowns · kicking · defense</small></div>
      <div class="parlay-grid">${(day.cards || []).filter((card) => card.key !== "touchdown_ticket").map((card) => parlayTicket(card, stale)).join("")}</div>
    </section>`).join("");
}

function renderFullBoard() {
  const rows = filteredBoard();
  const body = $("#boardBody");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="12" class="table-empty">No NFL Ontario-regulated prop rows are available for this filter.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td><span class="tier ${tierClass(row.tier)}">${row.tier === "PASS" ? "WATCH" : escapeHtml(row.tier)}</span></td>
      <td><strong>${escapeHtml(row.pick)}</strong><small>${escapeHtml(row.market)}</small></td>
      <td>${escapeHtml(formatStart(row.start_time))}<small>${escapeHtml(row.matchup)}</small></td>
      <td>${escapeHtml(row.book || "—")}</td>
      <td class="num">${american(row.price_american)}</td>
      <td class="num">${pct(row.model_prob_no_push ?? row.model_prob)}</td>
      <td class="num">${pct(row.market_fair_prob)}</td>
      <td class="num">${pct(row.push_prob)}</td>
      <td class="num">${pct(row.edge)}</td>
      <td class="num">${pct(row.edge_real ?? row.ev)}</td>
      <td>${Number(row.projection_samples) || 0} games<small>${pct(row.confidence)} confidence</small></td>
      <td class="${row.tier === "PASS" ? "watch-reason" : "qualified-reason"}">${escapeHtml(row.reason || "Qualified")}</td>
    </tr>`).join("");
}

function renderProjections() {
  const rows = filteredProjections().slice(0, 160);
  const board = $("#projectionBoard");
  if (!rows.length) {
    board.innerHTML = '<div class="empty-state"><strong>No regular-season projection yet</strong><span>Preseason is excluded. Rookies and players without enough recent NFL games remain blank.</span></div>';
    return;
  }
  board.innerHTML = rows.map((row) => `
    <article class="projection-card">
      <div class="card-kicker"><span>${escapeHtml(marketGroup(row.market))}</span><span>${Number(row.samples) || 0} games</span></div>
      <h3>${escapeHtml(row.player)}</h3>
      <p class="game-line">${escapeHtml(formatStart(row.start_time))} · ${escapeHtml(row.matchup)}</p>
      <div class="projection-value">${Number(row.projection).toFixed(1)}</div>
      <strong>${escapeHtml(row.market)}</strong>
      <p class="matchup-line"><span>${escapeHtml(row.opponent || "Opponent pending")}</span><b class="${String(row.matchup_quality || "unknown").toLowerCase()}">${escapeHtml(row.matchup_quality || "Unknown")}</b><em>${Number(row.defense_adjustment) >= 0 ? "+" : ""}${pct(row.defense_adjustment)}</em></p>
      <p class="recent-values">Recent: ${(row.recent || []).map((value) => Number(value).toFixed(0)).join(" · ")}</p>
      <div class="projection-foot"><span>${pct(row.confidence)} confidence</span><span>${Number(row.current_season_samples) || 0} current</span><span>SD ${Number(row.standard_deviation || 0).toFixed(1)}</span><span>${Number(row.trend) >= 0 ? "▲" : "▼"} ${Math.abs(Number(row.trend) || 0).toFixed(1)}</span></div>
    </article>`).join("");
}

function simulatorGameKey(row) {
  return String(row.event_id || `${row.start_time}|${row.matchup}`);
}

function simulatorRows() {
  return state.projections
    .filter((row) => row.sport === "NFL" && Number.isFinite(Number(row.projection)))
    .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)) || a.player.localeCompare(b.player) || a.market.localeCompare(b.market));
}

function setSelectOptions(selector, entries, selected) {
  const select = $(selector);
  select.innerHTML = entries.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join("");
  if (entries.some(([value]) => value === selected)) select.value = selected;
  else if (entries.length) select.value = entries[0][0];
  return select.value;
}

function selectedSimulatorRow() {
  return simulatorRows().find((row) => (
    simulatorGameKey(row) === state.simulator.game
    && row.player === state.simulator.player
    && row.market === state.simulator.market
  ));
}

function populateSimulator({ resetPlayer = false, resetMarket = false, resetLine = false } = {}) {
  const rows = simulatorRows();
  const gameMap = new Map();
  rows.forEach((row) => {
    const key = simulatorGameKey(row);
    if (!gameMap.has(key)) gameMap.set(key, `${formatStart(row.start_time)} · ${row.matchup}`);
  });
  state.simulator.game = setSelectOptions("#simGame", [...gameMap.entries()], state.simulator.game);

  const gameRows = rows.filter((row) => simulatorGameKey(row) === state.simulator.game);
  const playerMap = new Map();
  gameRows.forEach((row) => {
    if (!playerMap.has(row.player)) playerMap.set(row.player, `${row.player} · ${row.position || row.team}`);
  });
  if (resetPlayer) state.simulator.player = "";
  state.simulator.player = setSelectOptions("#simPlayer", [...playerMap.entries()], state.simulator.player);

  const playerRows = gameRows.filter((row) => row.player === state.simulator.player);
  const markets = [...new Set(playerRows.map((row) => row.market))].sort();
  if (resetMarket) state.simulator.market = "";
  state.simulator.market = setSelectOptions("#simMarket", markets.map((market) => [market, market]), state.simulator.market);

  const row = selectedSimulatorRow();
  const signature = row ? `${simulatorGameKey(row)}|${row.player}|${row.market}` : "";
  if (row && (resetLine || state.simulator.signature !== signature || state.simulator.line == null)) {
    state.simulator.line = S.defaultLine(row);
    $("#simLine").value = state.simulator.line;
  }
  state.simulator.signature = signature;
  $("#simGame").disabled = !rows.length;
  $("#simPlayer").disabled = !playerRows.length;
  $("#simMarket").disabled = !markets.length;
  $("#simLine").disabled = !row;
  $("#simSide").disabled = !row;
  $("#runSimulator").disabled = !row;
}

function matchingLiveQuote(row, side, line) {
  const candidates = state.board.filter((quote) => (
    normalized(quote.player) === normalized(row.player)
    && normalized(quote.market) === normalized(row.market)
    && dateKey(quote.start_time) === dateKey(row.start_time)
    && quote.side === side
  ));
  return candidates.sort((a, b) => Math.abs(Number(a.line) - line) - Math.abs(Number(b.line) - line))[0] || null;
}

function renderSimulator() {
  const row = selectedSimulatorRow();
  const empty = $("#simulatorEmpty");
  const resultsPanel = $("#simulatorResults");
  if (!row || !S) {
    empty.hidden = false;
    resultsPanel.hidden = true;
    return;
  }
  empty.hidden = true;
  resultsPanel.hidden = false;
  const line = Math.max(0, Number($("#simLine").value) || S.defaultLine(row));
  state.simulator.line = line;
  state.simulator.side = $("#simSide").value;
  const simulation = S.run(row, line, 10000);
  const isOver = state.simulator.side === "over";
  const hitProbability = isOver ? simulation.overProbability : simulation.underProbability;
  const fairOdds = isOver ? simulation.overFairAmerican : simulation.underFairAmerican;
  const otherSide = isOver ? "Under" : "Over";
  const sideLabel = isOver ? "Over" : "Under";
  const anytime = String(row.market).toLowerCase().includes("anytime touchdown");
  $("#simSide option[value='over']").textContent = anytime ? "Yes" : "Over";
  $("#simSide option[value='under']").textContent = anytime ? "No" : "Under";
  const displayedSide = anytime ? (isOver ? "Yes" : "No") : sideLabel;

  $("#simMarketLabel").textContent = `${marketGroup(row.market)} · ${row.market}`;
  $("#simPlayerLabel").textContent = row.player;
  $("#simMatchupLabel").textContent = `${formatStart(row.start_time)} · ${row.matchup} · ${row.venue || "Venue pending"}`;
  $("#simSideLabel").textContent = displayedSide.toUpperCase();
  $("#simHitProbability").textContent = pct(hitProbability);
  $("#simProbabilityRing").style.background = `conic-gradient(var(--cyan) 0 ${Math.max(0, Math.min(100, hitProbability * 100))}%, rgba(255,255,255,.07) 0)`;
  $("#simProbabilityRing").setAttribute("aria-label", `${displayedSide} hit probability ${pct(hitProbability)}`);

  const call = hitProbability >= 0.58
    ? `${displayedSide} leads the simulation`
    : hitProbability >= 0.52
      ? `${displayedSide} has a slight simulation lean`
      : `${anytime ? (isOver ? "No" : "Yes") : otherSide} appears more often`;
  $("#simOutcomeCall").textContent = call;
  $("#simOutcomeDetail").textContent = `${displayedSide} ${line.toFixed(1)} occurred in ${Math.round(hitProbability * simulation.iterations).toLocaleString()} of ${simulation.iterations.toLocaleString()} trials. Scenario only; sportsbook qualification remains separate.`;
  $("#simBaseProjection").textContent = Number(row.base_projection ?? row.projection).toFixed(1);
  $("#simAdjustedProjection").textContent = Number(row.projection).toFixed(1);
  $("#simAverage").textContent = simulation.average.toFixed(1);
  $("#simRange").textContent = `${simulation.floor.toFixed(1)}–${simulation.ceiling.toFixed(1)}`;
  $("#simFairOdds").textContent = american(fairOdds);

  const quoteSide = anytime ? (isOver ? "yes" : "no") : state.simulator.side;
  const live = matchingLiveQuote(row, quoteSide, line);
  $("#simLivePrice").textContent = live
    ? `${live.side.toUpperCase()} ${live.line == null ? "" : Number(live.line).toFixed(1)} ${american(live.price_american)}`
    : "NOT POSTED";

  const quality = String(row.matchup_quality || "Unknown");
  const badge = $("#simMatchupBadge");
  badge.className = `matchup-badge ${quality.toLowerCase()}`;
  badge.textContent = quality.toUpperCase();
  $("#simOpponentLabel").textContent = `${row.opponent || "Opponent"} vs ${row.position || "player"} ${row.market}`;
  const rank = Number(row.opponent_defense_rank);
  const teams = Number(row.opponent_defense_teams);
  $("#simDefenseRank").textContent = rank > 0 && teams > 0 ? `#${rank} of ${teams}` : "RANK PENDING";
  const rankPercent = rank > 0 && teams > 1 ? ((rank - 1) / (teams - 1)) * 100 : 50;
  $("#simRankBar").style.width = `${Math.max(3, Math.min(100, rankPercent))}%`;
  $("#simDefenseAverage").textContent = row.opponent_defense_average == null ? "—" : Number(row.opponent_defense_average).toFixed(1);
  $("#simDefenseSamples").textContent = row.opponent_defense_samples
    ? `${row.opponent_defense_samples} games · ${row.opponent_defense_current_samples || 0} current-season`
    : "No matched sample";
  $("#simLeagueAverage").textContent = row.league_defense_average == null ? "—" : Number(row.league_defense_average).toFixed(1);
  $("#simAdjustment").textContent = `${Number(row.defense_adjustment) >= 0 ? "+" : ""}${pct(row.defense_adjustment)}`;
  $("#simAdjustment").className = Number(row.defense_adjustment) > 0 ? "positive" : Number(row.defense_adjustment) < 0 ? "negative" : "";

  $("#simSampleLabel").textContent = `${row.samples || 0} regular-season games · ${pct(row.confidence)} confidence`;
  $("#simRecentResults").innerHTML = (row.recent || []).map((value, index) => `<span><small>G${index + 1}</small><strong>${Number(value).toFixed(1)}</strong></span>`).join("");
  const checks = [
    { label: "Preseason excluded", pass: true },
    { label: `${row.samples || 0} player samples`, pass: Number(row.samples) >= 4 },
    { label: `${row.opponent_defense_samples || 0} opponent samples`, pass: Number(row.opponent_defense_samples) >= 4 },
    ...simulation.risks.map((label) => ({ label, pass: false })),
  ];
  $("#simRiskFlags").innerHTML = checks.map((item) => `<span class="${item.pass ? "pass" : "risk"}">${item.pass ? "✓" : "!"} ${escapeHtml(item.label)}</span>`).join("");

  const generated = new Date(state.meta?.generated_at);
  const ageHours = (Date.now() - generated.getTime()) / 3600000;
  $("#simDataState").textContent = Number.isNaN(generated.getTime()) ? "TIME UNKNOWN" : ageHours > 12 ? "STALE DATA" : "AUTO-UPDATED";
}

function renderLedger() {
  const summary = L.summary(state.ledger, state.bankroll);
  $("#ledgerSummary").innerHTML = [
    ["Bets", summary.bets],
    ["Pending", summary.pending],
    ["Record", `${summary.wins}–${summary.losses}`],
    ["Exposure", money(summary.exposure)],
    ["Available", money(summary.available)],
    ["P/L", money(summary.pnl, true)],
    ["ROI", pct(summary.roi)],
  ].map(([label, value]) => `<article><span>${label}</span><strong>${value}</strong></article>`).join("");
  const body = $("#ledgerBody");
  if (!state.ledger.length) {
    body.innerHTML = '<tr><td colspan="12" class="table-empty">My Ledger is empty. Review a qualified card and click Add to My Ledger only after placing the wager.</td></tr>';
    return;
  }
  body.innerHTML = state.ledger.map((item) => {
    const itemProfit = L.profit(item);
    const itemClv = L.clv(item);
    return `
      <tr>
        <td>${escapeHtml(item.start_time ? new Date(item.start_time).toLocaleDateString() : "—")}</td>
        <td><span class="tier ${tierClass(item.tier)}">${escapeHtml(item.tier)}</span></td>
        <td><strong>${escapeHtml(item.pick)}</strong><small>${escapeHtml(item.matchup)}</small></td>
        <td>${escapeHtml(item.book)}<small>${american(item.price_american)}</small></td>
        <td class="num">${pct(item.edge)}</td>
        <td class="num"><input class="ledger-stake" data-id="${escapeHtml(item.id)}" type="number" min="0" step="0.5" value="${Number(item.stake).toFixed(2)}" /></td>
        <td><select class="ledger-result" data-id="${escapeHtml(item.id)}">${["Pending", "Win", "Loss", "Push", "Void"].map((status) => `<option ${item.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></td>
        <td><input class="ledger-closing" data-id="${escapeHtml(item.id)}" type="number" step="1" placeholder="Odds" value="${escapeHtml(item.closing_odds)}" /></td>
        <td class="num ${itemClv == null ? "" : itemClv >= 0 ? "positive" : "negative"}">${pct(itemClv)}</td>
        <td class="num ${itemProfit > 0 ? "positive" : itemProfit < 0 ? "negative" : ""}">${money(itemProfit, true)}</td>
        <td><input class="ledger-notes" data-id="${escapeHtml(item.id)}" type="text" placeholder="Injury, line move…" value="${escapeHtml(item.notes)}" /></td>
        <td><button class="remove-ledger" data-id="${escapeHtml(item.id)}">Remove</button></td>
      </tr>`;
  }).join("");
}

function renderModel() {
  if (!state.meta) return;
  const source = (state.meta.source_by_sport || {}).NFL || {};
  $("#sourceHealth").innerHTML = `
    <div class="source-row"><span>League</span><strong>NFL only</strong></div>
    <div class="source-row"><span>Combined source</span><strong>${escapeHtml(source.source || "Unavailable")}</strong></div>
    <div class="source-row"><span>Schedule window</span><strong>${Number(state.meta.lookahead_days) || 21} days</strong></div>
    <div class="source-row"><span>Live price rows</span><strong>${Number(source.priced_quotes) || 0}</strong></div>
    <div class="source-row"><span>Eligible Ontario rows</span><strong>${Number(source.eligible_priced_quotes ?? source.target_priced_quotes ?? state.meta.counts?.eligible_priced_quotes ?? state.meta.counts?.target_priced_quotes) || 0}</strong></div>
    <div class="source-row"><span>Books returned</span><strong>${Number(state.meta.counts?.eligible_books) || 0}</strong></div>
    <div class="source-row"><span>Priced markets</span><strong>${Number(state.meta.counts?.priced_markets) || 0}</strong></div>
    <div class="source-row"><span>Form projections</span><strong>${Number(source.projections) || 0}</strong></div>
    <div class="source-row"><span>Matchup-adjusted rows</span><strong>${Number(state.meta.counts?.matchup_adjusted) || 0}</strong></div>
    <div class="source-row"><span>Simulator players</span><strong>${Number(state.meta.counts?.simulator_players) || 0}</strong></div>
    <div class="source-row"><span>Roster-verified teams</span><strong>${Number(state.meta.counts?.roster_verified_teams) || 0}</strong></div>
    <div class="source-row"><span>Projected markets</span><strong>${Number(state.meta.counts?.projected_markets) || 0}</strong></div>
    <div class="source-row"><span>Errors</span><strong>${(source.errors || []).length}</strong></div>`;
  $("#modelStatus").textContent = state.meta.model_status || "Model status unavailable.";
  $("#modelNotes").innerHTML = (state.meta.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("");
  $("#marketCoverage").innerHTML = (state.meta.market_coverage || []).map((market) => `<li>${escapeHtml(market)}</li>`).join("");
}

function renderSettings() {
  const host = $("#stakingSettings");
  if (!host || !STAKING) return;
  const settings = STAKING.normalise(state.staking);
  host.innerHTML = `
    <div class="staking-intro"><strong>${escapeHtml(STAKING.description(settings))}</strong><span>Saved only in this browser. Open ledger exposure is subtracted before new stake suggestions are planned.</span></div>
    <div class="staking-grid">
      <label><span>Starting bankroll<small>Used for local ledger and stake sizing.</small></span><div><b>C$</b><input id="setBankroll" type="number" min="1" step="25" value="${settings.bankroll}"></div></label>
      <label><span>Staking system<small>Choose one sizing method.</small></span><select id="setSystem"><option value="kelly" ${settings.system === "kelly" ? "selected" : ""}>Fractional Kelly</option><option value="flat" ${settings.system === "flat" ? "selected" : ""}>Flat stake</option><option value="percent" ${settings.system === "percent" ? "selected" : ""}>Bankroll percentage</option></select></label>
      <label><span>Kelly fraction<small>15% is the conservative model default.</small></span><div><input id="setKelly" type="number" min="0" max="100" step="5" value="${(settings.kelly_fraction * 100).toFixed(0)}"><b>%</b></div></label>
      <label><span>Flat stake<small>Used only with flat staking.</small></span><div><b>C$</b><input id="setFlat" type="number" min="0" step="1" value="${settings.flat_stake}"></div></label>
      <label><span>Bankroll stake<small>Used only with percentage staking.</small></span><div><input id="setPercent" type="number" min="0" max="100" step="0.5" value="${(settings.bankroll_pct * 100).toFixed(1)}"><b>%</b></div></label>
      <label><span>Maximum per play<small>Hard cap after every sizing method.</small></span><div><input id="setMaxPlay" type="number" min="0" max="100" step="1" value="${(settings.max_stake_pct * 100).toFixed(0)}"><b>%</b></div></label>
      <label><span>Maximum slate exposure<small>Scales all visible qualified props together.</small></span><div><input id="setMaxSlate" type="number" min="0" max="100" step="5" value="${(settings.max_slate_exposure_pct * 100).toFixed(0)}"><b>%</b></div></label>
      <label><span>Minimum stake<small>Smaller calculations display C$0.</small></span><div><b>C$</b><input id="setMinimum" type="number" min="0" step="0.5" value="${settings.min_stake}"></div></label>
      <label><span>Round stakes to<small>Sportsbook-friendly increment.</small></span><div><b>C$</b><input id="setRound" type="number" min="0.01" step="0.25" value="${settings.round_to}"></div></label>
    </div>
    <div class="staking-actions"><button id="saveStaking" class="button primary">Save on this device</button><button id="resetStaking" class="button">Restore model defaults</button><span id="stakingStatus"></span></div>
    <p class="staking-limit"><b>Important:</b> custom sizing changes suggested dollars only. It cannot turn a WATCH row into a bet, bypass a stale-price or roster block, or add a parlay to My Ledger.</p>`;
  const read = (selector) => Number($(selector).value);
  $("#saveStaking").onclick = () => {
    state.staking = STAKING.normalise({
      bankroll: read("#setBankroll"), system: $("#setSystem").value,
      kelly_fraction: read("#setKelly") / 100, flat_stake: read("#setFlat"),
      bankroll_pct: read("#setPercent") / 100, max_stake_pct: read("#setMaxPlay") / 100,
      max_slate_exposure_pct: read("#setMaxSlate") / 100,
      min_stake: read("#setMinimum"), round_to: read("#setRound"),
    });
    state.bankroll = state.staking.bankroll;
    const saved = STAKING.save(SETTINGS_KEY, state.staking);
    $("#bankrollInput").value = state.bankroll;
    render();
    $("#stakingStatus").textContent = saved ? "Saved locally." : "Browser storage is blocked.";
  };
  $("#resetStaking").onclick = () => {
    STAKING.clear(SETTINGS_KEY);
    state.staking = STAKING.normalise(null);
    state.bankroll = state.staking.bankroll;
    $("#bankrollInput").value = state.bankroll;
    render();
    $("#stakingStatus").textContent = "Defaults restored.";
  };
}

function switchView(view) {
  state.view = view;
  $$(".nav-tabs button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
}

function download(name, content, type) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

$$(".nav-tabs button").forEach((button) => button.addEventListener("click", () => {
  switchView(button.dataset.view);
  if (button.dataset.view === "simulator") renderSimulator();
}));
$("#dateSelect").addEventListener("change", (event) => { state.date = event.target.value; render(); });
$("#searchInput").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); render(); });
$$(".market-filter button").forEach((button) => button.addEventListener("click", () => {
  $$(".market-filter button").forEach((item) => item.classList.toggle("active", item === button));
  state.market = button.dataset.market;
  render();
}));
$("#simGame").addEventListener("change", (event) => {
  state.simulator.game = event.target.value;
  populateSimulator({ resetPlayer: true, resetMarket: true, resetLine: true });
  renderSimulator();
});
$("#simPlayer").addEventListener("change", (event) => {
  state.simulator.player = event.target.value;
  populateSimulator({ resetMarket: true, resetLine: true });
  renderSimulator();
});
$("#simMarket").addEventListener("change", (event) => {
  state.simulator.market = event.target.value;
  populateSimulator({ resetLine: true });
  renderSimulator();
});
$("#simLine").addEventListener("change", renderSimulator);
$("#simSide").addEventListener("change", renderSimulator);
$("#runSimulator").addEventListener("click", () => {
  const button = $("#runSimulator");
  button.textContent = "Running 10,000 trials…";
  button.disabled = true;
  requestAnimationFrame(() => {
    renderSimulator();
    button.textContent = "Run 10,000 simulations";
    button.disabled = false;
  });
});
$("#bankrollInput").addEventListener("change", (event) => {
  state.bankroll = Math.max(1, Number(event.target.value) || 500);
  state.staking = STAKING ? STAKING.normalise({ ...(state.staking || {}), bankroll: state.bankroll }) : state.staking;
  saveSettings();
  render();
});
$("#exportCsv").addEventListener("click", () => download("nfl-props-my-ledger.csv", L.toCsv(state.ledger), "text/csv"));
$("#exportJson").addEventListener("click", () => download("nfl-props-my-ledger.json", L.toJson(state.ledger), "application/json"));
$("#importJson").addEventListener("click", () => $("#ledgerFile").click());
$("#ledgerFile").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    state.ledger = L.fromJson(await file.text());
    L.save(localStorage, state.ledger);
    render();
  } catch (error) {
    window.alert(error.message);
  } finally {
    event.target.value = "";
  }
});
$("#clearLedger").addEventListener("click", () => {
  if (!state.ledger.length || !window.confirm("Clear every wager from My Ledger? Export a backup first if needed.")) return;
  state.ledger = [];
  L.save(localStorage, state.ledger);
  render();
});

document.addEventListener("click", (event) => {
  const addButton = event.target.closest(".add-ledger");
  if (addButton) {
    const row = state.betIndex[addButton.dataset.key];
    const stake = Number($(`#stake-${CSS.escape(addButton.dataset.key)}`)?.value);
    if (!row) return;
    const blocked = window.QuoteEligibility.blockReason(row, state.meta?.max_odds_age_hours ?? 12);
    if (blocked) { window.alert(blocked); render(); return; }
    const result = L.add(state.ledger, row, stake);
    if (!result.added) {
      if (result.reason === "stake") window.alert("Enter the actual stake before adding this wager.");
      return;
    }
    state.ledger = result.rows;
    L.save(localStorage, state.ledger);
    render();
    return;
  }
  const removeButton = event.target.closest(".remove-ledger");
  if (removeButton) {
    state.ledger = L.remove(state.ledger, removeButton.dataset.id);
    L.save(localStorage, state.ledger);
    render();
  }
});

document.addEventListener("change", (event) => {
  const id = event.target.dataset.id;
  if (!id) return;
  let patch = null;
  if (event.target.classList.contains("ledger-stake")) patch = { stake: Math.max(0, Number(event.target.value) || 0) };
  if (event.target.classList.contains("ledger-result")) patch = { status: event.target.value };
  if (event.target.classList.contains("ledger-closing")) patch = { closing_odds: event.target.value === "" ? "" : Number(event.target.value) };
  if (event.target.classList.contains("ledger-notes")) patch = { notes: event.target.value };
  if (!patch) return;
  state.ledger = L.update(state.ledger, id, patch);
  L.save(localStorage, state.ledger);
  render();
});

function showError(error) {
  const banner = $("#statusBanner");
  banner.className = "status-banner warn";
  banner.textContent = `Could not load the NFL prop data: ${error.message}`;
  $("#feedState").textContent = "OFFLINE";
}

loadSettings();
state.ledger = L.load(localStorage);
loadData().catch(showError);
function refreshExpiredQuotes() {
  if (state.meta && state.board.some(row=>row.tier!=="PASS" && window.QuoteEligibility.blockReason(row,state.meta.max_odds_age_hours??12))) render();
  else if (state.meta) renderParlays();
}
setInterval(refreshExpiredQuotes, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshExpiredQuotes(); });
