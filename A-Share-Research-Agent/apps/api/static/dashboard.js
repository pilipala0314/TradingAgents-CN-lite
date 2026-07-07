// A 股投研 Dashboard 前端逻辑
// 调 /api/dashboard/* 拿数据，渲染到 DOM。失败一律降级显示，不阻断其他 section。


// --- Chart.js Global Defaults for Dark Theme ---
if (window.Chart) {
  Chart.defaults.color = '#94a3b8';
  if (Chart.defaults.scale && Chart.defaults.scale.grid) {
    Chart.defaults.scale.grid.color = '#1e293b';
  }
  Chart.defaults.borderColor = '#334155';
  Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
  if (Chart.defaults.plugins && Chart.defaults.plugins.tooltip) {
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(15, 23, 42, 0.9)';
    Chart.defaults.plugins.tooltip.titleColor = '#f8fafc';
    Chart.defaults.plugins.tooltip.bodyColor = '#e2e8f0';
    Chart.defaults.plugins.tooltip.borderColor = '#334155';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
  }
  if (Chart.defaults.elements && Chart.defaults.elements.line) {
    Chart.defaults.elements.line.tension = 0.3; // smoother curves
  }
}

let _klineChart = null;

async function fetchJSON(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = "";
    const contentType = r.headers.get("content-type") || "";
    try {
      if (contentType.includes("application/json")) {
        const payload = await r.json();
        if (typeof payload?.detail === "string") {
          detail = payload.detail;
        } else if (typeof payload?.message === "string") {
          detail = payload.message;
        } else if (payload && typeof payload === "object" && Object.keys(payload).length > 0) {
          detail = JSON.stringify(payload);
        }
      } else {
        detail = (await r.text()).trim();
      }
    } catch (_error) {
      detail = "";
    }
    throw new Error(detail ? `HTTP ${r.status}: ${detail}` : `HTTP ${r.status}`);
  }
  return await r.json();
}

