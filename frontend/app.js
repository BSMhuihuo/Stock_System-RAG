const state = {
  selectedSymbol: "600519",
  selectedName: "贵州茅台",
  marketPage: 1,
  marketLoaded: false,
  marketRequestId: 0,
  selectedRequestId: 0,
  opportunityTab: "watch",
  settings: {
    paper_initial_cash: 100000,
    auto_trade_budget_ratio: 0.2,
    default_auto_trade_strategy: "ensemble_rag",
    auto_trade_refresh_seconds: 30,
    enable_ollama_decision: true,
    ollama_timeout_seconds: 180,
    research_top_k: 4,
    market_page_size: 30,
    rag_retrieval_mode: "hybrid",
  },
};

const analysisCache = new Map();
let searchTimer = null;
let autoRefreshTimer = null;
let autoTradeLoopTimer = null;
let klineChart = null;
let searchAbortController = null;
let marketAbortController = null;
let searchRequestSeq = 0;
let watchRenderToken = 0;
let autoTradeRunning = false;
let autoTradeBusy = false;

const refs = {
  connectionStatus: document.getElementById("connectionStatus"),
  messageBar: document.getElementById("messageBar"),
  refreshAllBtn: document.getElementById("refreshAllBtn"),
  settingsBtn: document.getElementById("settingsBtn"),
  stockSearchInput: document.getElementById("stockSearchInput"),
  searchResults: document.getElementById("searchResults"),
  watchPane: document.getElementById("watchPane"),
  recommendPane: document.getElementById("recommendPane"),
  selectedStockName: document.getElementById("selectedStockName"),
  selectedStockCode: document.getElementById("selectedStockCode"),
  selectedStockMeta: document.getElementById("selectedStockMeta"),
  selectedStockPrice: document.getElementById("selectedStockPrice"),
  selectedStockChange: document.getElementById("selectedStockChange"),
  metricOpen: document.getElementById("metricOpen"),
  metricHighLow: document.getElementById("metricHighLow"),
  metricPrevClose: document.getElementById("metricPrevClose"),
  metricTimestamp: document.getElementById("metricTimestamp"),
  detailMetricsToggleBtn: document.getElementById("detailMetricsToggleBtn"),
  detailMetricsToggleText: document.getElementById("detailMetricsToggleText"),
  detailMetricsBody: document.getElementById("detailMetricsBody"),
  detailScoreGrid: document.getElementById("detailScoreGrid"),
  detailFactorsGrid: document.getElementById("detailFactorsGrid"),
  detailReasons: document.getElementById("detailReasons"),
  indexTape: document.getElementById("indexTape"),
  marketQueryInput: document.getElementById("marketQueryInput"),
  marketPageInput: document.getElementById("marketPageInput"),
  marketLoadBtn: document.getElementById("marketLoadBtn"),
  marketPageMeta: document.getElementById("marketPageMeta"),
  marketTableBody: document.getElementById("marketTableBody"),
  researchChunkingInfo: document.getElementById("researchChunkingInfo"),
  researchQuestionInput: document.getElementById("researchQuestionInput"),
  runResearchBtn: document.getElementById("runResearchBtn"),
  researchAnswer: document.getElementById("researchAnswer"),
  researchSources: document.getElementById("researchSources"),
  researchSourcesFull: document.getElementById("researchSourcesFull"),
  researchSourcesToggleBtn: document.getElementById("researchSourcesToggleBtn"),
  researchSourcesToggleText: document.getElementById("researchSourcesToggleText"),
  researchContexts: document.getElementById("researchContexts"),
  paperCash: document.getElementById("paperCash"),
  paperEquity: document.getElementById("paperEquity"),
  autoStrategySelect: document.getElementById("autoStrategySelect"),
  tradeQuantityInput: document.getElementById("tradeQuantityInput"),
  buyBtn: document.getElementById("buyBtn"),
  sellBtn: document.getElementById("sellBtn"),
  autoTradeBtn: document.getElementById("autoTradeBtn"),
  autoTradeMeta: document.getElementById("autoTradeMeta"),
  autoTradeMetaFull: document.getElementById("autoTradeMetaFull"),
  autoTradeMetaToggleBtn: document.getElementById("autoTradeMetaToggleBtn"),
  autoTradeMetaToggleText: document.getElementById("autoTradeMetaToggleText"),
  positionsTableBody: document.getElementById("positionsTableBody"),
  ordersTableBody: document.getElementById("ordersTableBody"),
  settingsModal: document.getElementById("settingsModal"),
  closeSettingsBtn: document.getElementById("closeSettingsBtn"),
  cancelSettingsBtn: document.getElementById("cancelSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  settingPaperInitialCash: document.getElementById("settingPaperInitialCash"),
  settingAutoTradeBudgetRatio: document.getElementById("settingAutoTradeBudgetRatio"),
  settingDefaultAutoTradeStrategy: document.getElementById("settingDefaultAutoTradeStrategy"),
  settingAutoTradeRefreshSeconds: document.getElementById("settingAutoTradeRefreshSeconds"),
  settingEnableOllamaDecision: document.getElementById("settingEnableOllamaDecision"),
  settingOllamaTimeoutSeconds: document.getElementById("settingOllamaTimeoutSeconds"),
  checkOllamaBtn: document.getElementById("checkOllamaBtn"),
  checkOllamaResult: document.getElementById("checkOllamaResult"),
  settingResearchTopK: document.getElementById("settingResearchTopK"),
  settingRagRetrievalMode: document.getElementById("settingRagRetrievalMode"),
  settingMarketPageSize: document.getElementById("settingMarketPageSize"),
  settingResetPaperAccount: document.getElementById("settingResetPaperAccount"),
};

function formatNumber(value, digits = 2) {
  const num = Number(value || 0);
  return Number.isFinite(num)
    ? num.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "--";
}

function formatSignedPct(value) {
  const num = Number(value || 0);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(2)}%`;
}

function getChangeClass(value) {
  const num = Number(value || 0);
  if (num > 0) return "positive";
  if (num < 0) return "negative";
  return "neutral";
}

function isAbortError(error) {
  return error && (error.name === "AbortError" || String(error).includes("aborted"));
}

async function apiGet(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: options.signal,
  });
  if (!response.ok) {
    throw new Error(`GET ${url} failed: ${response.status}`);
  }
  return response.json();
}

async function apiPost(url, data) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`POST ${url} failed: ${response.status} ${detail}`);
  }
  return response.json();
}

function setStatus(text, ok = true) {
  refs.connectionStatus.textContent = text;
  refs.connectionStatus.style.color = ok ? "#93f5b1" : "#fda4af";
  refs.connectionStatus.style.background = ok ? "rgba(34, 197, 94, 0.12)" : "rgba(239, 68, 68, 0.12)";
}

function setMessage(text, error = false) {
  refs.messageBar.textContent = text;
  refs.messageBar.className = `message-bar ${error ? "message-error" : ""}`;
}

function truncateText(text, maxLength = 120) {
  const normalized = String(text || "").trim();
  if (!normalized) return "";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength)}...`;
}

