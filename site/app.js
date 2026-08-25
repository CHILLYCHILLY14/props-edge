const state = { meta: null, board: [], projections: [], imported: [], ledger: [], betIndex: {}, sport: "ALL", date: "ALL", search: "" };
const LEDGER_KEY = "props-edge-ledger-v1";
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
  try { const saved = JSON.parse(localStorage.getItem(LEDGER_KEY) || "[]"); state.ledger = Array.isArray(saved) ? saved : []; }
  catch { state.ledger = []; }
}

function saveLedger() {
  try { localStorage.setItem(LEDGER_KEY, JSON.stringify(state.ledger)); }
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
  return sport && date && query;
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
  renderStatus(); renderTierBoards(actionable); renderLedger(); renderProjections(projections); renderSources();
}

function renderStatus() {
  const banner = $("#statusBanner");
  const configured = state.meta.configured || {};
  const when = new Date(state.meta.generated_at).toLocaleString();
  if ((state.meta.counts?.priced_quotes || 0) > 0) {
    banner.className = "status-banner ok";
    banner.textContent = `Priced props loaded. Last model update: ${when}.`;
  } else {
    banner.className = "status-banner warn";
    banner.textContent = `No sportsbook prop prices were available. Showing ESPN projections only. Last update: ${when}.`;
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
  return `
    <article class="bet-card ${escapeHtml(row.tier.toLowerCase())}">
      <div class="card-top"><span>${escapeHtml(row.sport)} · ${escapeHtml(row.book)}</span><span class="badge">${escapeHtml(row.tier)}</span></div>
      <h3>${escapeHtml(row.pick)}</h3><div class="matchup">${escapeHtml([formatStart(row.start_time), row.matchup].filter(Boolean).join(" · "))}</div>
      <div class="model-source">${escapeHtml(row.model_label || `${row.consensus_books || 0}-book no-vig consensus`)}</div>
      <div class="numbers"><div><strong>${american(row.price_american)}</strong><span>Price</span></div><div><strong>${pct(row.edge)}</strong><span>Edge</span></div><div><strong>${pct(row.ev)}</strong><span>EV</span></div></div>
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
    pick: row.pick,
    matchup: row.matchup,
    book: row.book,
    price_american: row.price_american,
    edge: row.edge,
    ev: row.ev,
    stake: 10,
    status: "Pending",
  });
  saveLedger(); render();
}

function ledgerProfit(item) {
  const stake = Math.max(0, Number(item.stake) || 0);
  if (item.status === "Win") return stake * (decimalOdds(item.price_american) - 1);
  if (item.status === "Loss") return -stake;
  return 0;
}

function renderLedger() {
  const profit = state.ledger.reduce((total, item) => total + ledgerProfit(item), 0);
  const pending = state.ledger.filter((item) => item.status === "Pending").length;
  const settled = state.ledger.filter((item) => item.status === "Win" || item.status === "Loss").length;
  $("#ledgerSummary").innerHTML = `<span><strong>${state.ledger.length}</strong> bets</span><span><strong>${pending}</strong> pending</span><span><strong>${settled}</strong> settled</span><span class="${profit > 0 ? "profit-positive" : profit < 0 ? "profit-negative" : ""}"><strong>${profit >= 0 ? "+" : ""}$${profit.toFixed(2)}</strong> P/L</span>`;
  const body = $("#ledgerBody");
  if (!state.ledger.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty">No bets saved yet. Use Add to ledger on a qualified card.</td></tr>';
    return;
  }
  body.innerHTML = state.ledger.map((item) => {
    const itemProfit = ledgerProfit(item); const profitClass = itemProfit > 0 ? "profit-positive" : itemProfit < 0 ? "profit-negative" : "";
    const date = item.start_time ? new Date(item.start_time).toLocaleDateString() : "—";
    return `<tr>
      <td>${escapeHtml(date)}</td><td><span class="badge">${escapeHtml(item.tier)}</span></td>
      <td class="ledger-pick"><strong>${escapeHtml(item.pick)}</strong><div class="matchup">${escapeHtml(item.matchup)}</div></td>
      <td>${escapeHtml(item.book)}<br>${american(item.price_american)}</td><td>${pct(item.edge)}</td>
      <td><input class="ledger-stake" data-ledger-id="${escapeHtml(item.id)}" type="number" min="0" step="1" value="${Number(item.stake) || 0}" aria-label="Stake" /></td>
      <td><select class="ledger-status" data-ledger-id="${escapeHtml(item.id)}" aria-label="Status">${["Pending", "Win", "Loss", "Void"].map((status) => `<option value="${status}" ${item.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></td>
      <td class="${profitClass}">${itemProfit >= 0 ? "+" : ""}$${itemProfit.toFixed(2)}</td>
      <td><button class="ledger-remove" data-ledger-id="${escapeHtml(item.id)}">Remove</button></td>
    </tr>`;
  }).join("");
}

function csvCell(value) { return `"${String(value ?? "").replaceAll('"', '""')}"`; }
function exportLedger() {
  const headers = ["date", "sport", "tier", "pick", "matchup", "book", "american_odds", "edge", "ev", "stake", "status", "profit_loss"];
  const rows = state.ledger.map((item) => [item.start_time, item.sport, item.tier, item.pick, item.matchup, item.book, item.price_american, item.edge, item.ev, item.stake, item.status, ledgerProfit(item)]);
  const content = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], { type: "text/csv" })); link.download = "props-edge-ledger.csv"; link.click(); URL.revokeObjectURL(link.href);
}

