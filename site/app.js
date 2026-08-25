const state = { meta: null, board: [], projections: [], imported: [], ledger: [], betIndex: {}, sport: "ALL", market: "ALL", date: "ALL", search: "", bankroll: 500, maxStake: 50 };
const LEDGER_KEY = "props-edge-ledger-v1";
const SETTINGS_KEY = "props-edge-settings-v1";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
const american = (value) => value == null ? "—" : `${Number(value) > 0 ? "+" : ""}${Math.round(Number(value))}`;
const normalized = (value) => String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
const dateKey = (value) => {
  if (!value) return "";
  const date = new Date(value); if (Number.isNaN(date.getTime())) return "";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};
const formatStart = (value) => value ? new Date(value).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

function loadLedger() {
  try {
    const saved = JSON.parse(localStorage.getItem(LEDGER_KEY) || "[]");
    state.ledger = Array.isArray(saved) ? saved.map((item) => ({ market: "Player prop", confidence: null, closing_odds: "", notes: "", ...item })) : [];
  }
  catch { state.ledger = []; }
}

function saveLedger() {
  try { localStorage.setItem(LEDGER_KEY, JSON.stringify(state.ledger)); }
  catch { /* Private browsing may disable persistent storage. */ }
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    state.bankroll = Math.max(1, Number(saved.bankroll) || 500);
    state.maxStake = Math.max(1, Number(saved.maxStake) || 50);
  } catch { state.bankroll = 500; state.maxStake = 50; }
  $("#bankrollInput").value = state.bankroll;
  $("#maxStakeInput").value = state.maxStake;
}

function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify({ bankroll: state.bankroll, maxStake: state.maxStake })); }
  catch { /* Private browsing may disable persistent storage. */ }
}

async function loadData() {
  const stamp = Date.now();
  const [meta, board, projections] = await Promise.all([
    fetch(`data/meta.json?v=${stamp}`).then((r) => r.json()),
    fetch(`data/board.json?v=${stamp}`).then((r) => r.json()),
    fetch(`data/projections.json?v=${stamp}`).then((r) => r.json()),
  ]);
  state.meta = meta; state.board = board; state.projections = projections;
  populateDates();
  render();
}

function dateLabel(key) {
  const [year, month, day] = key.split("-").map(Number); const target = new Date(year, month - 1, day); const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()); const difference = Math.round((target - today) / 86400000);
  const prefix = difference === 0 ? "Today — " : difference === 1 ? "Tomorrow — " : "";
  return `${prefix}${target.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}`;
}

function populateDates() {
  const dates = [...new Set([...state.board, ...state.projections].map((row) => dateKey(row.start_time)).filter(Boolean))].sort();
  const select = $("#dateSelect"); const current = state.date;
  select.innerHTML = '<option value="ALL">All upcoming dates</option>' + dates.map((date) => `<option value="${date}">${escapeHtml(dateLabel(date))}</option>`).join("");
  if (current !== "ALL" && dates.includes(current)) select.value = current; else { state.date = "ALL"; select.value = "ALL"; }
}

function visible(row) {
  const sport = state.sport === "ALL" || row.sport === state.sport;
  const date = state.date === "ALL" || dateKey(row.start_time) === state.date;
  const query = !state.search || `${row.player} ${row.market} ${row.matchup || ""}`.toLowerCase().includes(state.search);
  const market = state.sport !== "NFL" || state.market === "ALL" || marketGroup(row.market) === state.market;
  return sport && date && query && market;
}

function marketGroup(market) {
  const value = String(market || "").toLowerCase();
  if (value.includes("touchdown")) return "TOUCHDOWNS";
  if (value.includes("target")) return "TARGETS";
  if (value.includes("pass") || value.includes("interception")) return "PASSING";
  if (value.includes("rush") || value.includes("carr")) return "RUSHING";
  if (value.includes("receiv") || value.includes("reception")) return "RECEIVING";
  if (value.includes("tackle") || value.includes("sack") || value.includes("defens")) return "DEFENSE";
  if (value.includes("kick") || value.includes("field goal") || value.includes("extra point")) return "KICKING";
  return "OTHER";
}

function money(value, signed = false) {
  const number = Number(value) || 0;
  return `${signed && number >= 0 ? "+" : ""}$${number.toFixed(2)}`;
}