function bindCollapsibleToggle(toggleBtn, bodyEl, textEl) {
  if (!toggleBtn || !bodyEl || !textEl) return;
  toggleBtn.addEventListener("click", () => {
    bodyEl.classList.toggle("hidden");
    textEl.textContent = bodyEl.classList.contains("hidden") ? "展开" : "收起";
  });
}

function bindResearchSourcesToggle() {
  if (!refs.researchSourcesToggleBtn || !refs.researchSourcesFull || !refs.researchSourcesToggleText) return;
  refs.researchSourcesToggleBtn.addEventListener("click", () => {
    const willExpand = refs.researchSourcesFull.classList.contains("hidden");
    refs.researchSourcesFull.classList.toggle("hidden", !willExpand);
    if (refs.researchContexts) {
      refs.researchContexts.classList.toggle("hidden", !willExpand);
    }
    refs.researchSourcesToggleText.textContent = willExpand ? "收起" : "展开";
  });
}

function setCollapsibleText({ previewEl, fullEl, fullText, previewMax = 120, toggleBtn, toggleTextEl }) {
  const normalized = String(fullText || "").trim();
  const previewText = normalized ? truncateText(normalized.replace(/\s+/g, " "), previewMax) : "暂无内容";
  if (previewEl) previewEl.textContent = previewText;
  if (fullEl) fullEl.textContent = normalized || "暂无内容";
  if (toggleBtn) toggleBtn.disabled = !normalized;
  if (toggleTextEl && (!normalized || fullEl?.classList.contains("hidden"))) {
    toggleTextEl.textContent = "展开";
  }
}

function setAutoTradeRunningState(running) {
  autoTradeRunning = running;
  refs.autoTradeBtn.classList.toggle("is-running", running);
  refs.autoTradeBtn.textContent = running ? "停止自动交易" : "执行自动交易";
}

function createEmptyBlock(text) {
  const div = document.createElement("div");
  div.className = "empty-block";
  div.textContent = text;
  return div;
}

function createListCard({ title, subtitle, extra, onClick }) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "list-card";
  card.innerHTML = `
    <div class="list-card-title">${title}</div>
    <div class="list-card-subtitle">${subtitle || ""}</div>
    <div class="list-card-extra">${extra || ""}</div>
  `;
  if (onClick) card.addEventListener("click", onClick);
  return card;
}

function ensureChart() {
  const dom = document.getElementById("klineChart");
  if (!klineChart) {
    klineChart = echarts.init(dom);
    window.addEventListener("resize", () => klineChart && klineChart.resize());
  }
  return klineChart;
}

