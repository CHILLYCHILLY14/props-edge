const state = {
  meta: null,
  board: [],
  projections: [],
  ledger: [],
  betIndex: {},
  view: "best",
  market: "ALL",
  date: "ALL",
  search: "",
  bankroll: 500,
  schemaReady: false,
};

const L = window.NFLPropsLedger;
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
  if (value.includes("touchdown")) return "TOUCHDOWNS";
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
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    state.bankroll = Math.max(1, Number(saved.bankroll) || 500);
  } catch {
    state.bankroll = 500;
  }
  $("#bankrollInput").value = state.bankroll;
}

function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ bankroll: state.bankroll }));
}

async function loadData() {
  const stamp = Date.now();
  const [meta, board, projections] = await Promise.all([
    fetch(`data/meta.json?v=${stamp}`).then((response) => {
      if (!response.ok) throw new Error("meta feed unavailable");
      return response.json();
    }),
    fetch(`data/board.json?v=${stamp}`).then((response) => response.json()),
    fetch(`data/projections.json?v=${stamp}`).then((response) => response.json()),
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
  populateDates();
  render();
}

function populateDates() {
  const dates = [...new Set(
    [...state.board, ...state.projections].map((row) => dateKey(row.start_time)).filter(Boolean),
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

function render() {
  renderStatus();
  renderMetrics();
  renderCards();
  renderFullBoard();
  renderProjections();
  renderLedger();
  renderModel();
}

function renderStatus() {
  if (!state.meta) return;
  const banner = $("#statusBanner");
  const generated = new Date(state.meta.generated_at);
  const ageHours = (Date.now() - generated.getTime()) / 3600000;
  const counts = state.meta.counts || {};
  const source = (state.meta.source_by_sport || {}).NFL || {};
  const lookahead = Number(state.meta.lookahead_days) || 21;
  const formReady = Number(source.projections) > 0;
  const pricesReady = Number(source.priced_quotes) > 0;
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
  } else if (Number(source.priced_quotes) > 0) {
    banner.className = "status-banner ok";
    banner.textContent = `${counts.actionable || 0} qualified NFL props from ${source.priced_quotes} live price rows. ${state.meta.model_status || ""}`;
    $("#feedState").textContent = "LIVE";
  } else if (Number(source.projections) > 0) {
    banner.className = "status-banner warn";
    banner.textContent = `Regular-season form is ready and the model checks ${lookahead} days ahead, but no current ${state.meta.target_book || "target-book"} player-prop prices were returned. Books often post these closer to kickoff; no wager can qualify without a complete live price.`;
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
  const stake = Number(row.recommended_stake) || 0;
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
        <div><span>Edge</span><strong>${pct(row.edge)}</strong></div>
        <div><span>Price value</span><strong>${pct(row.edge_real ?? row.ev)}</strong></div>
        <div><span>Samples</span><strong>${Number(row.projection_samples) || 0}</strong></div>
      </div>
      <div class="confidence-row"><span>Confidence ${pct(confidence)}</span><span>Push ${pct(row.push_prob)}</span></div>
      <div class="confidence-track"><i style="width:${Math.max(3, confidence * 100)}%"></i></div>
      <div class="manual-add">
        <label>Stake <span>C$</span><input id="stake-${escapeHtml(key)}" type="number" min="1" step="0.5" value="${stake.toFixed(2)}" /></label>
        <button class="add-ledger" data-key="${escapeHtml(key)}" ${saved ? "disabled" : ""}>${saved ? "In My Ledger" : "Add to My Ledger"}</button>
      </div>
      <p class="manual-note">Review the current line first. This button is the only way a wager is added.</p>
    </article>`;
}

function renderCardGroup(selector, rows, message) {
  $(selector).innerHTML = rows.length
    ? rows.map(betCard).join("")
    : `<div class="empty-state"><strong>No qualifying plays</strong><span>${escapeHtml(message)}</span></div>`;
}

function renderCards() {
  state.betIndex = {};
  const actionable = filteredBoard().filter((row) => row.tier !== "PASS");
  renderCardGroup("#bestBoard", actionable.filter((row) => row.tier === "BEST"), "Nothing clears every Best Bet gate for this filter.");
  renderCardGroup("#goodBoard", actionable.filter((row) => row.tier === "GOOD"), "No Good Plays clear the current data and price gates.");
  renderCardGroup("#leanBoard", actionable.filter((row) => row.tier === "LEAN"), "No Leans clear both model-edge and price-value gates.");
}

function renderFullBoard() {
  const rows = filteredBoard();
  const body = $("#boardBody");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="12" class="table-empty">No NFL target-book prop rows are available for this filter.</td></tr>';
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
      <p class="recent-values">Recent: ${(row.recent || []).map((value) => Number(value).toFixed(0)).join(" · ")}</p>
      <div class="projection-foot"><span>${pct(row.confidence)} confidence</span><span>${Number(row.current_season_samples) || 0} current</span><span>SD ${Number(row.standard_deviation || 0).toFixed(1)}</span><span>${Number(row.trend) >= 0 ? "▲" : "▼"} ${Math.abs(Number(row.trend) || 0).toFixed(1)}</span></div>
    </article>`).join("");
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
    <div class="source-row"><span>Priced markets</span><strong>${Number(state.meta.counts?.priced_markets) || 0}</strong></div>
    <div class="source-row"><span>Form projections</span><strong>${Number(source.projections) || 0}</strong></div>
    <div class="source-row"><span>Projected markets</span><strong>${Number(state.meta.counts?.projected_markets) || 0}</strong></div>
    <div class="source-row"><span>Errors</span><strong>${(source.errors || []).length}</strong></div>`;
  $("#modelStatus").textContent = state.meta.model_status || "Model status unavailable.";
  $("#modelNotes").innerHTML = (state.meta.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("");
  $("#marketCoverage").innerHTML = (state.meta.market_coverage || []).map((market) => `<li>${escapeHtml(market)}</li>`).join("");
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

$$(".nav-tabs button").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$("#dateSelect").addEventListener("change", (event) => { state.date = event.target.value; render(); });
$("#searchInput").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); render(); });
$$(".market-filter button").forEach((button) => button.addEventListener("click", () => {
  $$(".market-filter button").forEach((item) => item.classList.toggle("active", item === button));
  state.market = button.dataset.market;
  render();
}));
$("#bankrollInput").addEventListener("change", (event) => {
  state.bankroll = Math.max(1, Number(event.target.value) || 500);
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
