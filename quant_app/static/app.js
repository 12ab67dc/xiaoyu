const state = { dashboard: null, usSymbol: "QQQ", filter: "all", selectedId: null, usChart: null, detailChart: null, pollTimer: null };
const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const riskText = { critical: "紧急", high: "高", medium: "关注", normal: "正常" };

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  document.getElementById("todayLabel").textContent = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
  loadDashboard();
});

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-view-link]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.viewLink)));
  document.getElementById("refreshButton").addEventListener("click", refreshAll);
  ["addHolding", "addHoldingTop"].forEach(id => document.getElementById(id).addEventListener("click", () => openHoldingDialog()));
  ["closeDialog", "cancelDialog"].forEach(id => document.getElementById(id).addEventListener("click", closeHoldingDialog));
  document.getElementById("holdingForm").addEventListener("submit", saveHolding);
  document.getElementById("capitalInput").addEventListener("change", saveCapital);
  document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("active", item === button));
    renderHoldings();
  }));
}

function switchView(view) {
  document.querySelectorAll(".view").forEach(item => item.classList.toggle("active", item.id === `${view}View`));
  document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item.dataset.view === view));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const payload = await response.json(); message = payload.detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

async function loadDashboard(silent = false) {
  if (!silent) setSync("loading", "加载行情");
  try {
    state.dashboard = await api("/api/dashboard");
    renderDashboard();
    const refresh = state.dashboard.refresh;
    if (refresh.running) {
      setSync("loading", "刷新中");
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(() => loadDashboard(true), 1800);
    } else if (refresh.last_error) {
      setSync("", "部分数据失败");
    } else {
      const stamp = refresh.last_finished_at ? formatTime(refresh.last_finished_at) : "已载入缓存";
      setSync("live", stamp);
    }
  } catch (error) {
    setSync("", "连接失败");
    showToast(error.message);
  }
}

function renderDashboard() {
  const data = state.dashboard;
  const portfolio = data.portfolio;
  document.getElementById("capitalValue").textContent = money.format(portfolio.capital);
  document.getElementById("investedValue").textContent = money.format(portfolio.invested_value);
  document.getElementById("holdingCount").textContent = `${portfolio.holding_count} 个真实持仓`;
  const highRisk = portfolio.risk_counts.critical + portfolio.risk_counts.high;
  document.getElementById("riskCount").textContent = `${highRisk} 个`;
  document.getElementById("riskCaption").textContent = highRisk ? "需要优先复核" : "暂无高风险触发";
  document.getElementById("capitalInput").value = portfolio.capital;
  renderReport(data.report);
  renderRisks(data.holdings);
  renderHoldings();
}

function renderReport(report) {
  if (!report) return;
  document.getElementById("marketScore").textContent = signed(report.score);
  document.getElementById("marketRegime").textContent = `${report.regime} · ${report.tone}`;
  document.getElementById("regimeBadge").textContent = report.regime;
  document.getElementById("reportTone").textContent = report.tone;
  document.getElementById("reportAdvice").textContent = report.suggestion;
  document.getElementById("reportAsOf").textContent = `生成 ${formatTime(report.generated_at)}`;
  document.getElementById("marketDate").textContent = `行情截至 ${report.market_as_of || "--"}`;
  document.getElementById("observations").innerHTML = report.observations.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.getElementById("gaugeScore").textContent = signed(report.score);
  const gaugePosition = Math.max(2, Math.min(98, (report.score + 100) / 2));
  document.getElementById("gaugePin").style.left = `${gaugePosition}%`;
  renderUsTable(report.items);
  renderUsSelector(report.items);
  renderUsChart();
}

function renderUsTable(items) {
  document.getElementById("usMarketBody").innerHTML = items.map(item => {
    const trendUp = item.ma20 && item.close >= item.ma20;
    return `<tr><td><div class="asset-cell"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)} · ${escapeHtml(item.theme)}</span></div></td>
      <td>${number.format(item.close)}</td><td class="${direction(item.daily_change_pct)}">${pct(item.daily_change_pct)}</td>
      <td class="${direction(item.return_5d_pct)}">${pct(item.return_5d_pct)}</td><td class="${direction(item.return_20d_pct)}">${pct(item.return_20d_pct)}</td>
      <td>${fmt(item.rsi14)}</td><td class="trend ${trendUp ? "uptrend" : "downtrend"}">${trendUp ? "MA20 上方" : "MA20 下方"}</td></tr>`;
  }).join("");
}

