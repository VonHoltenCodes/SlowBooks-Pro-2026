/**
 * ============================================================================
 * Slowbooks Pro 2026 — Analytics SPA Page (Phase 9, inline integration)
 *
 * Lives inside the main SPA shell as a hash-routed page (#/analytics),
 * following the same conventions as reports.js / bills.js / banking.js:
 *
 *   AnalyticsPage.render()  →  returns an HTML string. The router
 *                              assigns it to #page-content.innerHTML.
 *
 * Because Chart.js needs canvas elements to exist in the DOM before
 * Chart(ctx, ...) is called, render() queues a microtask (via
 * setTimeout 0) that fires after the router's assignment, attaches
 * event listeners, and builds the charts.
 *
 * Chart types used (all from self-hosted /static/js/chart.umd.js — no CDN):
 *   * Revenue trend ........ line chart (12 calendar months)
 *   * Expenses ............. doughnut chart (by account)
 *   * A/R + A/P aging ...... stacked horizontal bar chart
 *   * Cash forecast ........ dual-line (collections vs payments) + net bar
 * ============================================================================
 */

const AnalyticsPage = {
  // Persistent state — survives route navigation so users return to their
  // last-selected period.
  state: {
    period: "month",
    data: null,
    charts: {},
    // Phase 9.5: AI insights are generated on demand (not on every
    // page load — LLM calls aren't free). We cache the last result in
    // state so switching routes and coming back keeps it visible.
    aiInsights: null, // { insights, provider_label, model, generated_at, cached }
    aiBusy: false,
    aiConfig: null, // last-known provider config (no raw key)
    // Phase 9.5b: Tool-calling Q&A assistant
    chatMessages: [], // array of {role: 'user'|'assistant', content, tool_calls?, timestamp}
    chatBusy: false,
  },

  // ------------------------------------------------------------------
  // Entry point — router calls this, we return an HTML string and then
  // finish wiring up the page in a post-render microtask.
  // ------------------------------------------------------------------
  async render() {
    let data;
    let errMsg = null;
    try {
      data = await API.get(
        `/analytics/dashboard?period=${encodeURIComponent(this.state.period)}`,
      );
      this.state.data = data;
    } catch (err) {
      errMsg = err && err.message ? err.message : String(err);
      data = null;
    }

    // Schedule post-innerHTML work: bind listeners + init charts.
    setTimeout(() => this._postRender(), 0);

    return this._buildHtml(data, errMsg);
  },

  // ------------------------------------------------------------------
  // HTML skeleton
  // ------------------------------------------------------------------
  _buildHtml(data, errMsg) {
    if (errMsg) {
      return `
                <div class="page-header"><h2>Analytics</h2></div>
                <div class="analytics-error">
                    <strong>Failed to load analytics.</strong>
                    <div>${escapeHtml(errMsg)}</div>
                </div>`;
    }

    const period = (data && data.period) || {};
    const periodLabel = period.name
      ? `${period.name.toUpperCase()} &middot; ${period.start} &rarr; ${period.end}`
      : "";

    const totalRevenue = this._sum(data.revenue_by_customer);
    const totalExpenses = this._sum(data.expenses_by_category);
    const dso = Number(data.dso) || 0;
    const margin =
      totalRevenue > 0
        ? ((totalRevenue - totalExpenses) / totalRevenue) * 100
        : 0;

    return `
            <div class="page-header analytics-header">
                <h2>Analytics</h2>
                <div class="analytics-controls">
                    <label for="analytics-period">Period</label>
                    <select id="analytics-period">
                        <option value="month"${this.state.period === "month" ? " selected" : ""}>Month to Date</option>
                        <option value="quarter"${this.state.period === "quarter" ? " selected" : ""}>Quarter to Date</option>
                        <option value="year"${this.state.period === "year" ? " selected" : ""}>Year to Date</option>
                    </select>
                    <button class="btn btn-secondary btn-sm" id="analytics-refresh" title="Refresh (R)">&#x21bb; Refresh</button>
                    <button class="btn btn-secondary btn-sm" id="analytics-csv" title="Export CSV">Export CSV</button>
                    <button class="btn btn-secondary btn-sm" id="analytics-pdf" title="Export PDF">Export PDF</button>
                    <button class="btn btn-secondary btn-sm" id="analytics-ai-settings" title="Configure AI provider">&#9881; AI</button>
                    <button class="btn btn-primary btn-sm"   id="analytics-ai-run" title="Generate AI insights">&#10024; AI Insights</button>
                </div>
            </div>

            <div class="analytics-meta">${periodLabel}</div>

            ${this._aiPanelHtml()}

            ${this._aiChatHtml()}

            <div class="analytics-kpi-grid">
                <div class="analytics-kpi"><div class="analytics-kpi-label">Revenue</div><div class="analytics-kpi-value kpi-green">${formatCurrency(totalRevenue)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">Expenses</div><div class="analytics-kpi-value kpi-red">${formatCurrency(totalExpenses)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">DSO (Days)</div><div class="analytics-kpi-value kpi-blue">${dso.toFixed(1)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">Margin %</div><div class="analytics-kpi-value kpi-purple">${margin.toFixed(1)}%</div></div>
            </div>

            <div class="analytics-section-title">Revenue Trend &mdash; Last 12 Months</div>
            <div class="analytics-card"><div class="chart-wrap"><canvas id="chart-revenue-trend"></canvas></div></div>

            <div class="analytics-two-col">
                <div class="analytics-card">
                    <div class="analytics-section-title">Revenue by Customer</div>
                    ${this._revenueTable(data.revenue_by_customer)}
                </div>
                <div class="analytics-card">
                    <div class="analytics-section-title">Expenses by Category</div>
                    <div class="chart-wrap chart-wrap-sm"><canvas id="chart-expenses"></canvas></div>
                    ${this._expensesTable(data.expenses_by_category)}
                </div>
            </div>

            <div class="analytics-section-title">Accounts Receivable Aging</div>
            <div class="analytics-card">
                <div class="chart-wrap chart-wrap-sm"><canvas id="chart-ar-aging"></canvas></div>
                ${this._agingTable(data.ar_aging, "Customer")}
            </div>

            <div class="analytics-section-title">Accounts Payable Aging</div>
            <div class="analytics-card">
                <div class="chart-wrap chart-wrap-sm"><canvas id="chart-ap-aging"></canvas></div>
                ${this._agingTable(data.ap_aging, "Vendor")}
            </div>

            <div class="analytics-section-title">90-Day Cash Forecast</div>
            <div class="analytics-card">
                <div class="chart-wrap"><canvas id="chart-cash-forecast"></canvas></div>
                ${this._cashForecastTable(data.cash_forecast)}
            </div>
        `;
  },

  // ------------------------------------------------------------------
  // Tables (rendered inside _buildHtml, so no DOM lookups needed)
  // ------------------------------------------------------------------
  _revenueTable(byCustomer) {
    const entries = Object.entries(byCustomer || {}).sort(
      (a, b) => b[1] - a[1],
    );
    if (entries.length === 0) {
      return '<div class="analytics-empty">No paid invoices this period.</div>';
    }
    const rows = entries
      .map(
        ([c, r]) => `
            <tr><td>${escapeHtml(c)}</td><td class="amount green">${formatCurrency(r)}</td></tr>
        `,
      )
      .join("");
    return `
            <div class="table-container">
                <table>
                    <thead><tr><th>Customer</th><th class="amount">Revenue</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
  },

  _expensesTable(byCategory) {
    const entries = Object.entries(byCategory || {}).sort(
      (a, b) => b[1] - a[1],
    );
    if (entries.length === 0) {
      return '<div class="analytics-empty">No paid bills this period.</div>';
    }
    const rows = entries
      .map(
        ([c, a]) => `
            <tr><td>${escapeHtml(c)}</td><td class="amount red">${formatCurrency(a)}</td></tr>
        `,
      )
      .join("");
    return `
            <div class="table-container">
                <table>
                    <thead><tr><th>Category</th><th class="amount">Expense</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
  },

  _agingTable(aging, nameHeader) {
    aging = aging || {};
    const names = new Set([
      ...Object.keys(aging.current || {}),
      ...Object.keys(aging["30"] || {}),
      ...Object.keys(aging["60"] || {}),
      ...Object.keys(aging["90"] || {}),
    ]);
    if (names.size === 0) {
      return '<div class="analytics-empty">Nothing outstanding.</div>';
    }
    const rows = Array.from(names)
      .map((name) => {
        const cur = (aging.current && aging.current[name]) || 0;
        const d30 = (aging["30"] && aging["30"][name]) || 0;
        const d60 = (aging["60"] && aging["60"][name]) || 0;
        const d90 = (aging["90"] && aging["90"][name]) || 0;
        return { name, cur, d30, d60, d90, total: cur + d30 + d60 + d90 };
      })
      .sort((a, b) => b.total - a.total);

    let tCur = 0,
      t30 = 0,
      t60 = 0,
      t90 = 0,
      tAll = 0;
    const bodyRows = rows
      .map((r) => {
        tCur += r.cur;
        t30 += r.d30;
        t60 += r.d60;
        t90 += r.d90;
        tAll += r.total;
        return `
                <tr>
                    <td>${escapeHtml(r.name)}</td>
                    <td class="amount">${formatCurrency(r.cur)}</td>
                    <td class="amount amber">${formatCurrency(r.d30)}</td>
                    <td class="amount amber">${formatCurrency(r.d60)}</td>
                    <td class="amount red">${formatCurrency(r.d90)}</td>
                    <td class="amount green"><strong>${formatCurrency(r.total)}</strong></td>
                </tr>
            `;
      })
      .join("");

    const totalsRow = `
            <tr class="totals-row">
                <td><strong>TOTAL</strong></td>
                <td class="amount"><strong>${formatCurrency(tCur)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t30)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t60)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t90)}</strong></td>
                <td class="amount green"><strong>${formatCurrency(tAll)}</strong></td>
            </tr>
        `;

    return `
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>${escapeHtml(nameHeader)}</th>
                            <th class="amount">Current</th>
                            <th class="amount">30+</th>
                            <th class="amount">60+</th>
                            <th class="amount">90+</th>
                            <th class="amount">Total</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}${totalsRow}</tbody>
                </table>
            </div>
        `;
  },

  _cashForecastTable(forecast) {
    const rows = forecast || [];
    if (rows.length === 0) {
      return '<div class="analytics-empty">No forecast data.</div>';
    }
    const bodyRows = rows
      .map((r) => {
        const net = Number(r.net) || 0;
        const netCls = net >= 0 ? "green" : "red";
        return `
                <tr>
                    <td>${escapeHtml(r.date)}</td>
                    <td class="amount green">${formatCurrency(r.collections)}</td>
                    <td class="amount red">${formatCurrency(r.payments)}</td>
                    <td class="amount ${netCls}"><strong>${formatCurrency(net)}</strong></td>
                </tr>
            `;
      })
      .join("");
    return `
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Due by</th>
                            <th class="amount">Expected Collections</th>
                            <th class="amount">Expected Payments</th>
                            <th class="amount">Net</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}</tbody>
                </table>
            </div>
        `;
  },

  // ------------------------------------------------------------------
  // Post-render: listeners + Chart.js init
  // ------------------------------------------------------------------
  _postRender() {
    // Attach control listeners. Guard every lookup so a missing id
    // during error rendering doesn't throw.
    const sel = $("#analytics-period");
    if (sel) {
      sel.addEventListener("change", () => {
        this.state.period = sel.value;
        this._reload();
      });
    }
    const refresh = $("#analytics-refresh");
    if (refresh) refresh.addEventListener("click", () => this._reload());
    const csv = $("#analytics-csv");
    if (csv) csv.addEventListener("click", () => this._download("csv"));
    const pdf = $("#analytics-pdf");
    if (pdf) pdf.addEventListener("click", () => this._download("pdf"));
    // Phase 9.5 AI buttons
    const aiRun = $("#analytics-ai-run");
    if (aiRun)
      aiRun.addEventListener("click", () => this._runAiInsights(false));
    const aiSettings = $("#analytics-ai-settings");
    if (aiSettings)
      aiSettings.addEventListener("click", () => this._openAiSettings());
    // Phase 9.5b chat listeners
    this._wireChatListeners();

    // Charts
    if (this.state.data && typeof Chart !== "undefined") {
      this._destroyCharts();
      this._renderRevenueChart();
      this._renderExpensesChart();
      this._renderAgingChart(
        "chart-ar-aging",
        this.state.data.ar_aging,
        "A/R",
        "#00c48f",
      );
      this._renderAgingChart(
        "chart-ap-aging",
        this.state.data.ap_aging,
        "A/P",
        "#ff6b6b",
      );
      this._renderCashForecastChart();
    }
  },

  async _reload() {
    App.setStatus("Loading Analytics...");
    const html = await this.render();
    $("#page-content").innerHTML = html;
    App.setStatus("Analytics — Ready");
  },

  _download(kind) {
    const url = `/api/analytics/export.${kind}?period=${encodeURIComponent(this.state.period)}`;
    // Use a transient anchor so the browser honors Content-Disposition.
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 100);
    toast(`${kind.toUpperCase()} download started`, "success");
  },

  _destroyCharts() {
    Object.values(this.state.charts).forEach((c) => {
      try {
        c.destroy();
      } catch (_) {}
    });
    this.state.charts = {};
  },

  // ------------------------------------------------------------------
  // Charts
  // ------------------------------------------------------------------
  _chartDefaults() {
    // Read theme from <html data-theme> to pick label/grid colors.
    const isDark =
      (document.documentElement.getAttribute("data-theme") || "light") ===
      "dark";
    return {
      text: isDark ? "#e6e6f0" : "#1a1a28",
      grid: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      muted: isDark ? "#a0a0b8" : "#5a5a70",
    };
  },

  _renderRevenueChart() {
    const ctx = $("#chart-revenue-trend");
    if (!ctx) return;
    const trend = this.state.data.revenue_trend || {};
    const labels = Object.keys(trend);
    const values = labels.map((k) => Number(trend[k]) || 0);
    const theme = this._chartDefaults();

    this.state.charts.revenue = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Monthly Paid Revenue",
            data: values,
            borderColor: "#00c48f",
            backgroundColor: "rgba(0,196,143,0.15)",
            fill: true,
            tension: 0.3,
            pointRadius: 4,
            pointHoverRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: theme.text } },
          tooltip: {
            callbacks: {
              label: (c) => formatCurrency(c.parsed.y),
            },
          },
        },
        scales: {
          x: { ticks: { color: theme.muted }, grid: { color: theme.grid } },
          y: {
            ticks: {
              color: theme.muted,
              callback: (v) => formatCurrency(v),
            },
            grid: { color: theme.grid },
            beginAtZero: true,
          },
        },
      },
    });
  },

  _renderExpensesChart() {
    const ctx = $("#chart-expenses");
    if (!ctx) return;
    const byCat = this.state.data.expenses_by_category || {};
    const entries = Object.entries(byCat).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) return;
    const theme = this._chartDefaults();

    const palette = [
      "#ff6b6b",
      "#5b7fff",
      "#ffa94d",
      "#a855f7",
      "#00c48f",
      "#e879f9",
      "#38bdf8",
      "#facc15",
    ];

    this.state.charts.expenses = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: entries.map((e) => e[0]),
        datasets: [
          {
            data: entries.map((e) => e[1]),
            backgroundColor: entries.map((_, i) => palette[i % palette.length]),
            borderColor: "transparent",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "right", labels: { color: theme.text } },
          tooltip: {
            callbacks: {
              label: (c) => `${c.label}: ${formatCurrency(c.parsed)}`,
            },
          },
        },
      },
    });
  },

  _renderAgingChart(canvasId, aging, label, hueHint) {
    const ctx = $("#" + canvasId);
    if (!ctx) return;
    aging = aging || {};
    const buckets = ["current", "30", "60", "90"];
    const names = Array.from(
      new Set(buckets.flatMap((b) => Object.keys(aging[b] || {}))),
    );
    if (names.length === 0) return;

    const theme = this._chartDefaults();
    const bucketColors = {
      current: "#00c48f",
      30: "#ffa94d",
      60: "#ff922b",
      90: "#ff4757",
    };
    const bucketLabels = {
      current: "Current",
      30: "30+ days",
      60: "60+ days",
      90: "90+ days",
    };

    const datasets = buckets.map((b) => ({
      label: bucketLabels[b],
      data: names.map((n) => (aging[b] && aging[b][n]) || 0),
      backgroundColor: bucketColors[b],
      borderColor: "transparent",
      stack: "aging",
    }));

    this.state.charts[canvasId] = new Chart(ctx, {
      type: "bar",
      data: { labels: names, datasets },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top", labels: { color: theme.text } },
          tooltip: {
            callbacks: {
              label: (c) => `${c.dataset.label}: ${formatCurrency(c.parsed.x)}`,
            },
          },
          title: {
            display: true,
            text: `${label} Aging (stacked balances)`,
            color: theme.text,
          },
        },
        scales: {
          x: {
            stacked: true,
            ticks: { color: theme.muted, callback: (v) => formatCurrency(v) },
            grid: { color: theme.grid },
          },
          y: {
            stacked: true,
            ticks: { color: theme.muted },
            grid: { color: theme.grid },
          },
        },
      },
    });
  },

  _renderCashForecastChart() {
    const ctx = $("#chart-cash-forecast");
    if (!ctx) return;
    const forecast = this.state.data.cash_forecast || [];
    if (forecast.length === 0) return;
    const theme = this._chartDefaults();

    const labels = forecast.map((r) => r.date);
    const collections = forecast.map((r) => Number(r.collections) || 0);
    const payments = forecast.map((r) => Number(r.payments) || 0);
    const net = forecast.map((r) => Number(r.net) || 0);

    this.state.charts.cash = new Chart(ctx, {
      data: {
        labels,
        datasets: [
          {
            type: "line",
            label: "Collections (cumulative)",
            data: collections,
            borderColor: "#00c48f",
            backgroundColor: "rgba(0,196,143,0.15)",
            fill: false,
            tension: 0.3,
            pointRadius: 3,
          },
          {
            type: "line",
            label: "Payments (cumulative)",
            data: payments,
            borderColor: "#ff6b6b",
            backgroundColor: "rgba(255,107,107,0.15)",
            fill: false,
            tension: 0.3,
            pointRadius: 3,
          },
          {
            type: "bar",
            label: "Net",
            data: net,
            backgroundColor: net.map((v) =>
              v >= 0 ? "rgba(0,196,143,0.35)" : "rgba(255,107,107,0.35)",
            ),
            borderColor: "transparent",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: theme.text } },
          tooltip: {
            callbacks: {
              label: (c) => `${c.dataset.label}: ${formatCurrency(c.parsed.y)}`,
            },
          },
        },
        scales: {
          x: { ticks: { color: theme.muted }, grid: { color: theme.grid } },
          y: {
            ticks: { color: theme.muted, callback: (v) => formatCurrency(v) },
            grid: { color: theme.grid },
            beginAtZero: true,
          },
        },
      },
    });
  },

  // ------------------------------------------------------------------
  // Phase 9.5 — AI insights panel + settings modal
  // ------------------------------------------------------------------
  _aiPanelHtml() {
    const ins = this.state.aiInsights;
    const busy = this.state.aiBusy;
    let body;
    if (busy) {
      body = `<div class="ai-insights-body ai-insights-busy">
                <span class="spinner"></span> Generating insights&hellip;
            </div>`;
    } else if (!ins) {
      body = `<div class="ai-insights-body ai-insights-empty">
                Click <strong>AI Insights</strong> to run the current snapshot through your
                configured LLM. Nothing is sent until you click.
            </div>`;
    } else {
      body = `
                <div class="ai-insights-meta">
                    ${escapeHtml(ins.provider_label || ins.provider || "")}
                    &middot; ${escapeHtml(ins.model || "")}
                    &middot; ${escapeHtml(ins.generated_at || "")}
                    ${ins.cached ? '<span class="ai-cache-badge">cached</span>' : ""}
                </div>
                <div class="ai-insights-body">${this._renderMarkdownish(ins.insights || "")}</div>
            `;
    }
    return `
            <div class="analytics-card ai-insights-card">
                <div class="analytics-section-title ai-insights-title">
                    <span>&#10024; AI Insights</span>
                </div>
                ${body}
            </div>
        `;
  },

  /**
   * Minimal markdown-ish renderer for the brief 3/3/3 reports LLMs return.
   * We intentionally don't pull in a full markdown library — the prompt
   * tells the model to use ### headings and - bullets, so a tiny
   * line-by-line pass is enough and keeps the attack surface small.
   */
  _renderMarkdownish(text) {
    const lines = String(text || "").split(/\r?\n/);
    let html = "";
    let inList = false;
    const closeList = () => {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
    };

    for (const raw of lines) {
      const line = raw.trim();
      if (!line) {
        closeList();
        continue;
      }
      if (line.startsWith("### ")) {
        closeList();
        html += `<h4>${escapeHtml(line.slice(4))}</h4>`;
      } else if (line.startsWith("## ")) {
        closeList();
        html += `<h3>${escapeHtml(line.slice(3))}</h3>`;
      } else if (line.startsWith("- ") || line.startsWith("* ")) {
        if (!inList) {
          html += "<ul>";
          inList = true;
        }
        html += `<li>${escapeHtml(line.slice(2))}</li>`;
      } else {
        closeList();
        html += `<p>${escapeHtml(line)}</p>`;
      }
    }
    closeList();
    return html || '<p class="ai-insights-empty">(empty response)</p>';
  },

  async _runAiInsights(force = false) {
    this.state.aiBusy = true;
    this._updateAiPanel();
    App.setStatus("Generating AI insights...");
    try {
      const qs = new URLSearchParams({ period: this.state.period });
      if (force) qs.set("force", "true");
      const res = await API.post(`/analytics/ai-insights?${qs.toString()}`, {});
      this.state.aiInsights = res;
      toast("AI insights ready", "success");
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      // 400 → config missing. Offer settings modal.
      if (/not configured/i.test(msg)) {
        toast("Configure an AI provider first", "error");
        this._openAiSettings();
      } else {
        toast("AI insights failed: " + msg, "error");
      }
    } finally {
      this.state.aiBusy = false;
      this._updateAiPanel();
      App.setStatus("Analytics — Ready");
    }
  },

  _updateAiPanel() {
    // Swap only the AI card, not the whole page — keeps chart state
    // intact (avoids a full _reload()).
    const host = document.querySelector(".ai-insights-card");
    if (!host) return;
    // Replace the inner content. We rebuild the whole card HTML and
    // splice just the children to avoid re-triggering listeners on
    // the control bar.
    const tmp = document.createElement("div");
    tmp.innerHTML = this._aiPanelHtml();
    const fresh = tmp.querySelector(".ai-insights-card");
    if (fresh) host.innerHTML = fresh.innerHTML;
  },

  async _openAiSettings() {
    // Fetch current config so the form reflects the stored values.
    let cfg;
    try {
      cfg = await API.get("/analytics/ai-config");
      this.state.aiConfig = cfg;
    } catch (err) {
      toast("Failed to load AI config: " + (err.message || err), "error");
      return;
    }

    const providers = cfg.providers || [];
    const currentProvider =
      cfg.provider || (providers[0] && providers[0].key) || "";
    const currentModel = cfg.model || "";
    const hasKey = !!cfg.has_api_key;

    const providerOptions = providers
      .map(
        (p) => `
            <option value="${escapeHtml(p.key)}"${p.key === currentProvider ? " selected" : ""}>
                ${escapeHtml(p.label)}
            </option>
        `,
      )
      .join("");

    const currentSpec =
      providers.find((p) => p.key === currentProvider) || providers[0] || {};
    const needsAccount = !!currentSpec.needs_account_id;
    const needsWorker = !!currentSpec.needs_worker_url;

    const html = `
            <form id="ai-settings-form" class="ai-settings-form" autocomplete="off">
                <label class="form-field">
                    <span>Provider</span>
                    <select id="ai-settings-provider">${providerOptions}</select>
                </label>
                <div id="ai-settings-hint" class="ai-settings-hint">
                    ${escapeHtml(currentSpec.free_tier_hint || "")}
                    ${currentSpec.docs_url ? ` &middot; <a href="${escapeHtml(currentSpec.docs_url)}" target="_blank" rel="noopener">Get a key</a>` : ""}
                </div>
                <label class="form-field">
                    <span>Model</span>
                    <input type="text" id="ai-settings-model"
                           value="${escapeHtml(currentModel || currentSpec.default_model || "")}"
                           placeholder="${escapeHtml(currentSpec.default_model || "")}">
                </label>
                <label class="form-field" id="ai-settings-cf-wrap" style="${needsAccount ? "" : "display:none"}">
                    <span>Cloudflare Account ID</span>
                    <input type="text" id="ai-settings-cf-account"
                           value="${escapeHtml(cfg.cloudflare_account_id || "")}"
                           placeholder="32-char hex (from dash.cloudflare.com)">
                </label>
                <fieldset id="ai-settings-worker-wrap" class="ai-worker-section"
                          style="${needsWorker ? "" : "display:none"}">
                    <legend>Cloudflare Worker Gateway</legend>
                    <p class="ai-worker-help">
                        Deploy <code>cloudflare/worker.js</code> in your own
                        Cloudflare account — the real AI credentials live
                        inside Cloudflare as a Worker secret, not in Slowbooks'
                        database. Slowbooks only holds the shared Bearer
                        token. See <code>cloudflare/README.md</code> for the
                        5-minute setup.
                    </p>
                    <label class="form-field">
                        <span>Worker URL <em class="ai-worker-required">(https only)</em></span>
                        <input type="url" id="ai-settings-worker-url"
                               value="${escapeHtml(cfg.worker_url || "")}"
                               placeholder="https://slowbooks-ai.yourname.workers.dev/v1/chat/completions"
                               autocomplete="off"
                               spellcheck="false">
                    </label>
                    <p class="ai-worker-security">
                        <strong>Security:</strong> only <code>https://</code>
                        URLs are accepted; private/loopback IPs, embedded
                        credentials, and non-HTTPS schemes are rejected.
                        Redirects are disabled and TLS certificates are
                        always verified.
                    </p>
                </fieldset>
                <label class="form-field">
                    <span>API Key / Shared Secret ${hasKey ? '<em class="ai-key-saved">(saved ✓)</em>' : ""}</span>
                    <input type="password" id="ai-settings-key"
                           placeholder="${hasKey ? "Leave blank to keep existing" : "Paste key or openssl rand -hex 32"}"
                           autocomplete="new-password">
                </label>
                <div class="ai-settings-buttons">
                    <button type="button" class="btn btn-secondary btn-sm" id="ai-settings-test">Test</button>
                    <span id="ai-settings-test-result" class="ai-settings-test-result"></span>
                    <div class="ai-settings-spacer"></div>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="closeModal()">Cancel</button>
                    <button type="button" class="btn btn-primary btn-sm" id="ai-settings-save">Save</button>
                </div>
            </form>
        `;

    openModal("AI Provider Settings", html);

    // Wire up dynamic bits
    const providerSel = document.getElementById("ai-settings-provider");
    const hintEl = document.getElementById("ai-settings-hint");
    const modelEl = document.getElementById("ai-settings-model");
    const cfWrap = document.getElementById("ai-settings-cf-wrap");
    const workerWrap = document.getElementById("ai-settings-worker-wrap");
    const saveBtn = document.getElementById("ai-settings-save");
    const testBtn = document.getElementById("ai-settings-test");
    const testRes = document.getElementById("ai-settings-test-result");

    providerSel.addEventListener("change", () => {
      const spec = providers.find((p) => p.key === providerSel.value) || {};
      hintEl.innerHTML =
        escapeHtml(spec.free_tier_hint || "") +
        (spec.docs_url
          ? ` &middot; <a href="${escapeHtml(spec.docs_url)}" target="_blank" rel="noopener">Get a key</a>`
          : "");
      modelEl.placeholder = spec.default_model || "";
      // Only clear the model input if it's empty — don't trample user edits.
      if (!modelEl.value) modelEl.value = spec.default_model || "";
      cfWrap.style.display = spec.needs_account_id ? "" : "none";
      workerWrap.style.display = spec.needs_worker_url ? "" : "none";
    });

    // Build the payload once — every save/test uses the same shape.
    const collectPayload = () => ({
      provider: providerSel.value,
      model: modelEl.value.trim(),
      cloudflare_account_id: document
        .getElementById("ai-settings-cf-account")
        .value.trim(),
      worker_url: document
        .getElementById("ai-settings-worker-url")
        .value.trim(),
      api_key: document.getElementById("ai-settings-key").value,
    });

    saveBtn.addEventListener("click", async () => {
      const payload = collectPayload();
      try {
        const updated = await API.put("/analytics/ai-config", payload);
        this.state.aiConfig = updated;
        toast("AI settings saved", "success");
        closeModal();
      } catch (err) {
        toast("Save failed: " + (err.message || err), "error");
      }
    });

    testBtn.addEventListener("click", async () => {
      // Save first (so the test uses the just-entered key), then /test.
      testRes.textContent = "Saving…";
      testRes.className = "ai-settings-test-result";
      const payload = collectPayload();
      try {
        await API.put("/analytics/ai-config", payload);
      } catch (err) {
        testRes.textContent = "Save failed: " + (err.message || err);
        testRes.classList.add("ai-test-fail");
        return;
      }
      testRes.textContent = "Testing…";
      try {
        const res = await API.post("/analytics/ai-config/test", {});
        testRes.textContent = `✓ ${res.provider_label} replied: "${res.reply}"`;
        testRes.classList.add("ai-test-ok");
      } catch (err) {
        testRes.textContent = "✗ " + (err.message || err);
        testRes.classList.add("ai-test-fail");
      }
    });
  },

  // ------------------------------------------------------------------
  // Phase 9.5b — Tool-calling Q&A chat assistant
  // ------------------------------------------------------------------
  _aiChatHtml() {
    const messages = this.state.chatMessages || [];
    const busy = this.state.chatBusy;

    let messagesHtml = "";
    if (messages.length === 0) {
      messagesHtml = `
                <div class="ai-chat-empty">
                    <p>Ask me anything about your books:</p>
                    <ul class="ai-chat-examples">
                        <li>&ldquo;How much did I spend at Jack in the Box in 2025?&rdquo;</li>
                        <li>&ldquo;What are my unpaid invoices from ABC Corp?&rdquo;</li>
                        <li>&ldquo;Show me my top 5 customers by revenue&rdquo;</li>
                        <li>&ldquo;What were my HSA-tagged payments in 2024?&rdquo;</li>
                    </ul>
                </div>
            `;
    } else {
      messagesHtml = messages
        .map((msg) => this._renderChatMessage(msg))
        .join("");
    }

    if (busy) {
      messagesHtml += `
                <div class="ai-chat-message ai-chat-assistant">
                    <div class="ai-chat-avatar">&#10024;</div>
                    <div class="ai-chat-bubble">
                        <span class="spinner"></span> Thinking&hellip;
                    </div>
                </div>
            `;
    }

    return `
            <div class="analytics-card ai-chat-card">
                <div class="analytics-section-title ai-chat-title">
                    <span>&#128172; Ask AI</span>
                    ${messages.length > 0 ? '<button class="btn btn-secondary btn-sm" id="ai-chat-clear" style="margin-left:auto">Clear</button>' : ""}
                </div>
                <div class="ai-chat-messages" id="ai-chat-messages">
                    ${messagesHtml}
                </div>
                <form class="ai-chat-form" id="ai-chat-form">
                    <input type="text" id="ai-chat-input"
                           placeholder="Ask a question..."
                           ${busy ? "disabled" : ""}
                           autocomplete="off">
                    <button type="submit" class="btn btn-primary btn-sm" ${busy ? "disabled" : ""}>
                        Send
                    </button>
                </form>
            </div>
        `;
  },

  _renderChatMessage(msg) {
    if (msg.role === "user") {
      return `
                <div class="ai-chat-message ai-chat-user">
                    <div class="ai-chat-avatar">&#128100;</div>
                    <div class="ai-chat-bubble">${escapeHtml(msg.content)}</div>
                </div>
            `;
    }
    // Assistant message may include tool calls
    let toolCallsHtml = "";
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      toolCallsHtml = `
                <details class="ai-chat-tools">
                    <summary>&#128296; Used ${msg.tool_calls.length} tool${msg.tool_calls.length === 1 ? "" : "s"}</summary>
                    <ul>
                        ${msg.tool_calls
                          .map(
                            (tc) => `
                            <li>
                                <code>${escapeHtml(tc.tool_name)}</code>
                                ${
                                  tc.params && Object.keys(tc.params).length > 0
                                    ? `(${Object.entries(tc.params)
                                        .map(
                                          ([k, v]) =>
                                            `${escapeHtml(k)}=${escapeHtml(String(v))}`,
                                        )
                                        .join(", ")})`
                                    : ""
                                }
                            </li>
                        `,
                          )
                          .join("")}
                    </ul>
                </details>
            `;
    }
    return `
            <div class="ai-chat-message ai-chat-assistant">
                <div class="ai-chat-avatar">&#10024;</div>
                <div class="ai-chat-bubble">
                    ${this._renderMarkdownish(msg.content || "")}
                    ${toolCallsHtml}
                </div>
            </div>
        `;
  },

  async _submitChatQuery(question) {
    if (!question || !question.trim()) return;
    question = question.trim();

    // Add user message to history
    this.state.chatMessages.push({
      role: "user",
      content: question,
      timestamp: new Date().toISOString(),
    });
    this.state.chatBusy = true;
    this._updateChatPanel();
    App.setStatus("Asking AI...");

    try {
      const url = `/analytics/ai-query?question=${encodeURIComponent(question)}`;
      const res = await API.post(url, {});
      this.state.chatMessages.push({
        role: "assistant",
        content: res.final_response || "(no response)",
        tool_calls: res.tool_calls || [],
        call_count: res.call_count || 0,
        success: res.success,
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      if (/not configured/i.test(msg)) {
        toast("Configure an AI provider first", "error");
        this._openAiSettings();
      } else {
        toast("Query failed: " + msg, "error");
        this.state.chatMessages.push({
          role: "assistant",
          content: `*Error:* ${msg}`,
          tool_calls: [],
          timestamp: new Date().toISOString(),
        });
      }
    } finally {
      this.state.chatBusy = false;
      this._updateChatPanel();
      App.setStatus("Analytics — Ready");
    }
  },

  _updateChatPanel() {
    const host = document.querySelector(".ai-chat-card");
    if (!host) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = this._aiChatHtml();
    const fresh = tmp.querySelector(".ai-chat-card");
    if (fresh) {
      host.innerHTML = fresh.innerHTML;
      this._wireChatListeners();
    }
  },

  _wireChatListeners() {
    const form = document.getElementById("ai-chat-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const input = document.getElementById("ai-chat-input");
        if (input && input.value.trim()) {
          const q = input.value;
          input.value = "";
          this._submitChatQuery(q);
        }
      });
    }
    const clearBtn = document.getElementById("ai-chat-clear");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        this.state.chatMessages = [];
        this._updateChatPanel();
      });
    }
  },

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  _sum(obj) {
    return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
  },
};