function recommendedStake(row) {
  const decimal = decimalOdds(row.price_american);
  const probability = Number(row.model_prob);
  let fullKelly = Number(row.full_kelly);
  if (!Number.isFinite(fullKelly) && Number.isFinite(probability) && decimal > 1) fullKelly = Math.max(0, (probability * decimal - 1) / (decimal - 1));
  if (!Number.isFinite(fullKelly)) return Math.min(state.maxStake, Number(row.recommended_stake) || 10);
  return Math.max(0, Math.min(state.maxStake, state.bankroll * fullKelly * 0.25));
}

function render() {
  const allBets = [...state.board, ...state.imported];
  const filteredBets = allBets.filter(visible); const filteredProjections = state.projections.filter(visible);
  const actionable = filteredBets.filter((row) => row.tier !== "PASS");
  const projections = filteredProjections.slice(0, 120);
  $("#metricBest").textContent = filteredBets.filter((row) => row.tier === "BEST").length;
  $("#metricGood").textContent = filteredBets.filter((row) => row.tier === "GOOD").length;
  $("#metricLean").textContent = filteredBets.filter((row) => row.tier === "LEAN").length;
  $("#metricProjection").textContent = filteredProjections.length;
  renderMode(); renderStatus(); renderTierBoards(actionable); renderLedger(); renderNflDashboard(); renderProjections(projections); renderSources();
}

function renderMode() {
  const nflMode = state.sport === "NFL";
  document.body.classList.toggle("nfl-mode", nflMode);
  $("#nflDashboard").hidden = !nflMode;
  $("#bestTitle").textContent = nflMode ? "NFL Best Bets" : "Best Bets";
  $("#goodTitle").textContent = nflMode ? "NFL Good Plays" : "Good Plays";
  $("#leanTitle").textContent = nflMode ? "NFL Leans" : "Leans";
  $("#projectionTitle").textContent = nflMode ? "NFL Projection Board" : "Best projections";
  $("#bestDescription").textContent = nflMode ? "Top NFL props ranked by probability edge, expected value and controlled stake size." : "Highest-rated qualified plays for the selected sport and date.";
}

function renderStatus() {
  const banner = $("#statusBanner");
  const configured = state.meta.configured || {};
  const when = new Date(state.meta.generated_at).toLocaleString();
  const sportInfo = state.sport === "ALL" ? null : (state.meta.source_by_sport || {})[state.sport];
  const priced = sportInfo ? Number(sportInfo.priced_quotes) || 0 : Number(state.meta.counts?.priced_quotes) || 0;
  const projections = sportInfo ? Number(sportInfo.projections) || 0 : Number(state.meta.counts?.projections) || 0;
  if (priced > 0) {
    banner.className = "status-banner ok";
    banner.textContent = `${state.sport === "ALL" ? "Priced props" : `${state.sport} priced props`} loaded. Last model update: ${when}.`;
  } else if (projections > 0) {
    banner.className = "status-banner warn";
    banner.textContent = `${state.sport === "ALL" ? "Player" : state.sport} projections are ready, but no current sportsbook prices were available for this filter. Last update: ${when}.`;
  } else {
    banner.className = "status-banner warn";
    banner.textContent = `No current prices or projections were available for ${state.sport === "ALL" ? "the board" : state.sport}. Last update: ${when}.`;
  }
  const workflow = $("#workflowButton");
  if (state.meta.workflow_url) workflow.href = state.meta.workflow_url; else workflow.style.display = "none";
  if (!configured.odds_api_io && !configured.the_odds_api) banner.textContent += " Odds-provider secrets are not configured.";
}

function betKey(row) {
  return [row.sport, row.event_id, row.book, row.player, row.market, row.side, row.line, row.start_time].map(normalized).join("-");
}