function fmtPct(v) {
  if (v == null) return "-";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

function fmtNum(v, digits = 2) {
  if (v == null) return "-";
  return Number(v).toFixed(digits);
}

function _escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// 后端 K 线字段是中文键（"日期"/"开盘"/...），统一规整为英文键，
// 否则前端 .date/.close/.volume 永远拿不到值，K 线图必然空白。
const _BAR_KEY_MAP = {
  "日期": "date",
  "股票代码": "code",
  "开盘": "open",
  "收盘": "close",
  "最高": "high",
  "最低": "low",
  "成交量": "volume",
  "成交额": "amount",
  "振幅": "amplitude",
  "涨跌幅": "change_pct",
  "涨跌额": "change_amt",
  "换手率": "turnover_pct",
};
function _normalizeBar(b) {
  if (!b || typeof b !== "object") return b;
  if (b.date !== undefined) return b; // 已经是英文键
  const out = {};
  for (const k of Object.keys(b)) {
    out[_BAR_KEY_MAP[k] || k] = b[k];
  }
  return out;
}
function _normalizeBars(arr) {
  return (arr || []).map(_normalizeBar).filter((b) => b && b.date);
}

function renderHealth(health) {
  const cards = document.getElementById("health-cards");
  const items = [
    { label: "Provider 数", value: health.providers.length },
    { label: "端点数", value: health.endpoints_total },
    { label: "总调用", value: health.calls_total },
    { label: "成功率", value: (health.calls_success_rate * 100).toFixed(1) + "%" },
    {
      label: "缓存命中率",
      value: ((health.cache.hit_rate || 0) * 100).toFixed(1) + "%",
    },
    { label: "缓存 hits", value: health.cache.hits || 0 },
  ];
  cards.innerHTML = items
    .map((i) => `<div class="metric"><div class="label">${i.label}</div><div class="value">${i.value}</div></div>`)
    .join("");
  document.getElementById("health-bar").innerHTML =
    `<span class="label">Providers: ${health.providers.join(" · ")}</span>`;
}

// ── P0-T3: 全球指数按 market 分组渲染 ───────────────────────────────

const _GLOBAL_TABLE_COLUMNS = [
  { key: "code", title: "代码", render: (code) => code },
  { key: "name", title: "名称", render: (v, row) => v || row.code || "-" },
  {
    key: "price", title: "价格",
    render: (v) => (v == null ? "-" : (window.fmtNum ? window.fmtNum(v) : v.toFixed(2))),
  },
  {
    key: "change_pct", title: "涨跌幅",
    render: (v) => {
      const cls = (v || 0) >= 0 ? "up" : "down";
      return `<span class="${cls}">${window.fmtPct ? window.fmtPct(v) : (v == null ? "-" : v.toFixed(2) + "%")}</span>`;
    },
  },
  {
    key: "range", title: "高/低", render: (_, row) => {
      const h = window.fmtNum ? window.fmtNum(row.high) : row.high;
      const l = window.fmtNum ? window.fmtNum(row.low) : row.low;
      return `${h} / ${l}`;
    },
  },
];

function _renderMarketTable(targetId, rows, emptyMessage) {
  const el = document.getElementById(targetId);
  if (!el) return;
  if (!rows || rows.length === 0) {
    el.innerHTML = `<p class="muted" style="padding: 12px;">${emptyMessage || "暂无数据"}</p>`;
    return;
  }
  const head = _GLOBAL_TABLE_COLUMNS.map((c) => `<th>${c.title}</th>`).join("");
  const body = rows.map((row) => {
    const cells = _GLOBAL_TABLE_COLUMNS.map((c) => {
      let val;
      if (c.key === "code") val = row._code;
      else if (c.key === "name") val = row.name;
      else if (c.key === "range") val = c.render(null, row);
      else val = row[c.key];
      return `<td>${c.render(val, row)}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  el.innerHTML = `
    <table>
      <thead><tr>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderGlobalTables(quotes) {
  // quotes: {code: {name, price, change_pct, high, low, market, ...}}
  if (!quotes || Object.keys(quotes).length === 0) {
    ["cn", "hk", "us", "other"].forEach((m) => _renderMarketTable(
      `global-table-${m}`, [],
      "Provider 未返回该市场数据（可能源不可用）"
    ));
    return;
  }
  const buckets = { cn: [], hk: [], us: [], other: [] };
  Object.entries(quotes).forEach(([code, q]) => {
    const m = q.market || "other";
    const bucket = buckets[m] || buckets.other;
    bucket.push([code, q]);
  });
  const emptyMessages = {
    cn: "Provider 未返回 A 股指数（akshare 暂停或网络问题）",
    hk: "Provider 未返回港股指数",
    us: "Provider 未返回美股指数",
    other: "暂无其他市场 / 大宗报价",
  };
  Object.entries(buckets).forEach(([m, rows]) => {
    _renderMarketTable(`global-table-${m}`, rows, emptyMessages[m]);
  });
}

// 向后兼容：保留 renderGlobalTable 作为 renderGlobalTables 的别名（单点）
function renderGlobalTable(quotes) {
  renderGlobalTables(quotes);
}

function renderStock(data, options = {}) {
  const { metric = "close", days = 60 } = options;
  // 实时行情
  const quote = data.quote || {};
  const codes = Object.keys(quote);
  const q = codes.length > 0 ? quote[codes[0]] : null;
  const qEl = document.getElementById("stock-quote");
  if (!q) {
    qEl.innerHTML = `<span class="meta">${data.code} 行情拉取失败${data.errors.quote ? ": " + data.errors.quote : ""}</span>`;
  } else {
    const cls = (q.change_pct || 0) >= 0 ? "up" : "down";
    qEl.innerHTML = `<span class="name">${codes[0]} ${q.name || ""}</span>
      <span class="price ${cls}">${fmtNum(q.price)}</span>
      <span class="meta ${cls}">${fmtPct(q.change_pct)}  开 ${fmtNum(q.open)}  高 ${fmtNum(q.high)}  低 ${fmtNum(q.low)}</span>`;
  }

  // K 线
  let bars = _normalizeBars(data.bars);
  bars = bars.slice(-days);
  if (bars.length === 0) {
    document.getElementById("kline-chart").getContext("2d").clearRect(0, 0, 800, 200);
  } else {
    const labels = bars.map((b) => b.date);
    let dataArr;
    let label;
    if (metric === "change_pct") {
      // 累计涨跌幅（首日归零）
      const first = Number(bars[0].close) || 1;
      dataArr = bars.map((b) => ((Number(b.close) / first - 1) * 100));
      label = "累计涨跌幅 (%)";
    } else if (metric === "volume") {
      dataArr = bars.map((b) => Number(b.volume || 0));
      label = "成交量";
    } else {
      dataArr = bars.map((b) => Number(b.close || 0));
      label = "收盘价";
    }
    const ctx = document.getElementById("kline-chart").getContext("2d");
    if (_klineChart) _klineChart.destroy();
    _klineChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label,
          data: dataArr,
          borderColor: "#0366d6",
          backgroundColor: "rgba(3,102,214,0.08)",
          fill: metric !== "volume",
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          zoom: {
            zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
            pan:  { enabled: true, mode: "x" },
          },
        },
      },
    });
  }

  // 公告
  const annEl = document.getElementById("stock-announcements");
  const anns = data.announcements || [];
  if (anns.length === 0) {
    annEl.innerHTML = "<p>暂无公告</p>";
  } else {
    annEl.innerHTML = `<table>
      <thead><tr><th>标题</th><th>类型</th><th>日期</th></tr></thead>
      <tbody>${anns
        .map(
          (a) => `<tr>
            <td><a href="${a.url || "#"}" target="_blank">${a.title || "-"}</a></td>
            <td>${a.type || "-"}</td>
            <td>${(a.published_at || "").slice(0, 10)}</td>
          </tr>`,
        )
        .join("")}</tbody>
    </table>`;
  }
}

async function loadOverview() {
  try {
    const data = await fetchJSON("/api/dashboard/overview");
    renderHealth(data.health);
    renderGlobalTables(data.global_quotes);
  } catch (e) {
    document.getElementById("health-bar").innerHTML =
      `<span style="color:var(--up)">/api/dashboard/overview 失败: ${e.message}</span>`;
  }
}

// ── 数据源健康监控 ────────────────────────────────────────────────

let _healthCache = null;
let _selectedHealthEndpointKey = "";

function _rateClass(rate) {
  if (rate < 0.5) return "rate-low";
  if (rate < 0.8) return "rate-mid";
  return "rate-high";
}

function _renderAlertBar(alerts) {
  const el = document.getElementById("health-alert-bar");
  const total = (alerts.low_success_rate || 0) + (alerts.last_error || 0) + (alerts.high_latency || 0);
  if (total === 0) {
    el.className = "alert-bar ok show";
    el.innerHTML = "✅ 全部端点健康 — 无告警";
    return;
  }
  const cls = (alerts.low_success_rate || 0) > 0 ? "error" : "warn";
  el.className = `alert-bar ${cls} show`;
  el.innerHTML = `⚠ 检测到 <strong>${total}</strong> 条告警：` +
    `成功率低 (${alerts.low_success_rate || 0}) · ` +
    `最近失败 (${alerts.last_error || 0}) · ` +
    `高延迟 (${alerts.high_latency || 0})`;
}

function _renderProviderFilter(providers) {
  const sel = document.getElementById("health-provider-filter");
  const current = sel.value;
  sel.innerHTML = '<option value="">全部</option>' +
    providers.map((p) => `<option value="${p}">${p}</option>`).join("");
  sel.value = current;
}

function _renderMethodFilter(endpoints) {
  const sel = document.getElementById("health-method-filter");
  if (!sel) return;
  const current = sel.value;
  const methods = [...new Set((endpoints || []).map((row) => row.method).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">全部</option>' +
    methods.map((m) => `<option value="${m}">${m}</option>`).join("");
  sel.value = methods.includes(current) ? current : "";
}

function _healthEndpointKey(row) {
  return `${row.method}:${row.provider}`;
}

function _renderHealthIoSamples(row) {
  const reqEl = document.getElementById("health-request-sample");
  const respEl = document.getElementById("health-response-sample");
  if (!reqEl || !respEl) return;
  if (!row) {
    reqEl.textContent = "等待选择数据源端点...";
    respEl.textContent = "等待选择数据源端点...";
    return;
  }
  const lastCalledAt = row.last_called_at
    ? new Date(row.last_called_at * 1000).toISOString()
    : "";
  reqEl.textContent = JSON.stringify({
    method: row.method,
    provider: row.provider,
    last_status: row.last_status || "idle",
    last_called_at: lastCalledAt,
    alerts: row.alerts || [],
    request_sample: row.request_sample || {},
  }, null, 2);
  respEl.textContent = JSON.stringify({
    last_status: row.last_status || "idle",
    response_sample: row.response_sample || {},
    error_sample: row.error_sample || {},
    last_error: row.last_error || "",
  }, null, 2);
}

function _selectHealthEndpoint(key) {
  _selectedHealthEndpointKey = key || "";
  const rows = _healthCache?.endpoints || [];
  const selected = rows.find((row) => _healthEndpointKey(row) === _selectedHealthEndpointKey) || null;
  document.querySelectorAll("#health-detail-table tr[data-endpoint-key]").forEach((tr) => {
    tr.classList.toggle("active-row", tr.dataset.endpointKey === _selectedHealthEndpointKey);
  });
  _renderHealthIoSamples(selected);
}

function _renderHealthTable(endpoints) {
  const el = document.getElementById("health-detail-table");
  if (!endpoints || endpoints.length === 0) {
    el.innerHTML = "<p>暂无调用数据</p>";
    _renderHealthIoSamples(null);
    return;
  }
  const rowsHtml = endpoints.map((e) => {
    const rateCls = _rateClass(e.success_rate);
    const p95Cls = e.p95_latency_ms > 3000 ? "p95-slow" : "";
    const status = e.last_status || (e.total > 0 ? "success" : "idle");
    const rowCls = status === "failure" || (e.alerts || []).includes("low_success_rate")
      ? "alert-row alert-error"
      : (e.alerts && e.alerts.length > 0 ? "alert-row" : "");
    const badges = (e.alerts || [])
      .map((a) => `<span class="badge ${a}">${a.replace(/_/g, " ")}</span>`)
      .join("");
    const errTrunc = e.last_error && e.last_error.length > 60
      ? e.last_error.slice(0, 60) + "..."
      : (e.last_error || "-");
    const statusCls = status === "success" ? "ok" : status === "failure" ? "error" : "warn";
    const rateText = e.total > 0 ? `${(e.success_rate * 100).toFixed(1)}%` : "-";
    const p95Text = e.total > 0 ? e.p95_latency_ms.toFixed(0) : "-";
    const fallbackText = e.total > 0 ? `${e.fallback_count} (${(e.fallback_rate * 100).toFixed(0)}%)` : "-";
    const lastCalledText = e.last_called_at
      ? new Date(e.last_called_at * 1000).toLocaleString("zh-CN", { hour12: false })
      : "-";
    const errCls = status === "failure" ? "error-cell" : "error-cell stale";
    return `<tr class="${rowCls}" data-endpoint-key="${_healthEndpointKey(e)}">
      <td><code>${e.method}</code></td>
      <td>${e.provider}</td>
      <td><span class="badge ${statusCls}">${status}</span></td>
      <td>${lastCalledText}</td>
      <td>${e.success} / ${e.total}</td>
      <td>${e.failure}</td>
      <td class="${rateCls}">${rateText}</td>
      <td class="${p95Cls}">${p95Text}</td>
      <td>${fallbackText}</td>
      <td>${badges || "-"}</td>
      <td class="${errCls}">${errTrunc}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `<table>
    <thead><tr>
      <th>method</th><th>provider</th><th>状态</th><th>最近调用</th><th>成功/总</th><th>失败</th>
      <th>成功率</th><th>p95 (ms)</th><th>fallback</th><th>告警</th><th>最近错误</th>
    </tr></thead>
    <tbody>${rowsHtml}</tbody>
  </table>`;
  document.querySelectorAll("#health-detail-table tr[data-endpoint-key]").forEach((tr) => {
    tr.addEventListener("click", () => _selectHealthEndpoint(tr.dataset.endpointKey || ""));
  });
  const current = endpoints.find((row) => _healthEndpointKey(row) === _selectedHealthEndpointKey);
  const defaultKey = _selectedHealthEndpointKey || (endpoints[0] ? _healthEndpointKey(endpoints[0]) : "");
  _selectHealthEndpoint(defaultKey);
  if (!current && endpoints.length === 0) {
    _renderHealthIoSamples(null);
  }
}

async function loadHealthDetail() {
  try {
    const data = await fetchJSON("/api/dashboard/health");
    _healthCache = data;
    
    // Render Health Summary
    const summaryCards = [
      { label: "Total Endpoints", value: data.endpoints_total },
      { label: "Total Calls", value: data.calls_total },
      { label: "Overall Success Rate", value: (data.calls_success_rate * 100).toFixed(1) + "%" },
      { label: "Active Alerts", value: (data.alerts.low_success_rate || 0) + (data.alerts.last_error || 0) + (data.alerts.high_latency || 0) }
    ];
    document.getElementById("health-summary").innerHTML = summaryCards.map(c => `
      <div class="kv">
        <div class="kv-label">${c.label}</div>
        <div class="kv-value" style="color: ${c.label === 'Active Alerts' && c.value > 0 ? 'var(--up)' : 'var(--text)'}">${c.value}</div>
      </div>
    `).join("");

    _renderAlertBar(data.alerts || {});
    _renderProviderFilter(data.providers || []);
    _renderMethodFilter(data.endpoints || []);
    _renderFilteredTable();
  } catch (e) {
    document.getElementById("health-detail-table").innerHTML =
      `<p style="color:var(--up)">/api/dashboard/health 失败: ${e.message}</p>`;
    _renderHealthIoSamples(null);
  }
}

// ── P0-T9 健康 tab ③ 自动降级历史 ─────────────────────────

function _formatTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts * 1000);
    if (Number.isNaN(d.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch (e) {
    return "—";
  }
}

function _renderFailoverActive(active) {
  const wrap = document.getElementById("health-failover-active");
  if (!wrap) return;
  if (!active || active.length === 0) {
    wrap.innerHTML = `<div class="failover-active-card none">
      <div class="failover-active-icon">✓</div>
      <div class="failover-active-body">
        <div class="failover-active-title">无 active failover</div>
        <div class="failover-active-meta">所有 provider chain 正常运行</div>
      </div>
    </div>`;
    return;
  }
  wrap.innerHTML = active
    .map((s) => {
      const sinceStr = _formatTs(s.since_ts);
      return `<div class="failover-active-card">
        <div class="failover-active-icon">⚠</div>
        <div class="failover-active-body">
          <div class="failover-active-title">${s.method}: ${s.primary} → ${s.backup}</div>
          <div class="failover-active-meta">since ${sinceStr} · ${s.reason || ""}</div>
        </div>
      </div>`;
    })
    .join("");
}

function _renderFailoverHistory(items) {
  const wrap = document.getElementById("health-failover-table");
  const countEl = document.getElementById("health-failover-count");
  if (!wrap) return;
  if (!items || items.length === 0) {
    if (countEl) countEl.textContent = "0 条历史";
    wrap.innerHTML = '<div class="muted" style="padding:24px; text-align:center;">尚无降级历史</div>';
    return;
  }
  if (countEl) countEl.textContent = `${items.length} 条历史`;
  const rows = items
    .map(
      (e) => `<tr>
      <td>${_formatTs(e.ts)}</td>
      <td><span class="failover-action-tag failover-action-${e.action}">${e.action.toUpperCase()}</span></td>
      <td><code>${e.method}</code></td>
      <td>${e.primary} <span class="muted">→</span> ${e.backup}</td>
      <td class="muted" style="font-size:11px;">${e.reason || ""}</td>
    </tr>`,
    )
    .join("");
  wrap.innerHTML = `<table class="data-table">
    <thead>
      <tr>
        <th>时间</th>
        <th>动作</th>
        <th>Method</th>
        <th>Provider Chain</th>
        <th>原因</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadFailoverOverview(triggerCheck) {
  const countEl = document.getElementById("health-failover-count");
  if (countEl) countEl.textContent = "扫描中…";
  try {
    const url = triggerCheck
      ? "/api/health/failover?trigger_check=true"
      : "/api/health/failover";
    const data = await fetchJSON(url);
    _renderFailoverActive(data.active || []);
    const limitEl = document.getElementById("health-failover-limit");
    const limit = limitEl ? limitEl.value : "50";
    const hist = await fetchJSON(`/api/health/failover/history?limit=${limit}`);
    _renderFailoverHistory(hist.items || []);
  } catch (e) {
    if (countEl) countEl.textContent = "拉取失败";
    const wrap = document.getElementById("health-failover-table");
    if (wrap) {
      wrap.innerHTML = `<div class="muted" style="color:#f87171; padding:18px; text-align:center;">
        降级历史拉取失败: ${e.message}
      </div>`;
    }
  }
}

function _renderFilteredTable() {
  if (!_healthCache) return;
  const provider = document.getElementById("health-provider-filter").value;
  const method = document.getElementById("health-method-filter").value;
  const alertOnly = document.getElementById("health-alert-only").checked;
  let rows = _healthCache.endpoints || [];
  if (provider) rows = rows.filter((e) => e.provider === provider);
  if (method) rows = rows.filter((e) => e.method === method);
  if (alertOnly) rows = rows.filter((e) => (e.alerts || []).length > 0);
  _renderHealthTable(rows);
}

async function loadStock(code, options) {
  if (!code || !/^\d{6}$/.test(code)) {
    alert("请输入 6 位股票代码");
    return;
  }
  try {
    const data = await fetchJSON(`/api/dashboard/stock/${code}`);
    renderStock(data, options || {});
  } catch (e) {
    document.getElementById("stock-quote").innerHTML =
      `<span class="meta">/api/dashboard/stock/${code} 失败: ${e.message}</span>`;
  }
  // 并行拉决策钻取（不阻塞主视图）
  loadStockDecisions(code).catch((e) => console.warn("loadStockDecisions failed:", e));
}

// ── P0-T11 决策钻取（stock tab ②） ─────────────────────────────

function _renderRoleTag(role) {
  if (role === "approved") {
    return '<span class="stock-decision-role stock-decision-role-approved">APPROVED</span>';
  }
  if (role === "blocked") {
    return '<span class="stock-decision-role stock-decision-role-blocked">BLOCKED</span>';
  }
  return '<span class="stock-decision-role" style="background:rgba(148,163,184,0.12);color:#94a3b8;">UNRELATED</span>';
}

function _renderEvidenceList(evidence) {
  if (!Array.isArray(evidence) || evidence.length === 0) {
    return '<div class="stock-decision-empty" style="padding:12px;font-size:12px;">无关联 evidence_refs</div>';
  }
  return `<div class="stock-decision-evidence">
    <div class="stock-decision-evidence-title">evidence_refs · ${evidence.length}</div>
    ${evidence
      .map(
        (e) =>
          `<div class="stock-decision-evidence-item">· ${typeof e === "string" ? e : JSON.stringify(e)}</div>`,
      )
      .join("")}
  </div>`;
}

function _renderDecisionRow(trace, idx) {
  const role = trace.role || "unrelated";
  const verdict = trace.verdict || "-";
  const date = trace.business_date || "-";
  const conf = Number(trace.confidence || 0);
  const confPct = Math.max(0, Math.min(100, conf * 100));
  const fallback = !!trace.fallback_used;
  const rationale = trace.rationale || "(无 rationale)";
  const evidence = trace.evidence_refs || [];
  const llmError = trace.llm_error || "";
  const modelVer = trace.model_version || "-";
  const layer = trace.decision_layer || "committee";

  return `<div class="stock-decision-row" data-idx="${idx}">
    <div class="stock-decision-head" onclick="this.parentElement.classList.toggle('open')">
      ${_renderRoleTag(role)}
      <span class="stock-decision-verdict">${verdict}</span>
      <span class="stock-decision-date">${date}</span>
      <div class="stock-decision-confidence">
        <span>置信 ${confPct.toFixed(0)}%</span>
        <div class="stock-decision-confidence-bar">
          <div class="stock-decision-confidence-fill ${fallback ? "fallback" : ""}" style="width:${confPct}%"></div>
        </div>
      </div>
      ${fallback ? '<span class="stock-decision-tag stock-decision-tag-fallback">FALLBACK</span>' : ""}
      <span class="stock-decision-toggle">▾</span>
    </div>
    <div class="stock-decision-body">
      <div class="stock-decision-body-inner">
        <div class="stock-decision-rationale">${rationale}</div>
        ${_renderEvidenceList(evidence)}
        ${llmError ? `<div class="stock-decision-llm-error">⚠ LLM 异常: ${llmError}</div>` : ""}
        <div class="stock-decision-meta">
          <span>layer: ${layer}</span>
          <span>model: ${modelVer}</span>
          <span>trace_id: ${(trace.trace_id || "-").slice(0, 8)}</span>
        </div>
      </div>
    </div>
  </div>`;
}

async function loadStockDecisions(code) {
  const listEl = document.getElementById("stock-decision-list");
  const countEl = document.getElementById("stock-decision-count");
  const linkEl = document.getElementById("stock-decision-link");
  if (!listEl) return;
  if (!code || !/^\d{6}$/.test(code)) {
    listEl.innerHTML = '<div class="stock-decision-empty">请输入 6 位股票代码</div>';
    if (countEl) countEl.textContent = "—";
    return;
  }
  const lookbackEl = document.getElementById("stock-decision-lookback");
  const limitEl = document.getElementById("stock-decision-limit");
  const lookback = lookbackEl ? lookbackEl.value : "30";
  const limit = limitEl ? limitEl.value : "10";
  if (linkEl) linkEl.href = `/static/index.html?tab=signals&code=${code}`;
  if (countEl) countEl.textContent = "加载中…";
  listEl.innerHTML = '<div class="stock-decision-empty">正在拉取委员会 trace…</div>';
  try {
    const data = await fetchJSON(
      `/api/dashboard/stock/${code}/decisions?lookback_days=${lookback}&limit=${limit}`,
    );
    const traces = Array.isArray(data.traces) ? data.traces : [];
    const total = data.total_returned || traces.length;
    if (countEl) {
      countEl.textContent = `${data.name || code} · ${total} 条`;
    }
    if (traces.length === 0) {
      listEl.innerHTML = `<div class="stock-decision-empty">
        委员会 ${lookback} 天内未对该股做出涉及决策<br/>
        <span style="font-size:11px;color:var(--text-muted);">可能原因：未到 run 窗口 / 委员会未启用 stock 层 / 数据未落库</span>
      </div>`;
      return;
    }
    const approved = traces.filter((t) => t.role === "approved").length;
    const blocked = traces.filter((t) => t.role === "blocked").length;
    const summary = `<div style="display:flex;gap:16px;font-size:12px;padding:0 4px 8px;color:var(--text-muted);">
      <span>涉及 trace：<strong style="color:var(--text);">${total}</strong></span>
      <span>批准：<strong style="color:#4ade80;">${approved}</strong></span>
      <span>拦截：<strong style="color:#f87171;">${blocked}</strong></span>
    </div>`;
    listEl.innerHTML = summary + traces.map((t, i) => _renderDecisionRow(t, i)).join("");
  } catch (e) {
    if (countEl) countEl.textContent = "拉取失败";
    listEl.innerHTML = `<div class="stock-decision-empty" style="color:#f87171;">
      决策钻取失败: ${e.message}<br/>
      <span style="font-size:11px;color:var(--text-muted);">接口 GET /api/dashboard/stock/{code}/decisions</span>
    </div>`;
  }
}

// ── 多股对比 ────────────────────────────────────────────────

let _compareChart = null;

function renderCompare(data) {
  const el = document.getElementById("compare-summary");
  const codes = data.codes || [];
  const series = data.series || {};
  const errors = data.errors || {};
  const normalize = document.getElementById("compare-normalize").checked;

  // summary
  const summaryParts = codes.map((c) => {
    const bars = _normalizeBars(series[c]);
    if (bars.length === 0) {
      const err = errors[c] ? `: ${errors[c]}` : ": 暂无数据";
      return `<span class="meta" style="color:#b31d28">${c} ${err}</span>`;
    }
    const last = bars[bars.length - 1];
    const first = bars[0];
    const chg = ((Number(last.close) / Number(first.close) - 1) * 100);
    const cls = chg >= 0 ? "up" : "down";
    return `<span class="meta">${c} <strong>${Number(last.close).toFixed(2)}</strong> <span class="${cls}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span></span>`;
  });
  el.innerHTML = summaryParts.length > 0
    ? summaryParts.join("  ·  ")
    : '<span class="meta">输入 2-5 只股票代码（逗号分隔）</span>';

  // 找最长 series 的日期轴
  let labels = [];
  for (const c of codes) {
    const bars = _normalizeBars(series[c]);
    if (bars.length > labels.length) labels = bars.map((b) => b.date);
  }
  // 对齐：对每个 code 用其自己 series 的 date 与 labels 取交集
  const datasets = codes.map((c, idx) => {
    const bars = _normalizeBars(series[c]);
    const dateMap = new Map();
    for (const b of bars) dateMap.set(b.date, b);
    let dataArr = labels.map((d) => {
      const b = dateMap.get(d);
      if (!b) return null;
      const close = Number(b.close) || 0;
      if (normalize) {
        const first = Number(bars[0].close) || 1;
        return (close / first - 1) * 100;
      }
      return close;
    });
    return {
      label: c,
      data: dataArr,
      borderColor: _chartColor(idx),
      backgroundColor: _chartColor(idx, 0.08),
      spanGaps: true,
      tension: 0.2,
    };
  });
  const ctx = document.getElementById("compare-chart").getContext("2d");
  if (_compareChart) _compareChart.destroy();
  _compareChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true, position: "top" },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
          pan:  { enabled: true, mode: "x" },
        },
      },
      scales: {
        y: { title: { display: true, text: normalize ? "涨跌幅 (%)" : "收盘价" } },
      },
    },
  });
}

const _CHART_COLORS = ["#0366d6", "#d73a49", "#28a745", "#6f42c1", "#d4a72c"];
function _chartColor(idx, alpha = 1) {
  const c = _CHART_COLORS[idx % _CHART_COLORS.length];
  if (alpha === 1) return c;
  // alpha 简化：返回 rgba
  const hex = c.replace("#", "");
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

async function loadCompare() {
  const raw = document.getElementById("compare-input").value.trim();
  if (!raw) {
    alert("请输入至少 1 个代码");
    return;
  }
  try {
    const data = await fetchJSON(`/api/dashboard/compare?codes=${encodeURIComponent(raw)}`);
    renderCompare(data);
  } catch (e) {
    document.getElementById("compare-summary").innerHTML =
      `<span class="meta" style="color:#b31d28">/api/dashboard/compare 失败: ${e.message}</span>`;
  }
}

// ── 今日信号 (P6) ──────────────────────────────────────────────

let _signalsCache = null;
let _decisionTraceCache = null;
let _researchAuditCache = null;
let _signalsApprovedChart = null;
let _signalsBlockedChart = null;
let _signalsAttributionChart = null;
let _signalsRunsCompareChart = null;
let _signalsModuleRebalanceChart = null;
let _signalsModuleScoreIcChart = null;
let _signalsModuleHoldingsChart = null;
let _signalsTraceGroupChart = null;
let _selectedTraceId = null;
let _selectedBacktestJobId = null;
let _signalsBacktestAttrCache = {};
let _signalsLoadError = "";
let _decisionTraceLoadError = "";
let _researchAuditLoadError = "";

function _setSignalsPanelMessage(id, message, tone = "muted") {
  const el = document.getElementById(id);
  if (!el) return;
  const color = tone === "error"
    ? "var(--up)"
    : tone === "warn"
      ? "#d4a72c"
      : "var(--text-muted)";
  el.innerHTML = `<p class="muted" style="color:${color}">${_escapeHtml(message)}</p>`;
}

function _setChartPlaceholder(canvasId, message = "") {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const parent = canvas.parentElement;
  if (!parent) return;
  let placeholder = parent.querySelector(`.chart-empty[data-for="${canvasId}"]`);
  if (!message) {
    if (placeholder) placeholder.remove();
    canvas.style.display = "";
    return;
  }
  if (!placeholder) {
    placeholder = document.createElement("div");
    placeholder.className = "chart-empty";
    placeholder.dataset.for = canvasId;
    canvas.insertAdjacentElement("afterend", placeholder);
  }
  placeholder.textContent = message;
  canvas.style.display = "none";
}

function _resetSignalsDerivedCharts() {
  if (_signalsApprovedChart) {
    _signalsApprovedChart.destroy();
    _signalsApprovedChart = null;
  }
  if (_signalsBlockedChart) {
    _signalsBlockedChart.destroy();
    _signalsBlockedChart = null;
  }
  if (_signalsAttributionChart) {
    _signalsAttributionChart.destroy();
    _signalsAttributionChart = null;
  }
  if (_signalsRunsCompareChart) {
    _signalsRunsCompareChart.destroy();
    _signalsRunsCompareChart = null;
  }
  if (_signalsModuleRebalanceChart) {
    _signalsModuleRebalanceChart.destroy();
    _signalsModuleRebalanceChart = null;
  }
  if (_signalsModuleScoreIcChart) {
    _signalsModuleScoreIcChart.destroy();
    _signalsModuleScoreIcChart = null;
  }
  if (_signalsModuleHoldingsChart) {
    _signalsModuleHoldingsChart.destroy();
    _signalsModuleHoldingsChart = null;
  }
  if (_signalsTraceGroupChart) {
    _signalsTraceGroupChart.destroy();
    _signalsTraceGroupChart = null;
  }
}

function _renderSignalsUnavailableState(message, detailHtml = "") {
  document.getElementById("signals-date").textContent = "业务日期: 主链路不可用";
  _setSignalsPanelMessage("signals-market", message, "error");
  _setSignalsPanelMessage("signals-sector", "板块信号暂停渲染：依赖主信号数据恢复。", "warn");
  _setSignalsPanelMessage("signals-approved", "准入信号暂停渲染：依赖主信号数据恢复。", "warn");
  _setSignalsPanelMessage("signals-blocked", "拦截信号暂停渲染：依赖主信号数据恢复。", "warn");
  document.getElementById("signals-audit-summary").innerHTML = "";
  document.getElementById("signals-audit-meta").innerHTML = detailHtml || '<span class="meta" style="color:var(--up)">决策审计未执行：主信号接口不可用</span>';
  _setSignalsPanelMessage("signals-trace-list", "决策轨迹未加载：依赖主信号业务日期。", "warn");
  document.getElementById("signals-trace-detail").innerHTML = '<span class="meta" style="color:#d4a72c">暂无可钻取的决策轨迹</span>';
  document.getElementById("signals-consistency-summary").innerHTML = "";
  document.getElementById("signals-consistency-issues").innerHTML = '<span class="meta" style="color:#d4a72c">一致性核验未执行：依赖主信号接口恢复</span>';
  _setSignalsPanelMessage("signals-linked-backtests", "关联 backtest run 未加载。", "warn");
  _setSignalsPanelMessage("signals-audit-bars", "审计覆盖率暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-audit-matrix", "核验矩阵暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-audit-timeline", "真实链路时间线暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-fallback-summary", "Fallback 对比暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-trace-diff-matrix", "Trace 差异矩阵暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-trace-group-summary", "Trace 分组统计暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-drilldown-chain", "决策钻取暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-drilldown-notes", "signal / trace / backtest 联动暂停。", "warn");
  _setSignalsPanelMessage("signals-trace-attribution-bridge", "trace 与 attribution 闭环对照暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-evidence-map", "个股证据映射暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-run-recommendation", "run 推荐解释暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-stock-review", "个股级 Trace-Run 对照暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-trace-compare", "多 Trace 横向对照暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-stock-diff", "个股差异视图暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-run-rank-diff", "Run 排序变化解释暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-backtest-attribution", "回测归因钻取暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-segment-review", "调仓段复盘暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-segment-compare", "Segment 横向比较暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-attribution-module-details", "Attribution 模块明细暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-backtest-attribution-raw", "归因原始结构暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-backtest-modules", "归因模块拆解暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-runs-compare-summary", "多 Run 对比摘要暂停渲染。", "warn");
  _setSignalsPanelMessage("signals-runs-peer-summary", "Run 对照摘要暂停渲染。", "warn");
  _setChartPlaceholder("signals-approved-chart", "主信号接口不可用，准入分布暂停渲染");
  _setChartPlaceholder("signals-blocked-chart", "主信号接口不可用，拦截分布暂停渲染");
  _setChartPlaceholder("signals-attribution-chart", "主信号接口不可用，归因指标图暂停渲染");
  _setChartPlaceholder("signals-module-rebalance-chart", "主信号接口不可用，Rebalance 图暂停渲染");
  _setChartPlaceholder("signals-module-scoreic-chart", "主信号接口不可用，Score IC 图暂停渲染");
  _setChartPlaceholder("signals-module-holdings-chart", "主信号接口不可用，Holdings 图暂停渲染");
}

function _signalDirectionClass(direction) {
  if (direction === "long") return "up";
  if (direction === "short" || direction === "avoid") return "down";
  return "";
}

function _renderSignalsMeta(summary, businessDate) {
  const el = document.getElementById("signals-meta");
  const total = (summary.market || 0) + (summary.sector || 0) +
    (summary.approved || 0) + (summary.blocked || 0);
  if (total === 0) {
    el.className = "alert-bar warn show";
    el.innerHTML = `⚠ 业务日期 ${businessDate || "?"} 暂无信号 — 今日 research flow 未跑 / 跑失败`;
    return;
  }
  el.className = "alert-bar ok show";
  el.innerHTML = `✅ 业务日期 <strong>${businessDate}</strong> 已落库：` +
    `market=${summary.market} · sector=${summary.sector} · approved=${summary.approved} · blocked=${summary.blocked} (合计 ${total})`;
}

function _renderSignalsMarket(rows) {
  const el = document.getElementById("signals-market");
  if (!rows || rows.length === 0) {
    el.innerHTML = '<span class="meta">暂无顶层市场信号</span>';
    return;
  }
  const m = rows[0];
  const p = m.output_payload || {};
  const cls = _signalDirectionClass(m.direction);
  const committeeTrace = (_decisionTraceCache?.traces || []).find((t) => t.decision_layer === "committee")
    || (_decisionTraceCache?.traces || [])[0]
    || null;
  
  const rationaleHTML = p.rationale 
    ? `<div class="meta" style="margin-top:12px; padding: 12px; background: rgba(255,255,255,0.03); border-radius: 6px; border: 1px solid var(--border); white-space:pre-wrap; font-family: var(--font-sans); line-height: 1.5; color: var(--text);">${_escapeHtml(p.rationale)}</div>`
    : `<div class="meta" style="margin-top:12px;">(无 rationale)</div>`;

  const fallbackHTML = p.fallback_used 
    ? `<div class="alert-bar warn show" style="margin-top: 12px; display: inline-block;">⚠ 本次决策 LLM 不可用，降级使用了规则 baseline</div>` 
    : `<div class="alert-bar info show" style="margin-top: 12px; display: inline-block;">✨ 智能体深度推理生成</div>`;

  let ruleBaselineHTML = "";
  if (committeeTrace && committeeTrace.rule_baseline && Object.keys(committeeTrace.rule_baseline).length > 0) {
    ruleBaselineHTML = `
      <details style="margin-top: 12px;">
        <summary style="cursor: pointer; color: var(--text-muted); font-size: 13px;">查看 Rule Baseline 数据</summary>
        <pre style="margin-top: 8px;">${_escapeHtml(JSON.stringify(committeeTrace.rule_baseline, null, 2))}</pre>
      </details>
    `;
  }

  const traceBadges = committeeTrace ? `
    <div class="meta" style="margin-top: 12px; display:flex; flex-wrap: wrap; gap: 6px;">
      <span class="badge" style="background: rgba(255,255,255,0.08); color: var(--text); border: 1px solid var(--border);">Model: ${_escapeHtml(committeeTrace.model_version || "-")}</span>
      <span class="badge" style="background: rgba(255,255,255,0.08); color: var(--text); border: 1px solid var(--border);">Layer: ${_escapeHtml(committeeTrace.decision_layer || "-")}</span>
      <span class="badge" style="background: rgba(255,255,255,0.08); color: var(--text); border: 1px solid var(--border);">Trace: ${_escapeHtml((committeeTrace.trace_id || "").slice(0, 8) || "-")}</span>
    </div>
  ` : "";

  el.innerHTML = `
    <div style="display: flex; align-items: baseline; flex-wrap: wrap;">
      <span class="name" style="font-size: 22px; font-weight: 700; text-transform: uppercase;">${(p.verdict || "neutral")}</span>
      <span class="price ${cls}" style="margin-left: 12px; font-size: 20px;">${(m.confidence * 100).toFixed(0)}%</span>
      <span class="meta" style="margin-left: 16px;">
        <span class="badge" style="background: rgba(255,255,255,0.1); color: var(--text); border: 1px solid var(--border);">Risk Budget: <strong>${p.risk_budget || "normal"}</strong></span>
        <span class="badge" style="background: rgba(255,255,255,0.1); color: var(--text); border: 1px solid var(--border); margin-left: 4px;">Market Regime: ${p.market_regime || "?"}</span>
      </span>
    </div>
    ${fallbackHTML}
    ${traceBadges}
    ${rationaleHTML}
    ${ruleBaselineHTML}
  `;
}

function _renderSignalsTable(el, rows, type) {
  if (!rows || rows.length === 0) {
    el.innerHTML = `<p class="muted">暂无 ${type} 信号</p>`;
    return;
  }
  const body = rows.map((r) => {
    const p = r.output_payload || {};
    const cls = _signalDirectionClass(r.direction);
    let col1, col2, col3;
    
    // Build expandable evidence details if available
    let evidenceHTML = "";
    if (r.supporting_evidence && r.supporting_evidence.length > 0) {
      evidenceHTML = `<details style="margin-top: 4px;"><summary style="font-size:11px; cursor:pointer; color:var(--text-muted)">证据列表</summary><ul style="font-size:11px; margin: 4px 0; padding-left: 16px; color:var(--text-muted)">` + 
        r.supporting_evidence.map(e => `<li>${_escapeHtml(typeof e === 'string' ? e : JSON.stringify(e))}</li>`).join("") + 
        `</ul></details>`;
    }

    if (type === "sector") {
      col1 = `<td><strong>${p.sector_name || p.sector_id || "?"}</strong>${evidenceHTML}</td>`;
      col2 = `<td><span class="badge" style="background: rgba(255,255,255,0.1); border: 1px solid var(--border); color: var(--text)">${p.sector_stage || "?"}</span></td>`;
      col3 = `<td>${p.mainline_flag ? "⭐ 主线" : "—"}</td>`;
    } else {
      col1 = `<td><strong>${p.symbol || "?"}</strong>${evidenceHTML}</td>`;
      col2 = `<td>${r.strategy_type === "committee_blocked"
        ? ((r.invalid_conditions && r.invalid_conditions[0]) || "blocked")
        : ((p.stock_snapshot && p.stock_snapshot.entry_timing) || "—")}</td>`;
      col3 = `<td>${(p.stock_snapshot && p.stock_snapshot.trend_signal) || "—"}</td>`;
    }
    return `<tr>
      ${col1}
      <td class="${cls}">${r.direction}</td>
      <td>${(r.confidence * 100).toFixed(0)}%</td>
      ${col2}
      ${col3}
      <td><span class="muted">${(r.created_at || "").slice(11, 19)}</span></td>
    </tr>`;
  }).join("");
  const header = type === "sector"
    ? `<th>板块</th><th>方向</th><th>置信度</th><th>Stage</th><th>主线</th><th>时间</th>`
    : `<th>代码</th><th>方向</th><th>置信度</th><th>${type === "blocked" ? "拦截原因" : "入场时机"}</th><th>趋势</th><th>时间</th>`;
  el.innerHTML = `<table>
    <thead><tr>${header}</tr></thead>
    <tbody>${body}</tbody>
  </table>`;
}

function _renderSignalsAuditSummary(data) {
  const summary = data?.summary || {};
  const el = document.getElementById("signals-audit-summary");
  const meta = document.getElementById("signals-audit-meta");
  const modelVersions = (summary.model_versions || []).length > 0 ? summary.model_versions.join(" · ") : "-";
  const marketRegimes = (summary.market_regimes || []).length > 0 ? summary.market_regimes.join(" · ") : "-";
  const cards = [
    { label: "Trace 数", value: summary.total ?? 0 },
    { label: "Fallback Rate", value: `${((summary.fallback_rate || 0) * 100).toFixed(1)}%` },
    { label: "LLM 解析成功率", value: `${((summary.llm_parse_success_rate || 0) * 100).toFixed(1)}%` },
    { label: "输入快照完整率", value: `${((summary.input_snapshot_complete_rate || 0) * 100).toFixed(1)}%` },
    { label: "基线覆盖率", value: `${((summary.rule_baseline_coverage_rate || 0) * 100).toFixed(1)}%` },
    { label: "Rationale 覆盖率", value: `${((summary.rationale_coverage_rate || 0) * 100).toFixed(1)}%` },
    { label: "平均置信度", value: `${((summary.avg_confidence || 0) * 100).toFixed(1)}%` },
    { label: "LLM 错误数", value: summary.llm_error_count ?? 0 },
  ];
  el.innerHTML = cards.map((c) => `
    <div class="kv">
      <div class="kv-label">${c.label}</div>
      <div class="kv-value">${c.value}</div>
    </div>
  `).join("");
  meta.innerHTML = `
    <div class="meta" style="line-height: 1.7;">
      <div><strong>模型版本:</strong> ${_escapeHtml(modelVersions)}</div>
      <div><strong>市场状态覆盖:</strong> ${_escapeHtml(marketRegimes)}</div>
      <div><strong>审计口径:</strong> 基于 fact_decision_trace 的 baseline、LLM 解析、输入快照、最终决策一致性字段统计。</div>
    </div>
  `;
}

function _auditStatusBadge(label, ok) {
  return `<span class="badge" style="background:${ok ? 'rgba(46,160,67,0.16)' : 'rgba(255,77,79,0.16)'}; color:${ok ? '#3fb950' : '#ff7b72'}; border:1px solid ${ok ? 'rgba(63,185,80,0.35)' : 'rgba(255,123,114,0.35)'};">${label}: ${ok ? "OK" : "FAIL"}</span>`;
}

function _fmtTs(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return _escapeHtml(String(value));
  return d.toLocaleString("zh-CN", { hour12: false });
}

function _diffMinutes(startValue, endValue) {
  if (!startValue || !endValue) return null;
  const start = new Date(startValue).getTime();
  const end = new Date(endValue).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return (end - start) / 60000;
}

function _selectedTrace() {
  const rows = _decisionTraceCache?.traces || [];
  if (rows.length === 0) return null;
  return rows.find((row) => row.trace_id === _selectedTraceId) || rows[0];
}

function _selectedTraceGroupBy() {
  return document.getElementById("signals-trace-groupby")?.value || "fallback_used";
}

function _traceGroupLabel(row, key) {
  if (!row) return "unknown";
  if (key === "fallback_used") return row.fallback_used ? "fallback" : "llm";
  return String(row[key] || "unknown");
}

function _extractApprovedTargetMeta(tgt) {
  if (typeof tgt === "string") return { symbol: tgt, confidence: 0.6, reason: "" };
  if (tgt && typeof tgt === "object") {
    return {
      symbol: String(tgt.symbol || tgt.code || ""),
      confidence: Number(tgt.confidence == null ? 0.6 : tgt.confidence),
      reason: String(tgt.reason || ""),
    };
  }
  return { symbol: "", confidence: 0.5, reason: "" };
}

function _extractBlockedTargetMeta(tgt) {
  if (typeof tgt === "string") return { symbol: tgt, confidence: 0.7, reason: "blocked" };
  if (tgt && typeof tgt === "object") {
    return {
      symbol: String(tgt.symbol || tgt.code || ""),
      confidence: Number(tgt.confidence == null ? 0.7 : tgt.confidence),
      reason: String(tgt.reason || "blocked"),
    };
  }
  return { symbol: "", confidence: 0.5, reason: "blocked" };
}

function _signalRowSymbol(row) {
  return String(
    row?.entity_id ||
    row?.output_payload?.symbol ||
    row?.output_payload?.code ||
    row?.output_payload?.stock_snapshot?.symbol ||
    row?.output_payload?.stock_snapshot?.code ||
    "",
  );
}

function _extractHoldingSymbol(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") return String(item.symbol || item.code || item.ticker || "");
  return "";
}

function _positionAttribution(attribution) {
  return attribution?.position || {};
}

function _allTraceRows() {
  return _decisionTraceCache?.traces || [];
}

function _traceTargetSymbols(trace) {
  const approved = (trace?.final_decision?.approved_targets || []).map((t) => _extractApprovedTargetMeta(t).symbol).filter(Boolean);
  const blocked = (trace?.final_decision?.blocked_targets || []).map((t) => _extractBlockedTargetMeta(t).symbol).filter(Boolean);
  return { approved, blocked };
}

function _isAnomalyMode() {
  return !!document.getElementById("signals-global-anomaly-only")?.checked;
}

function _syncWorkbenchControls() {
  const globalTrace = document.getElementById("signals-global-trace-groupby");
  const traceGroup = document.getElementById("signals-trace-groupby");
  if (globalTrace && traceGroup && traceGroup.value !== globalTrace.value) {
    traceGroup.value = globalTrace.value;
  }
  const globalRunSort = document.getElementById("signals-global-run-sort");
  const runSort = document.getElementById("signals-run-sort");
  if (globalRunSort && runSort && runSort.value !== globalRunSort.value) {
    runSort.value = globalRunSort.value;
  }
}

function _renderWorkbenchSummary() {
  const el = document.getElementById("signals-workbench-summary");
  const alertEl = document.getElementById("signals-workbench-alerts");
  if (!el || !alertEl) return;
  const traces = _allTraceRows();
  const issues = _researchAuditCache?.consistency?.issues || [];
  const selectedTrace = _selectedTrace();
  const selectedRun = _selectedBacktestRun();
  const anomalyCount =
    issues.length +
    traces.filter((row) => !!row.fallback_used).length +
    traces.filter((row) => !!row.llm_error).length;
  const loadFailures = [];
  if (_signalsLoadError) loadFailures.push(`signals=${_signalsLoadError}`);
  if (_decisionTraceLoadError) loadFailures.push(`trace=${_decisionTraceLoadError}`);
  if (_researchAuditLoadError) loadFailures.push(`audit=${_researchAuditLoadError}`);
  el.textContent = `selected_trace=${selectedTrace ? String(selectedTrace.trace_id || "").slice(0, 8) : "-"} · selected_run=${selectedRun ? String(selectedRun.job_id || "").slice(0, 8) : "-"} · traces=${traces.length} · anomalies=${anomalyCount}${loadFailures.length ? ` · unavailable=${loadFailures.length}` : ""}`;
  if (loadFailures.length > 0) {
    alertEl.innerHTML = `<div class="alert-bar error show">${loadFailures.map((line) => _escapeHtml(line)).join(" · ")}</div>`;
    return;
  }
  const lines = [];
  if (issues.length > 0) lines.push(`审计问题 ${issues.length} 条`);
  if (traces.some((row) => !!row.fallback_used)) lines.push(`存在 fallback trace ${traces.filter((row) => !!row.fallback_used).length} 条`);
  if (traces.some((row) => !!row.llm_error)) lines.push(`存在 llm_error trace ${traces.filter((row) => !!row.llm_error).length} 条`);
  if ((selectedRun?.status || "") !== "completed" && selectedRun) lines.push(`selected run 状态=${selectedRun.status}`);
  alertEl.innerHTML = lines.length === 0
    ? '<div class="alert-bar ok show">✅ 当前工作台未发现需优先处理的异常项</div>'
    : `<div class="alert-bar warn show">⚠ ${lines.map((line) => _escapeHtml(line)).join(" · ")}</div>`;
}

function _applySectionVisibility() {
  const anomalyOnly = _isAnomalyMode();
  const issues = _researchAuditCache?.consistency?.issues || [];
  const traces = _allTraceRows();
  const hasTraceAnomaly = traces.some((row) => !!row.fallback_used || !!row.llm_error);
  document.querySelectorAll(".signals-collapsible").forEach((section) => {
    const key = section.dataset.sectionKey || "";
    const shouldShow = !anomalyOnly ||
      (key === "audit") ||
      (key === "trace" && hasTraceAnomaly) ||
      (key === "consistency" && issues.length > 0) ||
      (key === "viz") ||
      (key === "drilldown");
    section.style.display = shouldShow ? "" : "none";
  });
}

function _toggleAllSignalsSections() {
  const sections = [...document.querySelectorAll(".signals-collapsible")];
  // 默认全 collapsed → 首次点击全展开；否则全折叠
  const allCollapsed = sections.every((section) => section.classList.contains("collapsed"));
  sections.forEach((section) => {
    section.classList.toggle("collapsed", !allCollapsed);
  });
}

function _filteredBacktestRuns(runs = null) {
  const rows = runs || _researchAuditCache?.backtest_runs || [];
  const completedOnly = !!document.getElementById("signals-run-filter-completed")?.checked;
  const attributionOnly = !!document.getElementById("signals-run-filter-attribution")?.checked;
  const filtered = rows.filter((run) => {
    if (completedOnly && run.status !== "completed") return false;
    if (attributionOnly && !_attributionForRun(run) && !run.attribution_json) return false;
    return true;
  });
  return _traceAwareSortRuns(_sortBacktestRuns(filtered));
}

function _selectedBacktestRun() {
  const runs = _filteredBacktestRuns();
  if (runs.length === 0) return null;
  return runs.find((run) => run.job_id === _selectedBacktestJobId) ||
    runs.find((run) => run.status === "completed") ||
    runs[0];
}

function _attributionForRun(run) {
  if (!run) return null;
  return run.attribution_json || _signalsBacktestAttrCache[run.job_id] || null;
}

function _selectedRunSortKey() {
  return document.getElementById("signals-run-sort")?.value || "alpha";
}

function _sortMetricValue(run, key) {
  if (!run) return Number.NEGATIVE_INFINITY;
  const attribution = _attributionForRun(run) || {};
  if (key === "turnover") return Number((attribution.rebalance || {}).total_turnover || 0);
  if (key === "ic_ir") return Number((attribution.score_ic || {}).ic_ir || 0);
  if (key === "beta") return Number(run.benchmark_beta == null ? Number.NEGATIVE_INFINITY : run.benchmark_beta);
  if (key === "created_at") return Date.parse(run.created_at || "") || Number.NEGATIVE_INFINITY;
  return Number(run.benchmark_alpha == null ? Number.NEGATIVE_INFINITY : run.benchmark_alpha);
}

function _sortBacktestRuns(runs) {
  const key = _selectedRunSortKey();
  return [...(runs || [])].sort((a, b) => _sortMetricValue(b, key) - _sortMetricValue(a, key));
}

function _median(values) {
  const rows = (values || []).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (rows.length === 0) return null;
  const mid = Math.floor(rows.length / 2);
  if (rows.length % 2 === 1) return rows[mid];
  return (rows[mid - 1] + rows[mid]) / 2;
}

function _avg(values) {
  const rows = (values || []).filter((v) => Number.isFinite(v));
  if (rows.length === 0) return null;
  return rows.reduce((acc, cur) => acc + cur, 0) / rows.length;
}

function _approvedCountForTrace(trace) {
  return (trace?.final_decision?.approved_targets || []).length;
}

function _blockedCountForTrace(trace) {
  return (trace?.final_decision?.blocked_targets || []).length;
}

function _runTopN(run) {
  const v = run?.config_json?.top_n ?? run?.config_json?.topn ?? null;
  return v == null ? null : Number(v);
}

function _traceAwareScore(run, trace) {
  if (!run) return Number.NEGATIVE_INFINITY;
  const approvedCount = _approvedCountForTrace(trace);
  const runTopN = _runTopN(run);
  const fitScore = approvedCount > 0 && Number.isFinite(runTopN)
    ? Math.max(0, 30 - Math.abs(runTopN - approvedCount) * 5)
    : 0;
  const completedScore = run.status === "completed" ? 20 : 0;
  const attrScore = _attributionForRun(run) || run.attribution_json ? 10 : 0;
  const alphaScore = Number.isFinite(Number(run.benchmark_alpha)) ? Number(run.benchmark_alpha) * 100 : 0;
  return fitScore + completedScore + attrScore + alphaScore;
}

function _traceAwareExplain(run, trace) {
  if (!run) {
    return {
      total: null,
      approvedCount: _approvedCountForTrace(trace),
      runTopN: null,
      fitScore: null,
      completedScore: null,
      attrScore: null,
      alphaScore: null,
    };
  }
  const approvedCount = _approvedCountForTrace(trace);
  const runTopN = _runTopN(run);
  const fitScore = approvedCount > 0 && Number.isFinite(runTopN)
    ? Math.max(0, 30 - Math.abs(runTopN - approvedCount) * 5)
    : 0;
  const completedScore = run.status === "completed" ? 20 : 0;
  const attrScore = _attributionForRun(run) || run.attribution_json ? 10 : 0;
  const alphaScore = Number.isFinite(Number(run.benchmark_alpha)) ? Number(run.benchmark_alpha) * 100 : 0;
  return {
    total: fitScore + completedScore + attrScore + alphaScore,
    approvedCount,
    runTopN,
    fitScore,
    completedScore,
    attrScore,
    alphaScore,
  };
}

function _traceAwareSortRuns(runs) {
  const trace = _selectedTrace();
  if (!trace) return runs;
  return [...(runs || [])].sort((a, b) => _traceAwareScore(b, trace) - _traceAwareScore(a, trace));
}

function _countBy(rows, extractor) {
  const counter = new Map();
  (rows || []).forEach((row) => {
    const raw = extractor(row);
    const key = String(raw || "unknown").trim() || "unknown";
    counter.set(key, (counter.get(key) || 0) + 1);
  });
  return [...counter.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
}

function _renderDistributionChart(existingChart, canvasId, labels, values, color, emptyMessage = "暂无图表数据") {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return existingChart;
  if (existingChart) existingChart.destroy();
  if (!labels || labels.length === 0) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    _setChartPlaceholder(canvasId, emptyMessage);
    return null;
  }
  _setChartPlaceholder(canvasId, "");
  const ctx = canvas.getContext("2d");
  return new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: color,
        borderColor: color,
        borderWidth: 1,
        borderRadius: 6,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          ticks: { color: "#8b949e" },
          grid: { color: "rgba(255,255,255,0.06)" },
        },
        y: {
          ticks: { color: "#c9d1d9" },
          grid: { display: false },
        },
      },
    },
  });
}