function renderKline(historyItems) {
  const chart = ensureChart();
  const normalizedItems = (historyItems || [])
    .map((item) => {
      const open = Number(item.open);
      const close = Number(item.close);
      const low = Number(item.low);
      const high = Number(item.high);
      const volume = Number(item.volume || 0);
      if (![open, close, low, high].every((num) => Number.isFinite(num))) return null;
      return {
        date: String(item.date).slice(0, 10),
        open,
        close,
        low,
        high,
        volume: Number.isFinite(volume) ? volume : 0,
      };
    })
    .filter(Boolean);

  if (!normalizedItems.length) {
    chart.clear();
    chart.setOption({
      title: {
        text: "暂无 K 线数据",
        left: "center",
        top: "center",
        textStyle: { color: "#93a4bd", fontSize: 16 },
      },
    }, { notMerge: true });
    return;
  }

  const categoryData = normalizedItems.map((item) => item.date);
  const values = normalizedItems.map((item) => [item.open, item.close, item.low, item.high]);
  const volumes = normalizedItems.map((item) => item.volume);
  const startPct = categoryData.length > 80 ? 70 : 0;

  chart.resize();
  chart.setOption({
    title: { show: false },
    backgroundColor: "transparent",
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: [0, 1],
        start: startPct,
        end: 100,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: true,
      },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        bottom: 4,
        height: 18,
        start: startPct,
        end: 100,
      },
    ],
    grid: [
      { left: 50, right: 20, top: 20, height: "62%" },
      { left: 50, right: 20, top: "74%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: categoryData, boundaryGap: true },
      { type: "category", gridIndex: 1, data: categoryData, boundaryGap: true, axisLabel: { show: false } },
    ],
    yAxis: [{ scale: true }, { scale: true, gridIndex: 1 }],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: values,
        itemStyle: { color: "#ef4444", color0: "#22c55e", borderColor: "#ef4444", borderColor0: "#22c55e" },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
      },
    ],
  }, { notMerge: true });
}

function switchOpportunityTab(tab) {
  state.opportunityTab = tab;
  document.querySelectorAll("[data-opportunity-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.opportunityTab === tab);
  });
  refs.watchPane.classList.toggle("hidden", tab !== "watch");
  refs.recommendPane.classList.toggle("hidden", tab !== "recommend");
}

function bindPanelToggles() {
  document.querySelectorAll(".panel-toggle[data-panel-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.panelTarget;
      const body = document.querySelector(`[data-panel-body="${target}"]`);
      if (!body) return;
      body.classList.toggle("hidden");
      const hidden = body.classList.contains("hidden");
      const indicator = button.querySelector(".toggle-indicator");
      if (indicator) indicator.textContent = hidden ? "展开" : "收起";
    });
  });
}

function bindDetailMetricsToggle() {
  if (!refs.detailMetricsToggleBtn || !refs.detailMetricsBody || !refs.detailMetricsToggleText) return;
  refs.detailMetricsToggleBtn.addEventListener("click", () => {
    refs.detailMetricsBody.classList.toggle("hidden");
    const isHidden = refs.detailMetricsBody.classList.contains("hidden");
    refs.detailMetricsToggleText.textContent = isHidden ? "展开" : "收起";
  });
}

function openSettingsModal() {
  refs.settingsModal.classList.remove("hidden");
}

function closeSettingsModal() {
  refs.settingsModal.classList.add("hidden");
  refs.settingResetPaperAccount.checked = false;
}

function setOllamaCheckResult(text, stateType = "") {
  if (!refs.checkOllamaResult) return;
  refs.checkOllamaResult.textContent = text;
  refs.checkOllamaResult.classList.remove("success", "error");
  if (stateType === "success") refs.checkOllamaResult.classList.add("success");
  if (stateType === "error") refs.checkOllamaResult.classList.add("error");
}

function applySettingsToForm(settingsData) {
  refs.settingPaperInitialCash.value = settingsData.paper_initial_cash;
  refs.settingAutoTradeBudgetRatio.value = settingsData.auto_trade_budget_ratio;
  refs.settingDefaultAutoTradeStrategy.value = settingsData.default_auto_trade_strategy;
  refs.settingAutoTradeRefreshSeconds.value = settingsData.auto_trade_refresh_seconds;
  refs.settingEnableOllamaDecision.checked = Boolean(settingsData.enable_ollama_decision);
  refs.settingOllamaTimeoutSeconds.value = settingsData.ollama_timeout_seconds;
  refs.settingResearchTopK.value = settingsData.research_top_k;
  refs.settingRagRetrievalMode.value = settingsData.rag_retrieval_mode || "hybrid";
  refs.settingMarketPageSize.value = settingsData.market_page_size;
  refs.autoStrategySelect.value = settingsData.default_auto_trade_strategy;
}

async function loadSystemSettings() {
  const data = await apiGet("/system/settings");
  state.settings = data;
  applySettingsToForm(data);
}

async function saveSystemSettings() {
  const payload = {
    paper_initial_cash: Number(refs.settingPaperInitialCash.value || 100000),
    auto_trade_budget_ratio: Number(refs.settingAutoTradeBudgetRatio.value || 0.2),
    default_auto_trade_strategy: refs.settingDefaultAutoTradeStrategy.value || "ensemble_rag",
    auto_trade_refresh_seconds: Number(refs.settingAutoTradeRefreshSeconds.value || 30),
    enable_ollama_decision: Boolean(refs.settingEnableOllamaDecision.checked),
    ollama_timeout_seconds: Number(refs.settingOllamaTimeoutSeconds.value || 180),
    research_top_k: Number(refs.settingResearchTopK.value || 4),
    rag_retrieval_mode: refs.settingRagRetrievalMode.value || "hybrid",
    market_page_size: Number(refs.settingMarketPageSize.value || 30),
    reset_paper_account: Boolean(refs.settingResetPaperAccount.checked),
  };
  const data = await apiPost("/system/settings", payload);
  state.settings = data;
  applySettingsToForm(data);
  if (autoTradeRunning) {
    stopAutoTradeLoop(false);
    startAutoTradeLoop();
  }
  setMessage("系统设置已保存", false);
  closeSettingsModal();
}