function betCard(row) {
  const key = betKey(row); state.betIndex[key] = row;
  const saved = state.ledger.some((item) => item.id === key);
  const stake = recommendedStake(row);
  const confidence = Number(row.confidence) || 0;
  return `
    <article class="bet-card ${escapeHtml(row.tier.toLowerCase())}">
      <div class="card-top"><span>${escapeHtml(row.sport)} · ${escapeHtml(row.book)} · ${escapeHtml(marketGroup(row.market))}</span><span class="badge">${escapeHtml(row.tier)}</span></div>
      <h3>${escapeHtml(row.pick)}</h3><div class="matchup">${escapeHtml([formatStart(row.start_time), row.matchup].filter(Boolean).join(" · "))}</div>
      <div class="model-source">${escapeHtml(row.model_label || `${row.consensus_books || 0}-book no-vig consensus`)}</div>
      <div class="confidence-track"><span style="width:${Math.max(4, confidence * 100)}%"></span></div>
      <div class="numbers"><div><strong>${american(row.price_american)}</strong><span>Price</span></div><div><strong>${pct(row.edge)}</strong><span>Edge</span></div><div><strong>${pct(row.ev)}</strong><span>EV</span></div><div><strong>${money(stake)}</strong><span>¼ Kelly stake</span></div></div>
      <div class="card-actions"><button class="ledger-add" data-bet-key="${escapeHtml(key)}" ${saved ? "disabled" : ""}>${saved ? "In ledger" : "Add to ledger"}</button></div>
    </article>`;
}

function renderTierBoard(selector, rows, emptyText) {
  const board = $(selector);
  board.innerHTML = rows.length ? rows.map(betCard).join("") : `<div class="empty">${escapeHtml(emptyText)}</div>`;
}

function renderTierBoards(rows) {
  state.betIndex = {};
  renderTierBoard("#bestBoard", rows.filter((row) => row.tier === "BEST"), "No Best Bets clear the 6% threshold for this filter.");
  renderTierBoard("#goodBoard", rows.filter((row) => row.tier === "GOOD"), "No Good Plays clear the 4% threshold for this filter.");
  renderTierBoard("#leanBoard", rows.filter((row) => row.tier === "LEAN"), "No Leans clear the 2% threshold for this filter.");
}

function addToLedger(row) {
  const id = betKey(row);
  if (state.ledger.some((item) => item.id === id)) return;
  state.ledger.unshift({
    id,
    added_at: new Date().toISOString(),
    start_time: row.start_time || "",
    sport: row.sport,
    tier: row.tier,
    market: row.market,
    bet_type: marketGroup(row.market),
    pick: row.pick,
    matchup: row.matchup,
    book: row.book,
    price_american: row.price_american,
    edge: row.edge,
    ev: row.ev,
    confidence: row.confidence,
    stake: Number(recommendedStake(row).toFixed(2)),
    status: "Pending",
    closing_odds: "",
    notes: "",
  });
  saveLedger(); render();
}

function ledgerProfit(item) {
  const stake = Math.max(0, Number(item.stake) || 0);
  if (item.status === "Win") return stake * (decimalOdds(item.price_american) - 1);
  if (item.status === "Loss") return -stake;
  return 0;
}

function ledgerClv(item) {
  const closing = Number(item.closing_odds);
  if (!Number.isFinite(closing) || closing === 0) return null;
  return decimalOdds(item.price_american) / decimalOdds(closing) - 1;
}

function ledgerStats(rows = state.ledger) {
  const profit = rows.reduce((total, item) => total + ledgerProfit(item), 0);
  const pendingRows = rows.filter((item) => item.status === "Pending");
  const settledRows = rows.filter((item) => ["Win", "Loss", "Push", "Void"].includes(item.status));
  const exposure = pendingRows.reduce((total, item) => total + Math.max(0, Number(item.stake) || 0), 0);
  const settledStake = settledRows.reduce((total, item) => total + Math.max(0, Number(item.stake) || 0), 0);
  const available = state.bankroll + profit - exposure;
  return { profit, pending: pendingRows.length, settled: settledRows.length, exposure, available, roi: settledStake ? profit / settledStake : 0 };
}

function renderNflDashboard() {
  const stats = ledgerStats(state.ledger.filter((item) => item.sport === "NFL"));
  $("#nflBankroll").textContent = money(state.bankroll);
  $("#nflExposure").textContent = `${money(stats.exposure)} · ${pct(stats.exposure / Math.max(1, state.bankroll + stats.profit))}`;
  $("#nflAvailable").textContent = money(stats.available);
  $("#nflProfit").textContent = money(stats.profit, true);
  $("#nflProfit").className = stats.profit > 0 ? "profit-positive" : stats.profit < 0 ? "profit-negative" : "";
  $("#nflRoi").textContent = pct(stats.roi);
}