function renderUsSelector(items) {
  const preferred = items.filter(item => ["QQQ", "SOXX", "SPY", "^VIX"].includes(item.symbol));
  if (!preferred.some(item => item.symbol === state.usSymbol) && preferred.length) state.usSymbol = preferred[0].symbol;
  document.getElementById("usChartSelector").innerHTML = preferred.map(item => `<button class="${item.symbol === state.usSymbol ? "active" : ""}" data-symbol="${item.symbol}">${escapeHtml(item.symbol)}</button>`).join("");
  document.querySelectorAll("#usChartSelector button").forEach(button => button.addEventListener("click", () => {
    state.usSymbol = button.dataset.symbol;
    renderUsSelector(items);
    renderUsChart();
  }));
}

function renderUsChart() {
  const report = state.dashboard?.report;
  const item = report?.items.find(value => value.symbol === state.usSymbol);
  if (!item || !window.Chart) return;
  state.usChart?.destroy();
  state.usChart = createLineChart(document.getElementById("usChart"), item.metrics?.history || item.history, item.name);
}

function createLineChart(canvas, history, label) {
  if (!history) return null;
  return new Chart(canvas, {
    type: "line",
    data: { labels: history.dates, datasets: [
      { label, data: history.close, borderColor: "#167655", backgroundColor: "rgba(22,118,85,.08)", borderWidth: 2, pointRadius: 0, tension: .18, fill: true },
      { label: "MA20", data: history.ma20, borderColor: "#9a6a2e", borderWidth: 1.4, pointRadius: 0, borderDash: [5, 4], tension: .18 }
    ]},
    options: { responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: "index" }, animation: { duration: 350 },
      plugins: { legend: { display: false }, tooltip: { displayColors: false } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 7, color: "#7a847e", font: { size: 10 } } }, y: { position: "right", grid: { color: "#edf0ed" }, ticks: { color: "#7a847e", font: { size: 10 } } } }
    }
  });
}

function renderRisks(holdings) {
  const actual = holdings.filter(item => Number(item.quantity) > 0);
  const risks = actual.flatMap(item => (item.signals || []).map(signal => ({ ...signal, holding: item })))
    .sort((a, b) => ({ critical: 3, high: 2, medium: 1, normal: 0 }[b.level] - ({ critical: 3, high: 2, medium: 1, normal: 0 }[a.level])));
  const container = document.getElementById("riskList");
  if (!risks.length) {
    container.innerHTML = `<div class="loading-block">尚未录入真实持仓</div>`;
    return;
  }
  container.innerHTML = risks.slice(0, 5).map(item => `<article class="risk-item ${item.level}"><div class="risk-head"><strong>${escapeHtml(item.holding.name)} · ${escapeHtml(item.title)}</strong><span class="risk-badge ${item.level}">${riskText[item.level]}</span></div><p>${escapeHtml(item.evidence)}</p><p class="action">${escapeHtml(item.action)}</p></article>`).join("");
}

function filteredHoldings() {
  const holdings = state.dashboard?.holdings || [];
  if (state.filter === "position") return holdings.filter(item => Number(item.quantity) > 0);
  if (state.filter === "watch") return holdings.filter(item => Number(item.quantity) === 0);
  return holdings;
}

function renderHoldings() {
  if (!state.dashboard) return;
  const holdings = filteredHoldings();
  document.getElementById("holdingsBody").innerHTML = holdings.map(item => holdingRow(item)).join("") || `<tr><td colspan="8"><div class="loading-block">当前筛选下没有标的</div></td></tr>`;
  document.getElementById("mobileHoldings").innerHTML = holdings.map(item => holdingCard(item)).join("") || `<div class="loading-block">当前筛选下没有标的</div>`;
  document.querySelectorAll("[data-select-id]").forEach(row => row.addEventListener("click", event => {
    if (event.target.closest("button")) return;
    selectHolding(Number(row.dataset.selectId));
  }));
  document.querySelectorAll("[data-edit-id]").forEach(button => button.addEventListener("click", () => openHoldingDialog(Number(button.dataset.editId))));
  document.querySelectorAll("[data-delete-id]").forEach(button => button.addEventListener("click", () => deleteHolding(Number(button.dataset.deleteId))));
  const selectedExists = state.selectedId && state.dashboard.holdings.some(item => item.id === state.selectedId);
  if (selectedExists) {
    selectHolding(state.selectedId, false);
  } else {
    state.selectedId = null;
    renderEmptyDetail();
  }
}