function _renderSignalsDrilldown() {
  const chainEl = document.getElementById("signals-drilldown-chain");
  const notesEl = document.getElementById("signals-drilldown-notes");
  const trace = _selectedTrace();
  const marketSignal = _researchAuditCache?.market_signal || (_signalsCache?.groups?.market || [])[0] || null;
  const selectedRun = _selectedBacktestRun();
  const approvedRows = _signalsCache?.groups?.approved || [];
  const blockedRows = _signalsCache?.groups?.blocked || [];

  const approvedDist = _countBy(
    approvedRows,
    (row) => row.output_payload?.stock_snapshot?.entry_timing || row.output_payload?.entry_timing || "unknown",
  );
  const blockedDist = _countBy(
    blockedRows,
    (row) => (row.invalid_conditions || [])[0] || row.output_payload?.reason || "unknown",
  );
  _signalsApprovedChart = _renderDistributionChart(
    _signalsApprovedChart,
    "signals-approved-chart",
    approvedDist.map(([label]) => label),
    approvedDist.map(([, value]) => value),
    "rgba(63,185,80,0.75)",
    "当前业务日无准入分布数据",
  );
  _signalsBlockedChart = _renderDistributionChart(
    _signalsBlockedChart,
    "signals-blocked-chart",
    blockedDist.map(([label]) => label),
    blockedDist.map(([, value]) => value),
    "rgba(255,123,114,0.75)",
    "当前业务日无拦截分布数据",
  );

  if (!trace && !marketSignal && !selectedRun) {
    chainEl.innerHTML = '<p class="muted">暂无可钻取链路</p>';
    notesEl.innerHTML = '<span class="meta">暂无钻取说明</span>';
    return;
  }

  const traceApproved = (trace?.final_decision?.approved_targets || []).length;
  const traceBlocked = (trace?.final_decision?.blocked_targets || []).length;
  const confidenceDelta = Number(_researchAuditCache?.consistency?.confidence_delta || 0).toFixed(6);
  chainEl.innerHTML = `
    <div class="audit-chain-card active">
      <div class="audit-chain-title">Decision Trace</div>
      <div class="audit-chain-value">${_escapeHtml(trace?.verdict || "-")}</div>
      <div class="audit-chain-meta">
        conf=${((Number(trace?.confidence || 0)) * 100).toFixed(1)}%<br>
        model=${_escapeHtml(trace?.model_version || "-")}<br>
        fallback=${trace?.fallback_used ? "true" : "false"}
      </div>
    </div>
    <div class="audit-chain-arrow">→</div>
    <div class="audit-chain-card active">
      <div class="audit-chain-title">Market Signal</div>
      <div class="audit-chain-value">${_escapeHtml((marketSignal?.output_payload || {}).verdict || marketSignal?.direction || "-")}</div>
      <div class="audit-chain-meta">
        conf=${((Number(marketSignal?.confidence || 0)) * 100).toFixed(1)}%<br>
        risk_budget=${_escapeHtml((marketSignal?.output_payload || {}).risk_budget || "-")}<br>
        regime=${_escapeHtml((marketSignal?.output_payload || {}).market_regime || "-")}
      </div>
    </div>
    <div class="audit-chain-arrow">→</div>
    <div class="audit-chain-card ${selectedRun ? "active" : ""}">
      <div class="audit-chain-title">Backtest Run</div>
      <div class="audit-chain-value">${_escapeHtml(selectedRun?.status || "none")}</div>
      <div class="audit-chain-meta">
        strategy=${_escapeHtml(selectedRun?.config_json?.strategy_name || "-")}<br>
        universe=${_escapeHtml(selectedRun?.config_json?.universe || "-")}<br>
        alpha=${selectedRun?.benchmark_alpha == null ? "-" : Number(selectedRun.benchmark_alpha).toFixed(4)}
      </div>
    </div>
  `;
  notesEl.innerHTML = `
    <div class="meta" style="line-height: 1.8;">
      <div><strong>当前 trace:</strong> ${_escapeHtml(trace?.trace_id || "-")} · created_at=${_escapeHtml(trace?.created_at || "-")}</div>
      <div><strong>目标结构:</strong> approved(trace/signal)=${traceApproved}/${approvedRows.length} · blocked(trace/signal)=${traceBlocked}/${blockedRows.length}</div>
      <div><strong>一致性差值:</strong> confidence delta=${confidenceDelta} · issues=${(_researchAuditCache?.consistency?.issues || []).length}</div>
      <div><strong>图表口径:</strong> 左侧为当前业务日已落库的 approved entry_timing 与 blocked reason 分布，非前端伪造数据。</div>
    </div>
  `;
  _renderEvidenceMap(trace);
  _renderRunRecommendation(trace, selectedRun);
  _renderStockReview(trace, selectedRun);
  _renderTraceCompare(_allTraceRows());
  _renderStockDiff(_allTraceRows());
  _renderRunRankDiff();
  _renderTraceAttributionBridge(trace, selectedRun);
}

function _renderSignalsAttributionChart(attribution) {
  if (!attribution) {
    if (_signalsAttributionChart) {
      _signalsAttributionChart.destroy();
      _signalsAttributionChart = null;
    }
    const canvas = document.getElementById("signals-attribution-chart");
    if (canvas) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    _setChartPlaceholder("signals-attribution-chart", "当前选中 run 无归因指标图");
    return;
  }
  const labels = ["总换手", "均换手", "最大换手", "IC 均值", "IC IR", "净换手"];
  const rebalance = attribution?.rebalance || {};
  const scoreIc = attribution?.score_ic || {};
  const holdings = attribution?.holdings_diff || {};
  const values = [
    Number(rebalance.total_turnover || 0),
    Number(rebalance.avg_turnover || 0),
    Number(rebalance.max_turnover || 0),
    Number(scoreIc.ic_mean || 0),
    Number(scoreIc.ic_ir || 0),
    Number(holdings.turnover || 0),
  ];
  _signalsAttributionChart = _renderDistributionChart(
    _signalsAttributionChart,
    "signals-attribution-chart",
    labels,
    values,
    "rgba(47,129,247,0.75)",
    "当前选中 run 无归因指标图",
  );
}

function _renderSignalsRunsCompareChart(runs) {
  const canvasId = "signals-runs-compare-chart";
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (_signalsRunsCompareChart) {
    _signalsRunsCompareChart.destroy();
    _signalsRunsCompareChart = null;
  }
  if (!runs || runs.length === 0) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const selectedRun = _selectedBacktestRun();
  const labels = runs.map((run) => `${run.config_json?.strategy_name || "-"}\n${String(run.job_id || "").slice(0, 8)}`);
  const alphaData = runs.map((run) => run.benchmark_alpha == null ? null : Number(run.benchmark_alpha));
  const betaData = runs.map((run) => run.benchmark_beta == null ? null : Number(run.benchmark_beta));
  const turnoverData = runs.map((run) => Number((_attributionForRun(run)?.rebalance || {}).total_turnover || 0));
  const icIrData = runs.map((run) => Number((_attributionForRun(run)?.score_ic || {}).ic_ir || 0));
  const selectedIndex = runs.findIndex((run) => run.job_id === selectedRun?.job_id);
  const ctx = canvas.getContext("2d");
  _signalsRunsCompareChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "alpha",
          data: alphaData,
          backgroundColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(47,129,247,0.98)" : "rgba(47,129,247,0.42)"),
          borderColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(255,255,255,0.95)" : "rgba(47,129,247,0.72)"),
          borderWidth: runs.map((_, idx) => idx === selectedIndex ? 2 : 1),
        },
        {
          label: "beta",
          data: betaData,
          backgroundColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(212,167,44,0.98)" : "rgba(212,167,44,0.42)"),
          borderColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(255,255,255,0.95)" : "rgba(212,167,44,0.72)"),
          borderWidth: runs.map((_, idx) => idx === selectedIndex ? 2 : 1),
        },
        {
          label: "turnover",
          data: turnoverData,
          backgroundColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(255,123,114,0.98)" : "rgba(255,123,114,0.42)"),
          borderColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(255,255,255,0.95)" : "rgba(255,123,114,0.72)"),
          borderWidth: runs.map((_, idx) => idx === selectedIndex ? 2 : 1),
        },
        {
          label: "IC IR",
          data: icIrData,
          backgroundColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(63,185,80,0.98)" : "rgba(63,185,80,0.42)"),
          borderColor: runs.map((_, idx) => idx === selectedIndex ? "rgba(255,255,255,0.95)" : "rgba(63,185,80,0.72)"),
          borderWidth: runs.map((_, idx) => idx === selectedIndex ? 2 : 1),
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: "#c9d1d9" } },
      },
      scales: {
        x: {
          ticks: { color: "#8b949e" },
          grid: { display: false },
        },
        y: {
          ticks: { color: "#8b949e" },
          grid: { color: "rgba(255,255,255,0.06)" },
        },
      },
    },
  });
}

function _renderSignalsRunsCompareSummary(allRuns, visibleRuns) {
  const el = document.getElementById("signals-runs-compare-summary");
  if (!el) return;
  const completed = (allRuns || []).filter((run) => run.status === "completed").length;
  const withAttr = (allRuns || []).filter((run) => !!_attributionForRun(run) || !!run.attribution_json).length;
  el.textContent = `展示 ${visibleRuns.length}/${(allRuns || []).length} · completed=${completed} · attribution=${withAttr} · sort=${_selectedRunSortKey()}`;
}

function _renderSignalsRunsPeerSummary(runs) {
  const el = document.getElementById("signals-runs-peer-summary");
  if (!el) return;
  const selected = _selectedBacktestRun();
  if (!selected || !runs || runs.length === 0) {
    el.innerHTML = '<span class="meta">当前过滤集合下暂无 peer 对比摘要</span>';
    return;
  }
  const key = _selectedRunSortKey();
  const metric = _sortMetricValue(selected, key);
  const ordered = _sortBacktestRuns(runs);
  const rank = ordered.findIndex((run) => run.job_id === selected.job_id) + 1;
  const values = ordered.map((run) => _sortMetricValue(run, key));
  const median = _median(values);
  const best = values[0] ?? null;
  const worst = values[values.length - 1] ?? null;
  const diffVsMedian = median == null || !Number.isFinite(metric) ? null : metric - median;
  el.innerHTML = `
    <div class="meta" style="line-height:1.8;">
      <div><strong>Peer Rank:</strong> 当前选中 run 在过滤集合中排名 ${rank}/${ordered.length}，排序口径=${_escapeHtml(key)}</div>
      <div><strong>Metric:</strong> current=${Number.isFinite(metric) ? Number(metric).toFixed(4) : "-"} · median=${median == null ? "-" : Number(median).toFixed(4)} · best=${best == null ? "-" : Number(best).toFixed(4)} · worst=${worst == null ? "-" : Number(worst).toFixed(4)}</div>
      <div><strong>Delta:</strong> vs median=${diffVsMedian == null ? "-" : Number(diffVsMedian).toFixed(4)} · selected_job=${_escapeHtml(String(selected.job_id || "").slice(0, 8))}</div>
    </div>
  `;
}

function _renderFallbackSummary(rows) {
  const el = document.getElementById("signals-fallback-summary");
  if (!el) return;
  const traces = rows || _decisionTraceCache?.traces || [];
  if (traces.length === 0) {
    el.innerHTML = '<span class="meta">暂无 fallback 分组样本</span>';
    return;
  }
  const fallbackRows = traces.filter((row) => !!row.fallback_used);
  const llmRows = traces.filter((row) => !row.fallback_used);
  const fallbackAvg = _avg(fallbackRows.map((row) => Number(row.confidence || 0)));
  const llmAvg = _avg(llmRows.map((row) => Number(row.confidence || 0)));
  el.innerHTML = `
    <div class="meta" style="line-height:1.8;">
      <div><strong>Fallback:</strong> ${fallbackRows.length} 条 · avg_conf=${fallbackAvg == null ? "-" : (fallbackAvg * 100).toFixed(1) + "%"}</div>
      <div><strong>LLM:</strong> ${llmRows.length} 条 · avg_conf=${llmAvg == null ? "-" : (llmAvg * 100).toFixed(1) + "%"}</div>
      <div><strong>Delta:</strong> conf_gap=${fallbackAvg == null || llmAvg == null ? "-" : ((llmAvg - fallbackAvg) * 100).toFixed(1) + " pct"} · sample=${traces.length}</div>
    </div>
  `;
}

function _renderTraceDiffMatrix(rows) {
  const el = document.getElementById("signals-trace-diff-matrix");
  if (!el) return;
  const traces = rows || _decisionTraceCache?.traces || [];
  if (traces.length === 0) {
    el.innerHTML = '<p class="muted">暂无 trace 差异矩阵</p>';
    return;
  }
  const verdicts = new Set(traces.map((row) => row.verdict || "-"));
  const regimes = new Set(traces.map((row) => row.market_regime || "-"));
  const models = new Set(traces.map((row) => row.model_version || "-"));
  const llmErrors = traces.filter((row) => !!row.llm_error).length;
  const selected = _selectedTrace();
  const cells = [
    ["Verdict 离散度", verdicts.size <= 1, `unique=${verdicts.size}`],
    ["Regime 离散度", regimes.size <= 1, `unique=${regimes.size}`],
    ["Model 离散度", models.size <= 1, `unique=${models.size}`],
    ["Fallback 比例", traces.filter((row) => !!row.fallback_used).length === 0, `fallback=${traces.filter((row) => !!row.fallback_used).length}`],
    ["LLM Error", llmErrors === 0, `errors=${llmErrors}`],
    ["Selected Trace", !!selected, selected ? `selected=${String(selected.trace_id || "").slice(0, 8)}` : "未选择"],
  ];
  el.innerHTML = cells.map(([title, ok, meta]) => `
    <div class="audit-matrix-cell ${ok ? "ok" : "fail"}">
      <div class="audit-matrix-title">${title}</div>
      <div class="audit-matrix-status">${ok ? "OK" : "DIFF"}</div>
      <div class="audit-matrix-meta">${_escapeHtml(meta)}</div>
    </div>
  `).join("");
}

function _renderTraceGroupCompare(rows) {
  const traces = rows || _decisionTraceCache?.traces || [];
  const summaryEl = document.getElementById("signals-trace-group-summary");
  if (!summaryEl) return;
  if (traces.length === 0) {
    summaryEl.textContent = "暂无分组样本";
    _signalsTraceGroupChart = _renderDistributionChart(
      _signalsTraceGroupChart,
      "signals-trace-group-chart",
      [],
      [],
      "rgba(47,129,247,0.75)",
    );
    return;
  }
  const groupBy = _selectedTraceGroupBy();
  const grouped = _countBy(traces, (row) => _traceGroupLabel(row, groupBy));
  const selected = _selectedTrace();
  const selectedLabel = _traceGroupLabel(selected, groupBy);
  const peerRows = traces.filter((row) => _traceGroupLabel(row, groupBy) === selectedLabel);
  const orderedPeers = [...peerRows].sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
  const rank = selected ? orderedPeers.findIndex((row) => row.trace_id === selected.trace_id) + 1 : 0;
  const peerAvg = _avg(peerRows.map((row) => Number(row.confidence || 0)));
  summaryEl.textContent = `group=${selectedLabel} · peers=${peerRows.length}/${traces.length} · rank=${rank || "-"} · avg_conf=${peerAvg == null ? "-" : (peerAvg * 100).toFixed(1) + "%"}`;
  _signalsTraceGroupChart = _renderDistributionChart(
    _signalsTraceGroupChart,
    "signals-trace-group-chart",
    grouped.map(([label]) => label),
    grouped.map(([, value]) => value),
    "rgba(47,129,247,0.75)",
  );
}

function _renderTraceAttributionBridge(trace, run) {
  const el = document.getElementById("signals-trace-attribution-bridge");
  if (!el) return;
  if (!trace && !run) {
    el.innerHTML = '<span class="meta">暂无 trace 与 attribution 闭环对照</span>';
    return;
  }
  const approvedCount = _approvedCountForTrace(trace);
  const blockedCount = _blockedCountForTrace(trace);
  const runTopN = _runTopN(run);
  const attribution = _attributionForRun(run);
  const turnover = Number((attribution?.rebalance || {}).total_turnover || 0);
  const icIr = Number((attribution?.score_ic || {}).ic_ir || 0);
  const topNGap = Number.isFinite(runTopN) ? runTopN - approvedCount : null;
  el.innerHTML = `
    <div class="meta" style="line-height:1.8;">
      <div><strong>Trace:</strong> verdict=${_escapeHtml(trace?.verdict || "-")} · confidence=${trace ? ((Number(trace.confidence || 0) * 100).toFixed(1) + "%") : "-"} · risk_budget=${_escapeHtml(trace?.final_decision?.risk_budget || trace?.risk_budget || "-")}</div>
      <div><strong>Targets:</strong> approved=${approvedCount} · blocked=${blockedCount} · run_top_n=${runTopN == null ? "-" : runTopN} · top_n_gap=${topNGap == null ? "-" : topNGap}</div>
      <div><strong>Attribution:</strong> status=${_escapeHtml(run?.status || "-")} · alpha=${run?.benchmark_alpha == null ? "-" : Number(run.benchmark_alpha).toFixed(4)} · turnover=${turnover.toFixed(4)} · IC_IR=${icIr.toFixed(4)}</div>
      <div><strong>说明:</strong> 闭环对照基于 selected trace 的目标结构与 selected run 的真实参数/归因结果并排展示，用于复盘，不伪造因果结论。</div>
    </div>
  `;
}