function renderLedger() {
  const stats = ledgerStats();
  $("#ledgerSummary").innerHTML = `<span><strong>${state.ledger.length}</strong> bets</span><span><strong>${stats.pending}</strong> pending</span><span><strong>${stats.settled}</strong> settled</span><span><strong>${money(stats.exposure)}</strong> open exposure</span><span><strong>${money(stats.available)}</strong> available</span><span class="${stats.profit > 0 ? "profit-positive" : stats.profit < 0 ? "profit-negative" : ""}"><strong>${money(stats.profit, true)}</strong> P/L</span><span><strong>${pct(stats.roi)}</strong> ROI</span>`;
  const body = $("#ledgerBody");
  if (!state.ledger.length) {
    body.innerHTML = '<tr><td colspan="13" class="empty">No bets saved yet. Use Add to ledger on a qualified card.</td></tr>';
    return;
  }
  body.innerHTML = state.ledger.map((item) => {
    const itemProfit = ledgerProfit(item); const profitClass = itemProfit > 0 ? "profit-positive" : itemProfit < 0 ? "profit-negative" : ""; const clv = ledgerClv(item);
    const date = item.start_time ? new Date(item.start_time).toLocaleDateString() : "—";
    return `<tr>
      <td>${escapeHtml(date)}</td><td><strong>${escapeHtml(item.sport)}</strong></td><td><span class="badge">${escapeHtml(item.tier)}</span></td>
      <td><span class="market-chip">${escapeHtml(item.bet_type || marketGroup(item.market))}</span></td>
      <td class="ledger-pick"><strong>${escapeHtml(item.pick)}</strong><div class="matchup">${escapeHtml(item.matchup)}</div></td>
      <td>${escapeHtml(item.book)}<br>${american(item.price_american)}</td><td>${pct(item.edge)}<br><span class="muted">${pct(item.confidence)} conf.</span></td>
      <td><input class="ledger-stake" data-ledger-id="${escapeHtml(item.id)}" type="number" min="0" max="${state.maxStake}" step="1" value="${Number(item.stake) || 0}" aria-label="Stake" /></td>
      <td><select class="ledger-status" data-ledger-id="${escapeHtml(item.id)}" aria-label="Status">${["Pending", "Win", "Loss", "Push", "Void"].map((status) => `<option value="${status}" ${item.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></td>
      <td><input class="ledger-closing" data-ledger-id="${escapeHtml(item.id)}" type="number" step="1" value="${escapeHtml(item.closing_odds)}" placeholder="Odds" aria-label="Closing odds" /><br><span class="${clv == null ? "muted" : clv >= 0 ? "profit-positive" : "profit-negative"}">${clv == null ? "— CLV" : `${pct(clv)} CLV`}</span></td>
      <td class="${profitClass}">${itemProfit >= 0 ? "+" : ""}$${itemProfit.toFixed(2)}</td>
      <td><input class="ledger-notes" data-ledger-id="${escapeHtml(item.id)}" type="text" value="${escapeHtml(item.notes)}" placeholder="Injury, line move…" aria-label="Notes" /></td>
      <td><button class="ledger-remove" data-ledger-id="${escapeHtml(item.id)}">Remove</button></td>
    </tr>`;
  }).join("");
}

function csvCell(value) { return `"${String(value ?? "").replaceAll('"', '""')}"`; }
function exportLedger() {
  const headers = ["date", "sport", "tier", "bet_type", "market", "pick", "matchup", "book", "american_odds", "edge", "confidence", "ev", "recommended_stake", "status", "closing_odds", "clv", "profit_loss", "notes"];
  const rows = state.ledger.map((item) => [item.start_time, item.sport, item.tier, item.bet_type || marketGroup(item.market), item.market, item.pick, item.matchup, item.book, item.price_american, item.edge, item.confidence, item.ev, item.stake, item.status, item.closing_odds, ledgerClv(item), ledgerProfit(item), item.notes]);
  const content = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], { type: "text/csv" })); link.download = "props-edge-ledger.csv"; link.click(); URL.revokeObjectURL(link.href);
}

function renderProjections(rows) {
  const board = $("#projectionBoard");
  if (!rows.length) { board.innerHTML = '<div class="empty">No upcoming-player projections are available for this filter yet.</div>'; return; }
  board.innerHTML = rows.map((row) => `
    <article class="projection-card">
      <div class="card-top"><span>${escapeHtml(row.sport)} · ${escapeHtml(marketGroup(row.market))}</span><span>${Math.round(row.confidence * 100)}% confidence</span></div>
      <h3>${escapeHtml(row.player)}</h3><div class="matchup">${escapeHtml([formatStart(row.start_time), row.matchup].filter(Boolean).join(" · "))}</div>
      <div class="projection-number">${Number(row.projection).toFixed(1)}</div>
      <strong>${escapeHtml(row.market)}</strong>
      <div class="recent">Recent: ${(row.recent || []).map((n) => Number(n).toFixed(0)).join(" · ")} · ${row.samples} games</div>
      <div class="projection-foot"><span>${Number(row.trend) >= 0 ? "▲" : "▼"} ${Math.abs(Number(row.trend) || 0).toFixed(1)} trend</span><span>SD ${Number(row.standard_deviation || 0).toFixed(1)}</span></div>
    </article>`).join("");
}

function renderSources() {
  const sources = state.meta.source_by_sport || {};
  $("#sourceHealth").innerHTML = Object.entries(sources).map(([sport, info]) => `<div class="source-row"><strong>${escapeHtml(sport)}</strong><span>${escapeHtml(info.source || "Unavailable")}</span></div>`).join("");
}

function parseCsvLine(line) {
  const cells = []; let current = ""; let quoted = false;
  for (let i = 0; i < line.length; i += 1) { const char = line[i]; if (char === '"' && line[i + 1] === '"') { current += '"'; i += 1; } else if (char === '"') quoted = !quoted; else if (char === "," && !quoted) { cells.push(current.trim()); current = ""; } else current += char; }
  cells.push(current.trim()); return cells;
}

function normalCdf(value) {
  const sign = value < 0 ? -1 : 1; const x = Math.abs(value) / Math.sqrt(2); const t = 1 / (1 + 0.3275911 * x);
  const erf = sign * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x));
  return 0.5 * (1 + erf);
}
function decimalOdds(price) { const p = Number(price); return p > 0 ? 1 + p / 100 : 1 + 100 / Math.abs(p); }
function tierFor(edge) { if (edge >= .06) return "BEST"; if (edge >= .04) return "GOOD"; if (edge >= .02) return "LEAN"; return "PASS"; }