function renderEmptyDetail() {
  state.detailChart?.destroy();
  state.detailChart = null;
  document.getElementById("holdingDetail").innerHTML = `<div class="empty-state"><span>⌁</span><strong>选择一个标的</strong><p>查看价格趋势与信号证据</p></div>`;
}

function holdingRow(item) {
  const metrics = item.metrics;
  const watch = Number(item.quantity) === 0;
  return `<tr data-id="${item.id}" data-select-id="${item.id}" class="${item.id === state.selectedId ? "selected" : ""}">
    <td><div class="asset-cell"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)}</span></div></td><td>${escapeHtml(item.asset_type)}</td>
    <td>${metrics ? number.format(metrics.close) : "--"}</td><td class="${metrics ? direction(metrics.daily_change_pct) : ""}">${metrics ? pct(metrics.daily_change_pct) : "--"}</td>
    <td>${watch ? "仅自选" : `${number.format(item.quantity)} / ${number.format(item.cost_price)}`}</td><td class="${direction(item.pnl_pct)}">${watch ? "--" : pct(item.pnl_pct)}</td>
    <td><span class="risk-badge ${item.risk_level || "normal"}">${watch ? "观察" : riskText[item.risk_level || "normal"]}</span></td>
    <td><div class="row-actions"><button class="mini-button" data-edit-id="${item.id}" title="编辑" aria-label="编辑">✎</button><button class="mini-button" data-delete-id="${item.id}" title="删除" aria-label="删除">×</button></div></td></tr>`;
}

function holdingCard(item) {
  const metrics = item.metrics;
  const watch = Number(item.quantity) === 0;
  return `<article class="holding-card" data-select-id="${item.id}"><div class="holding-card-head"><div class="asset-cell"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)} · ${escapeHtml(item.asset_type)}</span></div><span class="risk-badge ${item.risk_level || "normal"}">${watch ? "观察" : riskText[item.risk_level || "normal"]}</span></div>
  <div class="holding-card-values"><div>现价<strong>${metrics ? number.format(metrics.close) : "--"}</strong></div><div>当日<strong class="${metrics ? direction(metrics.daily_change_pct) : ""}">${metrics ? pct(metrics.daily_change_pct) : "--"}</strong></div><div>持仓盈亏<strong class="${direction(item.pnl_pct)}">${watch ? "--" : pct(item.pnl_pct)}</strong></div></div>
  <div class="holding-card-actions"><span>${watch ? "仅自选" : `${number.format(item.quantity)} 股`}</span><div><button class="mini-button" data-edit-id="${item.id}" aria-label="编辑">✎</button><button class="mini-button" data-delete-id="${item.id}" aria-label="删除">×</button></div></div></article>`;
}

