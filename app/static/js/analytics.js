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
  // What the figures count. The four cards, the trend and the doughnut are
  // the ledger's (accrual, as the Profit & Loss) — one basis, so the margin
  // is net income over revenue and not paid invoices over ledger expenses
  // (2.22.3 gate, NEW-73 / W-2). Revenue by customer is invoices paid in
  // full, and says so: September's "Revenue $742.20" once sat beside a
  // $10.0M P&L for the same month with nothing to say why (2.17.3, W-L12).
  REVENUE_BASIS:
    "Everything credited to income accounts in this period, less what was debited back — invoices and sales receipts when dated (paid or not), deposits to income, journal entries — as the Profit & Loss counts it.",
  EXPENSE_BASIS:
    "Everything posted to expense and cost-of-goods accounts in this period — bills when dated (paid or not), expenses, register entries, card charges, payroll and journal entries — as the Profit & Loss counts them.",
  MARGIN_BASIS:
    "Net income over revenue, both from the ledger for this period.",
  // The doughnut's colours, in slice order (largest first); the table under
  // it is its legend, each row with the slice's swatch (2.22.3 gate, NEW-74:
  // the categories carry the account's name now, and Chart.js's own legend
  // clipped "6000 - Advertising & Marketing" at the card's edge)
  EXPENSE_PALETTE: ["red", "blue", "orange", "purple", "green", "pink", "sky", "yellow"],
  BASIS_NOTE:
    "Revenue, expenses, net income and the margin count every posting in the ledger dated in the period (accrual), as the Profit & Loss report does. Revenue by customer counts invoices and sales receipts dated in the period and paid in full — a paid basis — so its total can be less than the revenue above.",

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
    // AI Predefined Analyses — replaces the free-form chat with a curated
    // dropdown. One result at a time; switching pages keeps the last one.
    aiActions: null, // [{category, actions: [{key, label, uses_period}]}] cached catalogue
    aiActionKey: "", // currently selected action in the dropdown
    aiActionResult: null, // last run result (analysis + meta)
    aiActionBusy: false,
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

    // the server's totals: one basis for all four, and the PDF and the AI
    // brief read the same block
    const totals = data.totals || {};
    const totalRevenue = Number(totals.revenue) || 0;
    const totalExpenses = Number(totals.expenses) || 0;
    const netIncome = Number(totals.net_income) || 0;
    // DSO is absent when nothing was paid in the last 30 days: no collection
    // period to measure, and 0.0 read as instant collection (NEW-80 / W-4)
    const dso = data.dso == null ? null : Number(data.dso);
    const dsoText = dso == null ? "&mdash;" : dso.toFixed(1);
    const dsoTitle =
      `${Terms.text('Days Sales Outstanding — average number of days between invoicing a customer and collecting the payment.')} Lower is better. Computed as (open A/R balance ÷ invoices and sales receipts dated in the last 30 days and paid in full) × 30.` +
      (dso == null ? ` ${Terms.text('No invoice or sales receipt dated in the last 30 days has been paid in full, so there is no collection period to measure.')}` : "");
    const margin = totals.margin_pct == null ? null : Number(totals.margin_pct);
    const marginText = margin == null ? "&mdash;" : `${margin.toFixed(1)}%`;
    const marginTitle =
      `${this.MARGIN_BASIS} Net income ${formatCurrency(netIncome)} on revenue ${formatCurrency(totalRevenue)}` +
      (margin == null ? " — no revenue to divide by." : ".");

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
                    <button class="btn btn-primary btn-sm"   id="analytics-ai-run" data-write title="Generate AI insights">&#10024; AI Insights</button>
                </div>
            </div>

            <div class="analytics-meta">${periodLabel}</div>

            ${this._aiPanelHtml()}

            ${this._aiActionsHtml()}

            <div class="analytics-kpi-grid">
                <div class="analytics-kpi" title="${escapeHtml(Terms.text(this.REVENUE_BASIS))}"><div class="analytics-kpi-label">${Terms.text('Revenue (ledger)')}</div><div class="analytics-kpi-value kpi-green">${formatCurrency(totalRevenue)}</div></div>
                <div class="analytics-kpi" title="${escapeHtml(this.EXPENSE_BASIS)}"><div class="analytics-kpi-label">Expenses (ledger)</div><div class="analytics-kpi-value kpi-red">${formatCurrency(totalExpenses)}</div></div>
                <div class="analytics-kpi" title="${escapeHtml(dsoTitle)}"><div class="analytics-kpi-label">DSO (Days)</div><div class="analytics-kpi-value kpi-blue">${dsoText}</div></div>
                <div class="analytics-kpi" title="${escapeHtml(marginTitle)}"><div class="analytics-kpi-label">Margin %</div><div class="analytics-kpi-value kpi-purple">${marginText}</div></div>
            </div>

            <div class="analytics-meta">${escapeHtml(Terms.text(this.BASIS_NOTE))}</div>

            <div class="analytics-section-title">Revenue Trend &mdash; Last 12 Months</div>
            <div class="analytics-card">
                <div class="chart-wrap"><canvas id="chart-revenue-trend"></canvas></div>
                ${this._trendTable(data.revenue_trend)}
            </div>

            <div class="analytics-two-col">
                <div class="analytics-card">
                    <div class="analytics-section-title">${Terms.text('Revenue by Customer')}</div>
                    ${this._revenueTable(data.revenue_by_customer)}
                </div>
                <div class="analytics-card">
                    <div class="analytics-section-title">Expenses by Category</div>
                    <div class="chart-wrap chart-wrap-sm"><canvas id="chart-expenses"></canvas></div>
                    ${this._expensesTable(data.expenses_by_category)}
                </div>
            </div>

            <div class="analytics-section-title">${T('Accounts Receivable Aging')}</div>
            <div class="analytics-card">
                <div class="chart-wrap chart-wrap-sm"><canvas id="chart-ar-aging"></canvas></div>
                ${this._agingTable(data.ar_aging, T("Customer"))}
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
      return `<div class="analytics-empty">${Terms.text('No paid invoices this period.')}</div>`;
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
                    <thead><tr><th scope="col">${T('Customer')}</th><th scope="col" class="amount">Revenue</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
  },

  // The doughnut's slices: the accounts with net spending, largest first.
  // An account that nets to a credit for the period (a refund larger than
  // the spending, since the ledger can do that and paid bills could not)
  // has no slice — Chart.js would size one by the absolute value, as if it
  // were spending (NEW-78) — and its table row has no swatch.
  _expenseSlices(byCategory) {
    return Object.entries(byCategory || {})
      .filter(([, a]) => Number(a) > 0)
      .sort((a, b) => b[1] - a[1]);
  },

  _expensesTable(byCategory) {
    const entries = Object.entries(byCategory || {}).sort(
      (a, b) => b[1] - a[1],
    );
    if (entries.length === 0) {
      return '<div class="analytics-empty">No expenses posted this period.</div>';
    }
    const palette = this.EXPENSE_PALETTE;
    const sliced = this._expenseSlices(byCategory).map(([c]) => c);
    const swatch = (c) => {
      const i = sliced.indexOf(c);
      return i < 0
        ? ""
        : `<span aria-hidden="true" class="chart-swatch" style="background:var(--chart-${palette[i % palette.length]})"></span> `;
    };
    const rows = entries
      .map(
        ([c, a]) => `
            <tr><td>${swatch(c)}${escapeHtml(c)}</td><td class="amount ${Number(a) < 0 ? "green" : "red"}">${formatCurrency(a)}</td></tr>
        `,
      )
      .join("");
    return `
            <div class="table-container">
                <table>
                    <thead><tr><th scope="col">Category</th><th scope="col" class="amount">Expense</th></tr></thead>
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
                            <th scope="col">${escapeHtml(nameHeader)}</th>
                            <th scope="col" class="amount">Current</th>
                            <th scope="col" class="amount">1-30</th>
                            <th scope="col" class="amount">31-60</th>
                            <th scope="col" class="amount">61-90+</th>
                            <th scope="col" class="amount">Total</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}${totalsRow}</tbody>
                </table>
            </div>
        `;
  },

  // Every month's figure in text, under the chart (NEW-81 / W-5: a point at
  // the chart's edge or top could not carry its figure, and the card had no
  // table to fall back on; the PDF always had this one)
  _trendTable(trend) {
    const entries = Object.entries(trend || {});
    if (entries.length === 0) {
      return '<div class="analytics-empty">No revenue data.</div>';
    }
    const rows = entries
      .map(
        ([m, v]) => `
            <tr><td>${escapeHtml(m)}</td><td class="amount green">${formatCurrency(v)}</td></tr>
        `,
      )
      .join("");
    return `
            <div class="table-container">
                <table>
                    <thead><tr><th scope="col">Month</th><th scope="col" class="amount">Revenue (ledger)</th></tr></thead>
                    <tbody>${rows}</tbody>
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
                            <th scope="col">Due by</th>
                            <th scope="col" class="amount">Expected Collections</th>
                            <th scope="col" class="amount">Expected Payments</th>
                            <th scope="col" class="amount">Net</th>
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
    // Phase 9.5 AI button. The gear was removed — provider config lives in
    // Settings → AI Insights, reachable via the main nav or via this button
    // when no provider is configured (see _openAiSettings call below).
    const aiRun = $("#analytics-ai-run");
    if (aiRun)
      aiRun.addEventListener("click", () => this._runAiInsights(false));
    // AI predefined-analyses panel
    this._wireAiActionsListeners();
    if (!this.state.aiActions) this._loadAiActions();

    // Charts
    this._redrawCharts();
  },

  // Also called on a theme toggle: canvas ink is painted with the theme that
  // was active at creation, so the charts are rebuilt rather than restyled.
  _redrawCharts() {
    if (!document.getElementById("chart-ar-aging")) return; // page not on screen
    if (this.state.data && typeof Chart !== "undefined") {
      this._destroyCharts();
      this._renderRevenueChart();
      this._renderExpensesChart();
      this._renderAgingChart(
        "chart-ar-aging",
        this.state.data.ar_aging,
        T("A/R Aging").replace(/ Aging$/, ""),
        chartColor("green"),
      );
      this._renderAgingChart(
        "chart-ap-aging",
        this.state.data.ap_aging,
        "A/P",
        chartColor("red"),
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
      plugins: [chartFigures()],
      data: {
        labels,
        datasets: [
          {
            label: "Monthly Revenue (ledger)",
            data: values,
            borderColor: chartColor("green"),
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
    const entries = this._expenseSlices(this.state.data.expenses_by_category);
    if (entries.length === 0) return;
    const theme = this._chartDefaults();

    const palette = this.EXPENSE_PALETTE.map((name) => chartColor(name));

    this.state.charts.expenses = new Chart(ctx, {
      type: "doughnut",
      plugins: [chartFigures()],
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
          // the table under the chart is the legend (its rows carry the swatches)
          legend: { display: false },
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
    // one row per name, every name on the axis (gate round 1, W-6: a 220px
    // chart skipped half of them), and bars thick enough for their figures
    const wrap = ctx.parentElement;
    if (wrap) wrap.style.height = `${Math.max(220, names.length * 24 + 80)}px`;

    const theme = this._chartDefaults();
    const bucketColors = {
      current: chartColor("green"),
      30: chartColor("orange"),
      60: chartColor("amber"),
      90: chartColor("crimson"),
    };
    const bucketLabels = {
      current: "Current",
      30: "1-30 days",
      60: "31-60 days",
      90: "61-90+ days",
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
      plugins: [chartFigures()],
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
            ticks: { color: theme.muted, autoSkip: false },
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
      plugins: [chartFigures()],
      data: {
        labels,
        datasets: [
          {
            type: "line",
            label: "Collections (cumulative)",
            data: collections,
            borderColor: chartColor("green"),
            backgroundColor: "rgba(0,196,143,0.15)",
            fill: false,
            tension: 0.3,
            pointRadius: 3,
          },
          {
            type: "line",
            label: "Payments (cumulative)",
            data: payments,
            borderColor: chartColor("red"),
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
            <div class="analytics-card ai-insights-card" data-write>
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
      // the provider is chosen but has no key of its own: say so (W-2)
      const noKey = msg.match(/No API key saved[^]*$/i);
      if (noKey || /not configured/i.test(msg)) {
        toast(noKey ? noKey[0] : "Configure an AI provider first", "error");
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

  // Deep-link into Settings → AI Insights. Single source of truth for the
  // provider/key/model form lives there now; this just routes the user.
  // Use location.hash (not App.navigate) so the URL stays in sync with the
  // rendered page — otherwise the next nav click to Analytics is a no-op
  // because the hash never moved off "#/analytics".
  _openAiSettings() {
    sessionStorage.setItem("settings_focus", "settings-ai");
    if (location.hash === "#/settings") {
      // Already on the URL we want — force a re-render + scroll
      App.navigate("#/settings");
    } else {
      location.hash = "#/settings";
    }
  },

  // ------------------------------------------------------------------
  // AI Predefined Analyses (replaces the free-form chat panel).
  // The dropdown options come from /api/analytics/ai-actions; running an
  // action POSTs to /api/analytics/ai-actions/{key}?period=…
  // ------------------------------------------------------------------
  _aiActionsHtml() {
    const groups = this.state.aiActions;
    const result = this.state.aiActionResult;
    const busy = this.state.aiActionBusy;
    const selected = this.state.aiActionKey;

    let dropdown;
    // the placeholders are named as the real picker is: every field on
    // every page has a name (v2.18.2), a disabled one too
    if (!groups) {
      dropdown = '<select disabled aria-label="Analysis"><option>Loading analyses…</option></select>';
    } else if (groups.length === 0) {
      dropdown = '<select disabled aria-label="Analysis"><option>(none available)</option></select>';
    } else {
      const opts = groups
        .map(
          (g) => `
            <optgroup label="${escapeHtml(g.category)}">
                ${g.actions
                  .map(
                    (a) => `
                    <option value="${escapeHtml(a.key)}"${a.key === selected ? " selected" : ""}>
                        ${escapeHtml(a.label)}
                    </option>`,
                  )
                  .join("")}
            </optgroup>`,
        )
        .join("");
      dropdown = `<select id="ai-actions-select" aria-label="Analysis" ${busy ? "disabled" : ""}>
            <option value="" disabled${selected ? "" : " selected"}>Choose an analysis…</option>
            ${opts}
        </select>`;
    }

    let resultHtml = "";
    if (busy) {
      resultHtml = `
            <div class="ai-actions-body ai-actions-busy">
                <span class="spinner"></span> Running analysis&hellip;
            </div>`;
    } else if (result) {
      resultHtml = `
            <div class="ai-actions-meta">
                <strong>${escapeHtml(result.label || "")}</strong>
                &middot; ${escapeHtml(result.provider || "")}
                &middot; ${escapeHtml(result.model || "")}
                ${result.period ? `&middot; ${escapeHtml(result.period.start)} → ${escapeHtml(result.period.end)}` : ""}
            </div>
            <div class="ai-actions-body">${this._renderMarkdownish(result.analysis || "")}</div>`;
    } else if (groups) {
      resultHtml = `
            <div class="ai-actions-body ai-actions-empty">
                Pick an analysis from the dropdown and click Run.
                Your selection runs through the AI provider configured in
                Settings → AI Insights.
            </div>`;
    }

    return `
        <div class="analytics-card ai-actions-card" data-write>
            <div class="analytics-section-title ai-actions-title">
                <span>&#129504; AI Analysis</span>
                ${result ? '<button class="btn btn-secondary btn-sm" id="ai-actions-clear" style="margin-left:auto">Clear</button>' : ""}
            </div>
            <form class="ai-actions-form" id="ai-actions-form">
                ${dropdown}
                <button type="submit" class="btn btn-primary btn-sm" ${busy ? "disabled" : ""}>
                    Run Analysis
                </button>
            </form>
            ${resultHtml}
        </div>`;
  },

  async _loadAiActions() {
    try {
      const res = await API.get("/analytics/ai-actions");
      this.state.aiActions = res.groups || [];
      this._updateAiActionsPanel();
    } catch (err) {
      // Soft-fail — leave dropdown disabled with an error in the body.
      this.state.aiActions = [];
      this._updateAiActionsPanel();
      toast(
        "Failed to load AI analyses: " + (err.message || err),
        "error",
      );
    }
  },

  async _runAiAction(key) {
    if (!key) return;
    this.state.aiActionKey = key;
    this.state.aiActionBusy = true;
    this.state.aiActionResult = null;
    this._updateAiActionsPanel();
    App.setStatus("Running AI analysis...");

    try {
      const period = this.state.period || "month";
      const url = `/analytics/ai-actions/${encodeURIComponent(key)}?period=${encodeURIComponent(period)}`;
      const result = await API.post(url, {});
      this.state.aiActionResult = result;
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      // the provider is chosen but has no key of its own: say so (W-2)
      const noKey = msg.match(/No API key saved[^]*$/i);
      if (noKey || /not configured/i.test(msg)) {
        toast(noKey ? noKey[0] : "Configure an AI provider first", "error");
        this._openAiSettings();
      } else {
        toast("Analysis failed: " + msg, "error");
      }
    } finally {
      this.state.aiActionBusy = false;
      this._updateAiActionsPanel();
      App.setStatus("Analytics — Ready");
    }
  },

  _updateAiActionsPanel() {
    const host = document.querySelector(".ai-actions-card");
    if (!host) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = this._aiActionsHtml();
    const fresh = tmp.querySelector(".ai-actions-card");
    if (fresh) {
      host.innerHTML = fresh.innerHTML;
      this._wireAiActionsListeners();
    }
  },

  _wireAiActionsListeners() {
    const form = document.getElementById("ai-actions-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const sel = document.getElementById("ai-actions-select");
        if (sel && sel.value) this._runAiAction(sel.value);
      });
    }
    const sel = document.getElementById("ai-actions-select");
    if (sel) {
      // Track current selection so re-renders preselect the right option.
      sel.addEventListener("change", () => {
        this.state.aiActionKey = sel.value;
      });
    }
    const clearBtn = document.getElementById("ai-actions-clear");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        this.state.aiActionResult = null;
        this._updateAiActionsPanel();
      });
    }
  },

  // ------------------------------------------------------------------
  // [removed] Phase 9.5b free-form chat panel — replaced by curated
  // dropdown above. Backend /api/analytics/ai-query still exists for
  // any external callers but is no longer wired into the UI.
  // ------------------------------------------------------------------

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  _sum(obj) {
    return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
  },
};
document.addEventListener("slowbooks:themechange", () => AnalyticsPage._redrawCharts());