function touchdownYesProbability(projection) {
  const recent = projection.recent || [projection.projection];
  const hits = recent.filter((value) => Number(value) >= 1).length;
  const empirical = (hits + 0.5) / (recent.length + 2);
  const poisson = 1 - Math.exp(-Math.max(0, Number(projection.projection) || 0));
  const confidence = Math.max(.2, Math.min(.72, Number(projection.confidence) || .4));
  return Math.max(.04, Math.min(.85, .2 + (((empirical + poisson) / 2) - .2) * confidence));
}

function evaluateImported(rows) {
  return rows.flatMap((line) => {
    const projection = state.projections.find((row) => row.sport === line.sport && normalized(row.player) === normalized(line.player) && normalized(row.market) === normalized(line.market) && (state.date === "ALL" || dateKey(row.start_time) === state.date));
    if (!projection) return [];
    const touchdown = normalized(line.market) === "anytimetouchdown";
    let options = [];
    if (touchdown) {
      const yesP = touchdownYesProbability(projection);
      options = [["yes", Number(line.yes_odds), yesP], ["no", Number(line.no_odds), 1 - yesP]];
    } else {
      const point = Number(line.line); if (!Number.isFinite(point)) return [];
      const sd = Math.max(Number(projection.standard_deviation) || 0, 0.75);
      const rawOver = 1 - normalCdf((point - Number(projection.projection)) / sd);
      const confidence = Number(projection.confidence) || .4;
      const overP = .5 + (rawOver - .5) * confidence; const underP = 1 - overP;
      options = [["over", Number(line.over_odds), overP], ["under", Number(line.under_odds), underP]];
    }
    options = options.filter((row) => Number.isFinite(row[1]) && row[1] !== 0);
    if (!options.length) return [];
    const scored = options.map(([side, price, probability]) => { const decimal = decimalOdds(price); return { side, price, probability, decimal, edge: probability - 1 / decimal, ev: probability * decimal - 1 }; }).sort((a, b) => b.ev - a.ev)[0];
    const tier = tierFor(scored.edge);
    const pickLine = touchdown ? "" : ` ${line.line}`;
    const decimal = decimalOdds(scored.price); const fullKelly = Math.max(0, (scored.probability * decimal - 1) / (decimal - 1));
    return [{ ...projection, line: touchdown ? null : Number(line.line), book: line.book || "Imported book", matchup: line.matchup || projection.matchup, side: scored.side, price_american: scored.price, model_prob: scored.probability, full_kelly: fullKelly, recommended_stake: Math.min(state.maxStake, state.bankroll * fullKelly * .25), edge: scored.edge, ev: scored.ev, tier, pick: `${projection.player} — ${scored.side[0].toUpperCase() + scored.side.slice(1)}${pickLine} ${projection.market}`, mode: "local import", model_label: "Imported price vs ESPN projection" }];
  });
}