function selectHolding(id, scroll = true) {
  const item = state.dashboard?.holdings.find(value => value.id === id);
  if (!item) return;
  state.selectedId = id;
  document.querySelectorAll("tr[data-id]").forEach(row => row.classList.toggle("selected", Number(row.dataset.id) === id));
  const detail = document.getElementById("holdingDetail");
  if (!item.metrics) {
    detail.innerHTML = `<div class="empty-state"><strong>${escapeHtml(item.name)}</strong><p>行情尚未加载，请刷新后重试</p></div>`;
    return;
  }
  const metrics = item.metrics;
  detail.innerHTML = `<div class="detail-head"><div><h2>${escapeHtml(item.name)}</h2><div class="detail-meta">${escapeHtml(item.symbol)} · ${escapeHtml(item.asset_type)} · ${escapeHtml(item.source || "公开行情")}</div></div><div class="detail-price"><strong>${number.format(metrics.close)}</strong><span class="${direction(metrics.daily_change_pct)}">${pct(metrics.daily_change_pct)}</span></div></div>
    <div class="metric-strip"><div><span>5 日</span><strong class="${direction(metrics.return_5d_pct)}">${pct(metrics.return_5d_pct)}</strong></div><div><span>20 日</span><strong class="${direction(metrics.return_20d_pct)}">${pct(metrics.return_20d_pct)}</strong></div><div><span>RSI14</span><strong>${fmt(metrics.rsi14)}</strong></div></div>
    <div class="detail-chart"><canvas id="detailChartCanvas"></canvas></div><div class="detail-signals"><h3>当前信号与解释</h3>${(item.signals || []).map(signal => `<article class="risk-item ${signal.level}"><div class="risk-head"><strong>${escapeHtml(signal.title)}</strong><span class="risk-badge ${signal.level}">${riskText[signal.level]}</span></div><p>${escapeHtml(signal.evidence)}</p><p class="action">${escapeHtml(signal.action)}</p></article>`).join("")}</div>`;
  if (window.Chart) {
    state.detailChart?.destroy();
    state.detailChart = createLineChart(document.getElementById("detailChartCanvas"), metrics.history, item.name);
  }
  if (scroll && window.innerWidth <= 980) detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

function openHoldingDialog(id = null) {
  const item = id ? state.dashboard?.holdings.find(value => value.id === id) : null;
  document.getElementById("dialogTitle").textContent = item ? "编辑标的" : "添加标的";
  document.getElementById("holdingId").value = item?.id || "";
  document.getElementById("symbolInput").value = item?.symbol || "";
  document.getElementById("nameInput").value = item?.name || "";
  document.getElementById("typeInput").value = item?.asset_type || "ETF";
  document.getElementById("quantityInput").value = item?.quantity ?? 0;
  document.getElementById("costInput").value = item?.cost_price ?? 0;
  document.getElementById("lossInput").value = item?.loss_limit_pct ?? 8;
  document.getElementById("drawdownInput").value = item?.trailing_drawdown_pct ?? 8;
  document.getElementById("formError").textContent = "";
  document.getElementById("holdingDialog").showModal();
}

function closeHoldingDialog() { document.getElementById("holdingDialog").close(); }

async function saveHolding(event) {
  event.preventDefault();
  const id = document.getElementById("holdingId").value;
  const payload = { symbol: document.getElementById("symbolInput").value, name: document.getElementById("nameInput").value,
    asset_type: document.getElementById("typeInput").value, quantity: Number(document.getElementById("quantityInput").value), cost_price: Number(document.getElementById("costInput").value),
    loss_limit_pct: Number(document.getElementById("lossInput").value), trailing_drawdown_pct: Number(document.getElementById("drawdownInput").value), enabled: true };
  try {
    await api(id ? `/api/holdings/${id}` : "/api/holdings", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
    closeHoldingDialog(); showToast(id ? "标的已更新" : "标的已添加"); await refreshAll();
  } catch (error) { document.getElementById("formError").textContent = error.message; }
}

async function deleteHolding(id) {
  const item = state.dashboard?.holdings.find(value => value.id === id);
  if (!item || !window.confirm(`确认从列表删除“${item.name}”？`)) return;
  try { await api(`/api/holdings/${id}`, { method: "DELETE" }); state.selectedId = null; showToast("标的已删除"); await loadDashboard(true); }
  catch (error) { showToast(error.message); }
}

async function saveCapital(event) {
  const value = Number(event.target.value);
  if (!value) return;
  try { await api("/api/settings", { method: "PUT", body: JSON.stringify({ portfolio_capital: value }) }); showToast("资金规模已更新"); await loadDashboard(true); }
  catch (error) { showToast(error.message); }
}

async function refreshAll() {
  try { const response = await api("/api/refresh", { method: "POST", body: "{}" }); setSync("loading", "刷新中"); showToast(response.message); clearTimeout(state.pollTimer); state.pollTimer = setTimeout(() => loadDashboard(true), 1000); }
  catch (error) { showToast(error.message); }
}

function setSync(kind, text) { const node = document.getElementById("syncState"); node.className = `sync-state ${kind}`; node.querySelector("span").textContent = text; }
function showToast(message) { const node = document.getElementById("toast"); node.textContent = message; node.classList.add("show"); clearTimeout(node.timer); node.timer = setTimeout(() => node.classList.remove("show"), 2600); }
function pct(value) { return value == null ? "--" : `${value >= 0 ? "+" : ""}${Number(value).toFixed(2)}%`; }
function signed(value) { return value == null ? "--" : `${value > 0 ? "+" : ""}${Number(value).toFixed(1)}`; }
function fmt(value) { return value == null ? "--" : Number(value).toFixed(1); }
function direction(value) { return value == null || Number(value) === 0 ? "" : Number(value) > 0 ? "up" : "down"; }
function formatTime(value) { try { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); } catch (_) { return "--"; } }
function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value ?? ""; return div.innerHTML; }