function _renderEvidenceMap(trace) {
  const el = document.getElementById("signals-evidence-map");
  if (!el) return;
  const approvedRows = _signalsCache?.groups?.approved || [];
  const blockedRows = _signalsCache?.groups?.blocked || [];
  const approvedTargets = (trace?.final_decision?.approved_targets || []).map((tgt) => ({ side: "approved", ..._extractApprovedTargetMeta(tgt) }));
  const blockedTargets = (trace?.final_decision?.blocked_targets || []).map((tgt) => ({ side: "blocked", ..._extractBlockedTargetMeta(tgt) }));
  const targets = [...approvedTargets, ...blockedTargets];
  if (targets.length === 0) {
    el.innerHTML = '<p class="muted">当前 trace 无个股级目标映射</p>';
    return;
  }
  const rows = targets.map((tgt) => {
    const sourceRows = tgt.side === "approved" ? approvedRows : blockedRows;
    const matched = sourceRows.find((row) => _signalRowSymbol(row) === tgt.symbol);
    const payload = matched?.output_payload || {};
    const stockSnapshot = payload.stock_snapshot || {};
    const evidenceCount = Array.isArray(matched?.supporting_evidence) ? matched.supporting_evidence.length : 0;
    return {
      symbol: tgt.symbol || "-",
      side: tgt.side,
      traceConfidence: tgt.confidence,
      traceReason: tgt.reason || "-",
      signalConfidence: matched?.confidence,
      signalMeta: tgt.side === "approved"
        ? (stockSnapshot.entry_timing || payload.entry_timing || "-")
        : ((matched?.invalid_conditions || [])[0] || payload.reason || "-"),
      evidenceCount,
      linked: !!matched,
    };
  });
  el.innerHTML = `<table>
    <thead><tr><th>symbol</th><th>side</th><th>trace_conf</th><th>trace_reason</th><th>signal_conf</th><th>signal_meta</th><th>evidence</th><th>link</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><code>${_escapeHtml(row.symbol)}</code></td>
        <td>${_escapeHtml(row.side)}</td>
        <td>${Number(row.traceConfidence || 0).toFixed(2)}</td>
        <td>${_escapeHtml(String(row.traceReason || "-"))}</td>
        <td>${row.signalConfidence == null ? "-" : Number(row.signalConfidence).toFixed(2)}</td>
        <td>${_escapeHtml(String(row.signalMeta || "-"))}</td>
        <td>${row.evidenceCount}</td>
        <td>${row.linked ? '<span class="badge ok">matched</span>' : '<span class="badge warn">missing</span>'}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

function _renderRunRecommendation(trace, run) {
  const el = document.getElementById("signals-run-recommendation");
  if (!el) return;
  const explain = _traceAwareExplain(run, trace);
  if (!run) {
    el.innerHTML = '<span class="meta">暂无 run 推荐解释</span>';
    return;
  }
  el.innerHTML = `
    <div class="meta" style="line-height:1.8;">
      <div><strong>Selected Run:</strong> ${_escapeHtml(String(run.job_id || "").slice(0, 8))} · strategy=${_escapeHtml(run.config_json?.strategy_name || "-")} · status=${_escapeHtml(run.status || "-")}</div>
      <div><strong>Target Fit:</strong> approved_targets=${explain.approvedCount} · run_top_n=${explain.runTopN == null ? "-" : explain.runTopN} · fit_score=${explain.fitScore == null ? "-" : explain.fitScore.toFixed(2)}</div>
      <div><strong>Run Quality:</strong> completed_score=${explain.completedScore == null ? "-" : explain.completedScore.toFixed(2)} · attribution_score=${explain.attrScore == null ? "-" : explain.attrScore.toFixed(2)} · alpha_score=${explain.alphaScore == null ? "-" : explain.alphaScore.toFixed(2)}</div>
      <div><strong>Total:</strong> trace_aware_score=${explain.total == null ? "-" : explain.total.toFixed(2)} · 该分数仅用于同业务日 run 推荐排序，不伪装为收益预测。</div>
    </div>
  `;
}

function _renderAttributionModuleDetails(attribution) {
  const el = document.getElementById("signals-attribution-module-details");
  if (!el) return;
  if (!attribution) {
    el.innerHTML = '<p class="muted">暂无 attribution 模块逐项展开</p>';
    return;
  }
  const rebalance = attribution.rebalance || {};
  const scoreIc = attribution.score_ic || {};
  const holdings = attribution.holdings_diff || attribution.position || {};
  const segments = Array.isArray(rebalance.segments) ? rebalance.segments : [];
  el.innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap: 12px;">
      <details open>
        <summary style="cursor:pointer; color:var(--text-muted);">Rebalance 逐段 (${segments.length})</summary>
        ${segments.length === 0 ? '<p class="muted">暂无调仓段明细</p>' : `<table style="margin-top:8px;"><thead><tr><th>date</th><th>turnover</th><th>cum_return</th><th>days</th></tr></thead><tbody>${segments.slice(0, 8).map((seg) => `<tr><td>${_escapeHtml(String(seg.date || "-"))}</td><td>${Number(seg.turnover || 0).toFixed(4)}</td><td>${Number(seg.cum_return || 0).toFixed(4)}</td><td>${seg.n_days ?? 0}</td></tr>`).join("")}</tbody></table>`}
      </details>
      <details open>
        <summary style="cursor:pointer; color:var(--text-muted);">Score IC 原始块</summary>
        <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify(scoreIc, null, 2))}</pre>
      </details>
      <details open>
        <summary style="cursor:pointer; color:var(--text-muted);">Holdings / Position 原始块</summary>
        <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify(holdings, null, 2))}</pre>
      </details>
    </div>
  `;
}