async function checkOllamaAvailability() {
  if (!refs.checkOllamaBtn) return;
  const timeoutSeconds = Math.max(5, Number(refs.settingOllamaTimeoutSeconds.value || state.settings.ollama_timeout_seconds || 180));
  refs.checkOllamaBtn.disabled = true;
  refs.checkOllamaBtn.textContent = "检测中...";
  setOllamaCheckResult("检测中...");
  try {
    const data = await apiGet(`/system/ollama/check?timeout=${encodeURIComponent(timeoutSeconds)}`);
    const parts = [];
    parts.push(data.available ? "可用" : "不可用");
    if (Number.isFinite(Number(data.latency_ms))) {
      parts.push(`${Number(data.latency_ms)}ms`);
    }
    if (data.status_code !== null && data.status_code !== undefined) {
      parts.push(`HTTP ${data.status_code}`);
    }
    if (data.error) {
      parts.push(`错误: ${data.error}`);
    } else if (data.response_preview) {
      parts.push(`响应: ${data.response_preview}`);
    }
    setOllamaCheckResult(parts.join(" · "), data.available ? "success" : "error");
    setMessage(data.available ? "Ollama可用" : "Ollama不可用", !data.available);
  } catch (error) {
    console.error(error);
    setOllamaCheckResult(`检测失败: ${error.message || String(error)}`, "error");
    setMessage("Ollama检测失败", true);
  } finally {
    refs.checkOllamaBtn.disabled = false;
    refs.checkOllamaBtn.textContent = "检测Ollama可用性";
  }
}

function bindBaseEvents() {
  bindPanelToggles();
  bindDetailMetricsToggle();
  bindCollapsibleToggle(refs.autoTradeMetaToggleBtn, refs.autoTradeMetaFull, refs.autoTradeMetaToggleText);
  bindResearchSourcesToggle();
  document.querySelectorAll("[data-opportunity-tab]").forEach((button) => {
    button.addEventListener("click", () => switchOpportunityTab(button.dataset.opportunityTab));
  });

  refs.settingsBtn.addEventListener("click", () => openSettingsModal());
  refs.closeSettingsBtn.addEventListener("click", () => closeSettingsModal());
  refs.cancelSettingsBtn.addEventListener("click", () => closeSettingsModal());
  refs.saveSettingsBtn.addEventListener("click", () => saveSystemSettings().catch((error) => {
    console.error(error);
    setMessage("保存设置失败", true);
  }));
  if (refs.checkOllamaBtn) {
    refs.checkOllamaBtn.addEventListener("click", () => {
      checkOllamaAvailability().catch((error) => {
        console.error(error);
        setMessage("Ollama检测失败", true);
      });
    });
  }

  refs.settingsModal.addEventListener("click", (event) => {
    if (event.target === refs.settingsModal) closeSettingsModal();
  });

  refs.refreshAllBtn.addEventListener("click", () => {
    refreshAll({ includeMarket: state.marketLoaded }).catch((error) => {
      console.error(error);
      setMessage("刷新失败", true);
    });
  });

  refs.stockSearchInput.addEventListener("input", (event) => {
    const query = event.target.value.trim();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      loadSearchResults(query).catch((error) => {
        console.error(error);
        setMessage("搜索加载失败", true);
      });
    }, 320);
  });

  refs.marketLoadBtn.addEventListener("click", () => {
    loadMarketPage().catch((error) => {
      console.error(error);
      setMessage("市场分页加载失败", true);
    });
  });

  refs.marketQueryInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    loadMarketPage().catch((error) => {
      console.error(error);
      setMessage("市场分页加载失败", true);
    });
  });

  refs.marketPageInput.addEventListener("change", () => {
    state.marketPage = Number(refs.marketPageInput.value || 1);
  });

  refs.runResearchBtn.addEventListener("click", () => {
    runResearch().catch((error) => {
      console.error(error);
      setMessage("RAG 检索失败", true);
    });
  });

  refs.buyBtn.addEventListener("click", () => {
    placeTrade("BUY").catch((error) => {
      console.error(error);
      setMessage("买入失败", true);
    });
  });

  refs.sellBtn.addEventListener("click", () => {
    placeTrade("SELL").catch((error) => {
      console.error(error);
      setMessage("卖出失败", true);
    });
  });

  refs.autoTradeBtn.addEventListener("click", () => {
    toggleAutoTradeLoop();
  });

  window.addEventListener("beforeunload", () => {
    stopAutoTradeLoop(false);
  });
}

async function getStockAnalysis(symbol) {
  const normalized = String(symbol || "").trim();
  if (!normalized) {
    throw new Error("symbol required");
  }
  if (analysisCache.has(normalized)) {
    return analysisCache.get(normalized);
  }
  const pending = apiGet(`/stocks/${normalized}/analysis`).catch((error) => {
    analysisCache.delete(normalized);
    throw error;
  });
  analysisCache.set(normalized, pending);
  return pending;
}

function renderIndexes(indexes) {
  refs.indexTape.innerHTML = "";
  if (!indexes || !indexes.length) {
    refs.indexTape.appendChild(createEmptyBlock("指数数据暂不可用"));
    return;
  }
  indexes.forEach((item) => {
    const div = document.createElement("div");
    div.className = "index-card";
    div.innerHTML = `
      <div class="index-card-name">${item.name || "--"}</div>
      <div class="index-card-price">${formatNumber(item.price)}</div>
      <div class="${getChangeClass(item.change_pct)}">${formatSignedPct(item.change_pct)}</div>
    `;
    refs.indexTape.appendChild(div);
  });
}