async function importLines(file) {
  const text = await file.text(); const lines = text.split(/\r?\n/).filter(Boolean); if (lines.length < 2) return;
  const headers = parseCsvLine(lines[0]).map((h) => h.toLowerCase());
  const rows = lines.slice(1).map((line) => Object.fromEntries(parseCsvLine(line).map((value, i) => [headers[i], value])));
  state.imported = evaluateImported(rows); render();
}

function downloadTemplate() {
  const content = "sport,player,market,line,over_odds,under_odds,yes_odds,no_odds,book,matchup\nNFL,Player Name,Receiving yards,64.5,-110,-110,,,DraftKings,Away @ Home\nNFL,Player Name,Anytime touchdown,,,,+150,,DraftKings,Away @ Home\nWNBA,Player Name,Points,20.5,-110,-110,,,theScore Bet,Away @ Home\n";
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], { type: "text/csv" })); link.download = "props-lines-template.csv"; link.click(); URL.revokeObjectURL(link.href);
}

$("#importButton").addEventListener("click", () => $("#lineFile").click());
$("#lineFile").addEventListener("change", (event) => event.target.files[0] && importLines(event.target.files[0]));
$("#downloadTemplate").addEventListener("click", downloadTemplate);
$("#exportLedger").addEventListener("click", exportLedger);
$("#dateSelect").addEventListener("change", (event) => { state.date = event.target.value; render(); });
$("#searchInput").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); render(); });
$$('#sportTabs button').forEach((button) => button.addEventListener("click", () => { $$('#sportTabs button').forEach((row) => row.classList.remove("active")); button.classList.add("active"); state.sport = button.dataset.sport; state.market = "ALL"; $$('#nflMarketTabs button').forEach((row) => row.classList.toggle("active", row.dataset.market === "ALL")); render(); }));
$$('#nflMarketTabs button').forEach((button) => button.addEventListener("click", () => { $$('#nflMarketTabs button').forEach((row) => row.classList.remove("active")); button.classList.add("active"); state.market = button.dataset.market; render(); }));
$("#bankrollInput").addEventListener("change", (event) => { state.bankroll = Math.max(1, Number(event.target.value) || 500); saveSettings(); render(); });
$("#maxStakeInput").addEventListener("change", (event) => { state.maxStake = Math.max(1, Number(event.target.value) || 50); saveSettings(); render(); });
document.addEventListener("click", (event) => {
  const addButton = event.target.closest(".ledger-add");
  if (addButton && state.betIndex[addButton.dataset.betKey]) addToLedger(state.betIndex[addButton.dataset.betKey]);
  const removeButton = event.target.closest(".ledger-remove");
  if (removeButton) { state.ledger = state.ledger.filter((item) => item.id !== removeButton.dataset.ledgerId); saveLedger(); render(); }
});
document.addEventListener("change", (event) => {
  const id = event.target.dataset.ledgerId; if (!id) return;
  const item = state.ledger.find((row) => row.id === id); if (!item) return;
  if (event.target.classList.contains("ledger-stake")) item.stake = Math.max(0, Number(event.target.value) || 0);
  if (event.target.classList.contains("ledger-status")) item.status = event.target.value;
  if (event.target.classList.contains("ledger-closing")) item.closing_odds = event.target.value;
  if (event.target.classList.contains("ledger-notes")) item.notes = event.target.value;
  saveLedger(); renderLedger(); renderNflDashboard();
});
function showError(error) { const banner = $("#statusBanner"); banner.className = "status-banner warn"; banner.textContent = `Could not load data: ${error.message}`; }
loadLedger();
loadSettings();
loadData().catch(showError);