function _renderStockReview(trace, run) {
  const el = document.getElementById("signals-stock-review");
  if (!el) return;
  const approvedRows = _signalsCache?.groups?.approved || [];
  const blockedRows = _signalsCache?.groups?.blocked || [];
  const attribution = _attributionForRun(run);
  const position = _positionAttribution(attribution);
  const holdingSymbols = Array.isArray(position.holding_symbols)
    ? position.holding_symbols.map(_extractHoldingSymbol).filter(Boolean)
    : [];
  const returnMap = new Map(
    holdingSymbols.map((sym, idx) => [sym, Array.isArray(position.top_n_total_returns) ? position.top_n_total_returns[idx] : null]),
  );
  const approvedTargets = (trace?.final_decision?.approved_targets || []).map((tgt) => ({ side: "approved", ..._extractApprovedTargetMeta(tgt) }));
  const blockedTargets = (trace?.final_decision?.blocked_targets || []).map((tgt) => ({ side: "blocked", ..._extractBlockedTargetMeta(tgt) }));
  const targets = [...approvedTargets, ...blockedTargets];
  if (targets.length === 0) {
    el.innerHTML = '<p class="muted">当前 trace 无个股级复盘对象</p>';
    return;
  }
  const rows = targets.map((tgt) => {
    const sourceRows = tgt.side === "approved" ? approvedRows : blockedRows;
    const matched = sourceRows.find((row) => _signalRowSymbol(row) === tgt.symbol);
    const inHolding = holdingSymbols.includes(tgt.symbol);
    const signalStatus = matched ? "linked" : "missing";
    const realized = returnMap.get(tgt.symbol);
    return {
      symbol: tgt.symbol || "-",
      side: tgt.side,
      traceConfidence: tgt.confidence,
      signalStatus,
      inHolding,
      realized,
      meta: tgt.side === "approved"
        ? (matched?.output_payload?.stock_snapshot?.entry_timing || matched?.output_payload?.entry_timing || "-")
        : ((matched?.invalid_conditions || [])[0] || matched?.output_payload?.reason || tgt.reason || "-"),
    };
  });
  el.innerHTML = `<table>
    <thead><tr><th>symbol</th><th>side</th><th>trace_conf</th><th>signal</th><th>in_run_holdings</th><th>realized_return</th><th>meta</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><code>${_escapeHtml(row.symbol)}</code></td>
        <td>${_escapeHtml(row.side)}</td>
        <td>${Number(row.traceConfidence || 0).toFixed(2)}</td>
        <td>${row.signalStatus === "linked" ? '<span class="badge ok">linked</span>' : '<span class="badge warn">missing</span>'}</td>
        <td>${row.inHolding ? '<span class="badge ok">yes</span>' : '<span class="badge warn">no</span>'}</td>
        <td>${row.realized == null ? "-" : fmtPct(Number(row.realized) * 100)}</td>
        <td>${_escapeHtml(String(row.meta || "-"))}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

function _renderSegmentReview(attribution) {
  const el = document.getElementById("signals-segment-review");
  if (!el) return;
  const rebalance = attribution?.rebalance || {};
  const segments = Array.isArray(rebalance.segments) ? rebalance.segments : [];
  const best = rebalance.best_segment || null;
  const worst = rebalance.worst_segment || null;
  if (segments.length === 0) {
    el.innerHTML = '<p class="muted">暂无调仓段复盘数据</p>';
    return;
  }
  el.innerHTML = `
    <div class="meta" style="margin-bottom:12px; line-height:1.8;">
      <div><strong>Best Segment:</strong> ${best ? `${_escapeHtml(String(best.date || "-"))} · cum_return=${Number(best.cum_return || 0).toFixed(4)} · turnover=${Number(best.turnover || 0).toFixed(4)}` : "-"}</div>
      <div><strong>Worst Segment:</strong> ${worst ? `${_escapeHtml(String(worst.date || "-"))} · cum_return=${Number(worst.cum_return || 0).toFixed(4)} · turnover=${Number(worst.turnover || 0).toFixed(4)}` : "-"}</div>
    </div>
    <table>
      <thead><tr><th>date</th><th>sold</th><th>bought</th><th>turnover</th><th>cum_return</th><th>avg_daily</th><th>tag</th></tr></thead>
      <tbody>${segments.slice(0, 10).map((seg) => {
        const isBest = best && String(best.date || "") === String(seg.date || "");
        const isWorst = worst && String(worst.date || "") === String(seg.date || "");
        return `<tr>
          <td>${_escapeHtml(String(seg.date || "-"))}</td>
          <td>${seg.sold_n ?? 0}</td>
          <td>${seg.bought_n ?? 0}</td>
          <td>${Number(seg.turnover || 0).toFixed(4)}</td>
          <td>${Number(seg.cum_return || 0).toFixed(4)}</td>
          <td>${Number(seg.avg_daily || 0).toFixed(4)}</td>
          <td>${isBest ? '<span class="badge ok">best</span>' : isWorst ? '<span class="badge warn">worst</span>' : '<span class="muted">-</span>'}</td>
        </tr>`;
      }).join("")}</tbody>
    </table>
  `;
}

function _renderTraceCompare(rows) {
  const el = document.getElementById("signals-trace-compare");
  if (!el) return;
  const traces = (rows || _allTraceRows()).filter((trace) => {
    if (!_isAnomalyMode()) return true;
    return !!trace.fallback_used || !!trace.llm_error;
  });
  if (traces.length === 0) {
    el.innerHTML = '<p class="muted">暂无多 trace 横向对照</p>';
    return;
  }
  el.innerHTML = `<table>
    <thead><tr><th>trace</th><th>verdict</th><th>confidence</th><th>model</th><th>fallback</th><th>regime</th><th>approved</th><th>blocked</th><th>selected</th></tr></thead>
    <tbody>${traces.map((trace) => `
      <tr>
        <td><code>${_escapeHtml(String(trace.trace_id || "").slice(0, 8))}</code></td>
        <td>${_escapeHtml(trace.verdict || "-")}</td>
        <td>${(Number(trace.confidence || 0) * 100).toFixed(1)}%</td>
        <td>${_escapeHtml(trace.model_version || "-")}</td>
        <td>${trace.fallback_used ? '<span class="badge warn">fallback</span>' : '<span class="badge ok">llm</span>'}</td>
        <td>${_escapeHtml(trace.market_regime || "-")}</td>
        <td>${_approvedCountForTrace(trace)}</td>
        <td>${_blockedCountForTrace(trace)}</td>
        <td>${trace.trace_id === _selectedTrace()?.trace_id ? '<span class="badge ok">selected</span>' : '<span class="muted">-</span>'}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

function _renderStockDiff(rows) {
  const el = document.getElementById("signals-stock-diff");
  if (!el) return;
  const traces = rows || _allTraceRows();
  if (traces.length <= 1) {
    el.innerHTML = '<p class="muted">至少需要 2 条 trace 才能展示个股差异</p>';
    return;
  }
  const approvedCounter = new Map();
  const blockedCounter = new Map();
  traces.forEach((trace) => {
    const symbols = _traceTargetSymbols(trace);
    symbols.approved.forEach((sym) => approvedCounter.set(sym, (approvedCounter.get(sym) || 0) + 1));
    symbols.blocked.forEach((sym) => blockedCounter.set(sym, (blockedCounter.get(sym) || 0) + 1));
  });
  const diffs = [...new Set([...approvedCounter.keys(), ...blockedCounter.keys()])].map((symbol) => ({
    symbol,
    approvedCount: approvedCounter.get(symbol) || 0,
    blockedCount: blockedCounter.get(symbol) || 0,
  })).sort((a, b) => Math.abs((b.approvedCount - b.blockedCount)) - Math.abs((a.approvedCount - a.blockedCount)))
    .filter((row) => !_isAnomalyMode() || row.approvedCount !== row.blockedCount)
    .slice(0, 12);
  if (diffs.length === 0) {
    el.innerHTML = '<p class="muted">当前多 trace 无显著个股差异</p>';
    return;
  }
  el.innerHTML = `<table>
    <thead><tr><th>symbol</th><th>approved traces</th><th>blocked traces</th><th>delta</th></tr></thead>
    <tbody>${diffs.map((row) => `
      <tr>
        <td><code>${_escapeHtml(row.symbol)}</code></td>
        <td>${row.approvedCount}</td>
        <td>${row.blockedCount}</td>
        <td>${row.approvedCount - row.blockedCount}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

function _renderRunRankDiff(allRuns) {
  const el = document.getElementById("signals-run-rank-diff");
  if (!el) return;
  const runs = (allRuns || _researchAuditCache?.backtest_runs || []).filter((run) => run && run.job_id);
  const traces = _allTraceRows();
  if (runs.length === 0 || traces.length === 0) {
    el.innerHTML = '<p class="muted">暂无 run 排序变化解释</p>';
    return;
  }
  const rows = traces.map((trace) => {
    const ordered = [...runs].sort((a, b) => _traceAwareScore(b, trace) - _traceAwareScore(a, trace));
    const top = ordered[0] || null;
    const selected = _selectedBacktestRun();
    const selectedRank = selected ? ordered.findIndex((run) => run.job_id === selected.job_id) + 1 : 0;
    return {
      traceId: String(trace.trace_id || "").slice(0, 8),
      verdict: trace.verdict || "-",
      topJob: top ? String(top.job_id || "").slice(0, 8) : "-",
      topScore: top ? _traceAwareScore(top, trace).toFixed(2) : "-",
      selectedRank: selectedRank || "-",
      selectedScore: selected ? _traceAwareScore(selected, trace).toFixed(2) : "-",
    };
  }).filter((row) => !_isAnomalyMode() || String(row.selectedRank) !== "1");
  el.innerHTML = `<table>
    <thead><tr><th>trace</th><th>verdict</th><th>top run</th><th>top score</th><th>selected run rank</th><th>selected score</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><code>${_escapeHtml(row.traceId)}</code></td>
        <td>${_escapeHtml(row.verdict)}</td>
        <td><code>${_escapeHtml(row.topJob)}</code></td>
        <td>${row.topScore}</td>
        <td>${row.selectedRank}</td>
        <td>${row.selectedScore}</td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

function _renderSegmentCompare(rows, run) {
  const el = document.getElementById("signals-segment-compare");
  if (!el) return;
  const traces = rows || _allTraceRows();
  const attribution = _attributionForRun(run);
  const rebalance = attribution?.rebalance || {};
  const segments = Array.isArray(rebalance.segments) ? rebalance.segments : [];
  if (traces.length === 0 || segments.length === 0) {
    el.innerHTML = '<p class="muted">暂无 segment 横向比较</p>';
    return;
  }
  const avgConf = _avg(traces.map((trace) => Number(trace.confidence || 0)));
  el.innerHTML = `<table>
    <thead><tr><th>segment</th><th>cum_return</th><th>turnover</th><th>avg_daily</th><th>trace_avg_conf</th><th>selected trace</th></tr></thead>
    <tbody>${segments.slice(0, 8).map((seg) => `
      <tr>
        <td>${_escapeHtml(String(seg.date || "-"))}</td>
        <td>${Number(seg.cum_return || 0).toFixed(4)}</td>
        <td>${Number(seg.turnover || 0).toFixed(4)}</td>
        <td>${Number(seg.avg_daily || 0).toFixed(4)}</td>
        <td>${avgConf == null ? "-" : (avgConf * 100).toFixed(1) + "%"}</td>
        <td><code>${_escapeHtml(String(_selectedTrace()?.trace_id || "").slice(0, 8) || "-")}</code></td>
      </tr>
    `).join("")}</tbody>
  </table>`;
}

async function _refreshSignalsRunCompareByFilters() {
  if (!_researchAuditCache) return;
  if (document.getElementById("signals-run-filter-attribution")?.checked) {
    await _hydrateSignalsBacktestCompare(_researchAuditCache.backtest_runs || []);
  }
  _renderResearchAudit(_researchAuditCache);
}

function _renderSignalsBacktestModules(run) {
  const el = document.getElementById("signals-backtest-modules");
  const attribution = _attributionForRun(run);
  if (!run || !attribution) {
    el.innerHTML = '<p class="muted">暂无可拆解的归因模块</p>';
    return;
  }
  const rebalance = attribution.rebalance || {};
  const scoreIc = attribution.score_ic || {};
  const holdings = attribution.holdings_diff || {};
  el.innerHTML = `
    <section class="audit-module-card">
      <h3>Rebalance</h3>
      <div class="meta">
        n_rebalances=${rebalance.n_rebalances ?? 0}<br>
        total_turnover=${Number(rebalance.total_turnover || 0).toFixed(4)}<br>
        avg_turnover=${Number(rebalance.avg_turnover || 0).toFixed(4)}<br>
        max_turnover=${Number(rebalance.max_turnover || 0).toFixed(4)}
      </div>
    </section>
    <section class="audit-module-card">
      <h3>Score IC</h3>
      <div class="meta">
        ic_mean=${Number(scoreIc.ic_mean || 0).toFixed(4)}<br>
        ic_std=${Number(scoreIc.ic_std || 0).toFixed(4)}<br>
        ic_ir=${Number(scoreIc.ic_ir || 0).toFixed(4)}<br>
        positive_rate=${fmtPct((scoreIc.ic_positive_rate || 0) * 100)}
      </div>
    </section>
    <section class="audit-module-card">
      <h3>Holdings Diff</h3>
      <div class="meta">
        initial=${(holdings.initial || []).length}<br>
        final=${(holdings.final || []).length}<br>
        turnover=${Number(holdings.turnover || 0).toFixed(4)}<br>
        selected_run=${_escapeHtml(String(run.job_id || "").slice(0, 8))}
      </div>
    </section>
  `;
}

function _renderBacktestModuleCharts(attribution) {
  if (!attribution) {
    _signalsModuleRebalanceChart = _renderDistributionChart(
      _signalsModuleRebalanceChart,
      "signals-module-rebalance-chart",
      [],
      [],
      "rgba(255,123,114,0.75)",
      "当前选中 run 无 Rebalance 图",
    );
    _signalsModuleScoreIcChart = _renderDistributionChart(
      _signalsModuleScoreIcChart,
      "signals-module-scoreic-chart",
      [],
      [],
      "rgba(63,185,80,0.75)",
      "当前选中 run 无 Score IC 图",
    );
    _signalsModuleHoldingsChart = _renderDistributionChart(
      _signalsModuleHoldingsChart,
      "signals-module-holdings-chart",
      [],
      [],
      "rgba(47,129,247,0.75)",
      "当前选中 run 无 Holdings 图",
    );
    return;
  }
  const rebalance = attribution?.rebalance || {};
  const scoreIc = attribution?.score_ic || {};
  const holdings = attribution?.holdings_diff || {};
  _signalsModuleRebalanceChart = _renderDistributionChart(
    _signalsModuleRebalanceChart,
    "signals-module-rebalance-chart",
    ["n_rebalances", "total_turnover", "avg_turnover", "max_turnover"],
    [
      Number(rebalance.n_rebalances || 0),
      Number(rebalance.total_turnover || 0),
      Number(rebalance.avg_turnover || 0),
      Number(rebalance.max_turnover || 0),
    ],
    "rgba(255,123,114,0.75)",
    "当前选中 run 无 Rebalance 图",
  );
  _signalsModuleScoreIcChart = _renderDistributionChart(
    _signalsModuleScoreIcChart,
    "signals-module-scoreic-chart",
    ["ic_mean", "ic_std", "ic_ir", "positive_rate"],
    [
      Number(scoreIc.ic_mean || 0),
      Number(scoreIc.ic_std || 0),
      Number(scoreIc.ic_ir || 0),
      Number(scoreIc.ic_positive_rate || 0),
    ],
    "rgba(63,185,80,0.75)",
    "当前选中 run 无 Score IC 图",
  );
  _signalsModuleHoldingsChart = _renderDistributionChart(
    _signalsModuleHoldingsChart,
    "signals-module-holdings-chart",
    ["initial", "final", "turnover"],
    [
      Number((holdings.initial || []).length),
      Number((holdings.final || []).length),
      Number(holdings.turnover || 0),
    ],
    "rgba(47,129,247,0.75)",
    "当前选中 run 无 Holdings 图",
  );
}

async function _hydrateSignalsBacktestCompare(runs) {
  const rows = (runs || []).filter((run) => run && run.job_id);
  const missing = rows.filter((run) => !_attributionForRun(run));
  if (missing.length === 0) {
    _renderSignalsRunsCompareChart(rows);
    _renderSignalsRunsPeerSummary(_filteredBacktestRuns(rows));
    _renderSignalsBacktestModules(_selectedBacktestRun());
    return;
  }
  await Promise.all(missing.map(async (run) => {
    try {
      const data = await fetchJSON(`/v1/backtest/runs/${encodeURIComponent(run.job_id)}/attribution`);
      _signalsBacktestAttrCache[run.job_id] = data.attribution || null;
    } catch (_e) {
      _signalsBacktestAttrCache[run.job_id] = null;
    }
  }));
  _renderSignalsRunsCompareChart(rows);
  _renderSignalsRunsPeerSummary(_filteredBacktestRuns(rows));
  _renderSignalsBacktestModules(_selectedBacktestRun());
}

function _renderSignalsBacktestAttribution(run, attribution, errorMessage = "") {
  const el = document.getElementById("signals-backtest-attribution");
  const rawEl = document.getElementById("signals-backtest-attribution-raw");
  if (!run) {
    el.innerHTML = '<p class="muted">暂无回测归因钻取对象</p>';
    document.getElementById("signals-attribution-module-details").innerHTML = '<p class="muted">暂无 attribution 模块逐项展开</p>';
    document.getElementById("signals-segment-review").innerHTML = '<p class="muted">暂无调仓段复盘数据</p>';
    document.getElementById("signals-segment-compare").innerHTML = '<p class="muted">暂无 segment 横向比较</p>';
    rawEl.innerHTML = '<span class="meta">暂无原始归因结构</span>';
    _renderSignalsAttributionChart(null);
    _renderSignalsBacktestModules(null);
    _renderBacktestModuleCharts(null);
    _renderAttributionModuleDetails(null);
    _renderSegmentReview(null);
    _renderSegmentCompare(_allTraceRows(), null);
    return;
  }
  if (errorMessage) {
    el.innerHTML = `<div class="alert-bar warn show">⚠ ${_escapeHtml(errorMessage)}</div>`;
    document.getElementById("signals-attribution-module-details").innerHTML = '<p class="muted">归因模块逐项展开不可用</p>';
    document.getElementById("signals-segment-review").innerHTML = '<p class="muted">调仓段复盘不可用</p>';
    document.getElementById("signals-segment-compare").innerHTML = '<p class="muted">segment 横向比较不可用</p>';
    rawEl.innerHTML = '<span class="meta">归因原始结构不可用</span>';
    _renderSignalsAttributionChart(null);
    _renderSignalsBacktestModules(null);
    _renderBacktestModuleCharts(null);
    _renderAttributionModuleDetails(null);
    _renderSegmentReview(null);
    _renderSegmentCompare(_allTraceRows(), null);
    return;
  }
  if (!attribution) {
    el.innerHTML = '<p class="muted">该回测 run 暂无 attribution 数据</p>';
    document.getElementById("signals-attribution-module-details").innerHTML = '<p class="muted">该 run 无 attribution 模块展开</p>';
    document.getElementById("signals-segment-review").innerHTML = '<p class="muted">该 run 无调仓段复盘</p>';
    document.getElementById("signals-segment-compare").innerHTML = '<p class="muted">该 run 无 segment 横向比较</p>';
    rawEl.innerHTML = '<span class="meta">该 run 未落库 attribution_json</span>';
    _renderSignalsAttributionChart(null);
    _renderSignalsBacktestModules(null);
    _renderBacktestModuleCharts(null);
    _renderAttributionModuleDetails(null);
    _renderSegmentReview(null);
    _renderSegmentCompare(_allTraceRows(), null);
    return;
  }
  const rebalance = attribution.rebalance || {};
  const scoreIc = attribution.score_ic || {};
  const holdings = attribution.holdings_diff || {};
  const cards = [
    { label: "调仓次数", value: rebalance.n_rebalances ?? 0 },
    { label: "累计换手", value: `${Number(rebalance.total_turnover || 0).toFixed(2)}x` },
    { label: "平均换手", value: Number(rebalance.avg_turnover || 0).toFixed(4) },
    { label: "IC 均值", value: Number(scoreIc.ic_mean || 0).toFixed(4) },
    { label: "IC IR", value: Number(scoreIc.ic_ir || 0).toFixed(4) },
    { label: "持仓净换手", value: Number(holdings.turnover || 0).toFixed(4) },
  ];
  el.innerHTML = `
    <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap; margin-bottom:12px;">
      <div>
        <div class="name" style="font-size:18px;">回测归因钻取 · ${_escapeHtml(run.config_json?.strategy_name || "-")}</div>
        <div class="meta" style="margin-top:6px;">job_id=${_escapeHtml(run.job_id || "-")} · status=${_escapeHtml(run.status || "-")} · universe=${_escapeHtml(run.config_json?.universe || "-")}</div>
      </div>
      <div class="meta">created_at=${_escapeHtml((run.created_at || "").replace("T", " ").slice(0, 19) || "-")}</div>
    </div>
    <div class="grid" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 16px;">
      ${cards.map((card) => `
        <div class="kv">
          <div class="kv-label">${card.label}</div>
          <div class="kv-value">${card.value}</div>
        </div>
      `).join("")}
    </div>
    <table>
      <thead><tr><th>模块</th><th>核心字段</th><th>值</th></tr></thead>
      <tbody>
        <tr><td>rebalance</td><td>max_turnover</td><td>${Number(rebalance.max_turnover || 0).toFixed(4)}</td></tr>
        <tr><td>score_ic</td><td>positive_rate</td><td>${fmtPct((scoreIc.ic_positive_rate || 0) * 100)}</td></tr>
        <tr><td>score_ic</td><td>n_periods</td><td>${scoreIc.n_periods ?? 0}</td></tr>
        <tr><td>holdings</td><td>initial/final</td><td>${(holdings.initial || []).length} / ${(holdings.final || []).length}</td></tr>
      </tbody>
    </table>
  `;
  _renderSignalsAttributionChart(attribution);
  _renderSignalsBacktestModules(run);
  _renderBacktestModuleCharts(attribution);
  _renderAttributionModuleDetails(attribution);
  _renderSegmentReview(attribution);
  _renderSegmentCompare(_allTraceRows(), run);
  rawEl.innerHTML = `
    <details>
      <summary style="cursor:pointer; color:var(--text-muted);">查看原始 attribution_json</summary>
      <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify(attribution, null, 2))}</pre>
    </details>
  `;
}

async function _loadSignalsBacktestAttribution(jobId) {
  if (!jobId) {
    _renderSignalsBacktestAttribution(null, null);
    return;
  }
  const run = _selectedBacktestRun();
  if (!run || run.job_id !== jobId) {
    _renderSignalsBacktestAttribution(null, null);
    return;
  }
  if (_signalsBacktestAttrCache[jobId]) {
    _renderSignalsBacktestAttribution(run, _signalsBacktestAttrCache[jobId]);
    return;
  }
  if (run.attribution_json) {
    _signalsBacktestAttrCache[jobId] = run.attribution_json;
    _renderSignalsBacktestAttribution(run, run.attribution_json);
    return;
  }
  try {
    const data = await fetchJSON(`/v1/backtest/runs/${encodeURIComponent(jobId)}/attribution`);
    _signalsBacktestAttrCache[jobId] = data.attribution || null;
    _renderSignalsBacktestAttribution(run, data.attribution || null);
  } catch (e) {
    _renderSignalsBacktestAttribution(run, null, `job_id=${jobId} 归因拉取失败: ${e.message}`);
  }
}

function _renderAuditBars(traceData, auditData) {
  const el = document.getElementById("signals-audit-bars");
  const traceSummary = traceData?.summary || {};
  const consistency = auditData?.consistency || {};
  const items = [
    { label: "LLM 解析成功率", rate: traceSummary.llm_parse_success_rate || 0 },
    { label: "输入快照完整率", rate: traceSummary.input_snapshot_complete_rate || 0 },
    { label: "规则基线覆盖率", rate: traceSummary.rule_baseline_coverage_rate || 0 },
    { label: "Rationale 覆盖率", rate: traceSummary.rationale_coverage_rate || 0 },
    {
      label: "链路一致性",
      rate: [
        consistency.verdict_match,
        consistency.confidence_match,
        consistency.risk_budget_match,
        consistency.market_regime_match,
        consistency.fallback_flag_match,
        consistency.approved_count_match,
        consistency.blocked_count_match,
      ].filter(Boolean).length / 7,
    },
  ];
  el.innerHTML = items.map((item) => {
    const pct = Math.max(0, Math.min(100, Math.round((item.rate || 0) * 1000) / 10));
    const warnCls = pct < 80 ? "warn" : "";
    return `
      <div class="audit-bar-row">
        <div class="audit-bar-label">${item.label}</div>
        <div class="audit-bar-track">
          <div class="audit-bar-fill ${warnCls}" style="width:${pct}%;"></div>
        </div>
        <div class="audit-bar-value">${pct.toFixed(1)}%</div>
      </div>
    `;
  }).join("");
}

function _renderAuditMatrix(auditData) {
  const el = document.getElementById("signals-audit-matrix");
  const marketSignal = auditData?.market_signal || {};
  const trace = _selectedTrace() || auditData?.decision_trace || {};
  const signalPayload = marketSignal.output_payload || {};
  const confidenceDelta = Math.abs(Number(trace.confidence || 0) - Number(marketSignal.confidence || 0));
  const verdictMatch = (trace.verdict || "") === (signalPayload.verdict || marketSignal.direction || "");
  const riskBudgetMatch = (trace.final_decision || {}).risk_budget === signalPayload.risk_budget;
  const regimeMatch = (trace.market_regime || "") === (signalPayload.market_regime || "");
  const fallbackMatch = !!trace.fallback_used === !!signalPayload.fallback_used;
  const cells = [
    ["Trace 存在", !!trace.trace_id, trace.trace_id ? `trace=${String(trace.trace_id).slice(0, 8)}` : "缺少 trace"],
    ["Signal 存在", !!marketSignal.strategy_id, marketSignal.strategy_id ? `signal=${String(marketSignal.strategy_id).slice(0, 8)}` : "缺少 signal"],
    ["Verdict", verdictMatch, `${trace.verdict || "-"} / ${signalPayload.verdict || "-"}`],
    ["Confidence", confidenceDelta <= 1e-6, `delta=${confidenceDelta.toFixed(6)}`],
    ["Risk Budget", riskBudgetMatch, `${(trace.final_decision || {}).risk_budget || trace.risk_budget || "-"} / ${signalPayload.risk_budget || "-"}`],
    ["Regime", regimeMatch, `${trace.market_regime || "-"} / ${signalPayload.market_regime || "-"}`],
    ["Fallback", fallbackMatch, `${trace.fallback_used ? "true" : "false"} / ${signalPayload.fallback_used ? "true" : "false"}`],
    ["Backtest", !!_selectedBacktestRun(), `selected=${_selectedBacktestRun()?.job_id ? String(_selectedBacktestRun().job_id).slice(0, 8) : "-"} · fit=${_selectedBacktestRun() ? _traceAwareScore(_selectedBacktestRun(), trace).toFixed(2) : "-"}`],
  ];
  el.innerHTML = cells.map(([title, ok, meta]) => `
    <div class="audit-matrix-cell ${ok ? "ok" : "fail"}">
      <div class="audit-matrix-title">${title}</div>
      <div class="audit-matrix-status">${ok ? "OK" : "FAIL"}</div>
      <div class="audit-matrix-meta">${_escapeHtml(meta)}</div>
    </div>
  `).join("");
}

function _renderAuditTimeline(traceData, auditData) {
  const el = document.getElementById("signals-audit-timeline");
  const trace = _selectedTrace() || auditData?.decision_trace || (traceData?.traces || [])[0] || null;
  const marketSignal = auditData?.market_signal || (_signalsCache?.groups?.market || [])[0] || null;
  const selectedRun = _selectedBacktestRun();
  const rows = [];

  if (trace) {
    rows.push({
      time: _fmtTs(trace.created_at),
      title: "Decision Trace 生成",
      meta: `layer=${trace.decision_layer || "-"} · model=${trace.model_version || "-"} · fallback=${trace.fallback_used ? "true" : "false"}`,
      active: true,
    });
  }
  if (marketSignal) {
    rows.push({
      time: _fmtTs(marketSignal.created_at),
      title: "Market Signal 落库",
      meta: `verdict=${(marketSignal.output_payload || {}).verdict || marketSignal.direction || "-"} · confidence=${(Number(marketSignal.confidence || 0) * 100).toFixed(1)}%`,
      active: true,
    });
  }
  if (selectedRun) {
    rows.push({
      time: _fmtTs(selectedRun.started_at || selectedRun.created_at),
      title: "Backtest Run 启动",
      meta: `strategy=${selectedRun.config_json?.strategy_name || "-"} · universe=${selectedRun.config_json?.universe || "-"} · status=${selectedRun.status || "-"}`,
      active: true,
    });
    rows.push({
      time: _fmtTs(selectedRun.finished_at),
      title: "Backtest Run 完成",
      meta: `duration=${(() => {
        const minutes = _diffMinutes(selectedRun.started_at || selectedRun.created_at, selectedRun.finished_at);
        return minutes == null ? "-" : `${minutes.toFixed(2)} min`;
      })()} · alpha=${selectedRun.benchmark_alpha == null ? "-" : Number(selectedRun.benchmark_alpha).toFixed(4)} · beta=${selectedRun.benchmark_beta == null ? "-" : Number(selectedRun.benchmark_beta).toFixed(4)}`,
      active: selectedRun.status === "completed",
    });
  }

  if (rows.length === 0) {
    el.innerHTML = '<p class="muted">暂无可视化链路时间线</p>';
    return;
  }
  el.innerHTML = rows.map((row) => `
    <div class="audit-timeline-row ${row.active ? "active" : ""}">
      <div class="audit-timeline-time">${row.time}</div>
      <div class="audit-timeline-dot"></div>
      <div class="audit-timeline-card">
        <div class="audit-timeline-title">${_escapeHtml(row.title)}</div>
        <div class="audit-timeline-meta">${_escapeHtml(row.meta)}</div>
      </div>
    </div>
  `).join("");
}

function _renderResearchAudit(data) {
  _researchAuditCache = data;
  _syncWorkbenchControls();
  const consistency = data?.consistency || {};
  const summaryEl = document.getElementById("signals-consistency-summary");
  const issuesEl = document.getElementById("signals-consistency-issues");
  const backtestsEl = document.getElementById("signals-linked-backtests");
  const cards = [
    { label: "Verdict", value: consistency.verdict_match ? "OK" : "FAIL" },
    { label: "Risk Budget", value: consistency.risk_budget_match ? "OK" : "FAIL" },
    { label: "Regime", value: consistency.market_regime_match ? "OK" : "FAIL" },
    { label: "Fallback", value: consistency.fallback_flag_match ? "OK" : "FAIL" },
    { label: "Approved Count", value: consistency.approved_count_match ? "OK" : "FAIL" },
    { label: "Blocked Count", value: consistency.blocked_count_match ? "OK" : "FAIL" },
    { label: "Backtest Runs", value: consistency.backtest_run_count ?? 0 },
    { label: "Completed Runs", value: consistency.completed_backtest_count ?? 0 },
  ];
  summaryEl.innerHTML = cards.map((c) => `
    <div class="kv">
      <div class="kv-label">${c.label}</div>
      <div class="kv-value" style="color:${c.value === "FAIL" ? "var(--up)" : "var(--text)"}">${c.value}</div>
    </div>
  `).join("");

  const issues = consistency.issues || [];
  const badges = [
    _auditStatusBadge("Trace", !!consistency.trace_present),
    _auditStatusBadge("Market Signal", !!consistency.market_signal_present),
    _auditStatusBadge("Confidence", !!consistency.confidence_match),
  ].join(" ");
  if (issues.length === 0) {
    issuesEl.innerHTML = `
      <div class="alert-bar ok show">✅ 审计一致性通过，decision trace / market signal / backtest run 口径一致。</div>
      <div class="meta" style="margin-top: 12px; display:flex; gap:6px; flex-wrap:wrap;">${badges}</div>
      <div class="meta" style="margin-top: 12px;">confidence delta=${Number(consistency.confidence_delta || 0).toFixed(6)}</div>
    `;
  } else {
    issuesEl.innerHTML = `
      <div class="alert-bar warn show">⚠ 检测到 ${issues.length} 条需要复核的审计问题</div>
      <div class="meta" style="margin-top: 12px; display:flex; gap:6px; flex-wrap:wrap;">${badges}</div>
      <ul style="margin-top: 12px; padding-left: 18px; color: var(--text);">
        ${issues.map((issue) => `<li>${_escapeHtml(issue)}</li>`).join("")}
      </ul>
    `;
  }

  const allRuns = data?.backtest_runs || [];
  const runs = _filteredBacktestRuns(allRuns);
  _renderSignalsRunsCompareSummary(allRuns, runs);
  if (runs.length === 0) {
    _selectedBacktestJobId = null;
    backtestsEl.innerHTML = '<p class="muted">当前过滤条件下无匹配 run</p>';
  } else {
    if (!runs.some((run) => run.job_id === _selectedBacktestJobId)) {
      _selectedBacktestJobId = (runs.find((run) => run.status === "completed") || runs[0]).job_id || null;
    }
    const selectedTrace = _selectedTrace();
    backtestsEl.innerHTML = `<table>
      <thead><tr><th>动作</th><th>job_id</th><th>策略</th><th>股票池</th><th>状态</th><th>Trace Fit</th><th>Alpha</th><th>Beta</th><th>归因</th><th>创建时间</th></tr></thead>
      <tbody>${runs.map((run) => `
        <tr>
          <td><button class="audit-run-btn ${run.job_id === _selectedBacktestJobId ? "active" : ""}" data-job-id="${_escapeHtml(run.job_id || "")}">查看</button></td>
          <td><code>${_escapeHtml((run.job_id || "").slice(0, 8) || "-")}</code></td>
          <td>${_escapeHtml(run.config_json?.strategy_name || "-")}</td>
          <td>${_escapeHtml(run.config_json?.universe || "-")}</td>
          <td>${_escapeHtml(run.status || "-")}</td>
          <td>${selectedTrace ? _traceAwareScore(run, selectedTrace).toFixed(2) : "-"}</td>
          <td>${run.benchmark_alpha == null ? "-" : Number(run.benchmark_alpha).toFixed(4)}</td>
          <td>${run.benchmark_beta == null ? "-" : Number(run.benchmark_beta).toFixed(4)}</td>
          <td>${run.attribution_json ? "yes" : "no"}</td>
          <td><span class="muted">${_escapeHtml((run.created_at || "").replace("T", " ").slice(0, 19))}</span></td>
        </tr>
      `).join("")}</tbody>
    </table>`;
    backtestsEl.querySelectorAll(".audit-run-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        _selectedBacktestJobId = btn.dataset.jobId || null;
        _renderResearchAudit(_researchAuditCache);
      });
    });
  }

  _renderAuditBars(_decisionTraceCache, data);
  _renderAuditMatrix(data);
  _renderAuditTimeline(_decisionTraceCache, data);
  _renderFallbackSummary(_decisionTraceCache?.traces || []);
  _renderTraceDiffMatrix(_decisionTraceCache?.traces || []);
  _renderTraceGroupCompare(_decisionTraceCache?.traces || []);
  _renderSignalsDrilldown();
  _renderSignalsRunsCompareChart(runs);
  _loadSignalsBacktestAttribution(_selectedBacktestJobId);
  _hydrateSignalsBacktestCompare(runs);
  _renderWorkbenchSummary();
  _applySectionVisibility();
}

function _renderDecisionTraceDetail(trace) {
  const el = document.getElementById("signals-trace-detail");
  if (!trace) {
    el.innerHTML = '<span class="meta">暂无决策轨迹详情</span>';
    return;
  }
  const scientificChecks = trace.scientific_checks || {
    input_snapshot_complete: !!((trace.inputs_snapshot || {}).market_outlook) && !!((trace.inputs_snapshot || {}).sector_outlooks || []).length && !!((trace.inputs_snapshot || {}).stock_outlooks || []).length,
    rule_baseline_present: !!Object.keys(trace.rule_baseline || {}).length,
    llm_parse_success: !!Object.keys(trace.llm_parsed || {}).length && !(trace.llm_error || ""),
    final_decision_present: !!Object.keys(trace.final_decision || {}).length,
    rationale_present: !!String(trace.rationale || "").trim(),
  };
  const checkEntries = [
    ["输入快照完整", scientificChecks.input_snapshot_complete],
    ["规则基线存在", scientificChecks.rule_baseline_present],
    ["LLM 解析成功", scientificChecks.llm_parse_success],
    ["最终决策存在", scientificChecks.final_decision_present],
    ["Rationale 存在", scientificChecks.rationale_present],
  ];
  const checkBadges = checkEntries.map(([label, ok]) => `
    <span class="badge" style="background:${ok ? 'rgba(46,160,67,0.16)' : 'rgba(255,77,79,0.16)'}; color:${ok ? '#3fb950' : '#ff7b72'}; border:1px solid ${ok ? 'rgba(63,185,80,0.35)' : 'rgba(255,123,114,0.35)'};">
      ${label}: ${ok ? "OK" : "缺失"}
    </span>
  `).join("");
  el.innerHTML = `
    <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap;">
      <div>
        <div class="name" style="font-size:18px;">${_escapeHtml(trace.verdict || "-")} · ${(Number(trace.confidence || 0) * 100).toFixed(1)}%</div>
        <div class="meta" style="margin-top:6px;">trace_id=${_escapeHtml(trace.trace_id || "-")} · layer=${_escapeHtml(trace.decision_layer || "-")} · model=${_escapeHtml(trace.model_version || "-")}</div>
        <div class="meta" style="margin-top:4px;">business_date=${_escapeHtml(trace.business_date || "-")} · created_at=${_escapeHtml(trace.created_at || "-")} · fallback=${trace.fallback_used ? "true" : "false"}</div>
      </div>
      <div style="display:flex; gap:6px; flex-wrap:wrap;">${checkBadges}</div>
    </div>
    <div class="meta" style="margin-top:12px; padding:12px; background: rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:6px; white-space:pre-wrap; font-family: var(--font-sans); line-height:1.6;">${_escapeHtml(trace.rationale || "(无 rationale)")}</div>
    <details style="margin-top:12px;">
      <summary style="cursor:pointer; color:var(--text-muted);">查看最终决策</summary>
      <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify(trace.final_decision || {}, null, 2))}</pre>
    </details>
    <details style="margin-top:12px;">
      <summary style="cursor:pointer; color:var(--text-muted);">查看输入快照</summary>
      <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify(trace.inputs_snapshot || {}, null, 2))}</pre>
    </details>
    <details style="margin-top:12px;">
      <summary style="cursor:pointer; color:var(--text-muted);">查看 Rule Baseline / LLM Parsed</summary>
      <pre style="margin-top:8px;">${_escapeHtml(JSON.stringify({
        rule_baseline: trace.rule_baseline || {},
        llm_parsed: trace.llm_parsed || {},
        llm_error: trace.llm_error || "",
      }, null, 2))}</pre>
    </details>
  `;
}

function _renderDecisionTraceList(rows) {
  const el = document.getElementById("signals-trace-list");
  if (!rows || rows.length === 0) {
    el.innerHTML = '<p class="muted">暂无决策轨迹</p>';
    _selectedTraceId = null;
    _renderDecisionTraceDetail(null);
    _renderTraceGroupCompare([]);
    _renderEvidenceMap(null);
    _renderRunRecommendation(null, _selectedBacktestRun());
    _renderStockReview(null, _selectedBacktestRun());
    _renderTraceCompare([]);
    _renderStockDiff([]);
    _renderRunRankDiff([]);
    _renderTraceAttributionBridge(null, _selectedBacktestRun());
    _renderSignalsDrilldown();
    return;
  }
  const body = rows.map((row, idx) => `
    <tr>
      <td><button class="trace-detail-btn" data-trace-id="${_escapeHtml(row.trace_id)}" style="background: transparent; color: var(--accent); border: 1px solid var(--border); padding: 4px 8px;">查看</button></td>
      <td>${idx + 1}</td>
      <td>${_escapeHtml(row.verdict || "-")}</td>
      <td>${(Number(row.confidence || 0) * 100).toFixed(1)}%</td>
      <td>${_escapeHtml(row.model_version || "-")}</td>
      <td>${row.fallback_used ? '<span class="badge warn">fallback</span>' : '<span class="badge ok">llm</span>'}</td>
      <td>${row.llm_error ? '<span class="badge error">error</span>' : '<span class="badge ok">ok</span>'}</td>
      <td>${_escapeHtml(row.market_regime || "-")}</td>
      <td><span class="muted">${_escapeHtml((row.created_at || "").slice(11, 19))}</span></td>
    </tr>
  `).join("");
  el.innerHTML = `<table>
    <thead><tr><th>操作</th><th>#</th><th>Verdict</th><th>置信度</th><th>模型</th><th>Fallback</th><th>LLM</th><th>Regime</th><th>时间</th></tr></thead>
    <tbody>${body}</tbody>
  </table>`;
  el.querySelectorAll(".trace-detail-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const traceId = btn.dataset.traceId;
      const cached = (_decisionTraceCache?.traces || []).find((row) => row.trace_id === traceId);
      if (cached) {
        _selectedTraceId = traceId;
        _renderDecisionTraceDetail(cached);
        _renderTraceGroupCompare(_decisionTraceCache?.traces || []);
        if (_researchAuditCache) _renderResearchAudit(_researchAuditCache);
        _renderSignalsDrilldown();
        return;
      }
      try {
        const data = await fetchJSON(`/v1/research/decision-traces/${traceId}`);
        _selectedTraceId = traceId;
        _renderDecisionTraceDetail(data.trace || null);
        _renderTraceGroupCompare(_decisionTraceCache?.traces || []);
        if (_researchAuditCache) _renderResearchAudit(_researchAuditCache);
        _renderSignalsDrilldown();
      } catch (e) {
        document.getElementById("signals-trace-detail").innerHTML = `<span class="meta" style="color:var(--up)">加载 trace 失败: ${_escapeHtml(e.message)}</span>`;
      }
    });
  });
  if (!rows.some((row) => row.trace_id === _selectedTraceId)) {
    _selectedTraceId = rows[0].trace_id;
  }
  _renderDecisionTraceDetail(_selectedTrace() || rows[0]);
  _renderTraceGroupCompare(rows);
  _renderSignalsDrilldown();
}

async function loadDecisionTraces(businessDate = "") {
  const query = businessDate ? `?business_date=${encodeURIComponent(businessDate)}&limit=12` : "?limit=12";
  try {
    const data = await fetchJSON(`/v1/research/decision-traces/latest${query}`);
    _decisionTraceLoadError = "";
    _decisionTraceCache = data;
    _renderSignalsAuditSummary(data);
    _renderDecisionTraceList(data.traces || []);
  } catch (e) {
    _decisionTraceLoadError = e.message;
    _decisionTraceCache = null;
    document.getElementById("signals-audit-summary").innerHTML = "";
    document.getElementById("signals-audit-meta").innerHTML = `<span class="meta" style="color:var(--up)">决策审计加载失败: ${_escapeHtml(e.message)}</span>`;
    document.getElementById("signals-trace-list").innerHTML = `<p class="muted" style="color:var(--up)">/v1/research/decision-traces/latest 失败: ${_escapeHtml(e.message)}</p>`;
    document.getElementById("signals-trace-detail").innerHTML = '<span class="meta">暂无决策轨迹详情</span>';
    _setSignalsPanelMessage("signals-audit-bars", "审计覆盖率不可用：决策轨迹加载失败。", "error");
    _setSignalsPanelMessage("signals-fallback-summary", "Fallback 对比不可用：缺少决策轨迹。", "error");
    _setSignalsPanelMessage("signals-trace-diff-matrix", "Trace 差异矩阵不可用：缺少决策轨迹。", "error");
    _setSignalsPanelMessage("signals-trace-group-summary", "Trace 分组统计不可用：缺少决策轨迹。", "error");
    _setSignalsPanelMessage("signals-trace-attribution-bridge", "trace 与 attribution 闭环对照不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-audit-timeline", "真实链路时间线不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-evidence-map", "个股证据映射不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-run-recommendation", "run 推荐解释不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-stock-review", "个股级 Trace-Run 对照不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-trace-compare", "多 Trace 横向对照不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-stock-diff", "个股差异视图不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-run-rank-diff", "Run 排序变化解释不可用：缺少 trace。", "error");
    _setSignalsPanelMessage("signals-backtest-attribution", "回测归因钻取等待 audit 数据。", "warn");
    _setSignalsPanelMessage("signals-segment-review", "调仓段复盘等待 audit 数据。", "warn");
    _setSignalsPanelMessage("signals-segment-compare", "Segment 横向比较等待 audit 数据。", "warn");
    _setSignalsPanelMessage("signals-attribution-module-details", "Attribution 模块明细等待 audit 数据。", "warn");
    _setSignalsPanelMessage("signals-backtest-attribution-raw", "归因原始结构等待 audit 数据。", "warn");
    _setSignalsPanelMessage("signals-backtest-modules", "归因模块拆解等待 audit 数据。", "warn");
    _selectedTraceId = null;
    _renderWorkbenchSummary();
    _applySectionVisibility();
    _resetSignalsDerivedCharts();
    _renderSignalsDrilldown();
  }
}