function renderDetailAnalysis(analysis) {
  refs.detailScoreGrid.innerHTML = "";
  refs.detailFactorsGrid.innerHTML = "";
  refs.detailReasons.innerHTML = "";
  if (!analysis) {
    refs.detailScoreGrid.appendChild(createEmptyBlock("暂无评分数据"));
    return;
  }

  const scoreItems = [
    ["综合分", analysis.total_score],
    ["动量分", analysis.momentum_score],
    ["均值回归分", analysis.mean_reversion_score],
    ["质量分", analysis.quality_score],
    ["新闻分", analysis.news_score],
    ["风险分", analysis.risk_score],
  ];
  scoreItems.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "score-card";
    card.innerHTML = `
      <div class="score-label">${label}</div>
      <div class="score-value">${formatNumber(value, 2)}</div>
    `;
    refs.detailScoreGrid.appendChild(card);
  });

  const factors = analysis.factors || {};
  const factorKeys = ["ret_5d", "ret_20d", "rsi_14", "volatility_20d", "drawdown_60d", "ma_5", "ma_20", "ma_60", "volume_ratio"];
  factorKeys.forEach((key) => {
    if (!(key in factors)) return;
    const item = document.createElement("div");
    item.className = "factor-card";
    item.innerHTML = `
      <div class="factor-key">${key}</div>
      <div class="factor-value">${String(factors[key])}</div>
    `;
    refs.detailFactorsGrid.appendChild(item);
  });

  const reasons = analysis.reasons || [];
  if (!reasons.length) {
    refs.detailReasons.appendChild(createEmptyBlock("暂无解释"));
  } else {
    reasons.forEach((reason) => {
      const p = document.createElement("p");
      p.className = "reason-item";
      p.textContent = `- ${reason}`;
      refs.detailReasons.appendChild(p);
    });
  }
}

function setDefaultResearchQuestion() {
  if (!refs.researchQuestionInput) return;
  refs.researchQuestionInput.value = `请结合当前市场环境，解释为什么需要关注 ${state.selectedName} 这只股票。`;
}

async function loadSelectedStock() {
  const requestId = ++state.selectedRequestId;
  const symbol = state.selectedSymbol;
  const [quote, history] = await Promise.all([
    apiGet(`/stocks/${symbol}/realtime`),
    apiGet(`/stocks/${symbol}/history?limit=120`),
  ]);
  if (requestId !== state.selectedRequestId) return;

  refs.selectedStockName.textContent = quote.name || "--";
  refs.selectedStockCode.textContent = quote.symbol || "--";
  refs.selectedStockMeta.textContent = `数据源 ${quote.source || "--"} · ${quote.timestamp || "--"}`;
  refs.selectedStockPrice.textContent = formatNumber(quote.price, 2);
  refs.selectedStockChange.textContent = formatSignedPct(quote.change_pct);
  refs.selectedStockChange.className = `hero-change ${getChangeClass(quote.change_pct)}`;
  refs.selectedStockPrice.className = `hero-price ${getChangeClass(quote.change_pct)}`;
  refs.metricOpen.textContent = formatNumber(quote.open);
  refs.metricHighLow.textContent = `${formatNumber(quote.high)} / ${formatNumber(quote.low)}`;
  refs.metricPrevClose.textContent = formatNumber(quote.prev_close);
  refs.metricTimestamp.textContent = quote.timestamp || "--";
  state.selectedName = quote.name || state.selectedName;
  renderKline(history.items || []);

  getStockAnalysis(symbol)
    .then((analysis) => {
      if (requestId !== state.selectedRequestId) return;
      renderDetailAnalysis(analysis);
      refs.selectedStockMeta.textContent = `${refs.selectedStockMeta.textContent} · 综合分 ${formatNumber(analysis.total_score, 2)}`;
    })
    .catch((error) => {
      console.error(error);
      renderDetailAnalysis(null);
    });
}

async function loadSearchResults(query) {
  const normalized = String(query || "").trim();
  const requestSeq = ++searchRequestSeq;
  if (searchAbortController) {
    searchAbortController.abort();
  }
  searchAbortController = new AbortController();

  let data;
  try {
    data = await apiGet(`/stocks/search?q=${encodeURIComponent(normalized)}&limit=12`, {
      signal: searchAbortController.signal,
    });
  } catch (error) {
    if (isAbortError(error)) return;
    throw error;
  }
  if (requestSeq !== searchRequestSeq) return;

  refs.searchResults.innerHTML = "";
  const items = data.items || [];
  if (!items.length) {
    refs.searchResults.appendChild(createEmptyBlock("没有匹配股票"));
    return;
  }
  items.forEach((item) => {
    const card = createListCard({
      title: `${item.name} ${item.symbol}`,
      subtitle: "点击切换股票详情",
      extra: "",
      onClick: () => selectSymbol(item.symbol, item.name),
    });
    refs.searchResults.appendChild(card);
  });
}