function renderProjections(rows) {
  const board = $("#projectionBoard");
  if (!rows.length) { board.innerHTML = '<div class="empty">No upcoming-player projections are available for this filter yet.</div>'; return; }
  board.innerHTML = rows.map((row) => `
    <article class="projection-card">
      <div class="card-top"><span>${escapeHtml(row.sport)}</span><span>${Math.round(row.confidence * 100)}% confidence</span></div>
      <h3>${escapeHtml(row.player)}</h3><div class="matchup">${escapeHtml([formatStart(row.start_time), row.matchup].filter(Boolean).join(" · "))}</div>
      <div class="projection-number">${Number(row.projection).toFixed(1)}</div>
      <strong>${escapeHtml(row.market)}</strong>
      <div class="recent">Recent: ${(row.recent || []).map((n) => Number(n).toFixed(0)).join(" · ")} · ${row.samples} games</div>
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

function evaluateImported(rows) {
  return rows.flatMap((line) => {
    const projection = state.projections.find((row) => row.sport === line.sport && normalized(row.player) === normalized(line.player) && normalized(row.market) === normalized(line.market) && (state.date === "ALL" || dateKey(row.start_time) === state.date));
    if (!projection) return [];
    const sd = Math.max(Number(projection.standard_deviation) || 0, 0.75);
    const rawOver = 1 - normalCdf((Number(line.line) - Number(projection.projection)) / sd);
    const confidence = Number(projection.confidence) || .4;
    const overP = .5 + (rawOver - .5) * confidence; const underP = 1 - overP;
    const options = [["over", Number(line.over_odds), overP], ["under", Number(line.under_odds), underP]].filter((row) => Number.isFinite(row[1]) && row[1] !== 0);
    if (!options.length) return [];
    const scored = options.map(([side, price, probability]) => { const decimal = decimalOdds(price); return { side, price, probability, decimal, edge: probability - 1 / decimal, ev: probability * decimal - 1 }; }).sort((a, b) => b.ev - a.ev)[0];
    const tier = tierFor(scored.edge);
    return [{ ...projection, line: Number(line.line), book: line.book || "Imported book", matchup: line.matchup || projection.matchup, side: scored.side, price_american: scored.price, edge: scored.edge, ev: scored.ev, tier, pick: `${projection.player} — ${scored.side[0].toUpperCase() + scored.side.slice(1)} ${line.line} ${projection.market}`, mode: "local import" }];
  });
}

async function importLines(file) {
  const text = await file.text(); const lines = text.split(/\r?\n/).filter(Boolean); if (lines.length < 2) return;
  const headers = parseCsvLine(lines[0]).map((h) => h.toLowerCase());
  const rows = lines.slice(1).map((line) => Object.fromEntries(parseCsvLine(line).map((value, i) => [headers[i], value])));
  state.imported = evaluateImported(rows); render();
}

function downloadTemplate() {
  const content = "sport,player,market,line,over_odds,under_odds,book,matchup\nWNBA,Player Name,Points,20.5,-110,-110,theScore Bet,Away @ Home\n";
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], { type: "text/csv" })); link.download = "props-lines-template.csv"; link.click(); URL.revokeObjectURL(link.href);
}

$("#importButton").addEventListener("click", () => $("#lineFile").click());
$("#lineFile").addEventListener("change", (event) => event.target.files[0] && importLines(event.target.files[0]));
$("#downloadTemplate").addEventListener("click", downloadTemplate);
$("#exportLedger").addEventListener("click", exportLedger);
$("#dateSelect").addEventListener("change", (event) => { state.date = event.target.value; render(); });
$("#searchInput").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); render(); });
$$('#sportTabs button').forEach((button) => button.addEventListener("click", () => { $$('#sportTabs button').forEach((row) => row.classList.remove("active")); button.classList.add("active"); state.sport = button.dataset.sport; render(); }));
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
  saveLedger(); renderLedger();
});
function showError(error) { const banner = $("#statusBanner"); banner.className = "status-banner warn"; banner.textContent = `Could not load data: ${error.message}`; }
loadLedger();
loadData().catch(showError);