async function loadResearchAudit(businessDate = "") {
  const query = businessDate ? `?business_date=${encodeURIComponent(businessDate)}&backtest_limit=10` : "?backtest_limit=10";
  try {
    const data = await fetchJSON(`/v1/research/audit/latest${query}`);
    _researchAuditLoadError = "";
    _renderResearchAudit(data);
    if (document.getElementById("signals-run-filter-attribution")?.checked) {
      await _refreshSignalsRunCompareByFilters();
    }
  } catch (e) {
    _researchAuditLoadError = e.message;
    _researchAuditCache = null;
    document.getElementById("signals-consistency-summary").innerHTML = "";
    document.getElementById("signals-consistency-issues").innerHTML = `<span class="meta" style="color:var(--up)">一致性审计加载失败: ${_escapeHtml(e.message)}</span>`;
    _setSignalsPanelMessage("signals-linked-backtests", "关联 backtest run 加载失败。", "error");
    _setSignalsPanelMessage("signals-audit-matrix", "核验矩阵不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-evidence-map", "个股证据映射不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-run-recommendation", "run 推荐解释不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-stock-review", "个股级 Trace-Run 对照不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-trace-compare", "多 Trace 横向对照不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-stock-diff", "个股差异视图不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-run-rank-diff", "Run 排序变化解释不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-backtest-attribution", "回测归因钻取不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-segment-review", "调仓段复盘不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-segment-compare", "Segment 横向比较不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-attribution-module-details", "Attribution 模块明细不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-backtest-attribution-raw", "归因原始结构不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-backtest-modules", "归因模块拆解不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-runs-compare-summary", "多 Run 对比摘要不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-runs-peer-summary", "Run 对照摘要不可用：audit 接口失败。", "error");
    _setSignalsPanelMessage("signals-trace-attribution-bridge", "trace 与 attribution 闭环对照不可用：audit 接口失败。", "error");
    _selectedBacktestJobId = null;
    _renderWorkbenchSummary();
    _applySectionVisibility();
    _resetSignalsDerivedCharts();
  }
}

async function loadSignals() {
  try {
    const data = await fetchJSON("/v1/research/signals/latest");
    _signalsLoadError = "";
    _decisionTraceLoadError = "";
    _researchAuditLoadError = "";
    _signalsCache = data;
    const groups = data.groups || {};
    const summary = data.summary || {};
    document.getElementById("signals-date").textContent =
      `业务日期: ${data.business_date || "无"}`;
    _renderSignalsMeta(summary, data.business_date);
    _renderSignalsMarket(groups.market || []);
    _renderSignalsTable(document.getElementById("signals-sector"), groups.sector || [], "sector");
    _renderSignalsTable(document.getElementById("signals-approved"), groups.approved || [], "approved");
    _renderSignalsTable(document.getElementById("signals-blocked"), groups.blocked || [], "blocked");
    await Promise.all([
      loadDecisionTraces(data.business_date || ""),
      loadResearchAudit(data.business_date || ""),
    ]);
    if (_researchAuditCache) _renderResearchAudit(_researchAuditCache);
    _renderSignalsDrilldown();
    _renderSignalsMarket(groups.market || []);
    // P0-T12: 板块热力图（独立接口，不阻塞主信号）
    loadSectorHeatmap().catch((e) => console.warn("loadSectorHeatmap failed:", e));
  } catch (e) {
    const rootMessage = /getaddrinfo failed|Connect call failed|Connection refused/i.test(String(e.message || ""))
      ? `数据库连接不可达: ${e.message}`
      : e.message;
    const serviceHealth = await _fetchJSONSafe("/health?mode=summary");
    _signalsLoadError = e.message;
    _signalsCache = null;
    _decisionTraceCache = null;
    _researchAuditCache = null;
    document.getElementById("signals-meta").className = "alert-bar error show";
    document.getElementById("signals-meta").innerHTML =
      `/v1/research/signals/latest 失败: ${rootMessage}`;
    _selectedTraceId = null;
    _selectedBacktestJobId = null;
    _signalsBacktestAttrCache = {};
    let detailHtml = '<span class="meta" style="color:var(--up)">决策审计未执行：主信号接口不可用</span>';
    if (serviceHealth.ok) {
      const dbOk = !!serviceHealth.data.db_ok;
      const dbTarget = serviceHealth.data.db_target || "-";
      const dbDetail = serviceHealth.data.db_detail || rootMessage;
      const tone = dbOk ? "warn" : "error";
      detailHtml = `<div class="alert-bar ${tone} show">` +
        `DB Target: <strong>${_escapeHtml(dbTarget)}</strong>` +
        `${dbOk ? "" : ` · ${_escapeHtml(dbDetail)}`}` +
        `</div>`;
    }
    _renderSignalsUnavailableState(`主信号接口不可用: ${rootMessage}`, detailHtml);
    _renderWorkbenchSummary();
    _applySectionVisibility();
    _resetSignalsDerivedCharts();
  }
}

// ── P0-T12 板块热力图 ────────────────────────────

let _sectorHeatmapChart = null;
let _sectorHeatmapCache = null;

function _pctToColor(pct) {
  // -5% 红 → 0 灰 → +5% 绿，线性插值
  if (pct == null || Number.isNaN(pct)) return "rgba(100, 116, 139, 0.25)";
  const clamped = Math.max(-5, Math.min(5, pct));
  if (clamped >= 0) {
    const t = clamped / 5;
    // 0.0 → 灰，1.0 → 绿
    const r = Math.round(100 - 66 * t);
    const g = Math.round(116 + 50 * t);
    const b = Math.round(139 - 44 * t);
    return `rgba(${r}, ${g}, ${b}, ${0.4 + 0.45 * t})`;
  } else {
    const t = -clamped / 5;
    // 0.0 → 灰，1.0 → 红
    const r = Math.round(100 + 139 * t);
    const g = Math.round(116 - 70 * t);
    const b = Math.round(139 - 91 * t);
    return `rgba(${r}, ${g}, ${b}, ${0.4 + 0.45 * t})`;
  }
}

function _pctToText(pct) {
  if (pct == null || Number.isNaN(pct)) return "—";
  const s = pct >= 0 ? "+" : "";
  return `${s}${pct.toFixed(1)}%`;
}

function renderSectorHeatmap() {
  const canvas = document.getElementById("sector-heatmap-canvas");
  if (!canvas) return;
  if (_sectorHeatmapChart) {
    _sectorHeatmapChart.destroy();
    _sectorHeatmapChart = null;
  }
  if (!_sectorHeatmapCache || !_sectorHeatmapCache.matrix || _sectorHeatmapCache.matrix.length === 0) {
    canvas.parentElement.innerHTML =
      '<div class="sector-heatmap-empty">暂无板块热力图数据<br/><span style="font-size:11px;">接口 GET /api/dashboard/sector/heatmap</span></div>';
    return;
  }
  const matrix = _sectorHeatmapCache.matrix;
  const dates = _sectorHeatmapCache.dates || [];
  if (!window.Chart) {
    canvas.parentElement.innerHTML = '<div class="sector-heatmap-empty">Chart.js 未加载</div>';
    return;
  }
  const matrixType = window.Chart.registry.controllers.get("matrix") ? "matrix" : null;
  if (!matrixType) {
    canvas.parentElement.innerHTML =
      '<div class="sector-heatmap-empty">chartjs-chart-matrix 插件未加载</div>';
    return;
  }
  // chartjs-chart-matrix 数据格式：每个 cell = {x, y, v, w, h}
  const data = [];
  for (let r = 0; r < matrix.length; r++) {
    const row = matrix[r];
    for (let c = 0; c < (row.values || []).length; c++) {
      data.push({
        x: dates[c] || `D${c + 1}`,
        y: row.name || row.code,
        v: row.values[c],
      });
    }
  }
  _sectorHeatmapChart = new window.Chart(canvas, {
    type: "matrix",
    data: {
      datasets: [{
        label: "板块涨跌幅 (%)",
        data,
        backgroundColor: (ctx) => _pctToColor(ctx.raw?.v),
        borderColor: "rgba(15, 23, 42, 0.6)",
        borderWidth: 1,
        width: ({ chart }) => (chart.chartArea?.width / (dates.length || 1)) - 2,
        height: ({ chart }) => (chart.chartArea?.height / (matrix.length || 1)) - 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => `${items[0]?.raw?.y || ""} · ${items[0]?.raw?.x || ""}`,
            label: (ctx) => `涨跌幅: ${_pctToText(ctx.raw.v)}`,
          },
        },
      },
      scales: {
        x: {
          type: "category",
          labels: dates,
          grid: { color: "rgba(148, 163, 184, 0.06)" },
          ticks: { color: "#94a3b8", font: { size: 10 } },
        },
        y: {
          type: "category",
          labels: matrix.map((r) => r.name || r.code).reverse(),
          offset: true,
          grid: { color: "rgba(148, 163, 184, 0.06)" },
          ticks: { color: "#94a3b8", font: { size: 10 } },
        },
      },
    },
  });

  // 渲染图例
  const wrap = canvas.parentElement;
  if (wrap && !wrap.querySelector(".sector-heatmap-legend")) {
    const legend = document.createElement("div");
    legend.className = "sector-heatmap-legend";
    legend.innerHTML =
      `<span>-5%</span><div class="sector-heatmap-legend-bar"></div><span>+5%</span>
       <span style="margin-left:auto;">${matrix.length} 行业 × ${dates.length} 日 · ${_formatNextRunTime(_sectorHeatmapCache.generated_at)}</span>`;
    wrap.appendChild(legend);
  }
}

async function loadSectorHeatmap() {
  const daysEl = document.getElementById("signals-sector-heatmap-days");
  const nDays = daysEl ? daysEl.value : "5";
  const viewEl = document.getElementById("signals-sector-view");
  const view = viewEl ? viewEl.value : "table";
  const tableEl = document.getElementById("signals-sector");
  const heatmapEl = document.getElementById("signals-sector-heatmap");
  if (!tableEl || !heatmapEl) return;

  // 视图切换
  if (view === "heatmap") {
    tableEl.style.display = "none";
    heatmapEl.style.display = "block";
  } else {
    tableEl.style.display = "";
    heatmapEl.style.display = "none";
    return;
  }

  // 拉数据
  try {
    const data = await fetchJSON(`/api/dashboard/sector/heatmap?n_days=${nDays}`);
    _sectorHeatmapCache = data;
    renderSectorHeatmap();
  } catch (e) {
    heatmapEl.innerHTML = `<div class="sector-heatmap-empty" style="color:#f87171;">
      板块热力图拉取失败: ${e.message}
    </div>`;
  }
}

// ── Tab 切换 ────────────────────────────────────────────────

// ── P0-T7: 每日信号 cron 监控 ─────────────────────────────

let _schedulerTimer = null;
const SCHEDULER_POLL_MS = 30_000;

function _formatNextRunIn(sec) {
  if (sec == null) return "—";
  if (sec <= 0) return "即将执行";
  if (sec < 60) return `${Math.round(sec)} 秒后`;
  if (sec < 3600) return `${Math.round(sec / 60)} 分钟后`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)} 小时后`;
  return `${(sec / 86400).toFixed(1)} 天后`;
}

function _formatNextRunTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch (e) {
    return "—";
  }
}

function _renderSchedulerCard(job) {
  const next = job.next_run_in_sec;
  const isSoon = next != null && next < 600; // 10 分钟内即将执行
  const iconCls = job.coalesce ? "ok" : "idle";
  const iconChar = job.coalesce ? "✓" : "⏸";
  return `<div class="signal-scheduler-card" data-job-id="${job.id}">
    <div class="signal-scheduler-icon ${iconCls}">${iconChar}</div>
    <div class="signal-scheduler-body">
      <div class="signal-scheduler-name">${job.name || job.id}</div>
      <div class="signal-scheduler-trigger">${_escapeHtml(job.trigger || "")}</div>
      <div class="signal-scheduler-next ${isSoon ? "signal-scheduler-next-soon" : ""}">
        ⏱ 下次：${_formatNextRunTime(job.next_run_time)}（${_formatNextRunIn(next)}）
      </div>
    </div>
  </div>`;
}

function _renderSchedulerError(message) {
  return `<div class="signal-scheduler-card error" style="border-color:rgba(239,68,68,0.3);">
    <div class="signal-scheduler-icon error">⚠</div>
    <div class="signal-scheduler-body">
      <div class="signal-scheduler-name" style="color:#f87171;">${_escapeHtml(message)}</div>
      <div class="signal-scheduler-trigger">GET /admin/scheduler/status</div>
    </div>
  </div>`;
}

async function loadSchedulerStatus() {
  const cardsEl = document.getElementById("signal-scheduler-cards");
  const fetchedEl = document.getElementById("signal-scheduler-fetched");
  if (!cardsEl) return;
  try {
    const data = await fetchJSON("/admin/scheduler/status");
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    if (!data.running) {
      cardsEl.innerHTML = '<div class="signal-scheduler-card empty">scheduler 未注册（lifespan 未启动）</div>';
    } else if (jobs.length === 0) {
      cardsEl.innerHTML = '<div class="signal-scheduler-card empty">scheduler 运行中但无注册 job</div>';
    } else {
      cardsEl.innerHTML = jobs.map(_renderSchedulerCard).join("");
    }
    if (fetchedEl) {
      fetchedEl.textContent = `${data.job_count} jobs · ${data.timezone || ""} · ${_formatNextRunTime(data.fetched_at)}`;
    }
  } catch (e) {
    cardsEl.innerHTML = _renderSchedulerError(`scheduler 拉取失败: ${e.message}`);
    if (fetchedEl) fetchedEl.textContent = "—";
  }
}

function startSchedulerAutoRefresh() {
  if (_schedulerTimer) return;
  loadSchedulerStatus();
  _schedulerTimer = setInterval(loadSchedulerStatus, SCHEDULER_POLL_MS);
}

function stopSchedulerAutoRefresh() {
  if (_schedulerTimer) {
    clearInterval(_schedulerTimer);
    _schedulerTimer = null;
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((t) => {
    const isActive = t.dataset.tab === tabName;
    t.classList.toggle("active", isActive);
    if (isActive) {
      const titleEl = document.getElementById("current-page-title");
      if (titleEl) titleEl.innerText = t.innerText.trim();
    }
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.dataset.tab === tabName));
  // 切到对比/个股时重画 chart（tab 隐藏时 chart 宽度是 0，重画后才能正确显示）
  if (tabName === "compare") {
    if (!_compareChart) loadCompare();
  } else if (tabName === "stock") {
    if (_klineChart) _klineChart.resize();
  } else if (tabName === "signals") {
    if (!_signalsCache) loadSignals();
    loadSchedulerStatus();
  } else if (tabName === "health") {
    if (!_healthCache) loadHealthDetail();
    loadFailoverOverview();
  } else if (tabName === "overview") {
    // 强制重画全球表格（确保 tab 隐藏后第一次显示正常）
  } else if (tabName === "backtest") {
    if (!_btInited) initBacktest();
  } else if (tabName === "topology") {
    startTopologyAutoRefresh();
  } else if (tabName === "macro") {
    if (!_macroCache) loadMacro();
  }
  if (tabName !== "topology") stopTopologyAutoRefresh();
  if (tabName !== "signals") stopSchedulerAutoRefresh();
}

document.addEventListener("DOMContentLoaded", () => {
  loadOverview();
  loadStock("600519");
  document.getElementById("stock-load").addEventListener("click", () => {
    const metric = document.getElementById("kline-metric").value;
    const days = parseInt(document.getElementById("kline-days").value, 10);
    loadStock(document.getElementById("stock-code").value.trim(), { metric, days });
  });
  document.getElementById("stock-code").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const metric = document.getElementById("kline-metric").value;
      const days = parseInt(document.getElementById("kline-days").value, 10);
      loadStock(document.getElementById("stock-code").value.trim(), { metric, days });
    }
  });
  document.getElementById("kline-metric").addEventListener("change", () => {
    const metric = document.getElementById("kline-metric").value;
    const days = parseInt(document.getElementById("kline-days").value, 10);
    loadStock(document.getElementById("stock-code").value.trim(), { metric, days });
  });
  document.getElementById("kline-days").addEventListener("change", () => {
    const metric = document.getElementById("kline-metric").value;
    const days = parseInt(document.getElementById("kline-days").value, 10);
    loadStock(document.getElementById("stock-code").value.trim(), { metric, days });
  });
  // P0-T11 决策钻取刷新按钮
  const decRefresh = document.getElementById("stock-decision-refresh");
  if (decRefresh) {
    decRefresh.addEventListener("click", () => {
      const code = document.getElementById("stock-code").value.trim();
      loadStockDecisions(code);
    });
  }
  const decLookback = document.getElementById("stock-decision-lookback");
  if (decLookback) {
    decLookback.addEventListener("change", () => {
      const code = document.getElementById("stock-code").value.trim();
      loadStockDecisions(code);
    });
  }
  const decLimit = document.getElementById("stock-decision-limit");
  if (decLimit) {
    decLimit.addEventListener("change", () => {
      const code = document.getElementById("stock-code").value.trim();
      loadStockDecisions(code);
    });
  }
  // P0-T7 scheduler 监控刷新按钮
  const schedRefresh = document.getElementById("signal-scheduler-refresh");
  if (schedRefresh) {
    schedRefresh.addEventListener("click", () => {
      loadSchedulerStatus();
    });
  }
  document.getElementById("health-refresh").addEventListener("click", loadHealthDetail);
  // P0-T9 自动降级历史 刷新按钮
  const foRefresh = document.getElementById("health-failover-refresh");
  if (foRefresh) {
    foRefresh.addEventListener("click", () => {
      loadFailoverOverview(true);
    });
  }
  const foLimit = document.getElementById("health-failover-limit");
  if (foLimit) {
    foLimit.addEventListener("change", () => {
      loadFailoverOverview();
    });
  }
  document.getElementById("health-provider-filter").addEventListener("change", _renderFilteredTable);
  document.getElementById("health-method-filter").addEventListener("change", _renderFilteredTable);
  document.getElementById("health-alert-only").addEventListener("change", _renderFilteredTable);
  // 今日信号 tab
  document.getElementById("signals-refresh").addEventListener("click", () => {
    _signalsCache = null;
    _decisionTraceCache = null;
    _researchAuditCache = null;
    loadSignals();
  });
  // 细节 section 默认折叠（突出 5 段叙事：头图/市场/板块/选股/拦截）
  document.querySelectorAll(".signals-collapsible").forEach((s) => s.classList.add("collapsed"));
  document.getElementById("signals-run-filter-completed").addEventListener("change", () => {
    _refreshSignalsRunCompareByFilters();
  });
  document.getElementById("signals-run-filter-attribution").addEventListener("change", () => {
    _refreshSignalsRunCompareByFilters();
  });
  document.getElementById("signals-run-sort").addEventListener("change", () => {
    _refreshSignalsRunCompareByFilters();
  });
  document.getElementById("signals-trace-groupby").addEventListener("change", () => {
    _renderTraceGroupCompare(_decisionTraceCache?.traces || []);
  });
  document.getElementById("signals-global-trace-groupby").addEventListener("change", (e) => {
    document.getElementById("signals-trace-groupby").value = e.target.value;
    _renderTraceGroupCompare(_decisionTraceCache?.traces || []);
  });
  document.getElementById("signals-global-run-sort").addEventListener("change", async (e) => {
    document.getElementById("signals-run-sort").value = e.target.value;
    await _refreshSignalsRunCompareByFilters();
  });
  document.getElementById("signals-global-anomaly-only").addEventListener("change", () => {
    if (_researchAuditCache) _renderResearchAudit(_researchAuditCache);
    else _applySectionVisibility();
  });
  document.getElementById("signals-toggle-all").addEventListener("click", () => {
    _toggleAllSignalsSections();
  });
  // Tab 导航
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => switchTab(t.dataset.tab));
  });
  // 对比 tab
  document.getElementById("compare-load").addEventListener("click", loadCompare);
  document.getElementById("compare-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadCompare();
  });
  document.getElementById("compare-normalize").addEventListener("change", () => {
    if (_compareChart) loadCompare();
  });
  // 回测 tab
  initBacktest();
  document.getElementById("bt-run").addEventListener("click", runBacktest);
  document.getElementById("bt-4x30").addEventListener("click", runBacktest4x30);
  document.getElementById("bt-history").addEventListener("click", loadBtHistory);
  document.getElementById("bt-history-refresh").addEventListener("click", loadBtHistory);
  document.getElementById("bt-history-status").addEventListener("change", loadBtHistory);
  document.getElementById("bt-reset").addEventListener("click", () => {
    _btInitStrategies().then(loadBacktestDefault);
  });
});

// ── 回测 tab ─────────────────────────────────────────────────

let _btInited = false;
let _btStrategies = [];
let _btUniverses = [];
let _btNavChart = null;
let _bt4x30Chart = null;
let _btHistory = [];
let _btCurrentJobId = null;
let _btPollTimer = null;

async function _btInitStrategies() {
  try {
    const data = await fetchJSON("/v1/backtest/strategies");
    _btStrategies = data.strategies || [];
    const sel = document.getElementById("bt-strategy");
    const current = sel.value;
    sel.innerHTML = _btStrategies
      .map((s) => `<option value="${s.name}" title="${s.description}">${s.name} — ${s.description}</option>`)
      .join("");
    if (current) sel.value = current;
  } catch (e) {
    document.getElementById("bt-status").className = "alert-bar error show";
    document.getElementById("bt-status").innerHTML = `/v1/backtest/strategies 失败: ${e.message}`;
  }
}

async function _btInitUniverses() {
  try {
    const data = await fetchJSON("/v1/backtest/universes");
    _btUniverses = data.universes || [];
    const sel = document.getElementById("bt-universe");
    const current = sel.value || "hs300";
    sel.innerHTML = _btUniverses
      .map((u) => `<option value="${u.preset}" title="${u.name} (${u.exchange}: ${u.code})">${u.preset} — ${u.name}</option>`)
      .join("");
    if (current) sel.value = current;
  } catch (e) {
    // 静默失败
  }
}

async function loadBacktestDefault() {
  try {
    const cfg = await fetchJSON("/v1/backtest/default-config");
    document.getElementById("bt-strategy").value = cfg.strategy_name;
    document.getElementById("bt-freq").value = cfg.rebalance_freq;
    document.getElementById("bt-topn").value = cfg.top_n;
    document.getElementById("bt-start").value = cfg.start_date;
    document.getElementById("bt-end").value = cfg.end_date;
    document.getElementById("bt-capital").value = cfg.initial_capital;
    if (cfg.universe) document.getElementById("bt-universe").value = cfg.universe;
    if (cfg.benchmark) document.getElementById("bt-benchmark").value = cfg.benchmark;
  } catch (e) {
    // 静默失败
  }
}

async function initBacktest() {
  if (_btInited) return;
  _btInited = true;
  await _btInitStrategies();
  await _btInitUniverses();
  await loadBacktestDefault();
  document.getElementById("bt-metrics").innerHTML = '<p class="muted">点击「运行回测」开始</p>';
  document.getElementById("bt-cost").innerHTML = '<p class="muted">—</p>';
  document.getElementById("bt-benchmark-detail").innerHTML = '<p class="muted">—</p>';
  document.getElementById("bt-trades").innerHTML = '<p class="muted">尚无调仓记录</p>';
  document.getElementById("bt-skipped").innerHTML = '<p class="muted">—</p>';
  loadBtHistory();
}

function _renderBtMetrics(m) {
  const cards = [
    { label: "累计收益", value: fmtPct(m.total_return * 100), cls: m.total_return >= 0 ? "up" : "down" },
    { label: "年化收益", value: fmtPct(m.annualized_return * 100), cls: m.annualized_return >= 0 ? "up" : "down" },
    { label: "年化波动", value: fmtPct(m.annualized_volatility * 100) },
    { label: "夏普比率", value: fmtNum(m.sharpe_ratio, 2) },
    { label: "最大回撤", value: fmtPct(m.max_drawdown * 100), cls: "down" },
    { label: "Calmar", value: fmtNum(m.calmar_ratio, 2) },
    { label: "胜率", value: fmtPct(m.win_rate * 100) },
    { label: "盈亏比", value: fmtNum(m.profit_loss_ratio, 2) },
    { label: "期数", value: m.n_periods },
    { label: "盈/亏", value: `${m.n_wins} / ${m.n_losses}` },
    { label: "最佳单日", value: fmtPct(m.best_day * 100), cls: "up" },
    { label: "最差单日", value: fmtPct(m.worst_day * 100), cls: "down" },
  ];
  document.getElementById("bt-metrics").innerHTML = cards
    .map((i) => `<div class="metric"><div class="label">${i.label}</div><div class="value ${i.cls || ""}">${i.value}</div></div>`)
    .join("");
}

function _renderBtNavChart(data) {
  const ctx = document.getElementById("bt-nav-chart").getContext("2d");
  if (_btNavChart) _btNavChart.destroy();
  const nav = data.nav || [];
  const dates = data.trading_dates || [];
  // nav 长度 = dates 长度 + 1（首日 1.0）。补齐 X 轴首日 = dates[0]（同一日期）
  const labels = nav.length > 0 ? [dates[0] || "起点", ...dates] : [];
  const dataArr = nav;
  const rebalSet = new Set(data.rebalance_dates || []);
  const pointBg = labels.map((d, i) => (i === 0 || rebalSet.has(d)) ? "#d73a49" : "transparent");
  const pointBorder = labels.map((d, i) => (i === 0 || rebalSet.has(d)) ? "#d73a49" : "transparent");
  const finalVal = nav.length > 0 ? nav[nav.length - 1] : 1.0;
  _btNavChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `净值曲线 (终值 ${finalVal.toFixed(4)}x)`,
          data: dataArr,
          borderColor: "#0366d6",
          backgroundColor: "rgba(3,102,214,0.08)",
          fill: true,
          tension: 0.0,
          pointRadius: labels.map((d) => (rebalSet.has(d) ? 4 : 0)),
          pointBackgroundColor: pointBg,
          pointBorderColor: pointBorder,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true, position: "top" } },
      scales: {
        y: { title: { display: true, text: "净值倍数（相对起点 1.0）" } },
        x: {
          title: { display: true, text: "交易日" },
          ticks: { maxTicksLimit: 12, autoSkip: true },
        },
      },
    },
  });
}

function _renderBtTrades(trades) {
  const el = document.getElementById("bt-trades");
  if (!trades || trades.length === 0) {
    el.innerHTML = '<p class="muted">尚无调仓记录</p>';
    return;
  }
  const shown = trades.slice(0, 30);
  const rows = shown.map((t) => `<tr>
    <td>${t.date}</td>
    <td><span style="color:var(--down)">${(t.sold || []).length} 只</span> · ${(t.sold || []).slice(0, 3).join(", ")}${t.sold && t.sold.length > 3 ? "..." : ""}</td>
    <td><span style="color:var(--up)">${(t.bought || []).length} 只</span> · ${(t.bought || []).slice(0, 3).join(", ")}${t.bought && t.bought.length > 3 ? "..." : ""}</td>
    <td>${t.scores_n} / ${t.selected_n}</td>
  </tr>`).join("");
  el.innerHTML = `<table>
    <thead><tr><th>调仓日</th><th>卖出</th><th>买入</th><th>候选/入选</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>` +
  (trades.length > 30 ? `<p class="muted" style="font-size:12px">仅展示前 30 条，共 ${trades.length} 条</p>` : "");
}

function _renderBtSkipped(skipped) {
  const el = document.getElementById("bt-skipped");
  if (!skipped || skipped.length === 0) {
    el.innerHTML = '<p class="muted">无跳过 — 全部股票数据充足</p>';
    return;
  }
  el.innerHTML = `<table>
    <thead><tr><th>代码</th></tr></thead>
    <tbody>${skipped.map((c) => `<tr><td><code>${c}</code></td></tr>`).join("")}</tbody>
  </table>
  <p class="muted" style="font-size:12px">共 ${skipped.length} 只</p>`;
}

async function runBacktest() {
  const status = document.getElementById("bt-status");
  const btn = document.getElementById("bt-run");
  const originalText = btn.innerHTML;

  // 简单验证
  const startDate = document.getElementById("bt-start").value;
  const endDate = document.getElementById("bt-end").value;
  if (startDate && endDate && startDate >= endDate) {
    status.className = "alert-bar error show";
    status.innerHTML = "❌ 错误：起始日期必须早于结束日期";
    return;
  }

  const isAsync = document.getElementById("bt-async").checked;
  const benchmark = document.getElementById("bt-benchmark").value;
  const payload = {
    strategy_name: document.getElementById("bt-strategy").value,
    universe: document.getElementById("bt-universe").value,
    start_date: startDate || null,
    end_date: endDate || null,
    top_n: parseInt(document.getElementById("bt-topn").value, 10) || 20,
    rebalance_freq: document.getElementById("bt-freq").value,
    initial_capital: parseFloat(document.getElementById("bt-capital").value) || 1000000,
    benchmark: benchmark || null,
  };

  btn.disabled = true;
  btn.innerHTML = isAsync ? "⏳ 已提交…" : "⏳ 运行中...";
  btn.style.opacity = "0.7";
  btn.style.cursor = "not-allowed";

  if (isAsync) {
    status.className = "alert-bar warn show";
    status.innerHTML = "⏳ 已提交异步 job，可切到「📜 历史」查看进度…";
  } else {
    status.className = "alert-bar warn show";
    status.innerHTML = "⏳ 正在拉取股票日 K 线并执行回测引擎（预计耗时 10-60 秒）…";
  }

  try {
    const url = isAsync ? "/v1/backtest/run/async" : "/v1/backtest/run";
    const data = await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (isAsync) {
      _btCurrentJobId = data.job_id;
      status.innerHTML = `✅ 异步 job 已创建：<code>${data.job_id}</code>，每 3s 自动轮询…`;
      _btPollTimer = setInterval(() => _btPollJob(data.job_id, status, btn, originalText), 3000);
      loadBtHistory();
      return;
    }
    _renderBtResult(data, status);
  } catch (e) {
    status.className = "alert-bar error show";
    status.innerHTML = `❌ 回测失败: ${e.message}`;
    btn.disabled = false;
    btn.innerHTML = originalText;
    btn.style.opacity = "1";
    btn.style.cursor = "pointer";
  }
}

async function _btPollJob(jobId, status, btn, originalText) {
  try {
    const data = await fetchJSON(`/v1/backtest/runs/${jobId}`);
    if (data.status === "completed") {
      clearInterval(_btPollTimer);
      _btPollTimer = null;
      status.className = "alert-bar ok show";
      status.innerHTML = `✅ 异步 job 完成，job_id=<code>${jobId}</code>`;
      btn.disabled = false;
      btn.innerHTML = originalText;
      btn.style.opacity = "1";
      btn.style.cursor = "pointer";
      // 重新拉完整 run（取 result_json）
      const run = await fetchJSON(`/v1/backtest/runs/${jobId}`);
      if (run.result_json) _renderBtResult(run.result_json, status);
      loadBtHistory();
    } else if (data.status === "failed") {
      clearInterval(_btPollTimer);
      _btPollTimer = null;
      status.className = "alert-bar error show";
      status.innerHTML = `❌ 异步 job 失败：${data.error_message || "(no error msg)"}`;
      btn.disabled = false;
      btn.innerHTML = originalText;
      btn.style.opacity = "1";
      btn.style.cursor = "pointer";
      loadBtHistory();
    } else {
      status.innerHTML = `⏳ 异步 job <code>${jobId}</code> 状态：<strong>${data.status}</strong>，继续轮询…`;
    }
  } catch (e) {
    status.className = "alert-bar error show";
    status.innerHTML = `❌ 轮询失败: ${e.message}`;
  }
}

function _renderBtResult(data, status) {
  _renderBtMetrics(data.metrics || {});
  _renderBtNavChart(data);
  _renderBtCost(data.cost_summary || {});
  _renderBtBenchmark(data.benchmark || null);
  _renderBtTrades(data.trades || []);
  _renderBtSkipped(data.skipped_symbols || []);
  if (data.attribution) _renderBtAttribution(data.attribution);
  const m = data.metrics || {};
  status.className = "alert-bar ok show";
  status.innerHTML = `✅ 回测完成：${data.config.start_date} → ${data.config.end_date}，` +
    `共计调仓 ${(data.rebalance_dates || []).length} 次，最终累计收益 ${fmtPct((m.total_return || 0) * 100)}`;
}

// ── P2.4 成本明细 ─────────────────────────

function _renderBtCost(c) {
  const el = document.getElementById("bt-cost");
  if (!c || Object.keys(c).length === 0) {
    el.innerHTML = '<p class="muted">未计算成本（请确认 BacktestConfig 启用 cost 模型）</p>';
    return;
  }
  const cards = [
    { label: "总成本占净值", value: fmtPct((c.total_cost_pct || 0) * 100), cls: "down" },
    { label: "累计换手率", value: (c.total_turnover || 0).toFixed(2) + "x" },
    { label: "调仓次数", value: c.n_rebalances || 0 },
    { label: "佣金累计", value: fmtPct((c.commission_pct || 0) * 100) },
    { label: "印花税累计", value: fmtPct((c.stamp_tax_pct || 0) * 100) },
    { label: "滑点累计", value: fmtPct((c.slippage_pct || 0) * 100) },
  ];
  el.innerHTML = cards
    .map((i) => `<div class="metric"><div class="label">${i.label}</div><div class="value ${i.cls || ""}">${i.value}</div></div>`)
    .join("");
}

// ── P2.7 基准对比 ─────────────────────────

function _renderBtBenchmark(b) {
  const el = document.getElementById("bt-benchmark-detail");
  if (!b) {
    el.innerHTML = '<p class="muted">未指定基准 — 不对比</p>';
    return;
  }
  const alphaCls = (b.alpha || 0) >= 0 ? "up" : "down";
  const m = b.metrics || {};
  const cards = [
    { label: "基准代码", value: b.code || "—" },
    { label: "α (日度)", value: fmtNum(b.alpha, 5), cls: alphaCls },
    { label: "β (市场敏感)", value: fmtNum(b.beta, 3) },
    { label: "相关系数 R", value: fmtNum(b.correlation, 3) },
    { label: "基准年化", value: fmtPct(m.annual_return || 0) },
    { label: "基准波动", value: fmtPct(m.annual_volatility || 0) },
  ];
  el.innerHTML = cards
    .map((i) => `<div class="metric"><div class="label">${i.label}</div><div class="value ${i.cls || ""}">${i.value}</div></div>`)
    .join("");
}

// ── P4.5 归因面板 ─────────────────────────

function _renderBtAttribution(a) {
  const card = document.getElementById("bt-attribution-card");
  const el = document.getElementById("bt-attribution");
  if (!a) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  let html = "";
  if (a.rebalance) {
    const r = a.rebalance;
    html += `<h3>调仓归因</h3>
      <table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>
        <tr><td>总调仓次数</td><td>${r.n_rebalances || 0}</td></tr>
        <tr><td>累计换手率</td><td>${(r.total_turnover || 0).toFixed(2)}x</td></tr>
        <tr><td>换手率均值</td><td>${(r.avg_turnover || 0).toFixed(4)}</td></tr>
        <tr><td>最大换手</td><td>${(r.max_turnover || 0).toFixed(4)}</td></tr>
      </tbody></table>`;
  }
  if (a.score_ic) {
    const s = a.score_ic;
    html += `<h3 style="margin-top:16px">Score IC</h3>
      <table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>
        <tr><td>IC 均值</td><td>${(s.ic_mean || 0).toFixed(4)}</td></tr>
        <tr><td>IC 标准差</td><td>${(s.ic_std || 0).toFixed(4)}</td></tr>
        <tr><td>IC IR</td><td>${(s.ic_ir || 0).toFixed(4)}</td></tr>
        <tr><td>IC > 0 比例</td><td>${fmtPct((s.ic_positive_rate || 0) * 100)}</td></tr>
        <tr><td>样本数</td><td>${s.n_periods || 0}</td></tr>
      </tbody></table>`;
  }
  if (a.holdings_diff) {
    const h = a.holdings_diff;
    html += `<h3 style="margin-top:16px">持仓偏离</h3>
      <table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>
        <tr><td>期初持仓</td><td>${(h.initial || []).length} 只</td></tr>
        <tr><td>期末持仓</td><td>${(h.final || []).length} 只</td></tr>
        <tr><td>净换手</td><td>${(h.turnover || 0).toFixed(4)}</td></tr>
      </tbody></table>`;
  }
  el.innerHTML = html || "<p class='muted'>无归因数据</p>";
}

// ── 4×30 大回测对比（前端 demo：用 results_4x30.json 文件） ─────────

let _4x30Data = null;

async function runBacktest4x30() {
  const card = document.getElementById("bt-4x30-card");
  const meta = document.getElementById("bt-4x30-meta");
  const status = document.getElementById("bt-status");
  const btn = document.getElementById("bt-4x30");

  btn.disabled = true;
  meta.textContent = "⏳ 加载 results_4x30.json…";
  card.style.display = "block";
  try {
    const data = await fetchJSON("/static/results_4x30.json");
    _4x30Data = data;
    _renderBt4x30(data);
    status.className = "alert-bar ok show";
    status.innerHTML = `✅ 4×30 对比加载完成：${data.meta.n_completed}/${data.meta.strategies.length} 成功，${data.meta.n_symbols} 只 HS300 标的`;
  } catch (e) {
    meta.innerHTML = `<span style="color:var(--up)">加载失败：${e.message}<br/>需先跑 <code>python scripts/run_4x30_backtest.py</code> 生成 results_4x30.json</span>`;
  } finally {
    btn.disabled = false;
  }
}

function _renderBt4x30(data) {
  const meta = document.getElementById("bt-4x30-meta");
  const m = data.meta;
  meta.innerHTML = `生成于 <strong>${m.generated_at}</strong> · universe=<code>${m.universe}</code> · n_symbols=<strong>${m.n_symbols}</strong> · 耗时 <strong>${m.elapsed_sec}s</strong> · <strong>${m.n_completed}/${m.strategies.length}</strong> 策略成功`;
  // 表格
  const rows = data.results.map((r) => {
    const m = r.metrics || {};
    const bm = r.benchmark || {};
    const cls1 = (m.annual_return || 0) >= 0 ? "up" : "down";
    const cls2 = (m.max_drawdown || 0) >= 0 ? "down" : "up";
    const cls3 = (bm.alpha || 0) >= 0 ? "up" : "down";
    return `<tr>
      <td><strong>${r.strategy_name}</strong></td>
      <td><span class="${cls1}">${fmtPct(m.annual_return || 0)}</span></td>
      <td>${fmtNum(m.sharpe_ratio, 2)}</td>
      <td><span class="${cls2}">${fmtPct(m.max_drawdown || 0)}</span></td>
      <td>${fmtPct(m.win_rate || 0)}</td>
      <td>${fmtPct((r.cost_summary && r.cost_summary.total_cost_pct || 0) * 100)}</td>
      <td><span class="${cls3}">${fmtNum(bm.alpha, 4)}</span></td>
      <td>${fmtNum(bm.beta, 3)}</td>
      <td>${m.n_trades || 0}</td>
      <td>${r.status}</td>
    </tr>`;
  }).join("");
  document.getElementById("bt-4x30-table").innerHTML = `<table>
    <thead><tr><th>策略</th><th>年化</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>总成本</th><th>α</th><th>β</th><th>调仓</th><th>状态</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
  // 净值叠加图（每策略一条线）
  const ctx = document.getElementById("bt-4x30-chart").getContext("2d");
  if (_bt4x30Chart) _bt4x30Chart.destroy();
  const datasets = data.results.map((r, i) => ({
    label: r.strategy_name,
    data: (r.nav_curve || []).map((p) => p.nav),
    borderColor: _chartColor(i),
    backgroundColor: _chartColor(i, 0.05),
    spanGaps: true,
    tension: 0.1,
  }));
  const maxLen = Math.max(0, ...datasets.map((d) => d.data.length));
  const labels = Array.from({ length: maxLen }, (_, i) => i);
  _bt4x30Chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { display: true, position: "top" } },
      scales: { y: { title: { display: true, text: "净值倍数（相对起点 1.0）" } } },
    },
  });
}