async function hydrateWatchScoreCards(items, token) {
  const batchSize = 2;
  for (let index = 0; index < items.length; index += batchSize) {
    if (token !== watchRenderToken) return;
    const batch = items.slice(index, index + batchSize);
    await Promise.all(batch.map(async ({ symbol, card }) => {
      try {
        const analysis = await getStockAnalysis(symbol);
        if (token !== watchRenderToken || !card.isConnected) return;
        const extraNode = card.querySelector(".list-card-extra");
        if (extraNode) {
          extraNode.textContent = `综合分 ${formatNumber(analysis.total_score, 2)}`;
        }
      } catch (_) {
        if (token !== watchRenderToken || !card.isConnected) return;
        const extraNode = card.querySelector(".list-card-extra");
        if (extraNode) extraNode.textContent = "综合分 --";
      }
    }));
  }
}

async function loadWatchPane() {
  const renderToken = ++watchRenderToken;
  const data = await apiGet("/market/overview");
  refs.watchPane.innerHTML = "";
  const items = data.hot_stocks || [];
  if (!items.length) {
    refs.watchPane.appendChild(createEmptyBlock("暂无重点观察股票"));
  } else {
    const scoreTargets = [];
    items.forEach((item) => {
      const card = createListCard({
        title: `${item.name} ${item.symbol}`,
        subtitle: `现价 ${formatNumber(item.price)} · ${formatSignedPct(item.change_pct)}`,
        extra: "综合分 加载中...",
        onClick: () => selectSymbol(item.symbol, item.name),
      });
      refs.watchPane.appendChild(card);
      scoreTargets.push({ symbol: item.symbol, card });
    });
    void hydrateWatchScoreCards(scoreTargets, renderToken);
  }
  renderIndexes(data.indexes || []);
}

async function loadRecommendPane() {
  const data = await apiGet("/recommendations?limit=8&strategy=ensemble");
  refs.recommendPane.innerHTML = "";
  const items = data.items || [];
  if (!items.length) {
    refs.recommendPane.appendChild(createEmptyBlock("暂无推荐列表"));
    return;
  }
  items.forEach((item) => {
    const card = createListCard({
      title: `${item.name} ${item.symbol}`,
      subtitle: `价格 ${formatNumber(item.price)} · 综合分 ${formatNumber(item.score, 2)}`,
      extra: item.reason || "",
      onClick: () => selectSymbol(item.symbol, item.name),
    });
    refs.recommendPane.appendChild(card);
  });
}