// ── P2.5 历史 run 列表 ─────────────────────────

async function loadBtHistory() {
  const status = document.getElementById("bt-history-status").value;
  const strategy = document.getElementById("bt-history-strategy").value.trim();
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (strategy) params.set("strategy_name", strategy);
  params.set("limit", "50");
  try {
    const data = await fetchJSON(`/v1/backtest/runs?${params}`);
    _btHistory = data.runs || [];
    _renderBtHistory(_btHistory);
  } catch (e) {
    document.getElementById("bt-history-table").innerHTML =
      `<p style="color:var(--up)">/v1/backtest/runs 失败: ${e.message}</p>`;
  }
}

function _renderBtHistory(runs) {
  const el = document.getElementById("bt-history-table");
  if (!runs || runs.length === 0) {
    el.innerHTML = '<p class="muted">无历史 run</p>';
    return;
  }
  const rows = runs.map((r) => {
    const statusCls = {
      pending: "rate-mid", running: "rate-mid", completed: "rate-high",
      failed: "rate-low", cancelled: "rate-low",
    }[r.status] || "";
    const costPct = r.cost_total_pct != null ? fmtPct(r.cost_total_pct * 100) : "-";
    const alpha = r.benchmark_alpha != null ? fmtNum(r.benchmark_alpha, 4) : "-";
    const beta = r.benchmark_beta != null ? fmtNum(r.benchmark_beta, 3) : "-";
    return `<tr>
      <td><code style="font-size:11px">${(r.job_id || "").slice(0, 8)}…</code></td>
      <td>${(r.config_json && r.config_json.strategy_name) || "?"}</td>
      <td>${r.business_date}</td>
      <td><span class="badge ${statusCls}">${r.status}</span></td>
      <td>${costPct}</td>
      <td>${alpha}</td>
      <td>${beta}</td>
      <td>${(r.created_at || "").slice(0, 19).replace("T", " ")}</td>
      <td>
        <button class="bt-history-load" data-jid="${r.job_id}">查</button>
      </td>
    </tr>`;
  }).join("");
  el.innerHTML = `<table>
    <thead><tr><th>job_id</th><th>策略</th><th>业务日期</th><th>状态</th><th>总成本</th><th>α</th><th>β</th><th>创建时间</th><th>操作</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
  // 绑定行内按钮
  el.querySelectorAll(".bt-history-load").forEach((b) => {
    b.addEventListener("click", () => {
      const jid = b.dataset.jid;
      loadBtRunDetail(jid);
    });
  });
}

async function loadBtRunDetail(jobId) {
  const status = document.getElementById("bt-status");
  try {
    const data = await fetchJSON(`/v1/backtest/runs/${jobId}`);
    if (data.status === "completed" && data.result_json) {
      _renderBtResult(data.result_json, status);
      status.className = "alert-bar ok show";
      status.innerHTML = `✅ 已加载历史 run：<code>${jobId}</code>`;
    } else {
      status.className = "alert-bar warn show";
      status.innerHTML = `⚠ run <code>${jobId}</code> 状态：${data.status}（未完成或无 result）`;
    }
  } catch (e) {
    status.className = "alert-bar error show";
    status.innerHTML = `❌ 加载失败: ${e.message}`;
  }
}

// ════════════════════════════════════════════════════════════════
// P5.2 高级分析 Tab（walk-forward + 蒙特卡洛）
// ════════════════════════════════════════════════════════════════

function _parseReturns(text) {
  return text
    .split(/[,\s\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n));
}

function _renderWfSummary(summary, oosMetrics) {
  const grid = document.getElementById("wf-summary");
  const cards = [
    { label: "Folds", value: summary.n_folds },
    { label: "Mean OOS Sharpe", value: summary.mean_oos_sharpe.toFixed(4) },
    { label: "OOS Hit Rate", value: (summary.oos_hit_rate * 100).toFixed(1) + "%" },
    { label: "Consistency", value: (summary.consistency * 100).toFixed(1) + "%" },
    { label: "OOS 年化收益", value: (oosMetrics.annual_return * 100).toFixed(2) + "%" },
    { label: "OOS 最大回撤", value: (oosMetrics.max_drawdown * 100).toFixed(2) + "%" },
    { label: "OOS Sharpe", value: oosMetrics.sharpe.toFixed(4) },
    { label: "OOS 样本数", value: oosMetrics.n },
  ];
  grid.innerHTML = cards
    .map(
      (c) => `
      <div class="kv">
        <div class="kv-label">${c.label}</div>
        <div class="kv-value">${c.value}</div>
      </div>`,
    )
    .join("");
}

function _renderWfFolds(folds) {
  const rows = folds
    .map(
      (f) => `
      <tr>
        <td>${f.fold_id}</td>
        <td>${f.train_start}–${f.train_end}</td>
        <td>${f.test_start}–${f.test_end}</td>
        <td>${f.in_sample_metrics.sharpe.toFixed(3)}</td>
        <td>${(f.in_sample_metrics.annual_return * 100).toFixed(2)}%</td>
        <td>${(f.in_sample_metrics.max_drawdown * 100).toFixed(2)}%</td>
        <td>${f.out_sample_metrics.sharpe.toFixed(3)}</td>
        <td>${(f.out_sample_metrics.annual_return * 100).toFixed(2)}%</td>
        <td>${(f.out_sample_metrics.max_drawdown * 100).toFixed(2)}%</td>
      </tr>`,
    )
    .join("");
  document.getElementById("wf-folds-table").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Fold</th><th>Train 段</th><th>Test 段</th>
          <th>IS Sharpe</th><th>IS 年化</th><th>IS DD</th>
          <th>OOS Sharpe</th><th>OOS 年化</th><th>OOS DD</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function runWalkForward() {
  const status = document.getElementById("wf-status");
  const text = document.getElementById("wf-returns").value;
  const returns = _parseReturns(text);
  if (returns.length < 10) {
    status.className = "alert-bar error show";
    status.innerHTML = "❌ 日收益序列至少 10 个点";
    return;
  }
  const n_folds = Number(document.getElementById("wf-folds").value);
  const train_pct = Number(document.getElementById("wf-train-pct").value);
  const anchored = document.getElementById("wf-anchored").checked;
  status.className = "alert-bar show";
  status.innerHTML = `⏳ Walk-forward 跑 ${n_folds} 折（输入 ${returns.length} 个收益）...`;
  try {
    const r = await fetch("/v1/backtest/wf/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ returns, n_folds, train_pct, anchored }),
    });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t);
    }
    const data = await r.json();
    _renderWfSummary(data.summary, data.oos_metrics);
    _renderWfFolds(data.folds);
    status.className = "alert-bar ok show";
    status.innerHTML = `✅ Walk-forward 完成：${data.summary.n_folds} 折 / mean OOS Sharpe=${data.summary.mean_oos_sharpe}`;
  } catch (e) {
    status.className = "alert-bar error show";
    status.innerHTML = `❌ 跑失败: ${e.message}`;
  }
}

function randomizeReturns(targetId) {
  // 生成 80 个 ±0.5% 的伪收益
  const out = [];
  for (let i = 0; i < 80; i++) {
    out.push(((Math.random() - 0.5) * 0.01).toFixed(4));
  }
  document.getElementById(targetId).value = out.join(",");
}

let _mcChart = null;
function _renderMcDist(paths) {
  const finals = paths.map((p) => {
    let acc = 1;
    for (const r of p) acc *= 1 + r;
    return acc - 1;
  });
  // 直方图（50 bin）
  const min = Math.min(...finals);
  const max = Math.max(...finals);
  const bins = 50;
  const w = (max - min) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of finals) {
    const idx = Math.min(bins - 1, Math.floor((v - min) / w));
    counts[idx]++;
  }
  const labels = counts.map((_, i) => ((min + i * w) * 100).toFixed(1) + "%");
  const ctx = document.getElementById("mc-dist-chart");
  if (_mcChart) _mcChart.destroy();
  _mcChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "频次",
        data: counts,
        backgroundColor: "rgba(75, 192, 192, 0.6)",
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 10 } } },
    },
  });
}

function _renderMcSummary(summary) {
  const cards = [
    { label: "模拟次数", value: summary.n_sims },
    { label: "路径长度", value: summary.horizon },
    { label: "块大小", value: summary.block_size },
    { label: "盈利概率", value: (summary.prob_profit * 100).toFixed(1) + "%" },
    { label: "VaR 95%", value: (summary.var_95 * 100).toFixed(2) + "%" },
    { label: "CVaR 95%", value: (summary.cvar_95 * 100).toFixed(2) + "%" },
    { label: "年化收益 (mean)", value: (summary.final_return.mean * 100).toFixed(2) + "%" },
    { label: "Sharpe (mean)", value: summary.sharpe.mean.toFixed(3) },
    { label: "Max DD (mean)", value: (summary.max_drawdown.mean * 100).toFixed(2) + "%" },
  ];
  document.getElementById("mc-summary").innerHTML = cards
    .map(
      (c) => `
      <div class="kv">
        <div class="kv-label">${c.label}</div>
        <div class="kv-value">${c.value}</div>
      </div>`,
    )
    .join("");
}

async function runMonteCarlo() {
  const status = document.getElementById("mc-status");
  const text = document.getElementById("mc-returns").value;
  const returns = _parseReturns(text);
  if (returns.length < 10) {
    status.className = "alert-bar error show";
    status.innerHTML = "❌ 日收益序列至少 10 个点";
    return;
  }
  const n_sims = Number(document.getElementById("mc-sims").value);
  const horizon = Number(document.getElementById("mc-horizon").value);
  const block_size = Number(document.getElementById("mc-block").value);
  const seed = Number(document.getElementById("mc-seed").value);
  status.className = "alert-bar show";
  status.innerHTML = `⏳ MC 跑 ${n_sims} 次 × ${horizon} 日...`;
  try {
    const r = await fetch("/v1/backtest/mc/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ returns, n_sims, horizon, block_size, seed }),
    });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t);
    }
    const data = await r.json();
    _renderMcSummary(data.summary);
    _renderMcDist(data.summary.raw_returns_sample);
    status.className = "alert-bar ok show";
    status.innerHTML = `✅ MC 完成：${n_sims} 路径 / 盈利概率 ${(data.summary.prob_profit * 100).toFixed(1)}% / VaR95 ${(data.summary.var_95 * 100).toFixed(2)}%`;
  } catch (e) {
    status.className = "alert-bar error show";
    status.innerHTML = `❌ 跑失败: ${e.message}`;
  }
}

// ════════════════════════════════════════════════════════════════
// P3.5 告警 Tab
// ════════════════════════════════════════════════════════════════

async function alertListChannels() {
  const r = await fetch("/admin/alert/channels");
  const data = await r.json();
  const rows = data.channels
    .map(
      (c) => `
      <tr>
        <td>${c.name}</td>
        <td>${c.type}</td>
      </tr>`,
    )
    .join("");
  document.getElementById("alert-channels-table").innerHTML = `
    <table>
      <thead><tr><th>名称</th><th>类型</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function alertListRules() {
  const r = await fetch("/admin/alert/rules");
  const data = await r.json();
  const rows = data.rules
    .map(
      (r) => `
      <tr>
        <td>${r.name}</td>
        <td>${r.level}</td>
        <td>${r.comparator} ${r.threshold}</td>
        <td>${r.description}</td>
      </tr>`,
    )
    .join("");
  document.getElementById("alert-rules-table").innerHTML = `
    <table>
      <thead><tr><th>规则名</th><th>级别</th><th>阈值</th><th>说明</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function alertTest() {
  const r = await fetch("/admin/alert/test", { method: "POST" });
  const data = await r.json();
  const s = document.getElementById("alert-channels-table");
  s.className = "alert-bar ok show";
  s.innerHTML = `✅ 评估完成：触发 ${data.triggered} 条 events${
    data.events.length
      ? "（" + data.events.map((e) => `${e.rule}=${e.value.toFixed(4)}`).join(", ") + "）"
      : "（当前所有指标健康）"
  }`;
  await alertListChannels();
}

async function alertAddChannel() {
  const name = document.getElementById("alert-ch-name").value.trim();
  const url = document.getElementById("alert-ch-url").value.trim();
  if (!name || !url) {
    alert("请输入名称和 URL");
    return;
  }
  try {
    const r = await fetch("/admin/alert/channel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url }),
    });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t);
    }
    document.getElementById("alert-ch-name").value = "";
    document.getElementById("alert-ch-url").value = "";
    await alertListChannels();
  } catch (e) {
    alert("添加失败: " + e.message);
  }
}

async function alertDispatch() {
  const rule = document.getElementById("alert-d-rule").value.trim();
  const level = document.getElementById("alert-d-level").value;
  const message = document.getElementById("alert-d-msg").value.trim();
  if (!rule || !message) {
    alert("请输入规则名和消息");
    return;
  }
  const r = await fetch("/admin/alert/dispatch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule, level, message, value: 0, threshold: 0 }),
  });
  if (!r.ok) {
    alert("投递失败");
    return;
  }
  await alertListHistory();
}

async function alertListHistory() {
  const limit = Number(document.getElementById("alert-hist-limit").value);
  const r = await fetch(`/admin/alert/history?limit=${limit}`);
  const data = await r.json();
  const rows = data.history
    .map(
      (e) => `
      <tr>
        <td>${e.event_id.slice(0, 8)}</td>
        <td><b>${e.rule}</b></td>
        <td>${e.level}</td>
        <td>${e.message}</td>
        <td>${e.value.toFixed(4)}</td>
        <td>${e.threshold}</td>
        <td>${e.timestamp}</td>
      </tr>`,
    )
    .join("");
  document.getElementById("alert-history-table").innerHTML = `
    <table>
      <thead>
        <tr><th>ID</th><th>规则</th><th>级别</th><th>消息</th><th>值</th><th>阈值</th><th>时间</th></tr>
      </thead>
      <tbody>${rows || "<tr><td colspan='7' class='muted'>暂无告警历史</td></tr>"}</tbody>
    </table>`;
}

// ════════════════════════════════════════════════════════════════
// P3.4 监控 Tab
// ════════════════════════════════════════════════════════════════

async function monitorInfo() {
  const r = await fetch("/metrics/info");
  const data = await r.json();
  const cards = [
    { label: "Name", value: data.name },
    { label: "Version", value: data.version },
    { label: "Env", value: data.env },
    { label: "Uptime (s)", value: data.uptime_s },
  ];
  document.getElementById("mon-info-cards").innerHTML = cards
    .map(
      (c) => `
      <div class="kv">
        <div class="kv-label">${c.label}</div>
        <div class="kv-value">${c.value}</div>
      </div>`,
    )
    .join("");
}

async function monitorMetricsText() {
  const r = await fetch("/metrics");
  const text = await r.text();
  document.getElementById("mon-metrics-raw").textContent = text;
  document.querySelector("#monitor details").open = true;
}

function _parsePromText(text) {
  // 聚合同名 metric（label 不同行相加）
  const map = new Map();
  for (const line of text.split("\n")) {
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([a-zA-Z_][a-zA-Z0-9_]*)(?:\{[^}]*\})?\s+([-+0-9.eE]+)/);
    if (!m) continue;
    const name = m[1];
    const v = Number(m[2]);
    if (!Number.isFinite(v)) continue;
    map.set(name, (map.get(name) || 0) + v);
  }
  // 只保留 ashare_ 前缀
  const filtered = [...map.entries()].filter(([k]) => k.startsWith("ashare_"));
  filtered.sort((a, b) => a[0].localeCompare(b[0]));
  return filtered;
}

async function monitorMetricsSummary() {
  const r = await fetch("/metrics");
  const text = await r.text();
  const entries = _parsePromText(text);
  const rows = entries
    .map(
      ([name, v]) => `
      <tr>
        <td><code>${name}</code></td>
        <td>${v.toFixed(4)}</td>
      </tr>`,
    )
    .join("");
  document.getElementById("mon-metrics-summary").innerHTML = `
    <table>
      <thead><tr><th>指标</th><th>累计值</th></tr></thead>
      <tbody>${rows || "<tr><td colspan='2' class='muted'>暂无指标</td></tr>"}</tbody>
    </table>`;
}

async function monitorTraces() {
  const limit = Number(document.getElementById("mon-trace-limit").value);
  const r = await fetch(`/metrics/traces?limit=${limit}`);
  const data = await r.json();
  const rows = (data.spans || [])
    .map(
      (s) => `
      <tr>
        <td><code>${s.trace_id.slice(0, 8)}</code></td>
        <td>${s.name}</td>
        <td>${s.duration_ms.toFixed(1)}ms</td>
        <td>${s.status}</td>
        <td><code style="font-size: 10px;">${s.span_id.slice(0, 8)}</code></td>
      </tr>`,
    )
    .join("");
  document.getElementById("mon-trace-table").innerHTML = `
    <table>
      <thead>
        <tr><th>Trace</th><th>Name</th><th>耗时</th><th>状态</th><th>Span</th></tr>
      </thead>
      <tbody>${rows || "<tr><td colspan='5' class='muted'>暂无 span</td></tr>"}</tbody>
    </table>`;
}

// ════════════════════════════════════════════════════════════════
// P5.3 运维 Tab（多租户 + 数据保留）
// ════════════════════════════════════════════════════════════════

async function opsListTenants() {
  const r = await fetch("/admin/tenants");
  const data = await r.json();
  if (!data.tenants.length) {
    document.getElementById("ops-tenants-table").innerHTML =
      '<p class="muted">暂无租户（DB 不可用或无 tenant_id 数据）</p>';
    return;
  }
  const rows = data.tenants
    .map(
      (t) => `
      <tr>
        <td><b>${t.tenant_id}</b></td>
        <td>${t.total_rows.toLocaleString()}</td>
        <td><details><summary>展开</summary><pre style="font-size: 10px;">${JSON.stringify(t.tables, null, 2)}</pre></details></td>
      </tr>`,
    )
    .join("");
  document.getElementById("ops-tenants-table").innerHTML = `
    <table>
      <thead><tr><th>tenant_id</th><th>总行数</th><th>分表</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function opsRetentionStatus() {
  const r = await fetch("/admin/retention/status");
  const data = await r.json();
  const rows = Object.entries(data.tables)
    .map(
      ([tbl, info]) => `
      <tr>
        <td>${tbl}</td>
        <td>${info.retain_days} 天</td>
        <td>${info.min_rows.toLocaleString()}</td>
        <td>${info.current_rows.toLocaleString()}</td>
      </tr>`,
    )
    .join("");
  document.getElementById("ops-retention-table").innerHTML = `
    <table>
      <thead>
        <tr><th>表</th><th>保留天数</th><th>最少行数</th><th>当前行数</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="muted" style="font-size: 11px; margin-top: 8px;">检查时间：${data.checked_at}</p>`;
}

async function opsRetentionRun() {
  const dry = document.getElementById("ops-retention-dryrun").checked;
  if (!dry && !confirm("确定要真删超龄行吗？")) return;
  const r = await fetch(`/admin/retention/run?dry_run=${dry}`, { method: "POST" });
  const data = await r.json();
  const box = document.getElementById("ops-retention-result");
  box.className = "alert-bar ok show";
  const deletedLines = Object.entries(data.deleted)
    .map(([tbl, n]) => `<li>${tbl}: <b>${n.toLocaleString()}</b></li>`)
    .join("");
  box.innerHTML = `✅ 清理${data.dry_run ? "（dry-run）" : "完成"}：<ul style="margin: 4px 0;">${deletedLines}</ul>${
    data.errors.length ? "<b>失败表：</b>" + data.errors.join(", ") : ""
  }`;
  await opsRetentionStatus();
}

// ════════════════════════════════════════════════════════════════
// Topology 链路大屏
// ════════════════════════════════════════════════════════════════

const _TOPOLOGY_REFRESH_MS = 15000;
let _topologyRefreshTimer = null;
let _topologyLoading = false;
let _topologyState = {
  signals: null,
  traces: null,
  dashboardHealth: null,
  serviceHealth: null,
  retention: null,
  lastLoadedAt: null,
  selectedNode: null,
  lastErrors: {},
};

function _topologyJson(value) {
  return JSON.stringify(value, null, 2);
}

async function _fetchJSONSafe(url, opts = {}) {
  try {
    return { ok: true, data: await fetchJSON(url, opts) };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function _setTopologyMeta(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _formatRate(rate) {
  if (rate == null || Number.isNaN(Number(rate))) return "-";
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

function _formatCount(v) {
  if (v == null || Number.isNaN(Number(v))) return "-";
  return Number(v).toLocaleString();
}

function _formatIsoTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("zh-CN", { hour12: false });
}

function _isTopologyActive() {
  return !!document.querySelector('.tab-panel[data-tab="topology"].active');
}

function _topologyGroupRows(target) {
  const groups = _topologyState.signals?.groups || {};
  if (target === "market" || target === "committee") return groups.market || [];
  if (target === "sector") return groups.sector || [];
  if (target === "stock") return groups.approved || [];
  return [];
}

function _topologyLatestSpan() {
  const spans = _topologyState.traces?.spans || [];
  return spans.length > 0 ? spans[0] : null;
}

function _topologyWorstEndpoints() {
  const rows = _topologyState.dashboardHealth?.endpoints || [];
  return [...rows]
    .sort((a, b) => {
      const scoreA = (a.alerts?.length || 0) * 1000 + (a.failure || 0) + (a.fallback_count || 0);
      const scoreB = (b.alerts?.length || 0) * 1000 + (b.failure || 0) + (b.fallback_count || 0);
      return scoreB - scoreA;
    })
    .slice(0, 5);
}

function _topologyRowsSummary() {
  const tables = _topologyState.retention?.tables || {};
  return Object.values(tables).reduce((sum, row) => {
    const count = Number(row.current_rows);
    return sum + (count >= 0 ? count : 0);
  }, 0);
}

function _topologyRowHeadline(row) {
  const name = row.entity_name || row.symbol || row.entity_id || "unknown";
  return `${name} · ${row.direction || "-"} · conf=${_formatRate(row.confidence)}`;
}

function _topologySelection() {
  return _topologyState.selectedNode || "gateway";
}

function appendTopoLog(level, text) {
  const panel = document.getElementById("topo-log-panel");
  if (!panel) return;
  const time = new Date().toISOString().split("T")[1].slice(0, 12);
  const div = document.createElement("div");
  div.className = `log-line ${level}`;
  const stamp = document.createElement("span");
  stamp.className = "muted";
  stamp.textContent = `[${time}] `;
  div.appendChild(stamp);
  if (String(text).includes("\n")) {
    const pre = document.createElement("pre");
    pre.style.margin = "0";
    pre.style.font = "inherit";
    pre.style.whiteSpace = "pre-wrap";
    pre.style.wordBreak = "break-word";
    pre.textContent = String(text);
    div.appendChild(pre);
  } else {
    const span = document.createElement("span");
    span.textContent = String(text);
    div.appendChild(span);
  }
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
}

function _renderTopologyMeta() {
  const signals = _topologyState.signals;
  const dashboardHealth = _topologyState.dashboardHealth;
  const serviceHealth = _topologyState.serviceHealth;
  const retention = _topologyState.retention;
  const marketRow = (signals?.groups?.market || [])[0];
  const sectors = signals?.groups?.sector || [];
  const approved = signals?.groups?.approved || [];
  const blocked = signals?.groups?.blocked || [];
  const latestSpan = _topologyLatestSpan();
  const providers = serviceHealth?.router?.providers || dashboardHealth?.providers || [];
  const providerHealth = serviceHealth?.router?.provider_health || {};
  const healthyProviders = Object.values(providerHealth).filter((v) => Number(v) >= 0.8).length;
  const tableCount = Object.keys(retention?.tables || {}).length;
  const rowsTotal = _topologyRowsSummary();
  const activeAlerts = dashboardHealth
    ? (dashboardHealth.alerts.low_success_rate || 0) +
      (dashboardHealth.alerts.last_error || 0) +
      (dashboardHealth.alerts.high_latency || 0)
    : 0;

  _setTopologyMeta(
    "topo-meta-scheduler",
    `${signals?.business_date || "等待业务日"} · 17:30 Daily`,
  );
  _setTopologyMeta(
    "topo-meta-market",
    marketRow
      ? `${marketRow.direction || "-"} · ${_formatRate(marketRow.confidence)}`
      : "暂无信号",
  );
  _setTopologyMeta(
    "topo-meta-sector",
    sectors.length > 0
      ? `${sectors[0].entity_name || sectors[0].symbol || "top1"} / ${sectors.length} 组`
      : "暂无板块",
  );
  _setTopologyMeta("topo-meta-stock", `准入 ${approved.length} · 拦截 ${blocked.length}`);
  _setTopologyMeta(
    "topo-meta-committee",
    marketRow?.output_payload?.verdict
      ? `${marketRow.output_payload.verdict} · ${marketRow.output_payload.risk_budget || "normal"}`
      : "等待决策",
  );
  _setTopologyMeta(
    "topo-meta-sources",
    providers.length > 0
      ? `${healthyProviders}/${providers.length} healthy`
      : "provider 未就绪",
  );
  _setTopologyMeta(
    "topo-meta-gateway",
    dashboardHealth
      ? `${dashboardHealth.endpoints_total} ep · ${_formatRate(dashboardHealth.calls_success_rate)}`
      : "网关遥测缺失",
  );
  _setTopologyMeta(
    "topo-meta-ods",
    tableCount > 0
      ? `${tableCount} tables · ${_formatCount(rowsTotal)} rows`
      : "表状态未加载",
  );
  _setTopologyMeta(
    "topo-meta-retention",
    retention
      ? `${Object.keys(retention.policy || {}).length} policies · ${_formatIsoTime(retention.checked_at)}`
      : "策略状态未加载",
  );
  if (latestSpan && !signals?.business_date) {
    _setTopologyMeta("topo-meta-scheduler", `${latestSpan.name} · ${latestSpan.status}`);
  }
  if (activeAlerts > 0 && dashboardHealth) {
    _setTopologyMeta("topo-meta-gateway", `${dashboardHealth.endpoints_total} ep · alert=${activeAlerts}`);
  }
}

function _renderSchedulerNode() {
  const signals = _topologyState.signals;
  const latestSpan = _topologyLatestSpan();
  appendTopoLog("info", "> scheduler / daily_research_flow");
  appendTopoLog("muted", _topologyJson({
    business_date: signals?.business_date || null,
    refresh_interval_ms: _TOPOLOGY_REFRESH_MS,
    latest_span: latestSpan
      ? {
          name: latestSpan.name,
          status: latestSpan.status,
          trace_id: latestSpan.trace_id,
          end_ts: latestSpan.end_ts,
        }
      : null,
  }));
}

function _renderSourcesNode() {
  const serviceHealth = _topologyState.serviceHealth;
  if (!serviceHealth) {
    appendTopoLog("warn", "服务健康摘要不可用");
    return;
  }
  appendTopoLog("info", `DB: ${serviceHealth.db_ok ? "ok" : "degraded"} · cache_hit=${_formatRate(serviceHealth.router?.cache_hit_rate)}`);
  appendTopoLog("muted", _topologyJson({
    providers: serviceHealth.router?.providers || [],
    provider_health: serviceHealth.router?.provider_health || {},
    db_detail: serviceHealth.db_detail || "",
  }));
}

function _renderGatewayNode() {
  const health = _topologyState.dashboardHealth;
  if (!health) {
    appendTopoLog("warn", "DataRouter 健康遥测不可用");
    return;
  }
  appendTopoLog("info", `Active Endpoints: ${health.endpoints_total} · success=${_formatRate(health.calls_success_rate)}`);
  appendTopoLog("muted", _topologyJson({
    providers: health.providers,
    alerts: health.alerts,
    worst_endpoints: _topologyWorstEndpoints().map((row) => ({
      method: row.method,
      provider: row.provider,
      success_rate: row.success_rate,
      fallback_rate: row.fallback_rate,
      last_error: row.last_error,
      alerts: row.alerts,
    })),
  }));
}

function _renderAgentNode(target) {
  const rows = _topologyGroupRows(target);
  const labelMap = {
    market: "宏观智能体",
    sector: "板块智能体",
    stock: "个股智能体",
    committee: "委员会",
  };
  appendTopoLog("info", `${labelMap[target] || target} 输出 ${rows.length} 条`);
  if (rows.length === 0) {
    appendTopoLog("warn", "当前业务日无可视化结果");
    return;
  }

  if (target === "sector") {
    const topRows = rows.slice(0, 5).map((row) => ({
      entity_name: row.entity_name,
      direction: row.direction,
      confidence: row.confidence,
      sector_stage: row.output_payload?.sector_stage,
      fallback_used: row.output_payload?.fallback_used,
    }));
    appendTopoLog("muted", _topologyJson(topRows));
    return;
  }

  if (target === "stock") {
    const blocked = _topologyState.signals?.groups?.blocked || [];
    appendTopoLog("muted", _topologyJson({
      approved_top5: rows.slice(0, 5).map((row) => ({
        entity_name: row.entity_name,
        direction: row.direction,
        confidence: row.confidence,
        entry_timing: row.output_payload?.entry_timing,
        trend_signal: row.output_payload?.trend_signal,
        fallback_used: row.output_payload?.fallback_used,
      })),
      blocked_top5: blocked.slice(0, 5).map((row) => ({
        entity_name: row.entity_name,
        reason: row.output_payload?.reason || row.rationale,
        confidence: row.confidence,
      })),
    }));
    return;
  }

  const head = rows[0];
  appendTopoLog("info", _topologyRowHeadline(head));
  appendTopoLog("muted", _topologyJson({
    business_date: head.business_date,
    decision_layer: head.decision_layer,
    rationale: head.rationale,
    output_payload: head.output_payload,
    supporting_evidence: (head.supporting_evidence || []).slice(0, 5),
  }));
}

function _renderOdsNode() {
  const retention = _topologyState.retention;
  if (!retention) {
    appendTopoLog("warn", "Retention 状态不可用，无法读取 ODS 当前规模");
    return;
  }
  const entries = Object.entries(retention.tables || {})
    .sort((a, b) => Number(b[1].current_rows) - Number(a[1].current_rows));
  appendTopoLog("info", `PostgreSQL tables=${entries.length} · rows=${_formatCount(_topologyRowsSummary())}`);
  appendTopoLog("muted", _topologyJson(
    entries.slice(0, 8).map(([table, info]) => ({
      table,
      current_rows: info.current_rows,
      retain_days: info.retain_days,
      min_rows: info.min_rows,
    })),
  ));
}

function _renderRetentionNode() {
  const retention = _topologyState.retention;
  if (!retention) {
    appendTopoLog("warn", "Retention policy 未加载");
    return;
  }
  appendTopoLog("info", `Retention checked_at=${_formatIsoTime(retention.checked_at)}`);
  appendTopoLog("muted", _topologyJson({
    policy: retention.policy,
    tables: retention.tables,
  }));
}

function renderTopologyNode(target) {
  _topologyState.selectedNode = target;
  document.querySelectorAll(".topo-node").forEach((n) => {
    n.classList.toggle("active", n.dataset.target === target);
  });
  const panel = document.getElementById("topo-log-panel");
  if (panel) panel.innerHTML = "";
  appendTopoLog("info", `> connecting to node [${target}]...`);
  appendTopoLog("success", `> connected. telemetry_ts=${_formatIsoTime(_topologyState.lastLoadedAt)}`);

  if (target === "scheduler") {
    _renderSchedulerNode();
  } else if (target === "sources") {
    _renderSourcesNode();
  } else if (target === "gateway") {
    _renderGatewayNode();
  } else if (["market", "sector", "stock", "committee"].includes(target)) {
    _renderAgentNode(target);
  } else if (target === "ods") {
    _renderOdsNode();
  } else if (target === "retention") {
    _renderRetentionNode();
  } else {
    appendTopoLog("muted", "No telemetry data available for this node yet.");
  }

  const errors = _topologyState.lastErrors || {};
  if (Object.keys(errors).length > 0) {
    appendTopoLog("warn", "partial refresh detected");
    appendTopoLog("muted", _topologyJson(errors));
  }
}

async function loadTopologyData() {
  if (_topologyLoading) return;
  _topologyLoading = true;
  try {
    const [signals, traces, dashboardHealth, serviceHealth, retention] = await Promise.all([
      _fetchJSONSafe("/v1/research/signals/latest"),
      _fetchJSONSafe("/metrics/traces?limit=100"),
      _fetchJSONSafe("/api/dashboard/health"),
      _fetchJSONSafe("/health?mode=summary"),
      _fetchJSONSafe("/admin/retention/status"),
    ]);

    const errors = {};
    if (signals.ok) {
      _topologyState.signals = signals.data;
    } else {
      errors.signals = signals.error;
    }
    if (traces.ok) {
      _topologyState.traces = traces.data;
    } else {
      errors.traces = traces.error;
    }
    if (dashboardHealth.ok) {
      _topologyState.dashboardHealth = dashboardHealth.data;
    } else {
      errors.dashboard = dashboardHealth.error;
    }
    if (serviceHealth.ok) {
      _topologyState.serviceHealth = serviceHealth.data;
    } else {
      errors.service = serviceHealth.error;
    }
    if (retention.ok) {
      _topologyState.retention = retention.data;
    } else {
      errors.retention = retention.error;
    }

    _topologyState.lastLoadedAt = new Date().toISOString();
    _topologyState.lastErrors = errors;
    _renderTopologyMeta();
    renderTopologyNode(_topologySelection());
  } catch (e) {
    console.error("拓扑数据加载失败", e);
    const panel = document.getElementById("topo-log-panel");
    if (panel) panel.innerHTML = "";
    appendTopoLog("error", `topology refresh failed: ${e.message || e}`);
  } finally {
    _topologyLoading = false;
  }
}

function startTopologyAutoRefresh() {
  loadTopologyData();
  if (_topologyRefreshTimer) return;
  _topologyRefreshTimer = setInterval(() => {
    if (_isTopologyActive()) {
      loadTopologyData();
      return;
    }
    stopTopologyAutoRefresh();
  }, _TOPOLOGY_REFRESH_MS);
}

function stopTopologyAutoRefresh() {
  if (_topologyRefreshTimer) {
    clearInterval(_topologyRefreshTimer);
    _topologyRefreshTimer = null;
  }
}

function bindTopologyEvents() {
  document.querySelectorAll(".topo-node").forEach((node) => {
    node.addEventListener("click", async () => {
      const target = node.dataset.target;
      const staleMs = _topologyState.lastLoadedAt
        ? Date.now() - new Date(_topologyState.lastLoadedAt).getTime()
        : Number.POSITIVE_INFINITY;
      if (!_topologyState.lastLoadedAt || staleMs > _TOPOLOGY_REFRESH_MS) {
        _topologyState.selectedNode = target;
        await loadTopologyData();
        return;
      }
      renderTopologyNode(target);
    });
  });
}

// ════════════════════════════════════════════════════════════════
// 启动绑定
// ════════════════════════════════════════════════════════════════

function bindAllButtons() {
  const bind = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };
  // analysis
  bind("wf-run", runWalkForward);
  bind("wf-random", () => randomizeReturns("wf-returns"));
  bind("mc-run", runMonteCarlo);
  // alerts
  bind("alert-list-channels", alertListChannels);
  bind("alert-list-rules", alertListRules);
  bind("alert-test", alertTest);
  bind("alert-ch-add", alertAddChannel);
  bind("alert-d-go", alertDispatch);
  bind("alert-hist-refresh", alertListHistory);
  // monitor
  bind("mon-info", monitorInfo);
  bind("mon-metrics-text", monitorMetricsText);
  bind("mon-metrics-summary", monitorMetricsSummary);
  bind("mon-trace", monitorTraces);
  // ops
  bind("ops-tenant-refresh", opsListTenants);
  bind("ops-retention-status", opsRetentionStatus);
  bind("ops-retention-run", opsRetentionRun);
  
  bindTopologyEvents();
}

document.addEventListener("DOMContentLoaded", () => {
  bindAllButtons();
  // 默认进入各 tab 时按需拉取
  const tabBar = document.getElementById("tab-bar");
  if (tabBar) {
    tabBar.addEventListener("click", (ev) => {
      const t = ev.target.closest(".tab");
      if (!t) return;
      const key = t.dataset.tab;
      if (key === "alerts") {
        alertListChannels();
        alertListRules();
        alertListHistory();
      } else if (key === "monitor") {
        monitorInfo();
        monitorMetricsSummary();
        monitorTraces();
      } else if (key === "ops") {
        opsListTenants();
        opsRetentionStatus();
      } else if (key === "analysis") {
        // 用户主动点才跑，不自动
      }
    });
  }
});