async function loadMarketPage() {
  const query = refs.marketQueryInput.value.trim();
  const page = Number(refs.marketPageInput.value || 1);
  const pageSize = Number(state.settings.market_page_size || 30);
  const requestId = ++state.marketRequestId;
  if (marketAbortController) {
    marketAbortController.abort();
  }
  marketAbortController = new AbortController();
  refs.marketLoadBtn.disabled = true;
  refs.marketPageMeta.textContent = "正在加载市场分页...";

  let data;
  try {
    data = await apiGet(
      `/market/realtime?q=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}`,
      { signal: marketAbortController.signal }
    );
  } catch (error) {
    refs.marketLoadBtn.disabled = false;
    if (isAbortError(error)) return;
    throw error;
  }
  if (requestId !== state.marketRequestId) {
    refs.marketLoadBtn.disabled = false;
    return;
  }

  refs.marketTableBody.innerHTML = "";
  refs.marketPageMeta.textContent = `共 ${data.total} 条，当前第 ${data.page} 页，每页 ${data.page_size} 条`;
  state.marketLoaded = true;

  if (!(data.items || []).length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan=\"8\" class=\"table-empty\">没有匹配数据</td>`;
    refs.marketTableBody.appendChild(tr);
    refs.marketLoadBtn.disabled = false;
    return;
  }

  data.items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button type=\"button\" class=\"table-symbol-btn\" data-symbol=\"${item.symbol}\" data-name=\"${item.name}\">${item.symbol}</button></td>
      <td>${item.name}</td>
      <td>${formatNumber(item.price)}</td>
      <td class=\"${getChangeClass(item.change_pct)}\">${formatSignedPct(item.change_pct)}</td>
      <td>${formatNumber(item.open)}</td>
      <td>${formatNumber(item.high)}</td>
      <td>${formatNumber(item.low)}</td>
      <td>${item.timestamp || "--"}</td>
    `;
    refs.marketTableBody.appendChild(tr);
  });

  refs.marketTableBody.querySelectorAll("button[data-symbol]").forEach((button) => {
    button.addEventListener("click", () => selectSymbol(button.dataset.symbol, button.dataset.name));
  });
  refs.marketLoadBtn.disabled = false;
}

function selectSymbol(symbol, name) {
  state.selectedSymbol = symbol;
  state.selectedName = name || symbol;
  setDefaultResearchQuestion();
  loadSelectedStock().catch((error) => {
    console.error(error);
    setMessage("切换股票失败", true);
  });
}

function renderResearchContexts(contexts) {
  refs.researchContexts.innerHTML = "";
  if (!contexts || !contexts.length) {
    refs.researchContexts.appendChild(createEmptyBlock("没有检索到上下文"));
    return;
  }
  contexts.slice(0, 5).forEach((ctx) => {
    const div = document.createElement("div");
    div.className = "context-item";
    const heading = ctx.heading || ctx.title || "";
    div.innerHTML = `
      <div class="context-title">${ctx.source || "--"} ${heading ? "· " + heading : ""}</div>
      <div class="context-meta">score ${formatNumber(ctx.score || 0, 4)} · rank ${ctx.rank || "--"}</div>
      <div class="context-preview">${ctx.preview || ""}</div>
    `;
    refs.researchContexts.appendChild(div);
  });
}

async function loadResearchChunking() {
  const data = await apiGet("/research/chunking");
  refs.researchChunkingInfo.textContent =
    `模式 ${data.retrieval_mode || "--"} · 策略 ${data.retrieval_strategy || "--"} · 切片 ${data.mode} · max_chars ${data.max_chars} · overlap ${data.overlap_chars}`;
}

async function runResearch() {
  const query = refs.researchQuestionInput.value.trim();
  if (!query) {
    refs.researchAnswer.textContent = "请输入研究问题";
    return;
  }
  refs.researchAnswer.textContent = "RAG 检索中...";
  const topK = Number(state.settings.research_top_k || 4);
  const result = await apiPost("/research/query", {
    query,
    symbol: state.selectedSymbol,
    top_k: topK,
  });
  refs.researchAnswer.textContent = result.answer || "";
  const sourcesText = (result.sources || []).length ? (result.sources || []).join("\n") : "暂无来源";
  setCollapsibleText({
    previewEl: refs.researchSources,
    fullEl: refs.researchSourcesFull,
    fullText: sourcesText,
    previewMax: 72,
    toggleBtn: refs.researchSourcesToggleBtn,
    toggleTextEl: refs.researchSourcesToggleText,
  });
  renderResearchContexts(result.contexts || []);
  const hasDetail = Boolean((result.sources || []).length || (result.contexts || []).length);
  if (!hasDetail) {
    refs.researchSourcesFull.classList.add("hidden");
    refs.researchContexts.classList.add("hidden");
    refs.researchSourcesToggleText.textContent = "展开";
  } else if (refs.researchSourcesFull.classList.contains("hidden")) {
    refs.researchContexts.classList.add("hidden");
    refs.researchSourcesToggleText.textContent = "展开";
  } else {
    refs.researchContexts.classList.remove("hidden");
    refs.researchSourcesToggleText.textContent = "收起";
  }
  if (result.retrieval) {
    const modeText = result.retrieval.mode ? `模式 ${result.retrieval.mode} · ` : "";
    refs.researchChunkingInfo.textContent =
      `${modeText}检索策略 ${result.retrieval.strategy || "--"} · 切片 ${result.retrieval.chunking?.mode || "--"} · 总切片 ${result.retrieval.total_chunks || 0}`;
  }
}

async function loadAccount() {
  const [account, orders] = await Promise.all([
    apiGet("/accounts/paper"),
    apiGet("/orders/paper?limit=10"),
  ]);
  refs.paperCash.textContent = formatNumber(account.cash);
  refs.paperEquity.textContent = formatNumber(account.total_equity);
  refs.positionsTableBody.innerHTML = "";
  refs.ordersTableBody.innerHTML = "";

  if (!(account.positions || []).length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan=\"4\" class=\"table-empty\">当前无持仓</td>`;
    refs.positionsTableBody.appendChild(tr);
  } else {
    account.positions.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${item.symbol}</td>
        <td>${item.name}</td>
        <td>${item.quantity}</td>
        <td class=\"${getChangeClass(item.unrealized_pnl)}\">${formatNumber(item.unrealized_pnl)}</td>
      `;
      refs.positionsTableBody.appendChild(tr);
    });
  }

  if (!(orders.items || []).length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan=\"4\" class=\"table-empty\">暂无订单</td>`;
    refs.ordersTableBody.appendChild(tr);
  } else {
    orders.items.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${item.side}</td>
        <td>${item.symbol}</td>
        <td>${item.quantity}</td>
        <td>${item.status}</td>
      `;
      refs.ordersTableBody.appendChild(tr);
    });
  }
}

async function placeTrade(side) {
  const quantity = Number(refs.tradeQuantityInput.value || 0);
  if (!quantity || quantity <= 0) {
    throw new Error("invalid quantity");
  }
  await apiPost("/trade/order", {
    mode: "paper",
    symbol: state.selectedSymbol,
    name: state.selectedName,
    side,
    quantity,
    reason: `Web UI ${side}`,
  });
  setMessage(`${side} ${state.selectedName} ${quantity} 股已提交`, false);
  await Promise.all([loadAccount(), loadSelectedStock()]);
}

function appendAutoTradeLog(entryText) {
  const latest = String(entryText || "").trim();
  const existing = String(refs.autoTradeMetaFull?.textContent || "").trim();
  const hasExisting = existing && existing !== "暂无内容";
  const merged = hasExisting ? `${latest}\n\n-----\n${existing}` : latest;
  setCollapsibleText({
    previewEl: refs.autoTradeMeta,
    fullEl: refs.autoTradeMetaFull,
    fullText: merged,
    previewMax: 96,
    toggleBtn: refs.autoTradeMetaToggleBtn,
    toggleTextEl: refs.autoTradeMetaToggleText,
  });
}

async function runAutoTrade() {
  const strategy = refs.autoStrategySelect.value || state.settings.default_auto_trade_strategy || "ensemble_rag";
  const result = await apiPost("/trade/auto", {
    mode: "paper",
    top_n: 3,
    strategy,
  });
  const executed = result.executed || [];
  const skipped = result.skipped || [];
  const requested = result.requested_strategy || strategy;
  const finalStrategy = result.final_strategy || result.strategy || strategy;
  const finalDecision = result.final_decision || "EXECUTE";
  const conflict = result.conflict_detected ? "冲突已裁决" : "无冲突";
  const now = new Date().toLocaleString("zh-CN", { hour12: false });
  const lines = [
    `[${now}] 请求策略 ${requested} -> 最终策略 ${finalStrategy}`,
    `决策: ${finalDecision} · ${conflict} · 成交 ${executed.length} 笔 · 跳过 ${skipped.length} 笔`,
  ];
  if (Number.isFinite(Number(result.budget))) {
    lines.push(`预算: ${formatNumber(result.budget, 2)}`);
  }
  if (result.referee_reason) {
    lines.push(`RAG裁决:\n${result.referee_reason}`);
  }
  if (result.ollama_raw_output && String(result.ollama_raw_output).trim()) {
    lines.push(`Ollama返回:\n${String(result.ollama_raw_output).trim()}`);
  }
  if (executed.length) {
    lines.push("成交明细:");
    executed.slice(0, 10).forEach((item) => {
      lines.push(`- ${item.side || "BUY"} ${item.symbol} x ${item.quantity} @ ${item.price}`);
    });
  }
  if (skipped.length) {
    lines.push("跳过明细:");
    skipped.slice(0, 10).forEach((item) => {
      lines.push(`- ${item}`);
    });
  }
  appendAutoTradeLog(lines.join("\n"));
  await Promise.all([loadAccount(), loadRecommendPane(), loadWatchPane()]);
}

async function executeAutoTradeCycle() {
  if (autoTradeBusy) return;
  autoTradeBusy = true;
  try {
    await runAutoTrade();
  } catch (error) {
    console.error(error);
    appendAutoTradeLog(`自动交易执行失败: ${error.message || String(error)}`);
    setMessage("自动交易执行失败，循环已停止", true);
    stopAutoTradeLoop(false);
  } finally {
    autoTradeBusy = false;
  }
}

function startAutoTradeLoop() {
  if (autoTradeRunning) return;
  const intervalSeconds = Math.max(5, Number(state.settings.auto_trade_refresh_seconds || 30));
  const ollamaSwitch = state.settings.enable_ollama_decision ? "Ollama裁决开" : "Ollama裁决关";
  setAutoTradeRunningState(true);
  setMessage(`自动交易已启动（${intervalSeconds}s/次，${ollamaSwitch}），再次点击按钮可停止。`, false);
  void executeAutoTradeCycle();
  autoTradeLoopTimer = window.setInterval(() => {
    void executeAutoTradeCycle();
  }, intervalSeconds * 1000);
}

function stopAutoTradeLoop(showMessage = true) {
  if (autoTradeLoopTimer) {
    window.clearInterval(autoTradeLoopTimer);
    autoTradeLoopTimer = null;
  }
  setAutoTradeRunningState(false);
  if (showMessage) {
    setMessage("自动交易已停止。", false);
  }
}

function toggleAutoTradeLoop() {
  if (autoTradeRunning) {
    stopAutoTradeLoop(true);
    return;
  }
  startAutoTradeLoop();
}

function startAutoRefresh() {
  window.clearInterval(autoRefreshTimer);
  autoRefreshTimer = window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    loadSelectedStock().catch((error) => console.error(error));
    loadAccount().catch((error) => console.error(error));
  }, 20000);
}

async function refreshAll(options = {}) {
  const includeMarket = Boolean(options.includeMarket);
  setStatus("正在刷新", true);
  await Promise.all([
    loadSelectedStock(),
    loadSearchResults(refs.stockSearchInput.value.trim() || state.selectedSymbol),
    loadWatchPane(),
    loadRecommendPane(),
    loadAccount(),
    loadResearchChunking(),
  ]);
  if (includeMarket || state.marketLoaded) {
    await loadMarketPage();
  } else {
    refs.marketTableBody.innerHTML = "";
    refs.marketPageMeta.textContent = "点击“加载市场”获取全量实时分页行情。";
  }
  setStatus("已就绪", true);
  setMessage("界面已刷新，评分与检索结果已更新。", false);
}

async function init() {
  bindBaseEvents();
  setAutoTradeRunningState(false);
  setCollapsibleText({
    previewEl: refs.autoTradeMeta,
    fullEl: refs.autoTradeMetaFull,
    fullText: "当前未执行自动交易。",
    previewMax: 96,
    toggleBtn: refs.autoTradeMetaToggleBtn,
    toggleTextEl: refs.autoTradeMetaToggleText,
  });
  setCollapsibleText({
    previewEl: refs.researchSources,
    fullEl: refs.researchSourcesFull,
    fullText: "暂无来源",
    previewMax: 72,
    toggleBtn: refs.researchSourcesToggleBtn,
    toggleTextEl: refs.researchSourcesToggleText,
  });
  refs.researchSourcesFull.classList.add("hidden");
  refs.researchContexts.classList.add("hidden");
  refs.researchSourcesToggleText.textContent = "展开";
  switchOpportunityTab("watch");
  renderKline([]);
  await loadSystemSettings();
  setDefaultResearchQuestion();
  await refreshAll({ includeMarket: false });
  startAutoRefresh();
}

init().catch((error) => {
  console.error(error);
  setStatus("初始化失败", false);
  setMessage("app.js 第1步初始化失败", true);
});
