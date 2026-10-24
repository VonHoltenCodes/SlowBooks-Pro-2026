/**
 * Reports — every report is plain SQL on the backend; this viewer
 * renders the tables, print/PDF is WeasyPrint server-side.
 */
const ReportsPage = {
    // Every report view by its address name: #/reports/<name>?<params>
    // (R7, docs/dev/report-views.md). `open(params)` is what the address
    // runs, with the query as a plain object of strings; `label` names the
    // view on a "Back to …" button (a string, or a function returning the
    // term, where nonprofit vocabulary renames it); `asOf: true` marks a
    // view dated by one as_of_date rather
    // than a range; `keep` lists the drill-down params (beyond the dates)
    // that ride on the way back to the view. A saved report's report_type
    // is the name with underscores (profit_loss ↔ profit-loss), so saving
    // and reopening round-trip through the same address.
    _VIEWS: {
        'profit-loss':          { label: () => T('Profit & Loss'),      open: (p) => ReportsPage.profitLoss(p) },
        'balance-sheet':        { label: () => T('Balance Sheet'),      open: (p) => ReportsPage.balanceSheet(p), asOf: true },
        'profit-loss-by-class': { label: () => T('P&L by Class'),       open: (p) => ReportsPage.profitLossByClass(p) },
        'profit-loss-class':    { label: () => T('Profit & Loss'),      open: (p) => ReportsPage.profitLossOfClass(p.class_id, null, p, p.from || null), keep: ['class_id'] },
        'profit-loss-unclassified': { label: () => `${T('P&L')} Unclassified`, open: (p) => ReportsPage.profitLossUnclassified(p) },
        'profit-loss-by-job':   { label: () => `${T('P&L')} by ${T('Job')}`, open: (p) => ReportsPage.profitLossByJob(p) },
        'account-transactions': { label: 'Drill-down',         open: (p) => ReportsPage.openDrillDown(p.account_id, null, p.start_date, p.end_date, p.class_id || null, null, p.from || null, { job_id: p.job_id, period: p.period || null }) },
        'ar-aging':             { label: () => T('Accounts Receivable Aging'), open: (p) => ReportsPage.arAging(p), asOf: true },
        'ap-aging':             { label: 'Accounts Payable Aging',    open: (p) => ReportsPage.apAging(p), asOf: true },
        'sales-tax':            { label: 'Sales Tax Report',   open: (p) => ReportsPage.salesTax(p) },
        'general-ledger':       { label: 'General Ledger',     open: (p) => ReportsPage.generalLedger(p), keep: ['class_id'] },
        'income-by-customer':   { label: () => T('Income by Customer'), open: (p) => ReportsPage.incomeByCustomer(p) },
        'trial-balance':        { label: 'Trial Balance',      open: (p) => ReportsPage.trialBalance(p) },
        'adjusting-entries':    { label: 'Adjusting Journal Entries', open: (p) => ReportsPage.adjustingEntries(p) },
        'closing-date-exceptions': { label: 'Closing Date Exceptions', open: (p) => ReportsPage.closingDateExceptions(p) },
        'accountants-package':  { label: "Accountant's Package", open: (p) => ReportsPage.accountantsPackage(p) },
        'reclassify':           { label: 'Reclassify Transactions', open: (p) => ReportsPage.reclassify(p) },
        'possible-duplicates':  { label: 'Possible Duplicates', open: (p) => ReportsPage.possibleDuplicates(p) },
        'cash-flow':            { label: 'Cash Flow Statement', open: (p) => ReportsPage.cashFlow(p) },
        'job-profitability':    { label: () => T('Job Profitability'),  open: (p) => ReportsPage.jobProfitability(p) },
        'job-budget-vs-actual': { label: () => T('Job Budget vs Actual'), open: (p) => ReportsPage.jobBudgetVsActual(p) },
        'budget-vs-actual':     { label: 'Budget vs Actual',   open: (p) => { if (/^\d{4}$/.test(p.year || '')) BudgetsPage._year = parseInt(p.year, 10); return BudgetsPage.showVariance(p); } },
        'financial-statements': { label: 'Financial Statements Pack', open: (p) => ReportsPage.financialStatementsPdf(p) },
        'fixed-asset-reconciliation': { label: 'Fixed Asset Reconciliation', open: () => ReportsPage.fixedAssetReconciliation() },
        'reconciliation-reports': { label: 'Reconciliation Reports', open: () => ReportsPage.reconciliationReports() },
        'customer-statement':   { label: () => T('Customer Statement'), open: () => ReportsPage.customerStatementPicker() },
        '1099-summary':         { label: '1099 Summary',       open: (p) => ReportsPage.report1099(p) },
        'statement-of-financial-position': { label: 'Statement of Financial Position', open: (p) => ReportsPage.statementOfFinancialPosition(p), asOf: true },
        'statement-of-activities':         { label: 'Statement of Activities', open: (p) => ReportsPage.statementOfActivities(p) },
        'fund-balances':        { label: 'Fund Balances',      open: (p) => ReportsPage.fundBalances(p) },
        'functional-expenses':  { label: 'Statement of Functional Expenses', open: (p) => ReportsPage.functionalExpenses(p) },
        'pledges':              { label: 'Pledge Report',      open: (p) => ReportsPage.pledges(p) },
        'giving-statements':    { label: 'Year-End Giving Statements', open: (p) => ReportsPage.givingStatements(p) },
    },

    // The address of a view: '#/reports/<name>?<params>'. Empty values are
    // left out, and so is period=custom — dates alone mean custom, and a
    // preset period (this_month) names what to recompute on a later day.
    viewUrl(name, params) {
        const qs = new URLSearchParams();
        for (const [k, v] of Object.entries(params || {})) {
            if (v === null || v === undefined || v === '') continue;
            if (k === 'period' && v === 'custom') continue;
            qs.set(k, String(v));
        }
        const q = qs.toString();
        return `#/reports/${name}${q ? `?${q}` : ''}`;
    },

    // The registered view of that name, or null. Own names only: a name
    // the registry never had but Object.prototype does ('constructor',
    // '__proto__') opened a drill-down with a "Back to " button going
    // nowhere (R7 review).
    _view(name) {
        return Object.hasOwn(ReportsPage._VIEWS, String(name ?? '')) ? ReportsPage._VIEWS[name] : null;
    },

    // Put the view on the address bar. The same view with other params
    // (the period changed, a filter set) replaces the entry, so Back never
    // walks through every period a user tried; a different view (the
    // Report Center to a report, a report to its drill-down) pushes, so
    // Back returns to it. The pushed entry carries the address it was
    // pushed from in its history.state ({from}), as App.navigate's push
    // does: the entry itself knows where it came from, so Back, Forward,
    // Close and a reload cannot leave a stale note in memory (R7 review),
    // and a replace keeps the state the entry has.
    setAddress(name, params) {
        const url = ReportsPage.viewUrl(name, params);
        const here = location.hash || '#/';
        if (here === url) return;
        if (App.parseHash(here).path === `/reports/${name}`) history.replaceState(history.state, '', url);
        else {
            ReportsPage._leaveFrom();
            history.pushState(App.entryState(here), '', url);
            // the view is a dialog over the page it was pushed from (the
            // Report Center, the Budgets page) — or over whatever the view
            // it hopped from was over: closing it puts that page on the
            // bar (App.dialogClosed, NEW-23)
            App._dialogUnder = App._dialogUnder || here;
        }
        App.dialogAddressed();  // the view's own address: the toolbar's Back follows, live over it (NEW-30)
    },

    // The row a hop leaves from, noted on the view's own history entry
    // (history.state.focus) so that Back puts the keyboard back on it (R9):
    // the row's data-row-key, else its place among the dialog's controls.
    // A page's own row carries a key too (the chart's account links,
    // App.renderAccounts: W-7); the place among the controls is a dialog's
    // alone. Nothing is kept in memory, so Back, Forward and a reload agree.
    // The row left is the element clicked, not the focused one: a click with
    // the mouse does not put focus on a link in WebKit, so focus named
    // whatever had it before (W-7 review). A link or a row that hops passes
    // itself (`from`, its `this`); a hop reached from a click without it
    // (openDrillDown's links) is the element whose click is being handled,
    // the same `this`; anything else (a key, a script) the focused one. A
    // row passed is left from by its keyed link. A click that has the
    // browser open its link somewhere else (Ctrl, ⌘, Shift or Alt held, or
    // not the main button: a new tab or window, a download) leaves nothing
    // here: this page stays, and the note would have pulled focus onto
    // the link at the page's next draw (review).
    _leaveFrom(from) {
        const ev = window.event;
        if (ReportsPage._opensElsewhere(ev)) return;
        let el = (from && from.nodeType === 1) ? from
            : (ev && ev.type === 'click' && ev.currentTarget && ev.currentTarget.nodeType === 1) ? ev.currentTarget
            : document.activeElement;
        if (el && el.tagName === 'TR') el = el.querySelector('[data-row-key]') || el;
        if (!el || !el.closest) return;
        const inDialog = !!el.closest('#modal-body');
        const n = inDialog ? [...document.querySelectorAll('#modal-body a[href], #modal-body button')].indexOf(el) : -1;
        const key = el.getAttribute('data-row-key') || (n >= 0 ? `n:${n}` : null);
        if (!key) return;  // a select, a date, a page's unkeyed control: not a row left from
        history.replaceState({ ...(history.state || {}), focus: key }, '', location.hash || '#/');
    },

    // The click's link is followed by the browser, not here: a modifier
    // or a button other than the main one, on a real link (not the
    // javascript: links whose own click does the hop) whose following no
    // click handler has stopped (the customer page's row links stop it and
    // hop from the row). A new tab is ⌘-click on a Mac and Ctrl-click
    // elsewhere: a Windows or Super key click follows the link here, and
    // on a Mac a Ctrl-click is the context menu's.
    _opensElsewhere(ev) {
        if (!ev || (ev.type !== 'click' && ev.type !== 'auxclick')) return false;
        const newTab = App.isMac() ? ev.metaKey : ev.ctrlKey;
        if (!(newTab || ev.shiftKey || ev.altKey || ev.button !== 0)) return false;
        const a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
        return !!a && !/^\s*javascript:/i.test(a.getAttribute('href')) && !ev.defaultPrevented;
    },

    // Focus back on the row the view was left from, once; a later render
    // of the same view (a period change) leaves focus where the user has
    // it. True when it did. A page's render asks too (App.navigate, with
    // #page-content): a key that is a place among a dialog's controls is
    // not its to use, and is left for the dialog that opens over it.
    _refocusRow(root) {
        const key = history.state && history.state.focus;
        if (!key || !root) return false;
        if (key.startsWith('n:') && !root.closest('#modal-body')) return false;
        const el = key.startsWith('n:')
            ? document.querySelectorAll('#modal-body a[href], #modal-body button')[parseInt(key.slice(2), 10)]
            : root.querySelector(`[data-row-key="${CSS.escape(key)}"]`);
        if (!el) return false;
        history.replaceState({ ...history.state, focus: null }, '', location.hash || '#/');
        focusFor(el, _byKeyboard());  // the ring by keyboard, also on WebKit (NEW-43, round 3)
        return true;
    },

    // A report opened for one customer or vendor from their page (#238):
    // the row is marked and scrolled to, and takes focus on the view's
    // first render only (takeFocus) — not when Back just put focus on a
    // row, and not on a later render of the same view: a period change
    // from the keyboard was throwing focus off the period select onto the
    // row, so every next period needed Shift+Tab first (R8 review). The
    // report is still the whole company's — only the row is picked out —
    // so nothing about its totals changes.
    _spotlight(root, params, takeFocus) {
        const key = params.customer_id ? `customer:${params.customer_id}`
            : params.vendor_id ? `vendor:${params.vendor_id}` : null;
        if (!key || !root) return;
        const el = root.querySelector(`[data-row-key="${CSS.escape(key)}"]`);
        const row = el && el.closest('tr');
        if (!row) return;
        row.setAttribute('aria-current', 'true');
        row.style.background = 'var(--primary-light)';
        row.scrollIntoView({ block: 'center' });
        if (takeFocus) focusFor(el, _byKeyboard());
    },

    // A change inside the open period report that is not its dates — a
    // filter dropped, the drill-down's class or account — goes into the
    // address's params and re-renders in place (the address is replaced,
    // not pushed, as a period change is).
    _params: null,

    // A row's link to where it goes: the onclick payload is built outside
    // the template and HTML-escaped (JSON.stringify's quotes would break
    // the attribute); data-row-key is where focus returns on Back.
    _rowLink(call, text, key) {
        return `<a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;" data-row-key="${escapeHtml(key)}" onclick="${escapeHtml(call)}">${escapeHtml(text)}</a>`;
    },

    // A customer's or a vendor's page is a dialog over its list, with an
    // address of its own (#/customers/12, #/vendors/3: App.routes): one
    // history entry, so browser Back returns to the report, and the row
    // it left from.
    // (App.navigate closes the report itself, keeping its address on the
    // entry left behind, so Back returns to it.)
    // `from` is the row's link (its onclick's `this`), so the row noted is
    // the one clicked (ReportsPage._leaveFrom).
    openCustomer(id, from) {
        ReportsPage._leaveFrom(from);
        App.navigate(`#/customers/${id}`);
    },
    openVendor(id, from) {
        ReportsPage._leaveFrom(from);
        App.navigate(`#/vendors/${id}`);
    },

    // Back to a view, as its "Back to …" button does: through the browser's
    // history when this entry was pushed from that view (so Back and the
    // button agree, however deep the chain), otherwise by opening the
    // address. A view reached by its address alone (typed, pasted, a hash
    // set by hand) was pushed from no view, and goes forward.
    backTo(name, params) {
        const from = history.state && history.state.from;
        if (from && App.parseHash(from).path === `/reports/${name}`) {
            closeModal({ keepAddress: true });
            history.back();
            return;
        }
        App.navigate(ReportsPage.viewUrl(name, params));
    },

    // Back to a page with an address of its own (#/classes/:id, #234), by
    // the same rule: through history when this entry was pushed from that
    // page (its "from" is the page as it was left — its tab, its period),
    // else by opening the page on the dates the view was on.
    backToPage(path, params) {
        const from = history.state && history.state.from;
        if (from && App.parseHash(from).path === path) {
            history.back();
            return;
        }
        const qs = new URLSearchParams();
        for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== '') qs.set(k, String(v));
        const q = qs.toString();
        App.navigate(`#${path}${q ? `?${q}` : ''}`);
    },

    _backPageButton(key, path, params, label) {
        const call = escapeHtml(`ReportsPage.backToPage(${JSON.stringify(path)}, ${JSON.stringify(params || {})})`);
        return `<button type="button" class="btn btn-secondary" data-back-to="${escapeHtml(key)}" onclick="${call}">Back to ${escapeHtml(label)}</button>`;
    },

    // `detail` names which one, after the view's label, where the view is
    // a filtered one ("Back to Profit & Loss — Side Gig").
    _backButton(name, params, detail = '', cls = 'btn btn-secondary') {
        const view = ReportsPage._view(name);
        if (!view) return '';
        const call = escapeHtml(`ReportsPage.backTo(${JSON.stringify(name)}, ${JSON.stringify(params || {})})`);
        return `<button type="button" class="${cls}" data-back-to="${escapeHtml(name)}" onclick="${call}">${escapeHtml(ReportsPage._backText(name, detail))}</button>`;
    },

    _backText(name, detail) {
        const view = ReportsPage._view(name);
        const label = typeof view.label === 'function' ? view.label() : view.label;
        return `Back to ${label}${detail ? ` — ${detail}` : ''}`;
    },

    // The '/reports/:view' route: the view named in the address, opened
    // over the Report Center from its query. A name nobody registered, or a
    // view that cannot open, leaves the Report Center showing and says so;
    // the address falls back to the Report Center's.
    async openView(name, params) {
        const view = ReportsPage._view(name);
        if (!view) {
            ReportsPage._addressReportCenter();
            toast(`There is no report called "${name}"`, 'error');
            return;
        }
        try {
            await view.open(params || {});
        } catch (err) {
            ReportsPage._addressReportCenter();
            throw err;
        }
        // Opened nothing (a drill-down with no account, say, which says so
        // in a notice): the address is the Report Center's.
        const overlay = $('#modal-overlay');
        if (overlay && overlay.classList.contains('hidden')) ReportsPage._addressReportCenter();
    },

    // The Report Center's address in place of a view's that opened nothing.
    // The entry keeps its state (what it was pushed from), so the toolbar's
    // Back still knows what is behind it, and follows (NEW-30 review); no
    // dialog is under it any more (NEW-23).
    _addressReportCenter() {
        App._dialogUnder = null;
        history.replaceState(history.state, '', '#/reports');
        App.addressShown();
    },

    async render() {
        // Fetch saved reports separately so the page still renders if the
        // call fails (network blip, table missing, etc.).
        let savedHtml = '';
        try {
            const saved = await API.get('/saved-reports');
            if (saved && saved.length) {
                // A list, not a wall of cards: thirty saved reports must not
                // push the Report Center off the screen. Collapsed by default
                // once there are more than a handful; the choice sticks.
                let collapsed = false;
                try { collapsed = localStorage.getItem('sb_saved_reports_collapsed') === '1'; } catch (e) { /* ignore */ }
                if (saved.length > 6 && localStorage.getItem('sb_saved_reports_collapsed') === null) collapsed = true;
                const period = (p) => !p ? '' : (p.as_of_date ? `as of ${escapeHtml(p.as_of_date)}` : (p.start_date ? `${escapeHtml(p.start_date)} → ${escapeHtml(p.end_date || '')}` : (p.period ? escapeHtml(String(p.period).replace(/_/g, ' ')) : '')));
                const names = await ReportsPage._savedNames(saved);
                const rows = saved.slice().sort((a, b) => a.name.localeCompare(b.name)).map(s => `
                    <tr class="saved-report-row">
                        <td><a href="javascript:void(0)" onclick="ReportsPage.openSaved(${s.id})" style="font-weight:600;">${escapeHtml(s.name)}</a></td>
                        <td>${escapeHtml(ReportsPage._savedTitle(s, names))}</td>
                        <td style="color:var(--text-muted);">${period(s.parameters)}</td>
                        <td class="actions">
                            <button class="btn btn-sm btn-secondary" onclick="ReportsPage.openSaved(${s.id})">Open</button>
                            <button data-destructive class="btn btn-sm btn-secondary" aria-label="Delete saved report" onclick="ReportsPage.deleteSaved(${s.id})">Delete</button>
                        </td>
                    </tr>`).join('');
                savedHtml = `
                    <div style="display:flex; align-items:center; gap:10px; margin:0 0 8px;">
                        <button type="button" class="btn btn-sm btn-secondary" id="saved-reports-toggle" aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="saved-reports-list" onclick="ReportsPage.toggleSaved()">${collapsed ? '▸' : '▾'}</button>
                        <h3 style="font-size:13px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted); margin:0;">Saved Reports (${saved.length})</h3>
                        ${saved.length > 8 ? `<input type="text" id="saved-reports-filter" placeholder="Filter…" style="width:160px;" oninput="ReportsPage.filterSaved(this.value)">` : ''}
                    </div>
                    <div id="saved-reports-list" ${collapsed ? 'hidden' : ''} style="margin-bottom:20px;">
                        <div class="table-container"><table>
                            <thead><tr><th scope="col">Name</th><th scope="col">Report</th><th scope="col">Period</th><th scope="col">Actions</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table></div>
                    </div>`;
            }
        } catch (e) { /* render anyway */ }

        // The pinned cards (#395): the preference report_center_pins, read
        // beside the saved reports; none, or a failed read, is the fixed
        // order. A name no view has (a nonprofit's, in a business) is left out.
        ReportsPage._pins = [];
        try {
            const pref = await API.get('/preferences/report_center_pins');
            const pinned = pref && pref.value && pref.value.pinned;
            if (Array.isArray(pinned)) ReportsPage._pins = pinned.filter(v => typeof v === 'string' && ReportsPage._view(v));
        } catch (e) { /* the fixed order */ }

        return `
            <div class="page-header"><h2>Reports</h2></div>
            ${savedHtml}
            <div class="card-grid">${ReportsPage._cardGrid()}</div>`;
    },

    // The views pinned to the top of the grid, in the order they were pinned
    // (render reads them; togglePin writes them).
    _pins: [],

    // The Report Center's cards in their fixed order, each as its view and
    // its html (ReportsPage._pick): the nonprofit's own statements lead in
    // place of the P&L and Balance Sheet.
    _cards() {
        const card = ReportsPage._pick;
        const cards = [
            ...(Terms.isNonprofit() ? ReportsPage._nonprofitCards() : [card('profit-loss', T('Profit & Loss'), Terms.text('Income vs expenses for a period'))]),
            card('profit-loss-by-class', T('P&L by Class'), Terms.text('Income vs expenses split by class')),
            card('profit-loss-unclassified', `${T('P&L')} Unclassified`, Terms.text('Income and expenses with no class yet — the end-of-month cleanup list')),
            card('job-budget-vs-actual', T('Job Budget vs Actual'), Terms.text('Budget, committed, actual, projected, variance per job')),
            card('job-profitability', T('Job Profitability'), Terms.text('Income, costs and margin per job')),
            card('profit-loss-by-job', `${T('P&L')} by ${T('Job')}`, Terms.text('Every account down the side, a column per job')),
            card('financial-statements', 'Financial Statements Pack (PDF)', Terms.text('Profit & Loss + Balance Sheet + Trial Balance, one audit-ready PDF')),
            card('fixed-asset-reconciliation', 'Fixed Asset Reconciliation', 'Register totals vs GL by asset type'),
            card('reconciliation-reports', 'Reconciliation Reports', 'Each completed bank reconciliation by account and statement date, on screen or as PDF'),
            ...(Terms.isNonprofit() ? [] : [card('balance-sheet', T('Balance Sheet'), Terms.text('Assets, liabilities, and equity'))]),
            card('ar-aging', T('A/R Aging'), 'Outstanding receivables by age'),
            card('ap-aging', 'A/P Aging', 'Outstanding payables by age'),
            card('sales-tax', 'Sales Tax', 'Tax collected by invoice'),
            card('general-ledger', 'General Ledger', 'All journal entries by account'),
            card('income-by-customer', T('Income by Customer'), Terms.isNonprofit() ? 'Contribution totals per donor' : 'Sales totals per customer'),
            card('customer-statement', T('Customer Statement'), Terms.text('Invoice/payment history PDF')),
            card('trial-balance', 'Trial Balance', 'Debits and credits by account'),
            card('adjusting-entries', 'Adjusting Journal Entries', 'The accountant\'s adjustments for a period, with who posted each'),
            card('closing-date-exceptions', 'Closing Date Exceptions', 'Every change made into a closed period with the password, before and after'),
            card('accountants-package', "Accountant's Package", 'One zip: statements, ledger, agings, reconciliations, adjustments, exceptions and the audit log'),
            card('reclassify', 'Reclassify Transactions', 'Move many lines from one account to another, or onto a class, as one audited change'),
            card('possible-duplicates', 'Possible Duplicates', 'Bank and card entries that look entered twice: same amount, same or similar payee, a few days apart'),
            card('cash-flow', 'Cash Flow', 'Operating, investing, financing'),
            card('1099-summary', '1099 Summary', 'Vendor payments for 1099 filing'),
            card('budget-vs-actual', 'Budget vs Actual', 'Monthly budget variance analysis'),
        ];
        // Construction leads with the contractor reports (the job views the
        // trade lives in), pulled to the front in their issue order; the
        // rest keep their fixed order. Committed Costs and Job Cost by
        // Vendor aren't standalone cards yet, so only the three that exist
        // lead. Mirrors the nonprofit swap above.
        if (Terms.isConstruction()) {
            const lead = ['job-profitability', 'job-budget-vs-actual', 'profit-loss-by-job'];
            const front = lead.map(v => cards.find(c => c.view === v)).filter(Boolean);
            return [...front, ...cards.filter(c => !lead.includes(c.view))];
        }
        return cards;
    },

    // A card and its view, for arranging; the html is ReportsPage._card's.
    _pick(view, title, text) {
        return { view, html: ReportsPage._card(view, title, text) };
    },

    // The grid: the pinned cards first, in the order they were pinned, then
    // the rest in their fixed order (#395).
    _cardGrid() {
        const cards = ReportsPage._cards();
        const pinned = ReportsPage._pins.map(v => cards.find(c => c.view === v)).filter(Boolean);
        return [...pinned, ...cards.filter(c => !pinned.includes(c))].map(c => c.html).join('');
    },

    // Pin or unpin a card (#395): the list is saved as the preference
    // report_center_pins ({ pinned: [view, …] }), the grid redrawn with
    // focus back on the same star. A refused save (a read-only role: the
    // star is data-write, hidden for one, and the server refuses the PUT)
    // puts the list back and says so.
    async togglePin(view) {
        const was = ReportsPage._pins.slice();
        ReportsPage._pins = was.includes(view) ? was.filter(v => v !== view) : [...was, view];
        try {
            await API.put('/preferences/report_center_pins', { value: { pinned: ReportsPage._pins } });
        } catch (err) {
            ReportsPage._pins = was;
            toast(err.message, 'error');
            return;
        }
        const grid = document.querySelector('#page-content .card-grid');
        if (!grid) return;
        grid.innerHTML = ReportsPage._cardGrid();
        const star = grid.querySelector(`.card-pin[data-pin="${view}"]`);
        if (star) star.focus();
    },

    // A report's card on the Report Center. The cards were a <div> with a
    // click alone: neither the keyboard nor a screen reader could open a
    // report from here (review). Each is a link to its report's own address
    // (its view, with the view's own default period), so Tab reaches it
    // and Enter opens it, Space too, as on a button; named by its title as
    // written (the heading's capitals are the stylesheet's, and reached the
    // name), the line under it its description; and its key, noted on its
    // click (ReportsPage._leaveFrom), puts focus back on it when Back
    // returns here. It looks as the card did (.card-link). Its pin (#395)
    // is a star beside the link, not in it (a button in a link is neither):
    // a toggle button, named for the card, pressed while pinned, reached by
    // Tab after the card; the next Tab is the next card.
    _card(view, title, text) {
        const id = `report-card-${view}`;
        const pinned = ReportsPage._pins.includes(view);
        return `<div class="card-slot">
                <a class="card card-link" href="${ReportsPage.viewUrl(view, {})}" data-row-key="report:${view}"
                    aria-label="${escapeHtml(title)}" aria-describedby="${id}-text"
                    onclick="ReportsPage._leaveFrom(this)" onkeydown="ReportsPage._cardKey(event)">
                    <div class="card-header">${title}</div>
                    <p id="${id}-text" style="font-size:13px; color:var(--gray-500);">${text}</p>
                </a>
                <button type="button" class="card-pin" data-write data-pin="${view}" aria-pressed="${pinned}"
                    aria-label="Pin ${escapeHtml(title)}" title="${pinned ? 'Unpin' : 'Pin to the top'}"
                    onclick="ReportsPage.togglePin('${view}')">${pinned ? '★' : '☆'}</button>
            </div>`;
    },

    // Space on a card opens it, as Enter does (a link takes Enter alone).
    _cardKey(e) {
        if (e.key !== ' ' || e.altKey || e.ctrlKey || e.metaKey) return;
        e.preventDefault();
        if (!e.repeat) e.currentTarget.click();
    },

    // ----- Saved Reports (Phase 11) -----

    // A saved report's row names its view by the view's own title (the
    // "Back to …" label, in the company's vocabulary) and, from the saved
    // parameters, what it was saved on: "Profit & Loss — Lighting",
    // "Drill-down — Checking · Lighting", "P&L by Job — No job", "P&L by
    // Class — 2 classes chosen". The type's code ("profit loss class",
    // "account transactions") said neither (NEW-27).
    _savedTitle(s, names) {
        const type = String(s.report_type || '');
        const view = ReportsPage._view(type.replace(/_/g, '-'));
        const label = view
            ? (typeof view.label === 'function' ? view.label() : view.label)
            : Terms.text(type.replace(/_/g, ' '));
        const p = s.parameters || {};
        const given = (v) => v !== undefined && v !== null && v !== '';
        const parts = [];
        if (given(p.account_id)) parts.push(names.account(p.account_id));
        if (given(p.class_id)) parts.push(names.cls(p.class_id));
        if (given(p.job_id)) parts.push(`${T('Job')}: ${names.job(p.job_id)}`);
        for (const [key, one, many] of [['class_ids', names.cls, T('classes')], ['job_ids', names.job, T('jobs')]]) {
            const ids = String(p[key] || '').split(',').filter(Boolean);
            if (ids.length === 1) parts.push(one(ids[0]));
            else if (ids.length) parts.push(`${ids.length} ${many} chosen`);
        }
        return parts.length ? `${label} — ${parts.join(' · ')}` : label;
    },

    // The names the saved rows need, asked for once, and only when a row
    // has something to name; a name since deleted falls back to the id.
    async _savedNames(saved) {
        const has = (key) => saved.some(s => s.parameters && s.parameters[key] !== undefined && s.parameters[key] !== null && s.parameters[key] !== '');
        const lists = { cls: [], account: [], job: [] };
        try {
            [lists.cls, lists.account, lists.job] = await Promise.all([
                has('class_id') || has('class_ids') ? API.get('/classes?include_archived=true').catch(() => []) : [],
                has('account_id') ? API.get('/accounts').catch(() => []) : [],
                has('job_id') || has('job_ids') ? API.get('/jobs?include_inactive=true').catch(() => []) : [],
            ]);
        } catch (e) { /* the ids stand */ }
        const find = (list, id) => list.find(x => String(x.id) === String(id));
        return {
            cls: (id) => { const c = find(lists.cls, id); return c ? c.name : `${T('class')} #${id}`; },
            account: (id) => { const a = find(lists.account, id); return a ? a.name : `account #${id}`; },
            job: (id) => { if (String(id) === '0') return Terms.text('No job'); const j = find(lists.job, id); return j ? j.name : `${T('job')} #${id}`; },
        };
    },

    toggleSaved() {
        const list = $('#saved-reports-list');
        const btn = $('#saved-reports-toggle');
        if (!list || !btn) return;
        const nowHidden = !list.hidden;
        list.hidden = nowHidden;
        btn.textContent = nowHidden ? '▸' : '▾';
        btn.setAttribute('aria-expanded', nowHidden ? 'false' : 'true');
        try { localStorage.setItem('sb_saved_reports_collapsed', nowHidden ? '1' : '0'); } catch (e) { /* ignore */ }
    },

    filterSaved(q) {
        const needle = (q || '').trim().toLowerCase();
        $$('.saved-report-row').forEach(tr => { tr.hidden = needle !== '' && !tr.textContent.toLowerCase().includes(needle); });
    },

    async openSaved(id) {
        try {
            const all = await API.get('/saved-reports');
            const saved = all.find(s => s.id === id);
            if (!saved) { toast('Saved report not found', 'error'); return; }
            // Through the view's address, as a bookmark would open it: the
            // report is then on the address bar and a step back in history.
            const name = String(saved.report_type).replace(/_/g, '-');
            if (!ReportsPage._view(name)) {
                toast(`No opener registered for "${saved.report_type}"`, 'error');
                return;
            }
            await App.navigate(ReportsPage.viewUrl(name, saved.parameters || {}));
        } catch (err) { toast(err.message || 'Failed to open', 'error'); }
    },

    async saveCurrent(reportType, params) {
        const name = prompt('Name for this saved report:');
        if (!name || !name.trim()) return;
        try {
            await API.post('/saved-reports', {
                name: name.trim(),
                report_type: reportType,
                parameters: params || {},
            });
            toast(`Saved as "${name.trim()}" — it is listed under Saved Reports at the top of the Report Center`);
            // Refresh the page so the new one shows in the Saved section
            App.navigate(location.hash);
        } catch (err) { toast(err.message || 'Save failed', 'error'); }
    },

    async deleteSaved(id) {
        if (!confirm('Delete this saved report?')) return;
        try {
            await API.del(`/saved-reports/${id}`);
            toast('Deleted');
            App.navigate(location.hash);
        } catch (err) { toast(err.message || 'Delete failed', 'error'); }
    },

    // ----- Drill-down (Phase 11) -----
    // Hits /api/reports/account-transactions for one account in the date
    // range and shows the journal entries that rolled up into the row the
    // user clicked. Each entry's source_link routes to the originating
    // invoice / bill / payment / journal entry.
    // With `classId`, only that class's lines (#213). `from` names the view
    // that opened it (a ReportsPage._VIEWS key: 'profit-loss',
    // 'general-ledger' (#224), 'profit-loss-by-class', …): the "Back to …"
    // button returns there for the same dates, and the browser's Back does
    // the same. The view has an address of its own
    // (#/reports/account-transactions?account_id=…&from=…), so it is
    // rebuilt from the address after a reload, or on Back from a source
    // document (R7): the names are then taken from what the server says.
    // `extra` carries a job (#242): { job_id, job_name }, job_id 0 being
    // the lines with no job; the server names the job when only the id
    // is known; `extra.period` is the address's period preset (#241).
    async openDrillDown(accountId, accountName, startDate, endDate, classId = null, className = null, from = null, extra = {}) {
        accountId = parseInt(accountId, 10);
        if (!accountId) { toast('No account_id on this row', 'error'); return; }
        // A Balance Sheet line gives only its as-of date: the list starts on
        // January 1 of that date's year, not of this year — a date in an
        // earlier year (a prior-year comparison) otherwise has its start
        // after its end, and the dates are refused.
        if (!startDate && /^\d{4}-\d{2}-\d{2}$/.test(endDate || '')) startDate = `${endDate.slice(0, 4)}-01-01`;
        classId = classId ? parseInt(classId, 10) || null : null;
        const jobGiven = extra && extra.job_id !== undefined && extra.job_id !== null && extra.job_id !== '';
        const jobId = jobGiven ? parseInt(extra.job_id, 10) : null;
        const jobOn = jobGiven && !Number.isNaN(jobId);
        let jobName = (extra && extra.job_name) || null;
        const period = (extra && extra.period) || null;
        if (classId && !from) from = 'profit-loss-by-class';
        if (jobOn && !from) from = 'profit-loss-by-job';
        const back = ReportsPage._view(from) ? from : null;
        // The class's own page (#/classes/:id, #234) opens a drill-down for
        // its class too: `from=classes` names it as the way back, so the
        // button says "Back to Site Prep" and returns to the page as it was
        // left, not to a report the user never opened.
        const backPage = !back && from === 'classes' && classId ? `/classes/${classId}` : null;
        // The address's params ahead of the dates: the account, the class
        // and the way back. The toolbar's selects change the first two in
        // place (ReportsPage.setParam), and a saved drill-down keeps all
        // three with its period (#241).
        // (The period and dates fill their places, so the address reads
        // account, dates, class, from — as a link to it is written.)
        const params = { account_id: accountId, period: undefined, start_date: undefined, end_date: undefined, class_id: classId, job_id: jobOn ? String(jobId) : undefined, from: back || (backPage ? 'classes' : undefined) };
        ReportsPage._leaveFrom();
        // The account and class lists are asked for afresh each time the
        // drill-down opens: a class made, or an account first posted to,
        // since the last one is then in the selects and the Previous/Next
        // walk (R11 review). Within one open they are kept per key.
        ReportsPage._drillList = null;
        ReportsPage._classList = null;

        // The way back carries the dates the view was on (one as-of date
        // for a balance sheet) and whatever the view says it keeps. Back to
        // a class's own P&L says which class ("Back to Profit & Loss —
        // Side Gig"): the same words as the company P&L would mislead.
        let backBtn = '';
        const backToClass = back === 'profit-loss-class' && !!classId;
        if (back) {
            const v = ReportsPage._view(back);
            const backParams = v.asOf ? { as_of_date: endDate } : { start_date: startDate, end_date: endDate };
            if ((v.keep || []).includes('class_id') && classId) backParams.class_id = classId;
            // Back to the P&L or Balance Sheet returns to the dates and
            // choices it was showing — a prior-year drill-down runs on last
            // year's dates, which are not the report's own.
            if (ReportsPage._viewReturn[back]) Object.assign(backParams, ReportsPage._viewReturn[back]);
            backBtn = ReportsPage._backButton(back, backParams, backToClass ? (className || T('Class')) : '');
        } else if (backPage) {
            backBtn = ReportsPage._backPageButton('classes', backPage, { start_date: startDate, end_date: endDate }, className || T('Class'));
        }

        const title = (name, cls) => `Drill-down — ${name || `account ${accountId}`}${cls ? ` · ${cls}` : ''}${jobOn && jobName ? ` · ${T('Job')}: ${jobName}` : ''}`;
        // The toolbar beside the dates: the class, the account (the
        // report's accounts, in its order, so Previous/Next walk the report)
        // and the way back. Filled from the server on each render.
        const toolbar = `
            <div class="form-group"><label for="drill-class">${T('Class')}</label>
                <select id="drill-class" data-no-search onchange="ReportsPage.setParam('class_id', this.value)">
                    <option value="">All ${T('classes')}</option>
                    ${classId ? `<option value="${classId}" selected>${escapeHtml(className || T('Class'))}</option>` : ''}
                </select></div>
            <div class="form-group"><label for="drill-account">Account</label>
                <select id="drill-account" data-no-search onchange="ReportsPage.setParam('account_id', this.value)">
                    <option value="${accountId}" selected>${escapeHtml(accountName || `account ${accountId}`)}</option>
                </select></div>
            <div style="grid-column:1 / -1; display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                <button type="button" class="btn btn-sm btn-secondary" id="drill-prev" aria-label="Previous account" onclick="ReportsPage._drillStep(-1)">‹ Previous</button>
                <button type="button" class="btn btn-sm btn-secondary" id="drill-next" aria-label="Next account" onclick="ReportsPage._drillStep(1)">Next ›</button>
                <span id="drill-position" class="grid-live" role="status" aria-live="polite" style="font-size:11px; color:var(--text-muted);"></span>
            </div>`;

        await ReportsPage.openPeriodModal(title(accountName, className), 'this_year_to_date', async (_period, range) => {
            const q = new URLSearchParams({ account_id: params.account_id, start_date: range.start, end_date: range.end });
            if (params.class_id) q.set('class_id', params.class_id);
            if (params.job_id !== undefined && params.job_id !== null && params.job_id !== '') q.set('job_id', params.job_id);
            // The report's account list first (kept per dates and class, so
            // once), then the transactions: the one request a watcher of
            // this view sees last is the view's own.
            const accounts = await ReportsPage._drillAccounts(back, range, params.class_id);
            const data = await API.get(`/reports/account-transactions?${q.toString()}`);
            if (jobOn && !jobName) jobName = data.job_name;
            // the title reads as the row that opened it (a statement's row
            // says "4000 - Service Income", W-8) while it shows that account;
            // once the toolbar re-scopes it, the server's name
            $('#modal-title').textContent = title(accountName && data.account.id === accountId ? accountName : data.account.name, data.class_name);
            // Rebuilt from the address, the class's name arrives now
            if (backToClass && !className && data.class_name) {
                const btn = $('#modal-body [data-back-to="profit-loss-class"]');
                if (btn) btn.textContent = ReportsPage._backText(back, data.class_name);
            }
            if (backPage && !className && data.class_name) {
                const btn = $('#modal-body [data-back-to="classes"]');
                if (btn) btn.textContent = `Back to ${data.class_name}`;
            }
            await ReportsPage._fillDrillToolbar(data, accounts);

            // Every field the register sends: payee, the cleared mark (✓
            // cleared, R reconciled), a void struck through and badged; a
            // line with no document of its own opens its journal entry, as
            // the bank register does.
            const rows = (data.entries || []).map(e => {
                const link = e.source_link || (e.transaction_id ? `/#/journal/${e.transaction_id}` : null);
                // The line is named in the app's words and numbered by the
                // document its link opens (the last segment of the address):
                // an expense's source_id is its vendor, so two of Deb's
                // expenses both read "expense #6" (NEW-40).
                const m = link ? /\/(\d+)$/.exec(link) : null;
                const num = m ? m[1] : e.transaction_id;
                // The line's key is noted on the way out (ReportsPage._leaveFrom)
                // so that Back from the document puts focus on this link,
                // not on the period select (W-7).
                const src = link
                    ? `<a href="${escapeHtml(link)}" data-row-key="line:${escapeHtml(String(e.line_id || e.transaction_id || ''))}" onclick="ReportsPage._leaveFrom(this)" style="color:var(--text-link); text-decoration:none;">${escapeHtml(sourceWord(e.source_type || 'journal'))} #${num}</a>`
                    : (e.source_type ? escapeHtml(sourceWord(e.source_type)) : '');
                const mark = e.reconciliation_id ? 'R' : (e.cleared ? '✓' : '');
                return `<tr${e.voided ? ' class="row--void" style="color:var(--text-muted); text-decoration:line-through;"' : ''}>
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td>${escapeHtml(e.description || '')}</td>
                    <td>${src}${e.voided ? ' <span class="badge badge-void" style="text-decoration:none;">void</span>' : ''}</td>
                    <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ''}</td>
                    <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ''}</td>
                    <td class="amount">${formatCurrency(e.running_balance)}</td>
                    <td style="text-align:center;">${mark ? `<span title="${mark === 'R' ? 'Reconciled' : 'Cleared'}">${mark}</span>` : ''}</td>
                </tr>`;
            }).join('');
            const entries = data.entries || [];
            const closing = entries.length ? entries[entries.length - 1].running_balance : data.opening_balance;
            // Save CSV / Save PDF, as the other reports have (#333): the
            // account and range on the screen, the class and job with them
            const exportQs = q.toString();
            return `<div id="drilldown-body" style="font-size:11px; color:var(--gray-500);">
                ${ReportsPage._exportButtons('account-transactions', exportQs)}
                <p style="margin-bottom:8px; color:var(--gray-500); font-size:12px;">
                    ${escapeHtml(data.account.number || '')} · ${escapeHtml(data.account.name)}
                    ${data.class_name ? `&middot; ${T('Class')}: <strong>${escapeHtml(data.class_name)}</strong>` : ''}
                    ${jobOn && data.job_name ? `&middot; ${T('Job')}: <strong>${escapeHtml(data.job_name)}</strong>` : ''}
                    &middot; ${formatDate(data.start_date)} → ${formatDate(data.end_date)}
                    &middot; Opening: <strong>${formatCurrency(data.opening_balance)}</strong>
                    &middot; Net: <strong>${formatCurrency(data.period_net)}</strong>
                </p>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Ref</th><th scope="col">Description</th><th scope="col">Source</th>
                        <th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Running</th>
                        <th scope="col" aria-label="Cleared" title="✓ cleared · R reconciled">✓</th>
                    </tr></thead>
                    <tbody>
                        <tr style="color:var(--text-muted);"><td></td><td colspan="6">Balance brought forward</td><td class="amount">${formatCurrency(data.opening_balance)}</td><td></td></tr>
                        ${rows || '<tr><td colspan="9" style="text-align:center; color:var(--gray-400);">No entries in range</td></tr>'}
                        <tr style="font-weight:600; background:var(--gray-50);">
                            <td colspan="5">Period total</td>
                            <td class="amount">${formatCurrency(data.period_debit)}</td>
                            <td class="amount">${formatCurrency(data.period_credit)}</td>
                            <td class="amount">${formatCurrency(closing)}</td><td></td>
                        </tr>
                    </tbody>
                </table></div></div>`;
        }, 'Dates', false, { reportType: 'account_transactions', view: 'account-transactions', params, toolbar, actions: backBtn, wide: true, prefill: { period, start_date: startDate, end_date: endDate } });
    },

    // The accounts the drill-down steps through: the report's own, in its
    // order, for the dates and class in use; every active account when it
    // came from no report (a vendor's page, a saved report). Kept for the
    // same key so a render asks once.
    _drillList: null,
    async _drillAccounts(from, range, classId) {
        const key = `${from}|${range.start}|${range.end}|${classId || ''}`;
        if (ReportsPage._drillList && ReportsPage._drillList.key === key) return ReportsPage._drillList.list;
        const dates = `start_date=${range.start}&end_date=${range.end}`;
        const label = (a) => `${a.account_number ? a.account_number + ' - ' : ''}${a.account_name || a.name}`;
        const pick = (groups) => groups.flat().filter(a => a && a.account_id).map(a => ({ id: a.account_id, label: label(a) }));
        let list = [];
        try {
            let d;
            switch (from) {
                case 'profit-loss': case 'profit-loss-class': case 'profit-loss-by-class':
                    d = await API.get(`/reports/profit-loss?${dates}${classId ? `&class_id=${classId}` : ''}`);
                    list = pick([d.income, d.cogs, d.expenses]); break;
                case 'balance-sheet':
                    d = await API.get(`/reports/balance-sheet?as_of_date=${range.end}`);
                    list = pick([d.assets, d.liabilities, d.equity]); break;
                case 'statement-of-financial-position':
                    d = await API.get(`/reports/statement-of-financial-position?as_of_date=${range.end}`);
                    list = pick([d.assets, d.liabilities, d.net_assets]); break;
                case 'general-ledger':
                    d = await API.get(`/reports/general-ledger?${dates}`);
                    list = pick([d.accounts]); break;
                case 'trial-balance':
                    d = await API.get(`/reports/trial-balance?${dates}`);
                    list = pick([d.items]); break;
                case 'cash-flow':
                    d = await API.get(`/reports/cash-flow?${dates}`);
                    list = pick([d.adjustments || [], d.working_capital || [], d.investing, d.financing]); break;
                default:
                    d = await API.get('/accounts');
                    list = d.map(a => ({ id: a.id, label: `${a.account_number ? a.account_number + ' - ' : ''}${a.name}` }));
            }
        } catch (e) { list = []; }
        ReportsPage._drillList = { key, list };
        return list;
    },

    // The toolbar's selects, from what the server said: the account list
    // with the one shown (added if the report lacks it), the classes
    // (archived ones included, so the one shown is there), and
    // Previous/Next disabled at the ends. Where in the walk, "3 of 23:
    // 1000 - Checking", is a status (role=status, aria-live), so a screen
    // reader hears where Next landed, as it hears the grid's "Column n of
    // m" (macOS gate NEW-39): the title changes with it but is not live.
    _classList: null,
    async _fillDrillToolbar(data, accounts) {
        const acct = $('#drill-account');
        const cls = $('#drill-class');
        if (!acct || !cls) return;
        const list = accounts.some(a => a.id === data.account.id) ? accounts
            : [{ id: data.account.id, label: `${data.account.number ? data.account.number + ' - ' : ''}${data.account.name}` }, ...accounts];
        acct.innerHTML = list.map(a => `<option value="${a.id}" ${a.id === data.account.id ? 'selected' : ''}>${escapeHtml(a.label)}</option>`).join('');
        const i = list.findIndex(a => a.id === data.account.id);
        const prev = $('#drill-prev'), next = $('#drill-next'), pos = $('#drill-position');
        const focused = document.activeElement;
        if (prev) prev.disabled = i <= 0;
        if (next) next.disabled = i < 0 || i >= list.length - 1;
        // A button disabled under the keyboard (Next at the last account)
        // drops focus to the page; the other one takes it instead.
        if (focused === next && next.disabled && prev && !prev.disabled) focusFor(prev, _byKeyboard());
        else if (focused === prev && prev.disabled && next && !next.disabled) focusFor(next, _byKeyboard());
        if (pos) pos.textContent = list.length > 1 && i >= 0 ? `${i + 1} of ${list.length}: ${list[i].label}` : '';
        if (!ReportsPage._classList) {
            try { ReportsPage._classList = await API.get('/classes?include_archived=true'); } catch (e) { ReportsPage._classList = []; }
        }
        const classes = ReportsPage._classList.slice();
        if (data.class_id && !classes.some(c => c.id === data.class_id)) classes.push({ id: data.class_id, name: data.class_name });
        cls.innerHTML = `<option value="">All ${T('classes')}</option>`
            + classes.map(c => `<option value="${c.id}" ${c.id === data.class_id ? 'selected' : ''}>${escapeHtml(c.name)}${c.is_archived ? ' (archived)' : ''}</option>`).join('');
    },

    // Previous / Next account: the select's neighbour, applied in place.
    _drillStep(dir) {
        const sel = $('#drill-account');
        if (!sel) return;
        const i = sel.selectedIndex + dir;
        if (i < 0 || i >= sel.options.length) return;
        sel.selectedIndex = i;
        ReportsPage.setParam('account_id', sel.value);
    },

    periodOptions(selected) {
        const options = [
            ["this_month", "This Month"],
            ["this_quarter", "This Quarter"],
            ["this_year", "This Year"],
            ["this_year_to_date", "This Year to Date"],
            ["last_month", "Last Month"],
            ["last_quarter", "Last Quarter"],
            ["last_year", "Last Year"],
            ["last_year_to_date", "Last Year to Date"],
            ["custom", "Custom Date"],
        ];
        return options.map(([value, label]) =>
            `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
        ).join("");
    },

    _pad(value) {
        return String(value).padStart(2, "0");
    },

    _isoDate(dateObj) {
        return `${dateObj.getFullYear()}-${ReportsPage._pad(dateObj.getMonth() + 1)}-${ReportsPage._pad(dateObj.getDate())}`;
    },

    _quarterStart(monthIndex) {
        return Math.floor(monthIndex / 3) * 3;
    },

    getDateRange(period, customStart = null, customEnd = null) {
        const today = new Date();
        const year = today.getFullYear();
        const month = today.getMonth();
        const day = today.getDate();
        let start;
        let end;

        switch (period) {
            case "this_month":
                start = new Date(year, month, 1);
                end = new Date(year, month + 1, 0);
                break;
            case "this_quarter": {
                const qStart = ReportsPage._quarterStart(month);
                start = new Date(year, qStart, 1);
                end = new Date(year, qStart + 3, 0);
                break;
            }
            case "this_year":
                start = new Date(year, 0, 1);
                end = new Date(year, 11, 31);
                break;
            case "this_year_to_date":
                start = new Date(year, 0, 1);
                end = today;
                break;
            case "last_month":
                start = new Date(year, month - 1, 1);
                end = new Date(year, month, 0);
                break;
            case "last_quarter": {
                const thisQuarterStart = ReportsPage._quarterStart(month);
                start = new Date(year, thisQuarterStart - 3, 1);
                end = new Date(year, thisQuarterStart, 0);
                break;
            }
            case "last_year":
                start = new Date(year - 1, 0, 1);
                end = new Date(year - 1, 11, 31);
                break;
            case "last_year_to_date":
                start = new Date(year - 1, 0, 1);
                end = new Date(year - 1, month, Math.min(day, new Date(year - 1, month + 1, 0).getDate()));
                break;
            case "custom":
                return {
                    start: customStart || ReportsPage._isoDate(new Date(year, 0, 1)),
                    end: customEnd || ReportsPage._isoDate(today),
                };
            default:
                start = new Date(year, 0, 1);
                end = today;
                break;
        }

        return {
            start: ReportsPage._isoDate(start),
            end: ReportsPage._isoDate(end),
        };
    },

    getAsOfDate(period, customEnd = null) {
        if (period === "custom") return customEnd || todayISO();
        return ReportsPage.getDateRange(period).end;
    },

    customRangeHtml(initialStart, initialEnd) {
        return `
            <div id="report-custom-range" style="display:none; margin:4px 0 12px 0; font-size:11px; align-items:center; gap:8px;">
                <label for="report-custom-start">From:</label>
                <input id="report-custom-start" type="date" value="${initialStart}">
                <label for="report-custom-end">To:</label>
                <input id="report-custom-end" type="date" value="${initialEnd}">
            </div>`;
    },

    toggleCustomRange() {
        const select = $("#report-period-select");
        const row = $("#report-custom-range");
        if (!select || !row) return;
        row.style.display = select.value === "custom" ? "flex" : "none";
    },

    async openPeriodModal(title, initialPeriod, loadContent, label = "Dates", useAsOfOnly = false, opts = {}) {
        // opts.reportType (string) — when set, adds an "Add to Saved Reports" button
        // that captures the current period/range as parameters.
        // opts.prefill ({period?, start_date?, end_date?, as_of_date?}) —
        // used when reopening a saved report or a view's address; overrides
        // initialPeriod and pre-populates the date inputs. Dates with no
        // period mean a custom range; a period nobody knows is ignored.
        // opts.view (string) — the view's address name (ReportsPage._VIEWS):
        // each render puts the period and dates on the address bar, with
        // opts.params (an object: class_id, account_id, …) ahead of them.
        // opts.params is kept by reference and read on every render: a
        // control in the toolbar changes it through ReportsPage.setParam
        // and the view redraws on the new address. It rides into a saved
        // report's parameters too, so a class, a job or a column choice
        // reopens with the dates. A function as opts.params is called on
        // every render instead, for a view whose pickers keep their own
        // state (the General Ledger's, #236); setParam has nothing to
        // write to then.
        // opts.toolbar (html) — controls drawn once, beside the period
        // select, that stay as the report redraws (a class picker, Prev /
        // Next, a column chooser); the report's own body is redrawn by
        // loadContent(period, range, params) on every change.
        // opts.actions (html) — buttons ahead of Save and Close (a
        // "Back to …"). opts.wide — the wide dialog (openModal's), for a
        // grid with a column per class or job.
        const reportType = opts.reportType || null;
        // The dates of the address or the saved report: a date that is not
        // one, or From after To (NEW-35), is said so and ignored — the view
        // starts on its usual period — and the address is then rewritten
        // to the dates in use (datesFromQuery). An as-of view has one date,
        // checked below once its box has refused or taken it.
        const prefill = useAsOfOnly ? { ...(opts.prefill || {}) } : { ...(opts.prefill || {}), ...datesFromQuery(opts.prefill) };
        const view = opts.view || null;
        const paramsObj = typeof opts.params === 'function' ? null : (opts.params || {});
        const params = () => (paramsObj ? paramsObj : opts.params());
        ReportsPage._params = paramsObj;

        const currentYear = new Date().getFullYear();
        const defaultCustomStart = prefill.start_date || `${currentYear}-01-01`;
        const defaultCustomEnd = prefill.end_date || prefill.as_of_date || todayISO();
        const known = ReportsPage.periodOptions('').includes(`value="${prefill.period}"`);
        const dated = !!(prefill.start_date || prefill.end_date || prefill.as_of_date);
        const startingPeriod = (prefill.period && known) ? prefill.period : (dated ? 'custom' : initialPeriod);

        const saveBtn = reportType
            ? `<button class="btn btn-secondary" id="report-save-btn" data-write>Add to Saved Reports…</button>`
            : '';

        openModal(title, `
            <div class="form-grid report-toolbar" style="margin-bottom:4px;">
                <div class="form-group">
                    <label for="report-period-select">${label}</label>
                    <select id="report-period-select">${ReportsPage.periodOptions(startingPeriod)}</select>
                </div>
                ${opts.toolbar || ''}
            </div>
            ${ReportsPage.customRangeHtml(defaultCustomStart, defaultCustomEnd)}
            <div id="report-content">
                <div style="font-size:11px; color:var(--gray-500);">Loading report...</div>
            </div>
            <div class="form-actions">
                ${opts.actions || ''}
                ${saveBtn}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`, { wide: !!opts.wide });

        const select = $("#report-period-select");
        const startInput = $("#report-custom-start");
        const endInput = $("#report-custom-end");
        const content = $("#report-content");

        // A pasted as-of date that is not one: the date input refuses it
        // and the default stands. Said, rather than another date quietly
        // shown (R7 review); the address is then rewritten to the date in
        // use, and the box shows it (NEW-24).
        if (useAsOfOnly) {
            const asOfKey = prefill.end_date ? 'end_date' : 'as_of_date';
            if (prefill[asOfKey] && endInput.value !== prefill[asOfKey]) toast(`${asOfKey} in the address is not a date (${prefill[asOfKey]}) — ignored`, 'error');
        }
        if (prefill.period && !known) toast(`period in the address is not one of the choices (${prefill.period}) — ignored`, 'error');

        // Track current params so the Save button captures fresh values.
        let currentParams = {};
        // The range the report last ran on. From after To typed into the
        // boxes is refused — said, and the boxes go back to this range,
        // which the report still shows (NEW-35); on the first render, with
        // no range yet, the view's own period stands in.
        let lastRange = null;
        // The first render of the view: the only one that may move focus
        // to a spotlighted row (ReportsPage._spotlight). opts.spotlight
        // false leaves the row alone altogether — a view the server has
        // already filtered to the customer has no one row to pick out.
        let first = true;

        const render = async () => {
            ReportsPage.toggleCustomRange();
            const custom = select.value === 'custom';
            if (!useAsOfOnly && custom) {
                const keep = lastRange || ReportsPage.getDateRange(initialPeriod);
                if (reversedRangeRefused(startInput.value, endInput.value, keep)) {
                    startInput.value = keep.start;
                    endInput.value = keep.end;
                    if (lastRange) return;  // the report on it is already showing
                }
            }
            content.innerHTML = `<div style="font-size:11px; color:var(--gray-500);">Loading report...</div>`;
            try {
                if (useAsOfOnly) {
                    const asOfDate = ReportsPage.getAsOfDate(select.value, endInput.value || todayISO());
                    // the box shows the date the report runs on (NEW-24)
                    if (custom && endInput.value !== asOfDate) endInput.value = asOfDate;
                    currentParams = { period: select.value, as_of_date: asOfDate };
                    if (view) ReportsPage.setAddress(view, { ...params(), ...currentParams });
                    content.innerHTML = await loadContent(select.value, { as_of_date: asOfDate }, params());
                } else {
                    const range = ReportsPage.getDateRange(select.value, startInput.value, endInput.value);
                    // the boxes show the dates the report runs on: a date the
                    // address had that the box refused is replaced by the
                    // one used, not left blank (NEW-24)
                    if (custom) { startInput.value = range.start; endInput.value = range.end; }
                    lastRange = range;
                    currentParams = { period: select.value, start_date: range.start, end_date: range.end };
                    if (view) ReportsPage.setAddress(view, { ...params(), ...currentParams });
                    content.innerHTML = await loadContent(select.value, range, params());
                }
                const restored = ReportsPage._refocusRow(content);
                if (opts.spotlight !== false) ReportsPage._spotlight(content, params(), first && !restored);
                if (typeof opts.afterRender === 'function') opts.afterRender(content, first);
            } catch (err) {
                content.innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
            }
            first = false;
        };
        // The open report, for its toolbar's controls (ReportsPage.setParam,
        // ReportsPage.refresh): one period dialog is open at a time.
        ReportsPage._live = { view, params: paramsObj, render, select };

        select.addEventListener("change", render);
        startInput.addEventListener("change", () => { if (select.value === "custom" && !useAsOfOnly) render(); });
        endInput.addEventListener("change", () => { if (select.value === "custom") render(); });

        if (reportType) {
            const sb = $("#report-save-btn");
            if (sb) sb.addEventListener("click", () => {
                ReportsPage.saveCurrent(reportType, { ...params(), ...currentParams });
            });
        }

        await render();
    },

    // Redraw the open report on its current period and params: what a
    // toolbar control does after changing something.
    refresh() {
        if (ReportsPage._live) ReportsPage._live.render();
    },

    // Set one of the open report's params (a class, a job, a column
    // choice) and redraw; an empty value removes it from the address.
    setParam(key, value, redraw = true) {
        const live = ReportsPage._live;
        if (!live || !live.params) return;
        if (value === null || value === undefined || value === '') delete live.params[key];
        else live.params[key] = value;
        if (redraw) live.render();
    },

    async profitLoss(prefill) {
        // A P&L saved or addressed with a class is that class's own (#235):
        // the address is replaced by the class view's, so Back is not a
        // loop through the redirect.
        if (prefill && parseInt(prefill.class_id, 10)) {
            if (App.parseHash(location.hash || '#/').path === '/reports/profit-loss') {
                history.replaceState(history.state, '', ReportsPage.viewUrl('profit-loss-class', prefill));
            }
            await ReportsPage.profitLossOfClass(prefill.class_id, null, prefill);
            return;
        }
        // Compare to the same period a year earlier, with the $ and %
        // change columns on or off: toolbar checkboxes whose choices ride on
        // the address, saved reports and the PDF / CSV links.
        const opts = { ...ReportsPage._statementOpts(prefill) };
        const plParams = { ...opts.params, ...ReportsPage._compareParams(prefill) };
        await ReportsPage.openPeriodModal(T("Profit & Loss"), "this_year_to_date", async (_period, range, params) => {
            const cmp = params.compare === 'prior_year';
            const show = !params.hide_changes;
            ReportsPage._cmpSync(params);
            const data = await API.get(`/reports/profit-loss?start_date=${range.start}&end_date=${range.end}${ReportsPage._sortQs(params)}${cmp ? '&compare=prior_year' : ''}`);
            const prior = cmp ? data.prior : null;
            ReportsPage._viewReturn['profit-loss'] = { ...ReportsPage._cmpKeep(params), start_date: range.start, end_date: range.end };
            const pdfBtn = ReportsPage._exportButtons('profit-loss', `start_date=${range.start}&end_date=${range.end}${ReportsPage._sortQs(params)}${ReportsPage._cmpQs(params)}`);
            // Build the onclick payload outside the template so we can
            // HTML-escape the embedded double quotes from JSON.stringify().
            // Otherwise the inner " breaks the outer onclick="…" attribute.
            const drillCall = (i, start, end) => escapeHtml(
                `ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_label || i.account_name)},${JSON.stringify(start)},${JSON.stringify(end)},null,null,'profit-loss')`
            );
            const cols = ReportsPage._cmpCols(prior, show);
            const figures = (cur, before, item) => ReportsPage._cmpCells(cur, before, prior, show,
                item && prior ? drillCall(item, prior.start_date, prior.end_date) : null);
            // Sub-accounts beneath their parents, a total per family (#416):
            // the server's tree for the section, else its flat lines.
            const section = (items, key) => {
                const rows = (data.tree || {})[key];
                if (!items.length && !(rows && rows.length)) return `<tr><td colspan="${cols}" style="color:var(--gray-400);">None</td></tr>`;
                return ReportsPage._treeRows(rows || items, {
                    blank: '<td></td>'.repeat(cols - 1),
                    cells: (r) => figures(r.amount, r.prior_amount, r.kind === 'total' ? null : r),
                    call: (r) => drillCall(r, range.start, range.end),
                });
            };
            const head = (label) => `<tr><td><strong>${label}</strong></td>${'<td></td>'.repeat(cols - 1)}</tr>`;
            const total = (label, key, style) => `<tr style="${style}"><td>${label}</td>${figures(data[key], prior ? prior[key] : 0)}</tr>`;
            const sub = 'font-weight:600; background:var(--gray-50);';
            return `${pdfBtn}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}${prior ? ` &nbsp;vs&nbsp; ${formatDate(prior.start_date)} &mdash; ${formatDate(prior.end_date)}` : ''}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th>${ReportsPage._cmpHead(prior, show)}</tr></thead>
                    <tbody>
                        ${head(T('Income'))}
                        ${section(data.income, 'income')}
                        ${total(T('Total Income'), 'total_operating_income', sub)}
                        ${head('Cost of Goods Sold')}
                        ${section(data.cogs, 'cogs')}
                        ${total('Gross Profit', 'operating_gross_profit', sub)}
                        ${head('Expenses')}
                        ${section(data.expenses, 'expenses')}
                        ${total('Total Expenses', 'total_operating_expenses', sub)}
                        ${ReportsPage._plOther(data, head, section, (label, key) => total(label, key, sub))}
                        ${total(T('Net Income'), 'net_income', 'font-weight:700; font-size:15px; background:var(--primary-light);')}
                    </tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'profit_loss', view: 'profit-loss', prefill, params: plParams, toolbar: opts.toolbar + ReportsPage._compareToolbar(plParams, 'Compare to same period last year'), wide: plParams.compare === 'prior_year' });
    },

    // A statement section's rows, sub-accounts indented beneath their
    // parents (#416): the server's tree rows (kind account / heading /
    // direct / total, with a depth) or a flat list. `opts.cells(r)` writes
    // a row's amount cells, `opts.call(r)` its drill-down onclick (escaped),
    // `opts.blank` the empty cells of a heading. A family's total is not a
    // link: it is several accounts.
    _treeRows(rows, opts) {
        return rows.map(r => {
            const depth = r.depth || 0;
            const pad = 24 + 18 * depth;
            const label = escapeHtml(r.label || r.account_label || r.account_name);
            const link = (text) => r.account_id && opts.call
                ? `<a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;" onclick="${opts.call(r)}">${text}</a>`
                : text;
            if (r.kind === 'heading') {
                return `<tr class="pl-family"><td style="padding-left:${pad}px; font-weight:600;">${label}</td>${opts.blank}</tr>`;
            }
            if (r.kind === 'total') {
                return `<tr class="pl-family-total" style="font-weight:600;"><td style="padding-left:${pad}px;">${label}</td>${opts.cells(r)}</tr>`;
            }
            const text = r.kind === 'direct' ? `<em>${label}</em>` : label;
            return `<tr><td style="padding-left:${pad}px;">${link(text)}</td>${opts.cells(r)}</tr>`;
        }).join('');
    },

    // Other Income / Other Expense below Net Operating Income (#390): only
    // when either has a figure (the server's flags), so a section with just
    // a $0 account prints no heading and a P&L without them reads as it did.
    // `head`, `section` and `total` are the calling view's own row writers.
    _plOther(data, head, section, total) {
        if (!data.has_other) return '';
        let html = total(T('Net Operating Income'), 'net_operating_income');
        if (data.has_other_income) {
            html += head(T('Other Income')) + section(data.other_income, 'other_income') + total(T('Total Other Income'), 'total_other_income');
        }
        if (data.has_other_expense) {
            html += head('Other Expense') + section(data.other_expense, 'other_expense') + total('Total Other Expense', 'total_other_expense');
        }
        return html + total(T('Net Other Income'), 'net_other_income');
    },

    // The accounts' order on the two statements (#326): by number, then
    // name, as the Trial Balance lists them (the server's default), or by
    // name; a sub-account under its parent either way. The choice is a
    // report param, so it rides on the address, into a saved report and
    // into the PDF and CSV, and the toolbar's select is drawn once beside
    // Dates.
    _sortOpts(prefill) {
        const sort = prefill && prefill.sort === 'name' ? 'name' : '';
        const params = sort ? { sort } : {};
        const toolbar = `<div class="form-group">
                    <label for="report-sort-select">Sort by</label>
                    <select id="report-sort-select" data-no-search onchange="ReportsPage.setParam('sort', this.value === 'name' ? 'name' : '')">
                        <option value="number" ${sort ? '' : 'selected'}>Account number</option>
                        <option value="name" ${sort ? 'selected' : ''}>Account name</option>
                    </select>
                </div>`;
        return { params, toolbar };
    },
    _sortQs(params) {
        return (params && params.sort === 'name' ? '&sort=name' : '') + ReportsPage._adjQs(params);
    },
    // "Without adjusting entries" (#341): the statement before the
    // accountant's corrections. Rides on the address and the exports.
    _adjQs(params) {
        return params && params.exclude_adjusting ? '&exclude_adjusting=true' : '';
    },
    _adjOpts(prefill) {
        const on = !!(prefill && (prefill.exclude_adjusting === 'true' || prefill.exclude_adjusting === true || prefill.exclude_adjusting === '1'));
        const params = on ? { exclude_adjusting: 'true' } : {};
        const toolbar = `<div class="form-group">
                    <label style="display:flex; align-items:center; gap:6px; font-weight:400;">
                        <input type="checkbox" id="report-exclude-adjusting" ${on ? 'checked' : ''} onchange="ReportsPage.setParam('exclude_adjusting', this.checked ? 'true' : '')">
                        Without adjusting entries
                    </label>
                </div>`;
        return { params, toolbar };
    },
    // the two statements' toolbar: the sort choice and the adjusting box
    _statementOpts(prefill) {
        const sort = ReportsPage._sortOpts(prefill);
        const adj = ReportsPage._adjOpts(prefill);
        return { params: { ...sort.params, ...adj.params }, toolbar: sort.toolbar + adj.toolbar };
    },

    async balanceSheet(prefill) {
        // Compare to the same date a year earlier, as the P&L does.
        const opts = { ...ReportsPage._sortOpts(prefill) };
        const bsParams = { ...opts.params, ...ReportsPage._compareParams(prefill) };
        await ReportsPage.openPeriodModal(T("Balance Sheet"), "this_year_to_date", async (_period, params, live) => {
            const cmp = live.compare === 'prior_year';
            const show = !live.hide_changes;
            ReportsPage._cmpSync(live);
            const asOf = params.as_of_date;
            const data = await API.get(`/reports/balance-sheet?as_of_date=${asOf}${ReportsPage._sortQs(live)}${cmp ? '&compare=prior_year' : ''}`);
            const prior = cmp ? data.prior : null;
            ReportsPage._viewReturn['balance-sheet'] = { ...ReportsPage._cmpKeep(live), as_of_date: asOf };
            const pdfBtn = ReportsPage._exportButtons('balance-sheet', `as_of_date=${asOf}${ReportsPage._sortQs(live)}${ReportsPage._cmpQs(live)}`);
            const drillCall = (i, date) => escapeHtml(
                `ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_label || i.account_name)},null,${JSON.stringify(date)},null,null,'balance-sheet')`
            );
            const cols = ReportsPage._cmpCols(prior, show);
            const figures = (cur, before, item) => ReportsPage._cmpCells(cur, before, prior, show,
                item && prior && item.account_id ? drillCall(item, prior.as_of_date) : null);
            // A computed line (net income to date) has no account to open.
            const name = (i) => i.account_id
                ? `<a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;"
                       onclick="${drillCall(i, asOf)}">${escapeHtml(i.account_label || i.account_name)}</a>`
                : escapeHtml(i.account_name);
            const section = (items, pad = 24) => items.map(i =>
                `<tr><td style="padding-left:${pad}px;">${name(i)}</td>${figures(i.amount, i.prior_amount, i)}</tr>`
            ).join("") || `<tr><td colspan="${cols}" style="color:var(--gray-400);">None</td></tr>`;
            const head = (label) => `<tr><td><strong>${label}</strong></td>${'<td></td>'.repeat(cols - 1)}</tr>`;
            const total = (label, cur, before, style) => `<tr style="${style}"><td>${label}</td>${figures(cur, before)}</tr>`;
            const sub = 'font-weight:600; background:var(--gray-50);';
            const p = prior || {};
            // Current / Fixed / Other Assets, Current / Long Term
            // Liabilities (#390): the server hands the groups down ready-made.
            const grouped = (side) => {
                const groups = (data.groups || {})[side];
                if (!groups) return section(data[side]);
                if (!groups.length) return section([]);
                return groups.map(g => `<tr class="bs-group"><td style="padding-left:16px; font-weight:600;">${escapeHtml(g.heading)}</td>${'<td></td>'.repeat(cols - 1)}</tr>`
                    + section(g.lines, 36)
                    + `<tr style="font-weight:600;"><td style="padding-left:16px;">Total ${escapeHtml(g.heading)}</td>${figures(data[g.total_key], p[g.total_key])}</tr>`
                ).join('');
            };
            return `${pdfBtn}
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}${prior ? ` &nbsp;vs&nbsp; ${formatDate(prior.as_of_date)}` : ''}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th>${ReportsPage._cmpHead(prior, show)}</tr></thead>
                    <tbody>
                        ${head('Assets')}
                        ${grouped('assets')}
                        ${total('Total Assets', data.total_assets, p.total_assets, sub)}
                        ${head('Liabilities')}
                        ${grouped('liabilities')}
                        ${total('Total Liabilities', data.total_liabilities, p.total_liabilities, sub)}
                        ${head(T('Equity'))}
                        ${section(data.equity)}
                        ${total(T('Total Equity'), data.total_equity, p.total_equity, sub)}
                        ${total(T('Liabilities + Equity'), data.total_liabilities + data.total_equity, (p.total_liabilities || 0) + (p.total_equity || 0), 'font-weight:700; background:var(--primary-light);')}
                    </tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'balance_sheet', view: 'balance-sheet', prefill, params: bsParams, toolbar: opts.toolbar + ReportsPage._compareToolbar(bsParams, 'Compare to same date last year'), wide: bsParams.compare === 'prior_year' });
    },

    async salesTax(prefill) {
        await ReportsPage.openPeriodModal("Sales Tax Report", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/sales-tax?start_date=${range.start}&end_date=${range.end}`);
            // Credit memos come back as negative rows; a document with
            // nothing taxable shows no rate rather than "8.25%, $0.00".
            const rows = data.items.map(i =>
                `<tr>
                    <td>${formatDate(i.date)}</td>
                    <td>${escapeHtml(i.number)}${i.type === 'credit_memo' ? ` <span class="badge" style="font-size:9px">Credit Memo</span>` : ''}</td>
                    <td>${escapeHtml(i.customer_name)}</td>
                    <td class="amount">${formatCurrency(i.subtotal)}</td>
                    <td class="amount">${formatCurrency(i.taxable)}</td>
                    <td class="amount">${i.tax_rate == null ? '—' : SalesLines.taxPercent(i.tax_rate) + '%'}</td>
                    <td class="amount">${formatCurrency(i.tax_amount)}</td>
                </tr>`
            ).join("");
            const ledger = data.ledger;
            const agrees = ledger && Math.abs(ledger.difference) < 0.005;
            const reconcile = ledger ? `
                    <div style="font-size:12px; margin-top:8px; border-top:1px solid var(--gray-200); padding-top:6px;">
                        ${escapeHtml(ledger.account_number)} ${escapeHtml(ledger.account_name)}: tax posted this period <strong>${formatCurrency(ledger.tax_posted)}</strong>
                        ${agrees
                            ? '— agrees with this report.'
                            : `— <span style="color:var(--danger); font-weight:700;">differs from this report by ${formatCurrency(ledger.difference)}</span>. Something other than a sale or credit memo posted to the account in these dates (tax on a bill, a journal entry, a void of an earlier sale).`}
                        <div>Paid this period: ${formatCurrency(ledger.payments)} · Owed at ${formatDate(data.end_date)}: <strong>${formatCurrency(ledger.balance)}</strong></div>
                        ${Math.abs(ledger.purchase_tax_to_date || 0) >= 0.005 ? `<div style="margin-top:6px;">
                            Sales tax paid to suppliers on bills entered before SlowBooks Pro 2.18 was posted to ${escapeHtml(ledger.account_name)}, lowering that balance by <strong>${formatCurrency(ledger.purchase_tax)}</strong> this period; since 2.18 that tax is part of what the purchase cost. To correct it, post one journal entry: debit the expense or cost-of-goods account those purchases used, and credit ${escapeHtml(ledger.account_number)} ${escapeHtml(ledger.account_name)} <strong>${formatCurrency(ledger.purchase_tax_to_date)}</strong> (the total to ${formatDate(data.end_date)}).</div>` : ''}
                    </div>` : '';
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Date</th><th scope="col">${T('Invoice')} / Credit Memo</th><th scope="col">${T('Customer')}</th><th scope="col" class="amount">Sales</th><th scope="col" class="amount">Taxable</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Tax</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="7" style="text-align:center; color:var(--gray-400);">No taxable sales</td></tr>'}</tbody>
                </table></div>
                <div style="margin-top:12px; padding:8px; background:var(--gray-50); border:1px solid var(--gray-200);">
                    <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px;">
                        <span>Total Sales: <strong>${formatCurrency(data.total_sales)}</strong></span>
                        <span>Taxable: <strong>${formatCurrency(data.total_taxable)}</strong></span>
                        <span>Non-Taxable: <strong>${formatCurrency(data.total_non_taxable)}</strong></span>
                    </div>
                    <div style="font-size:12px;">Tax on sales ${formatCurrency(data.tax_on_sales)} less tax on credit memos ${formatCurrency(data.tax_credited)}</div>
                    <div style="font-size:14px; font-weight:700; color:var(--qb-navy);">Tax Collected: ${formatCurrency(data.total_tax)}</div>
                    ${reconcile}
                </div>`;
        }, "Dates", false, { reportType: 'sales_tax', view: 'sales-tax', prefill });
    },

    // The General Ledger's filters (#236): one account, one class, or both,
    // chosen in the report and carried on its address, its exports and a
    // saved report. The class test is P&L by Class's (a line's own class,
    // else its transaction's, else Uncategorized), applied to the balance
    // brought forward as well, so the running balances are that class's.
    _gl: { account_id: '', class_id: '' },
    _glLists: null,

    async generalLedger(prefill) {
        const p = prefill || {};
        ReportsPage._gl = {
            account_id: /^\d+$/.test(p.account_id || '') ? p.account_id : '',
            class_id: /^\d+$/.test(p.class_id || '') ? p.class_id : '',
        };
        ReportsPage._glLists = null;
        await ReportsPage.openPeriodModal("General Ledger", "this_year_to_date", async (_period, range) => {
            if (!ReportsPage._glLists) {
                const [accounts, classes] = await Promise.all([
                    API.get('/accounts').catch(() => []),
                    API.get('/classes?include_archived=true').catch(() => []),
                ]);
                ReportsPage._glLists = { accounts, classes };
            }
            const f = ReportsPage._gl;
            const qs = `start_date=${range.start}&end_date=${range.end}${f.account_id ? `&account_id=${f.account_id}` : ''}${f.class_id ? `&class_id=${f.class_id}` : ''}`;
            const data = await API.get(`/reports/general-ledger?${qs}`);
            const { accounts, classes } = ReportsPage._glLists;
            const acctOpts = accounts.map(a => `<option value="${a.id}"${String(a.id) === f.account_id ? ' selected' : ''}>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}${a.is_active === false ? ' (inactive)' : ''}</option>`).join('');
            const classOpts = classes.map(c => `<option value="${c.id}"${String(c.id) === f.class_id ? ' selected' : ''}>${escapeHtml(c.name)}${c.is_archived ? ' (archived)' : ''}</option>`).join('');
            const cls = data.class_name;
            let html = `
                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px;">
                    <label for="gl-account" style="font-size:11px;">Account</label>
                    <select id="gl-account" data-no-search style="max-width:220px;" onchange="ReportsPage.glFilter('account_id', this.value)"><option value="">All accounts</option>${acctOpts}</select>
                    <label for="gl-class" style="font-size:11px;">${T('Class')}</label>
                    <select id="gl-class" data-no-search style="max-width:200px;" onchange="ReportsPage.glFilter('class_id', this.value)"><option value="">All ${T('classes').toLowerCase()}</option>${classOpts}</select>
                    ${ReportsPage._exportButtons('general-ledger', qs)}
                </div>
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}${cls ? ` &middot; ${T('Class')}: <strong>${escapeHtml(cls)}</strong> &mdash; <span id="gl-class-note">this ${T('class').toLowerCase()}'s lines only; each balance brought forward and running balance counts them alone, not the account's whole balance</span>` : ''}</p>`;
            if (data.accounts.length === 0) {
                html += `<div class="empty-state"><p>No journal entries found</p></div>`;
            } else {
                // An account's name opens its register for these dates, as the
                // P&L's and Balance Sheet's do (#224), for the class shown when
                // one is. The onclick payload is built outside the template and
                // HTML-escaped, as profitLoss does, so JSON.stringify's quotes
                // can't break the attribute.
                // each account's heading: its number, then its path, a
                // sub-account under its parents ("8610 — Automobile: Fuel"),
                // as the statements' rows read (2.23.4 gate, W-12)
                const drillCall = (acct) => escapeHtml(
                    `ReportsPage.openDrillDown(${acct.account_id},${JSON.stringify(acct.account_path || acct.account_name)},${JSON.stringify(range.start)},${JSON.stringify(range.end)},${data.class_id || null},${JSON.stringify(cls || null)},'general-ledger')`
                );
                for (const acct of data.accounts) {
                    const words = acct.account_path || acct.account_name;
                    const name = acct.account_id
                        ? `<a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;" onclick="${drillCall(acct)}">${escapeHtml(words)}</a>`
                        : escapeHtml(words);
                    html += `<h3 style="margin:12px 0 4px; font-size:12px; color:var(--qb-navy);">${escapeHtml(acct.account_number)} &mdash; ${name}</h3>`;
                    html += `<div class="table-container"><table>
                        <thead><tr><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Reference</th><th scope="col">Source</th><th scope="col">${T('Class')}</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Balance</th></tr></thead><tbody>`;
                    html += `<tr style="color:var(--gray-500);"><td></td><td colspan="6">Balance brought forward</td><td class="amount">${formatCurrency(acct.opening_balance)}</td></tr>`;
                    for (const e of acct.entries) {
                        html += `<tr>
                            <td>${formatDate(e.date)}</td>
                            <td>${escapeHtml(e.description)}</td>
                            <td>${escapeHtml(e.reference)}</td>
                            <td style="font-size:10px; color:var(--gray-500);">${escapeHtml(sourceWord(e.source_type))}</td>
                            <td style="font-size:10px;">${escapeHtml(e.class_name || '')}</td>
                            <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ""}</td>
                            <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ""}</td>
                            <td class="amount">${formatCurrency(e.running_balance)}</td>
                        </tr>`;
                    }
                    html += `<tr style="font-weight:600; background:var(--gray-50);">
                        <td colspan="5">Period total</td>
                        <td class="amount">${formatCurrency(acct.total_debit)}</td>
                        <td class="amount">${formatCurrency(acct.total_credit)}</td>
                        <td class="amount">${formatCurrency(acct.closing_balance)}</td>
                    </tr></tbody></table></div>`;
                }
            }
            return html;
        }, "Dates", false, { reportType: 'general_ledger', view: 'general-ledger', params: () => ReportsPage._gl, prefill });
    },

    // A picker changed: the report re-renders on its period, and the
    // address, the exports and a saved report carry the filter (replaced
    // in place, as a period change is).
    glFilter(key, value) {
        ReportsPage._gl[key] = /^\d+$/.test(value || '') ? value : '';
        const select = $('#report-period-select');
        if (select) select.dispatchEvent(new Event('change'));
    },

    async incomeByCustomer(prefill) {
        await ReportsPage.openPeriodModal(T("Income by Customer"), "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/income-by-customer?start_date=${range.start}&end_date=${range.end}`);
            // Sales before tax, the tax beside it; Paid includes money not
            // yet applied to an invoice, and Balance is net of it.
            // A customer's name opens the customer's page (R9)
            let rows = data.items.map(i =>
                `<tr>
                    <td>${i.customer_id ? ReportsPage._rowLink(`ReportsPage.openCustomer(${i.customer_id}, this)`, i.customer_name, `customer:${i.customer_id}`) : escapeHtml(i.customer_name)}</td>
                    <td class="amount">${i.invoice_count}</td>
                    <td class="amount">${formatCurrency(i.total_sales)}</td>
                    <td class="amount">${formatCurrency(i.total_tax || 0)}</td>
                    <td class="amount">${formatCurrency(i.total_paid)}</td>
                    <td class="amount">${formatCurrency(i.total_balance)}</td>
                </tr>`
            ).join("");
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td>TOTAL</td>
                <td class="amount">${data.items.reduce((sum, item) => sum + item.invoice_count, 0)}</td>
                <td class="amount">${formatCurrency(data.total_sales)}</td>
                <td class="amount">${formatCurrency(data.total_tax || 0)}</td>
                <td class="amount">${formatCurrency(data.total_paid)}</td>
                <td class="amount">${formatCurrency(data.total_balance)}</td>
            </tr>`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">${T('Customer')}</th><th scope="col" class="amount">${T('Invoices')}</th><th scope="col" class="amount">Sales</th><th scope="col" class="amount">Sales Tax</th><th scope="col" class="amount">Paid</th><th scope="col" class="amount">Balance</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No sales data</td></tr>'}</tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'income_by_customer', view: 'income-by-customer', params: { customer_id: (prefill || {}).customer_id || null }, prefill });
    },

    async customerStatementPicker() {
        ReportsPage.setAddress('customer-statement', {});
        const customers = await API.get("/customers?active_only=true");
        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
        openModal(T("Customer Statement"), `
            <form onsubmit="ReportsPage.openStatement(event)" data-readonly-ok>
                <!-- minmax(0, …) and width:100%: a select sizes itself to its
                     longest option, and a 120-character customer name pushed
                     As of past the dialog's edge (macbase1, F22). The picked
                     name still shows in full in the open list. -->
                <div class="form-grid" style="grid-template-columns:minmax(0, 2fr) minmax(0, 1fr);">
                    <div class="form-group" style="min-width:0;"><label>${T('Customer')} *</label>
                        <select name="customer_id" required style="width:100%; min-width:0; max-width:100%;"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>As of Date</label>
                        <input name="as_of_date" type="date" value="${todayISO()}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Generate PDF</button>
                </div>
            </form>`);
    },

    openStatement(e) {
        e.preventDefault();
        const form = e.target;
        const cid = form.customer_id.value;
        const asOf = form.as_of_date.value || todayISO();
        window.open(`/api/reports/customer-statement/${cid}/pdf?as_of_date=${asOf}`, "_blank");
        closeModal();
    },

    async arAging(prefill) {
        await ReportsPage.openPeriodModal(T("Accounts Receivable Aging"), "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/ar-aging?as_of_date=${params.as_of_date}`);
            // Save CSV writes the file the Accountant's Package carries (#393):
            // the whole company, as the screen shows it.
            const csvQs = `as_of_date=${params.as_of_date}`;
            // The API nets a customer's credits (unapplied payments and
            // credit memos) into Current; show Current gross and the
            // credits on their own, so the row still adds up to Total and
            // Total is what account 1100 says.
            const credit = (r) => r.unapplied_credits || 0;
            const agingRow = (r, name, style = '') => `<tr style="${style}">
                    <td>${name}</td>
                    <td class="amount">${formatCurrency(r.current + credit(r))}</td>
                    <td class="amount">${formatCurrency(r.over_30)}</td>
                    <td class="amount">${formatCurrency(r.over_60)}</td>
                    <td class="amount">${formatCurrency(r.over_90)}</td>
                    <td class="amount">${credit(r) ? formatCurrency(-credit(r)) : ''}</td>
                    <td class="amount" style="font-weight:600;">${formatCurrency(r.total)}</td>
                </tr>`;
            // A customer's name opens the customer's page (R9)
            const name = (i) => i.customer_id
                ? ReportsPage._rowLink(`ReportsPage.openCustomer(${i.customer_id}, this)`, i.customer_name, `customer:${i.customer_id}`)
                : escapeHtml(i.customer_name);
            let rows = data.items.map(i => agingRow(i, name(i))).join("");
            const t = data.totals;
            rows += agingRow(t, 'TOTAL', 'font-weight:700; background:var(--gray-50);');
            return `${ReportsPage._exportButtons('ar-aging', csvQs, ['csv'])}
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div style="margin-bottom:12px; display:flex; gap:8px;" data-write>
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.applyLateFees()">Apply Late Fees</button>
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.batchEmailStatements()">Email All Overdue</button>
                    <select id="collection-letter-type" aria-label="Collection letter" style="font-size:11px; padding:2px 6px;">
                        <option value="30">30-Day Letter</option>
                        <option value="60">60-Day Letter</option>
                        <option value="90">90-Day Letter</option>
                    </select>
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.sendCollectionLetters()">Send Collection Letters</button>
                    <span style="display:inline-flex; align-items:center; gap:4px; margin-left:auto;">
                        <label for="write-off-under" style="font-size:11px;">Write off balances under</label>
                        <input id="write-off-under" type="number" step="0.01" min="0.01" value="5.00" style="width:76px; font-size:11px; padding:2px 6px;">
                        <button class="btn btn-sm btn-secondary" onclick="ReportsPage.writeOffSmall('${params.as_of_date}')">List them…</button>
                    </span>
                </div>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">${T('Customer')}</th><th scope="col" class="amount">Current</th><th scope="col" class="amount">1-30</th>
                        <th scope="col" class="amount">31-60</th><th scope="col" class="amount">61-90+</th><th scope="col" class="amount">Credits</th><th scope="col" class="amount">Total</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="7" style="text-align:center; color:var(--gray-400);">No outstanding receivables</td></tr>'}</tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'ar_aging', view: 'ar-aging', params: { customer_id: (prefill || {}).customer_id || null }, prefill });
    },

    async apAging(prefill) {
        await ReportsPage.openPeriodModal("Accounts Payable Aging", "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/ap-aging?as_of_date=${params.as_of_date}`);
            const csvQs = `as_of_date=${params.as_of_date}`;
            // A vendor's name opens the vendor's page (R9)
            let rows = data.items.map(i =>
                `<tr>
                    <td>${i.vendor_id ? ReportsPage._rowLink(`ReportsPage.openVendor(${i.vendor_id}, this)`, i.vendor_name, `vendor:${i.vendor_id}`) : escapeHtml(i.vendor_name)}</td>
                    <td class="amount">${formatCurrency(i.current)}</td>
                    <td class="amount">${formatCurrency(i.over_30)}</td>
                    <td class="amount">${formatCurrency(i.over_60)}</td>
                    <td class="amount">${formatCurrency(i.over_90)}</td>
                    <td class="amount" style="font-weight:600;">${formatCurrency(i.total)}</td>
                </tr>`
            ).join("");
            const t = data.totals;
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td>TOTAL</td>
                <td class="amount">${formatCurrency(t.current)}</td>
                <td class="amount">${formatCurrency(t.over_30)}</td>
                <td class="amount">${formatCurrency(t.over_60)}</td>
                <td class="amount">${formatCurrency(t.over_90)}</td>
                <td class="amount">${formatCurrency(t.total)}</td>
            </tr>`;
            return `${ReportsPage._exportButtons('ap-aging', csvQs, ['csv'])}
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">Vendor</th><th scope="col" class="amount">Current</th><th scope="col" class="amount">1-30</th>
                        <th scope="col" class="amount">31-60</th><th scope="col" class="amount">61-90+</th><th scope="col" class="amount">Total</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No outstanding payables</td></tr>'}</tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'ap_aging', view: 'ap-aging', params: { vendor_id: (prefill || {}).vendor_id || null }, prefill });
    },

    async trialBalance(prefill) {
        await ReportsPage.openPeriodModal("Trial Balance", "this_year_to_date", async (_period, range, live) => {
            const data = await API.get(`/reports/trial-balance?start_date=${range.start}&end_date=${range.end}${ReportsPage._adjQs(live)}`);
            // An account's name opens its register for the report's dates
            // (R9), with "Back to Trial Balance"
            const drill = (i) => i.account_id
                ? ReportsPage._rowLink(`ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_path || i.account_name)},${JSON.stringify(range.start)},${JSON.stringify(range.end)},null,null,'trial-balance')`, i.account_path || i.account_name, `drill:${i.account_id}`)
                : escapeHtml(i.account_path || i.account_name);
            let rows = data.items.map(i =>
                `<tr>
                    <td>${escapeHtml(i.account_number)}</td>
                    <td>${drill(i)}</td>
                    <td style="font-size:10px; color:var(--gray-400);">${i.account_type}</td>
                    <td class="amount">${i.total_debit > 0 ? formatCurrency(i.total_debit) : ''}</td>
                    <td class="amount">${i.total_credit > 0 ? formatCurrency(i.total_credit) : ''}</td>
                    <td class="amount">${formatCurrency(i.net_balance)}</td>
                </tr>`
            ).join('');
            const diffColor = Math.abs(data.difference) < 0.01 ? 'var(--text-success)' : 'var(--text-danger)';
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="3">TOTALS</td>
                <td class="amount">${formatCurrency(data.total_debit)}</td>
                <td class="amount">${formatCurrency(data.total_credit)}</td>
                <td class="amount" style="color:${diffColor}">${formatCurrency(data.difference)}</td>
            </tr>`;
            return `${ReportsPage._exportButtons('trial-balance', `start_date=${range.start}&end_date=${range.end}${ReportsPage._adjQs(live)}`)}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}${data.exclude_adjusting ? ' · without adjusting entries' : ''}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Number</th><th scope="col">Account</th><th scope="col">Type</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Net</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        }, "Dates", false, { view: 'trial-balance', prefill, ...ReportsPage._adjOpts(prefill) });
    },

    // The accountant's adjusting entries for a period (#341): each with its
    // lines and who posted it; the totals tie to the General Ledger.
    async adjustingEntries(prefill) {
        await ReportsPage.openPeriodModal("Adjusting Journal Entries", "this_year_to_date", async (_period, range) => {
            const qs = `start_date=${range.start}&end_date=${range.end}`;
            const data = await API.get(`/reports/adjusting-entries?${qs}`);
            const entries = data.entries.map(e => {
                const lines = e.lines.map(l =>
                    `<tr><td style="padding-left:24px;">${l.account_number ? `${escapeHtml(l.account_number)} ` : ''}${escapeHtml(l.account_name)}${l.description ? ` <span style="color:var(--gray-500);">— ${escapeHtml(l.description)}</span>` : ''}</td>
                         <td class="amount">${l.debit > 0 ? formatCurrency(l.debit) : ''}</td>
                         <td class="amount">${l.credit > 0 ? formatCurrency(l.credit) : ''}</td></tr>`).join('');
                return `<tr style="font-weight:600; background:var(--gray-50);">
                        <td><a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;" onclick="JournalPage.view(${e.id})">#${e.id}</a>
                            · ${formatDate(e.date)} · ${escapeHtml(e.description)}${e.reference ? ` (${escapeHtml(e.reference)})` : ''}
                            ${e.posted_by ? `<span style="color:var(--gray-500); font-weight:400;"> · posted by ${escapeHtml(e.posted_by)}</span>` : ''}
                            ${e.voided ? '<span class="journal-voided" style="color:var(--danger);"> · Voided</span>' : ''}
                            ${e.is_reversal ? '<span style="color:var(--gray-500); font-weight:400;"> · reversal</span>' : ''}</td>
                        <td class="amount">${formatCurrency(e.total_debit)}</td>
                        <td class="amount">${formatCurrency(e.total_credit)}</td>
                    </tr>${lines}`;
            }).join('');
            return `${ReportsPage._exportButtons('adjusting-entries', qs)}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)} · ${data.count} ${data.count === 1 ? 'entry' : 'entries'}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Entry</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th></tr></thead>
                    <tbody>${entries || '<tr><td colspan="3" style="text-align:center; color:var(--gray-400);">No adjusting entries in this period</td></tr>'}
                    <tr style="font-weight:700; background:var(--gray-50);"><td>TOTALS</td><td class="amount">${formatCurrency(data.total_debit)}</td><td class="amount">${formatCurrency(data.total_credit)}</td></tr>
                    </tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'adjusting_entries', view: 'adjusting-entries', prefill });
    },

    // What moved inside a closed period (#342): each use of the
    // closing-date password, with the rows it changed before and after.
    async closingDateExceptions(prefill) {
        await ReportsPage.openPeriodModal("Closing Date Exceptions", "this_year_to_date", async (_period, range) => {
            // the period's days and every time shown are the page's clock's:
            // the log keeps UTC, and an evening override was dated tomorrow
            // and missing from "this year to date" (NEW-152 / W-19)
            const qs = `start_date=${range.start}&end_date=${range.end}&tz_offset=${new Date().getTimezoneOffset()}`;
            const data = await API.get(`/reports/closing-date-exceptions?${qs}`);
            const when = (ev) => ev.when_local ? `${formatDate(ev.when_local.slice(0, 10))} ${ev.when_local.slice(11, 16)}` : (ev.when ? `${formatDate(ev.when.slice(0, 10))} ${ev.when.slice(11, 16)}` : '');
            const events = data.events.map(ev => {
                const changes = ev.changes.map(c => {
                    const record = `${c.action} ${c.record} ${c.record_id}${c.label ? ` (${escapeHtml(c.label)})` : ''}`;
                    if (!c.fields.length) return `<tr><td style="padding-left:24px;">${record}</td><td></td><td></td><td></td></tr>`;
                    return c.fields.map((f, i) =>
                        `<tr><td style="padding-left:24px;">${i === 0 ? record : ''}</td>
                             <td>${escapeHtml(f.field)}</td><td>${escapeHtml(f.before)}</td><td>${escapeHtml(f.after)}</td></tr>`).join('');
                }).join('') || `<tr><td colspan="4" style="padding-left:24px; color:var(--gray-500);">The password was used; the rows it changed were not marked before 2.24.0.</td></tr>`;
                return `<tr style="font-weight:600; background:var(--gray-50);">
                        <td colspan="4">${when(ev)} · ${escapeHtml(ev.who)} · closing date ${formatDate(ev.closing_date)} · a ${formatDate(ev.transaction_date)} change</td>
                    </tr>${changes}`;
            }).join('');
            return `${ReportsPage._exportButtons('closing-date-exceptions', qs)}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)} · ${data.count} ${data.count === 1 ? 'override' : 'overrides'}, ${data.changes} ${data.changes === 1 ? 'change' : 'changes'}${data.closing_date ? ` · closing date now ${formatDate(data.closing_date)}` : ' · no closing date set'}${data.timezone ? ` · times in ${escapeHtml(data.timezone)}` : ''}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Change</th><th scope="col">Field</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
                    <tbody>${events || '<tr><td colspan="4" style="text-align:center; color:var(--gray-400);">Nothing was changed inside a closed period with the password in this period</td></tr>'}</tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'closing_date_exceptions', view: 'closing-date-exceptions', prefill });
    },

    // One zip for the accountant (#343): what the dialog lists, then the
    // download. The zip is built once to list its files.
    async accountantsPackage(prefill) {
        await ReportsPage.openPeriodModal("Accountant's Package", "last_year", async (_period, range) => {
            const qs = `start_date=${range.start}&end_date=${range.end}&tz_offset=${new Date().getTimezoneOffset()}`;
            const m = await API.get(`/reports/accountants-package/manifest?${qs}`);
            const files = m.files.map(f => `<li style="font-family:var(--mono, monospace); font-size:12px;">${escapeHtml(f)}</li>`).join('');
            const notes = (m.notes || []).map(n => `<li>${escapeHtml(n)}</li>`).join('');
            return `<p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(m.start_date)} &mdash; ${formatDate(m.end_date)}${m.closing_date ? ` · closing date ${formatDate(m.closing_date)}` : ''}</p>
                <p style="margin-bottom:8px;">${Terms.text('Everything an outside reviewer asks for, in one file: the Financial Statements Pack, the General Ledger and Trial Balance, the A/R and A/P agings, each bank reconciliation completed in the period, the Adjusting Journal Entries and Closing Date Exceptions reports, and the audit log since the closing date. Each file is the same report run on its own.')}</p>
                <ul style="margin:0 0 12px 18px;">${files}</ul>
                ${notes ? `<ul style="margin:0 0 12px 18px; color:var(--gray-500);">${notes}</ul>` : ''}
                <button class="btn btn-primary" onclick="window.open('/api/reports/accountants-package?${qs}','_blank')">Download the package (zip)</button>`;
        }, "Dates", false, { view: 'accountants-package', prefill });
    },

    // Reclassify (#337): an account's lines for a period, the ticked ones
    // moved to another account and/or class as one audited change. The
    // account rides on the address (params.account_id), so Back and a
    // saved address reopen the same list. Ticks are kept across redraws by
    // line id; the move sends only the ticked lines still listed.
    async reclassify(prefill) {
        const p = prefill || {};
        const params = {};
        if (/^\d+$/.test(p.account_id || '')) params.account_id = p.account_id;
        // one payee's lines (#394): the vendor, and a free-text payee, ride
        // on the address like the account
        if (/^\d+$/.test(p.vendor_id || '')) params.vendor_id = p.vendor_id;
        if (p.payee) params.payee = p.payee;
        ReportsPage._rc = { lists: null, lines: [], ticked: new Set(), plan: null, range: null };
        await ReportsPage.openPeriodModal('Reclassify Transactions', 'this_year_to_date', async (_period, range, prm) => {
            const rc = ReportsPage._rc;
            rc.range = range;
            rc.plan = null;
            if (!rc.lists) {
                const [accounts, classes, vendors] = await Promise.all([
                    API.get('/accounts').catch(() => []),
                    API.get('/classes').catch(() => []),
                    API.get('/vendors').catch(() => []),
                ]);
                rc.lists = { accounts, classes, vendors };
            }
            const accountId = String(prm.account_id || '');
            const vendorId = String(prm.vendor_id || '');
            const payeeText = String(prm.payee || '');
            const acctOpt = (a, selected) => `<option value="${a.id}"${String(a.id) === String(selected) ? ' selected' : ''}>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}${a.is_active === false ? ' (inactive)' : ''}</option>`;
            const picker = `<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
                    <label for="rc-account" style="font-size:11px;">Lines on</label>
                    <select id="rc-account" data-no-search style="max-width:300px;" onchange="ReportsPage.setParam('account_id', this.value)"><option value="">Choose an account…</option>${rc.lists.accounts.map(a => acctOpt(a, accountId)).join('')}</select>
                    <label for="rc-vendor" style="font-size:11px;">Vendor</label>
                    <select id="rc-vendor" data-no-search style="max-width:260px;" onchange="ReportsPage.setParam('vendor_id', this.value)"><option value="">All payees</option>${rc.lists.vendors.map(v => `<option value="${v.id}"${String(v.id) === vendorId ? ' selected' : ''}>${escapeHtml(v.name)}${v.is_active === false ? ' (inactive)' : ''}</option>`).join('')}</select>
                    <label for="rc-payee" style="font-size:11px;">Payee</label>
                    <input type="text" id="rc-payee" placeholder="any part of the payee" style="max-width:220px;" value="${escapeHtml(payeeText)}" onchange="ReportsPage.setParam('payee', this.value.trim())">
                </div>`;
            if (!accountId) {
                rc.lines = [];
                return `${picker}<p style="color:var(--gray-500);">Pick the account the lines are parked on: a holding account, or one chosen by mistake. Tick the lines to move, choose where they belong, preview the move, then post it as one change. Every moved line is listed in the result and in the audit log.</p>`;
            }
            const data = await API.get(`/reclassify/lines?account_id=${accountId}&start_date=${range.start}&end_date=${range.end}${vendorId ? `&vendor_id=${vendorId}` : ''}${payeeText ? `&payee=${encodeURIComponent(payeeText)}` : ''}`);
            rc.lines = data.lines;
            const listed = new Set(data.lines.map(l => l.id));
            rc.ticked.forEach(id => { if (!listed.has(id)) rc.ticked.delete(id); });
            const rows = data.lines.map(l => {
                const tick = l.movable
                    ? `<input type="checkbox" class="rc-tick" data-write data-line="${l.id}" ${rc.ticked.has(l.id) ? 'checked' : ''} aria-label="Move the ${escapeHtml(l.source_label.toLowerCase())} of ${formatDate(l.date)}, ${escapeHtml(l.description || 'no memo')}" onchange="ReportsPage.rcTick(${l.id}, this.checked)">`
                    : `<span class="sr-only">Stays: </span>`;
                // a document names itself ("Bill UT-20"), the rest by their
                // type and reference ("Bill Bill UT-20" read the type twice: NEW-163)
                const what = l.document ? escapeHtml(l.document) : `${escapeHtml(l.source_label)}${l.reference ? ` <span style="color:var(--gray-500);">${escapeHtml(l.reference)}</span>` : ''}`;
                const muted = l.movable ? '' : ' style="color:var(--gray-400);"';
                return `<tr${muted}>
                        <td style="width:28px;">${tick}</td>
                        <td>${formatDate(l.date)}</td>
                        <td>${what}</td>
                        <td>${escapeHtml(l.payee || '')}</td>
                        <td>${escapeHtml(l.description || '')}${l.movable ? '' : ` <span style="font-size:11px;">(stays: ${escapeHtml(l.reason)})</span>`}</td>
                        <td>${escapeHtml(l.class_name || '')}</td>
                        <td class="amount">${formatCurrency(l.amount)}</td>
                    </tr>`;
            }).join('');
            const targets = rc.lists.accounts.filter(a => a.is_active !== false && !a.bank_kind && !a.is_control && String(a.id) !== accountId);
            const classOpts = rc.lists.classes.filter(c => !c.is_archived).map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
            const empty = `<tr><td colspan="7" style="text-align:center; color:var(--gray-400);">No lines${data.vendor ? ` for ${escapeHtml(data.vendor.name)}` : ''} on ${escapeHtml(data.account.label)} in this period</td></tr>`;
            return `${picker}
                <p style="margin-bottom:8px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)} · ${data.count} ${data.count === 1 ? 'line' : 'lines'}, ${data.movable} can move${data.count ? ` · lines that can move total ${formatCurrency(data.movable_total)}` : ''}</p>
                <div class="table-container"><table id="rc-table">
                    <thead><tr>
                        <th scope="col" style="width:28px;">${data.movable ? `<input type="checkbox" id="rc-all" data-write aria-label="Tick every line that can move" onchange="ReportsPage.rcTickAll(this.checked)">` : ''}</th>
                        <th scope="col">Date</th><th scope="col">Type</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">${T('Class')}</th><th scope="col" class="amount">Amount</th>
                    </tr></thead>
                    <tbody>${rows || empty}</tbody>
                </table></div>
                <div id="rc-move" data-accountant-ok data-write style="margin-top:12px; padding:10px; border:1px solid var(--gray-200); border-radius:4px;">
                    <div style="display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap;">
                        <div class="form-group" style="margin:0;">
                            <label for="rc-to-account" style="font-size:11px;">Move the ticked lines to</label>
                            <select id="rc-to-account" data-no-search data-write style="max-width:300px;"><option value="">Leave the account as it is</option>${targets.map(a => acctOpt(a, '')).join('')}</select>
                        </div>
                        <div class="form-group" style="margin:0;">
                            <label for="rc-to-class" style="font-size:11px;">${T('Class')}</label>
                            <select id="rc-to-class" data-no-search data-write style="max-width:220px;"><option value="">Leave as it is</option><option value="none">No ${T('class').toLowerCase()}</option>${classOpts}</select>
                        </div>
                        <button type="button" class="btn btn-secondary" data-write onclick="ReportsPage.rcPreview()">Preview the move</button>
                    </div>
                    <div id="rc-plan" style="margin-top:8px;" aria-live="polite"></div>
                </div>`;
        }, 'Dates', false, { view: 'reclassify', prefill, params });
    },

    rcTick(id, on) {
        const rc = ReportsPage._rc;
        if (on) rc.ticked.add(id); else rc.ticked.delete(id);
        const all = $('#rc-all');
        if (all) all.checked = rc.lines.filter(l => l.movable).every(l => rc.ticked.has(l.id));
        const plan = $('#rc-plan');
        if (plan && rc.plan) { rc.plan = null; plan.innerHTML = '<p style="color:var(--gray-500);">The ticks changed; preview the move again.</p>'; }
    },

    rcTickAll(on) {
        const rc = ReportsPage._rc;
        rc.lines.filter(l => l.movable).forEach(l => { if (on) rc.ticked.add(l.id); else rc.ticked.delete(l.id); });
        $$('#rc-table .rc-tick').forEach(cb => { cb.checked = on; });
        ReportsPage.rcTick(null, false);
    },

    _rcRequest(dryRun) {
        const rc = ReportsPage._rc;
        const ids = rc.lines.filter(l => l.movable && rc.ticked.has(l.id)).map(l => l.id);
        const toAccount = ($('#rc-to-account') || {}).value || '';
        const toClass = ($('#rc-to-class') || {}).value || '';
        const body = { line_ids: ids, dry_run: dryRun };
        if (toAccount) body.account_id = parseInt(toAccount, 10);
        if (toClass === 'none') body.clear_class = true;
        else if (toClass) body.class_id = parseInt(toClass, 10);
        return body;
    },

    _rcPlanHtml(r, done) {
        const where = [
            r.to_account ? `to <strong>${escapeHtml(r.to_account.label)}</strong>` : '',
            r.clear_class ? `with no ${T('class').toLowerCase()}` : (r.to_class ? `under ${T('class').toLowerCase()} <strong>${escapeHtml(r.to_class.name)}</strong>` : ''),
        ].filter(Boolean).join(', ');
        const rows = r.moved.map(m => `<tr>
                <td>${formatDate(m.date)}</td>
                <td>${m.document ? escapeHtml(m.document) : `${escapeHtml(m.source_label)}${m.reference ? ` <span style="color:var(--gray-500);">${escapeHtml(m.reference)}</span>` : ''}`}</td>
                <td>${escapeHtml(m.description || '')}</td>
                <td>${escapeHtml(m.from_account ? m.from_account.label : '')} → ${escapeHtml(m.to_account ? m.to_account.label : '')}</td>
                <td>${escapeHtml(m.from_class ? m.from_class.name : '')} → ${escapeHtml(m.to_class ? m.to_class.name : '')}</td>
                <td class="amount">${formatCurrency(m.amount)}</td>
            </tr>`).join('');
        const skipped = r.skipped.length ? `<p style="font-size:11px; color:var(--gray-500);">${r.skipped.length} ${r.skipped.length === 1 ? 'line stays' : 'lines stay'}: ${r.skipped.map(s => escapeHtml(s.reason)).filter((v, i, a) => a.indexOf(v) === i).join('; ')}.</p>` : '';
        const verb = done ? 'Moved' : 'This moves';
        return `<p style="margin:0 0 6px;"><strong>${verb} ${r.count} ${r.count === 1 ? 'line' : 'lines'}</strong> ${where}.${done ? ' Each is in the audit log.' : ''}</p>
            ${skipped}
            ${r.count ? `<div class="table-container"><table><thead><tr><th scope="col">Date</th><th scope="col">Type</th><th scope="col">Memo</th><th scope="col">Account</th><th scope="col">${T('Class')}</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}
            ${(!done && r.count) ? `<div style="margin-top:8px;"><button type="button" class="btn btn-primary" data-write onclick="ReportsPage.rcMove()">Move ${r.count} ${r.count === 1 ? 'line' : 'lines'}</button></div>` : ''}`;
    },

    async rcPreview() {
        const body = ReportsPage._rcRequest(true);
        const plan = $('#rc-plan');
        if (!body.line_ids.length) { plan.innerHTML = '<p style="color:var(--gray-500);">Tick the lines to move first.</p>'; return; }
        if (!body.account_id && !body.class_id && !body.clear_class) { plan.innerHTML = `<p style="color:var(--gray-500);">Choose the account to move them to, or a ${T('class').toLowerCase()}.</p>`; return; }
        try {
            const r = await API.post('/reclassify', body);
            ReportsPage._rc.plan = r;
            plan.innerHTML = ReportsPage._rcPlanHtml(r, false);
        } catch (err) {
            plan.innerHTML = `<p style="color:var(--danger);">${escapeHtml(err.message)}</p>`;
        }
    },

    async rcMove() {
        const body = ReportsPage._rcRequest(false);
        const plan = $('#rc-plan');
        const btn = plan.querySelector('button');
        if (btn) { btn.disabled = true; btn.textContent = 'Moving…'; }
        try {
            const r = await API.post('/reclassify', body);
            ReportsPage._rc.ticked.clear();
            toast(`Moved ${r.count} ${r.count === 1 ? 'line' : 'lines'}.`);
            // the list redraws without the moved lines; the result stays
            // under it until the next preview
            const live = ReportsPage._live;
            if (live && live.render) await live.render();
            const after = $('#rc-plan');
            if (after) after.innerHTML = ReportsPage._rcPlanHtml(r, true);
        } catch (err) {
            plan.innerHTML = `<p style="color:var(--danger);">${escapeHtml(err.message)}</p>`;
        }
    },

    async cashFlow(prefill) {
        await ReportsPage.openPeriodModal("Cash Flow Statement", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/cash-flow?start_date=${range.start}&end_date=${range.end}`);
            // Indirect method (banking, exploratory 2.17.3 W-M6/F18): net
            // income, what moved no cash, the change in working capital;
            // then investing and financing; the net change is the bank's.
            // An account's row opens its register for the report's dates
            // (R9); a row the statement makes up (no account_id) stays text.
            const name = (i) => i.account_id
                ? ReportsPage._rowLink(`ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_name)},${JSON.stringify(range.start)},${JSON.stringify(range.end)},null,null,'cash-flow')`, i.account_name, `drill:${i.account_id}`)
                : escapeHtml(i.account_name);
            const rows = (items, indent) => items.length
                ? items.map(i => `<tr><td style="padding-left:${indent}px;">${name(i)}</td><td class="amount">${formatCurrency(i.amount)}</td></tr>`).join('')
                : `<tr><td style="padding-left:${indent}px; color:var(--gray-400);">None</td><td></td></tr>`;
            const head = (title, indent = 0) => `<tr><td style="padding-left:${indent}px;"><strong>${title}</strong></td><td></td></tr>`;
            const total = (title, amount) => `<tr style="font-weight:600; background:var(--gray-50);"><td>Total ${title}</td><td class="amount">${formatCurrency(amount)}</td></tr>`;
            const adjustments = data.adjustments || [];
            const workingCapital = data.working_capital || [];
            const operating = `${head('Operating Activities')}
                <tr><td style="padding-left:24px;">${T('Net Income')}</td><td class="amount">${formatCurrency(data.net_income)}</td></tr>
                ${adjustments.length ? head('Adjustments for non-cash items', 24) + rows(adjustments, 48) : ''}
                ${workingCapital.length ? head('Changes in working capital', 24) + rows(workingCapital, 48) : ''}
                ${total('Operating Activities', data.total_operating)}`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>
                        ${operating}
                        ${head('Investing Activities')}${rows(data.investing, 24)}${total('Investing Activities', data.total_investing)}
                        ${head('Financing Activities')}${rows(data.financing, 24)}${total('Financing Activities', data.total_financing)}
                        <tr style="font-weight:700; font-size:15px; background:var(--primary-light);">
                            <td>Net Change in Cash</td><td class="amount">${formatCurrency(data.net_change)}</td>
                        </tr>
                        <tr><td>Cash at beginning of period</td><td class="amount">${formatCurrency(data.beginning_cash)}</td></tr>
                        <tr style="font-weight:700;"><td>Cash at end of period</td><td class="amount">${formatCurrency(data.ending_cash)}</td></tr>
                    </tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'cash_flow', view: 'cash-flow', prefill });
    },

    // #/reports/1099-summary?year=2025 opens the year's summary straight
    // away (R7); the address takes the year when Generate is clicked.
    // With vendor_id (from the vendor's page, #238) that vendor's row is
    // picked out; it rides on the content element and in the address.
    async report1099(prefill) {
        const currentYear = new Date().getFullYear();
        const year = /^\d{4}$/.test((prefill || {}).year || '') ? prefill.year : null;
        const vendorId = parseInt((prefill || {}).vendor_id, 10) || null;
        ReportsPage.setAddress('1099-summary', { year, vendor_id: vendorId });
        openModal('1099 Summary', `
            <div class="form-grid" style="margin-bottom:12px;">
                <div class="form-group"><label for="report-1099-year">Year</label>
                    <input id="report-1099-year" type="number" value="${year || currentYear}" style="width:100px;"></div>
                <div class="form-group" style="align-self:end;">
                    <button type="button" class="btn btn-primary" onclick="ReportsPage.load1099()">Generate</button></div>
            </div>
            <div id="report-1099-content" ${vendorId ? `data-vendor-id="${vendorId}"` : ''}><div style="font-size:11px; color:var(--gray-500);">Select year and click Generate</div></div>
            <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
        if (year) await ReportsPage.load1099(true);
    },

    // first: the view's opening render, the one that may move focus to a
    // spotlighted vendor row; Generate re-renders with focus left where
    // it is (R8 review).
    async load1099(first = false) {
        const year = $('#report-1099-year').value;
        const content = $('#report-1099-content');
        const vendorId = content.getAttribute('data-vendor-id') || null;
        ReportsPage.setAddress('1099-summary', { year, vendor_id: vendorId });
        content.innerHTML = '<div style="font-size:11px; color:var(--gray-500);">Loading...</div>';
        try {
            const data = await API.get(`/reports/1099-summary?year=${year}`);
            if (data.items.length === 0) {
                content.innerHTML = '<div class="empty-state"><p>No 1099 vendors found. Flag vendors as 1099 in the Vendors page.</p></div>';
                return;
            }
            // A vendor's name opens the vendor's page (R9)
            let rows = data.items.map(i =>
                `<tr${i.above_threshold ? ' style="background:var(--primary-light);"' : ''}>
                    <td>${i.vendor_id ? ReportsPage._rowLink(`ReportsPage.openVendor(${i.vendor_id}, this)`, i.vendor_name, `vendor:${i.vendor_id}`) : escapeHtml(i.vendor_name)}</td>
                    <td>${escapeHtml(i.tax_id)}</td>
                    <td>${escapeHtml(i.vendor_1099_type)}</td>
                    <td class="amount">${formatCurrency(i.total_paid)}</td>
                    <td>${i.w9_on_file ? 'On file' : (i.above_threshold ? '<span style="color:var(--danger); font-weight:700;">No W-9</span>' : 'No')}</td>
                    <td>${i.above_threshold ? '<span style="color:var(--danger); font-weight:700;">REPORT</span>' : ''}</td>
                </tr>`
            ).join('');
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="3">TOTAL</td><td class="amount">${formatCurrency(data.total)}</td>
                <td>${data.vendors_missing_w9 ? `${data.vendors_missing_w9} with no W-9` : ''}</td>
                <td>${data.vendors_above_threshold} vendor(s) above $${data.threshold}</td></tr>`;
            content.innerHTML = `
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Vendor</th><th scope="col">Tax ID</th><th scope="col">Type</th><th scope="col" class="amount">Total Paid</th><th scope="col">W-9</th><th scope="col">Status</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
            const restored = ReportsPage._refocusRow(content);
            ReportsPage._spotlight(content, { vendor_id: vendorId }, first && !restored);
        } catch (err) { content.innerHTML = `<div style="color:var(--danger);">${escapeHtml(err.message)}</div>`; }
    },

    // Small balances written off from A/R Aging (#255): the open invoices
    // under the figure typed, listed first with a tick each, then one
    // write-off credit memo per invoice to Bad Debt Expense, posted together.
    async writeOffSmall(asOf) {
        const under = parseFloat(document.getElementById('write-off-under').value);
        if (!(under > 0)) { toast('Enter the figure: balances under it are listed.', 'error'); return; }
        let rows;
        try {
            rows = await API.get(`/invoices/write-offs/candidates?under=${under}&as_of_date=${asOf}`);
        } catch (err) { toast(err.message, 'error'); return; }
        if (!rows.length) { toast(`No open balance under ${formatCurrency(under)} as of ${formatDate(asOf)}.`); return; }
        const list = rows.map(r => `<tr>
                <td><input type="checkbox" class="write-off-pick" value="${r.id}" checked aria-label="Write off ${escapeHtml(T('invoice'))} ${escapeHtml(r.invoice_number)}, ${escapeHtml(r.customer_name)}"></td>
                <td>${escapeHtml(r.customer_name)}</td><td>${escapeHtml(r.invoice_number)}</td>
                <td>${formatDate(r.date)}</td><td class="amount">${r.days_overdue || 0}</td>
                <td class="amount">${formatCurrency(r.balance_due)}</td></tr>`).join('');
        openModal(`Write Off Balances Under ${formatCurrency(under)}`, `
            <form onsubmit="ReportsPage.saveWriteOffSmall(event, ${under})">
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Write off</th><th scope="col">${T('Customer')}</th><th scope="col">#</th><th scope="col">Date</th><th scope="col" class="amount">Days over</th><th scope="col" class="amount">Balance</th></tr></thead>
                    <tbody>${list}</tbody>
                </table></div>
                <div class="form-grid" style="margin-top:8px;">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Memo</label><input name="memo" placeholder="e.g. too small to collect"></div>
                </div>
                <div style="font-size:11px;color:var(--gray-500);margin-top:6px">Posts one credit memo per ${T('invoice')} to Bad Debt Expense, applied at once; a balance that changed since this list was made is left alone. Void a credit memo to undo it.</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="App.refresh()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Write Off</button>
                </div>
            </form>`);
    },

    async saveWriteOffSmall(e, under) {
        e.preventDefault();
        const form = e.target;
        const ids = [...form.querySelectorAll('.write-off-pick:checked')].map(c => Number(c.value));
        if (!ids.length) { toast('Tick at least one.', 'error'); return; }
        try {
            const res = await API.post('/invoices/write-offs', { date: form.date.value, under, invoice_ids: ids, memo: form.memo.value || null });
            const n = res.written_off.length;
            let msg = `Wrote off ${n} balance${n === 1 ? '' : 's'} as credit memo${n === 1 ? '' : 's'}.`;
            if (res.skipped.length) msg += ` Left alone: ${res.skipped.map(s => `#${s.invoice_number || s.invoice_id} (${s.reason})`).join('; ')}.`;
            toast(msg, res.skipped.length ? 'warning' : 'success');
            App.refresh();  // the aging report again, at its address, without these balances
        } catch (err) { toast(err.message, 'error'); }
    },

    async applyLateFees() {
        if (!confirm('Apply late fees to all overdue invoices past the grace period?')) return;
        try {
            const result = await API.post('/invoices/apply-late-fees');
            toast(`Late fees applied to ${result.applied} of ${result.total_overdue} overdue invoices`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async batchEmailStatements() {
        if (!confirm('Email statements to all customers with overdue invoices?')) return;
        try {
            const result = await API.post('/reports/batch-email-statements');
            if (!result.sent && !result.failed) {
                toast(Terms.text('No customer has an overdue invoice, so there was nothing to send.'));
                return;
            }
            // Only what actually went out is "sent"; a customer who didn't
            // get one is named, with the reason (explore 2.17.3, W-H7).
            ReportsPage._sendResult('Statements',
                `Sent ${result.sent} statement${result.sent === 1 ? '' : 's'}.`, result.errors || []);
        } catch (err) { toast(err.message, 'error'); }
    },

    _sendResult(title, headline, errors) {
        if (!errors.length) { toast(headline); return; }
        openModal(title, `
            <p>${escapeHtml(headline)} ${errors.length} could not be sent:</p>
            <ul style="margin:8px 0 12px 20px;">${errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>
            <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async sendCollectionLetters() {
        const letterType = $('#collection-letter-type')?.value || '30';
        if (!confirm(`Send ${letterType}-day collection letters to all qualifying customers?`)) return;
        try {
            const result = await API.post('/reports/collection-letters', {
                letter_type: letterType,
                send_email: true,
            });
            ReportsPage._sendResult('Collection Letters',
                `Generated ${result.generated} letter${result.generated === 1 ? '' : 's'}, emailed ${result.emailed}.`, result.errors || []);
        } catch (err) { toast(err.message, 'error'); }
    },
};

// ---------------------------------------------------------------------------
// The wide grid: P&L accounts down the side, one column per class (or job)
// across, a Total column (R1, #231; R13, #242). The Account column and the
// header row stay put while the rest scrolls sideways inside its container,
// so the Total column is reachable however many columns there are. Above
// the table: a "Go to" picker (the type-ahead attaches to it), ‹ Prev /
// Next ›, and a status line a screen reader hears ("Column 4 of 12: Site
// Prep"). With the grid or the picker focused, ←/→ move one column, Home
// and End go to the first and last, Enter opens the column's own report.
// Nothing of the jump state reaches the address, a saved report or an
// export: it is a place in the table, not a filter.
// ---------------------------------------------------------------------------
ReportsPage._grid = { current: -1, count: 0, names: [] };

// spec: { title, noun (lowercase, T()'d), kind ('class' | 'job': the
// picker's name, so the type-ahead knows it lists them), columns
// [{id, name, income, cogs, gross_profit, expenses, net_income}], accounts
// {income, cogs, expenses} with amounts[] aligned to columns, totals
// {income, cogs, gross_profit, expenses, net_income}, drill(account, col)
// → call for an amount, head(col) → call for a heading, sum(col, key) →
// call for a subtotal cell (or null), note (html above the table),
// totalLabel }
// The grid's Total column, by the column keys the rows read (#390: the
// Other Income / Other Expense rows the P&L has).
ReportsPage._gridTotals = function (data) {
    return {
        income: data.total_income, cogs: data.total_cogs, gross_profit: data.total_gross_profit,
        operating_gross_profit: data.total_operating_gross_profit,
        expenses: data.total_expenses, net_operating_income: data.total_net_operating_income,
        operating_income: data.total_operating_income, operating_expenses: data.total_operating_expenses,
        other_income: data.total_other_income, other_expense: data.total_other_expense,
        net_other_income: data.total_net_other_income, net_income: data.total_net_income,
    };
};

ReportsPage._pivotGrid = function (spec) {
    const cols = spec.columns;
    const n = cols.length;
    const totals = spec.totals;
    const link = (call, text) => `<a href="javascript:void(0)" class="grid-link" style="color:var(--text-link); text-decoration:none;" onclick="${escapeHtml(call)}">${text}</a>`;
    const empties = cols.map((_, i) => `<td data-col="${i}"></td>`).join('') + '<td></td>';
    // the number and full path, as the P&L flat list prints a line (#416);
    // a row without the label (an older reply) still reads number - name
    const label = a => escapeHtml(a.account_label || `${a.account_number ? a.account_number + ' - ' : ''}${a.account_name}`);
    const cell = (a, i) => {
        const amount = a.amounts[i];
        if (!amount) return `<td class="amount" data-col="${i}"></td>`;
        return `<td class="amount" data-col="${i}">${link(spec.drill(a, cols[i]), formatCurrency(amount))}</td>`;
    };
    const section = (title, rows) => `<tr class="grid-section"><th scope="row">${title}</th>${empties}</tr>`
        + (rows.length
            ? rows.map(a => `<tr><th scope="row" style="padding-left:24px;">${label(a)}</th>${cols.map((_, i) => cell(a, i)).join('')}<td class="amount">${formatCurrency(a.total)}</td></tr>`).join('')
            : `<tr><td style="padding-left:24px; color:var(--gray-400);">None</td>${empties}</tr>`);
    const sum = (title, key, style) => `<tr style="${style}"><th scope="row">${title}</th>${cols.map((c, i) => {
        const text = formatCurrency(c[key]);
        const call = spec.sum ? spec.sum(c, key) : null;
        return `<td class="amount" data-col="${i}">${call ? link(call, text) : text}</td>`;
    }).join('')}<td class="amount">${formatCurrency(totals[key])}</td></tr>`;
    const subtotal = 'font-weight:600; background:var(--gray-50);';
    const heads = cols.map((c, i) => {
        const call = spec.head ? spec.head(c) : null;
        return `<th scope="col" class="amount" data-col="${i}">${call ? link(call, escapeHtml(c.name)) : escapeHtml(c.name)}</th>`;
    }).join('');
    const jumpOpts = cols.map((c, i) => `<option value="${i}">${escapeHtml(c.name)}</option>`).join('');
    ReportsPage._grid = { current: -1, count: n, names: cols.map(c => c.name), ids: cols.map(c => c.id) };
    return `
        <div class="grid-toolbar" id="grid-toolbar" onkeydown="ReportsPage.gridKey(event)">
            <label for="grid-jump">Go to ${escapeHtml(spec.noun)}</label>
            <select id="grid-jump" name="${spec.kind}_jump" onchange="if (this.value !== '') ReportsPage.gridGo(parseInt(this.value, 10))"><option value="">Type a ${escapeHtml(spec.noun)}…</option>${jumpOpts}</select>
            <button type="button" class="btn btn-sm btn-secondary" aria-label="Previous ${escapeHtml(spec.noun)}" onclick="ReportsPage.gridStep(-1)">&lsaquo; Prev</button>
            <button type="button" class="btn btn-sm btn-secondary" aria-label="Next ${escapeHtml(spec.noun)}" onclick="ReportsPage.gridStep(1)">Next &rsaquo;</button>
            <span id="grid-live" class="grid-live" role="status" aria-live="polite"></span>
        </div>
        ${spec.note || ''}
        <div class="table-container table-container--scroll grid-scroll" id="grid-scroll" tabindex="0" role="region" aria-label="${escapeHtml(spec.title)}" onkeydown="ReportsPage.gridKey(event)">
            <table class="pivot-grid">
                <thead><tr><th scope="col">Account</th>${heads}<th scope="col" class="amount">${escapeHtml(spec.totalLabel || 'Total')}</th></tr></thead>
                <tbody>
                    ${section(T('Income'), spec.accounts.income)}
                    ${sum(T('Total Income'), 'operating_income', subtotal)}
                    ${section('Cost of Goods Sold', spec.accounts.cogs)}
                    ${sum('Gross Profit', 'operating_gross_profit', subtotal)}
                    ${section('Expenses', spec.accounts.expenses)}
                    ${sum('Total Expenses', 'operating_expenses', subtotal)}
                    ${spec.hasOther ? sum(T('Net Operating Income'), 'net_operating_income', subtotal)
                        + ((spec.accounts.other_income || []).length ? section(T('Other Income'), spec.accounts.other_income) + sum(T('Total Other Income'), 'other_income', subtotal) : '')
                        + ((spec.accounts.other_expense || []).length ? section('Other Expense', spec.accounts.other_expense) + sum('Total Other Expense', 'other_expense', subtotal) : '')
                        + sum(T('Net Other Income'), 'net_other_income', subtotal) : ''}
                    ${sum(T('Net Income'), 'net_income', 'font-weight:700; background:var(--primary-light);')}
                </tbody>
            </table>
        </div>`;
};

// The grid scrolls under its frozen header row and Account column: a cell
// Tab moved to was scrolled only into the grid, not out from under them
// (13 of 85 stops forward on P&L by Class sat under the Account column).
// Its scroll padding (style.css .grid-scroll) is the header row's height
// and the Account column's width, measured (layout sizes, not the
// dialog's transform) once the grid is drawn and when the window changes.
ReportsPage._gridFit = function () {
    const g = document.getElementById('grid-scroll');
    const corner = g && g.querySelector('thead th:first-child');
    const head = g && g.querySelector('thead');
    if (!corner || !head) return;
    g.style.setProperty('--grid-frozen', `${corner.offsetWidth}px`);
    g.style.setProperty('--grid-head', `${head.offsetHeight}px`);
};
// And Chromium's focus leaves a link it finds partly in view where it is:
// the Roofing heading and its column's cells, a pixel past the grid's
// right edge, rings and all, counted as in view. The keyboard's stop is
// brought in whole, its ring and halo with it (its scroll-margin); not a
// mouse's, which must stay under the pointer that pressed it. (App.init
// hands every focusin here, and every resize to _gridFit.)
ReportsPage._gridReveal = function (e) {
    const a = e.target, g = a && a.closest && a.closest('#grid-scroll');
    if (g && a !== g && App.keyboardFocus()) a.scrollIntoView({ block: 'nearest', inline: 'nearest' });
};

// Column i of the open grid: scrolled into view (the frozen Account
// column allowed for), highlighted, named in the picker and said aloud.
// Land on a column by its class's (job's) id: the class page's link to
// the grid says which column it means (#238). Not part of the address.
ReportsPage.gridJumpTo = function (id) {
    const k = (ReportsPage._grid.ids || []).indexOf(parseInt(id, 10));
    if (k >= 0) ReportsPage.gridGo(k);
};

ReportsPage.gridGo = function (i) {
    const g = ReportsPage._grid;
    if (!g.count) return;
    i = Math.max(0, Math.min(g.count - 1, i));
    g.current = i;
    $$('#report-content .pivot-grid .is-current').forEach(el => el.classList.remove('is-current'));
    $$(`#report-content .pivot-grid [data-col="${i}"]`).forEach(el => el.classList.add('is-current'));
    const th = $(`#report-content .pivot-grid thead th[data-col="${i}"]`);
    const box = $('#grid-scroll');
    if (th && box) {
        // the last column brings the Total column beside it into view
        const fixed = box.querySelector('thead th:first-child');
        const edgeCell = i === g.count - 1 ? box.querySelector('thead th:last-child') : th;
        const b = box.getBoundingClientRect();
        const edge = b.left + (fixed ? fixed.getBoundingClientRect().width : 0);
        const right = edgeCell.getBoundingClientRect().right;
        if (right > b.right) box.scrollLeft += right - b.right;
        const left = th.getBoundingClientRect().left;
        if (left < edge) box.scrollLeft += left - edge;
    }
    const sel = $('#grid-jump');
    if (sel && sel.value !== String(i)) {
        sel.value = String(i);
        if (sel._cbx) sel._cbx.sync();
    }
    const live = $('#grid-live');
    if (live) live.textContent = `Column ${i + 1} of ${g.count}: ${g.names[i]}`;
};

ReportsPage.gridStep = function (delta) {
    const g = ReportsPage._grid;
    if (!g.count) return;
    if (g.current < 0) ReportsPage.gridGo(delta < 0 ? g.count - 1 : 0);
    else ReportsPage.gridGo(g.current + delta);
};

// ←/→ one column, Home/End the first and last, Enter the column's own
// report. In the picker's box the arrows are the caret's while its list
// is open; closed, they move the column.
ReportsPage.gridKey = function (e) {
    const g = ReportsPage._grid;
    if (!g.count) return;
    const t = e.target;
    if (t && t.getAttribute && t.getAttribute('aria-expanded') === 'true') return;
    const inText = t && t.tagName === 'INPUT' && t.getAttribute('role') !== 'combobox';
    if (inText) return;
    switch (e.key) {
        case 'ArrowLeft': e.preventDefault(); ReportsPage.gridStep(-1); return;
        case 'ArrowRight': e.preventDefault(); ReportsPage.gridStep(1); return;
        case 'Home': e.preventDefault(); ReportsPage.gridGo(0); return;
        case 'End': e.preventDefault(); ReportsPage.gridGo(g.count - 1); return;
        case 'Enter': {
            if (t && t.getAttribute && t.getAttribute('role') === 'combobox') return;
            if (t && (t.tagName === 'BUTTON' || t.tagName === 'A')) return;
            if (g.current < 0) return;
            const a = $(`#report-content .pivot-grid thead th[data-col="${g.current}"] a`);
            if (a) { e.preventDefault(); a.click(); }
            return;
        }
        default:
    }
};

// Choosing the grid's columns (R3, #233; the same for jobs, #242): the
// toolbar's "Classes: All / Active only" select, "show classes with no
// activity", and a "Choose classes…" panel of checkboxes. Each choice is a
// param on the address (show=active, include_empty=true, class_ids=3,5)
// and rides into the saved report and the export links. `items` are every
// value, inactive ones marked; `key` is the ids' param name.
ReportsPage._columnChooser = function (spec) {
    const chosen = new Set(String(spec.params[spec.key] || '').split(',').filter(Boolean).map(Number));
    const nouns = escapeHtml(spec.nouns);
    const picks = spec.items.map(i => `<label><input type="checkbox" name="${spec.kind}_pick" value="${i.id}" ${chosen.has(i.id) ? 'checked' : ''}> ${escapeHtml(i.name)}${i.inactive ? ` <span style="color:var(--text-muted)">(${escapeHtml(spec.inactiveWord)})</span>` : ''}</label>`).join('');
    return `
        <div class="form-group">
            <label for="grid-show">${escapeHtml(spec.Nouns)}</label>
            <select id="grid-show" name="${spec.kind}_show" data-no-search onchange="ReportsPage.setParam('show', this.value === 'all' ? '' : this.value)">
                <option value="all" ${spec.params.show !== 'active' ? 'selected' : ''}>All (${escapeHtml(spec.inactiveWord)} too)</option>
                <option value="active" ${spec.params.show === 'active' ? 'selected' : ''}>Active only</option>
            </select>
        </div>
        <div class="form-group full-width">
            <div class="grid-step__buttons">
                <label style="font-weight:normal; text-transform:none; font-size:11px; display:inline-flex; gap:4px; align-items:center;"><input type="checkbox" id="grid-empty" ${spec.params.include_empty ? 'checked' : ''} onchange="ReportsPage.setParam('include_empty', this.checked ? 'true' : '')"> Show ${nouns} with no activity</label>
                <button type="button" class="btn btn-sm btn-secondary" id="grid-choose-btn" aria-expanded="false" aria-controls="grid-chooser" onclick="ReportsPage.toggleChooser()">Choose ${nouns}…</button>
                <span id="grid-choice" class="grid-live">${chosen.size ? `${chosen.size} chosen` : ''}</span>
            </div>
            <fieldset id="grid-chooser" class="grid-chooser" hidden>
                <legend>${escapeHtml(spec.Nouns)} to show</legend>
                <div class="grid-chooser__list">${picks}</div>
                <div class="grid-chooser__actions">
                    <button type="button" class="btn btn-sm btn-primary" onclick="ReportsPage.applyChooser('${spec.key}')">Apply</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="ReportsPage.checkAll(true)">All</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="ReportsPage.checkAll(false)">None</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="ReportsPage.checkAll(false); ReportsPage.applyChooser('${spec.key}')">Show every ${escapeHtml(spec.noun)}</button>
                </div>
            </fieldset>
        </div>`;
};

ReportsPage.toggleChooser = function () {
    const panel = $('#grid-chooser');
    const btn = $('#grid-choose-btn');
    if (!panel || !btn) return;
    panel.hidden = !panel.hidden;
    btn.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
    if (!panel.hidden) focusFor(panel.querySelector('input'), _byKeyboard());
};

ReportsPage.checkAll = function (on) {
    $$('#grid-chooser input[type="checkbox"]').forEach(c => { c.checked = on; });
};

// The ticked ids become the address's class_ids (job_ids); none ticked
// means every column, as before. The count beside the button is of the
// ticks alone ("2 chosen"): the grid's note counts the columns with
// activity in the period, and "2 of 15 chosen" beside "2 of 11 classes
// shown" read as two counts of one thing (NEW-28).
ReportsPage.applyChooser = function (key) {
    const ids = $$('#grid-chooser input[type="checkbox"]:checked').map(c => c.value);
    const where = $('#grid-choice');
    if (where) where.textContent = ids.length ? `${ids.length} chosen` : '';
    // the panel folds away and the button has the focus back
    const panel = $('#grid-chooser');
    const btn = $('#grid-choose-btn');
    if (panel) panel.hidden = true;
    if (btn) { btn.setAttribute('aria-expanded', 'false'); focusFor(btn, _byKeyboard()); }
    ReportsPage.setParam(key, ids.join(','));
};

// The column-choice params of an address or a saved report, as the
// toolbar and the query string carry them.
ReportsPage._columnParams = function (prefill, key) {
    const p = prefill || {};
    const params = {};
    if (p.show === 'active') params.show = 'active';
    if (p.include_empty === true || p.include_empty === 'true') params.include_empty = 'true';
    if (p[key]) params[key] = String(p[key]);
    return params;
};

ReportsPage._columnQs = function (params, key) {
    let qs = '';
    if (params.show === 'active') qs += '&show=active';
    if (params.include_empty) qs += '&include_empty=true';
    if (params[key]) qs += `&${key}=${encodeURIComponent(params[key])}`;
    return qs;
};

// Said above a grid that shows part of the company (R3): which part, and
// the company's own figure for the dates.
// A grid with no column: either nothing was posted in the period, or the
// chooser named classes (jobs) that have nothing in it — said, with the way
// out, rather than "No activity" under a note that counts the columns.
// (The server leaves a chosen class with nothing in the period out of the
// columns unless "show with no activity" is on, and names it in
// chosen_empty, NEW-26.)
// A few names, then "and n more": a dozen chosen-and-empty classes would
// otherwise fill the note (NEW-26 review).
ReportsPage._someNames = function (xs) {
    const names = (xs || []).map(escapeHtml);
    return names.length > 4 ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} more` : names.join(', ');
};

ReportsPage._emptyGrid = function (data, key, noun, nouns) {
    if (!data.filtered) return `<div class="empty-state"><p>No activity in this period</p></div>`;
    const names = ReportsPage._someNames(data.chosen_empty);
    // the way out unticks the chooser too, so its count agrees (NEW-28)
    return `<div class="empty-state grid-filtered" role="status"><p>None of the chosen ${escapeHtml(nouns)}${names ? ` (${names})` : ''} has activity in this period.
        <a href="javascript:void(0)" onclick="ReportsPage.checkAll(false); ReportsPage.applyChooser('${key}')">Show every ${escapeHtml(noun)}</a></p></div>`;
};

// "n of m shown" counts the columns with activity in the period, as m
// does; a column drawn empty (asked for) or a chosen class left out for
// having none is said separately, in the words the CSV and PDF use
// (ledger_exports.filtered_phrase).
ReportsPage._filteredNote = function (data, columns, nouns) {
    if (!data.filtered) return '';
    const shown = columns.filter(c => !c.empty).length;
    const drawnEmpty = columns.length - shown;
    const chosenEmpty = data.chosen_empty || [];
    const names = ReportsPage._someNames(chosenEmpty);
    let text = `Filtered: ${shown} of ${data.columns_total} ${escapeHtml(nouns)} shown`;
    if (drawnEmpty) text += `, plus ${drawnEmpty} with no activity in this period${names ? ` (${names})` : ''}`;
    else if (chosenEmpty.length) text += `; ${chosenEmpty.length} chosen ${chosenEmpty.length === 1 ? 'has' : 'have'} no activity in this period and ${chosenEmpty.length === 1 ? 'is' : 'are'} left out (${names})`;
    return `<div class="grid-filtered" role="status">${text} — these totals are for the ${escapeHtml(nouns)} shown, not the company. Company ${T('Net Income')} for these dates: <strong>${formatCurrency(data.unfiltered.net_income)}</strong>.</div>`;
};

// Class tracking: Profit & Loss split by the class dimension.
ReportsPage.profitLossByClass = async function (prefill) {
    const params = ReportsPage._columnParams(prefill, 'class_ids');
    let classes = [];
    try { classes = await API.get('/classes?include_archived=true'); } catch (e) { classes = []; }
    const toolbar = ReportsPage._columnChooser({
        kind: 'class', key: 'class_ids', noun: T('class'), nouns: T('classes'), Nouns: T('Classes'), inactiveWord: 'archived',
        items: classes.map(c => ({ id: c.id, name: c.name, inactive: !!c.is_archived })), params,
    });
    await ReportsPage.openPeriodModal(T("P&L by Class"), "this_year_to_date", async (period, range, p) => {
        const qs = `start_date=${range.start}&end_date=${range.end}${ReportsPage._columnQs(p, 'class_ids')}`;
        const data = await API.get(`/reports/profit-loss-by-class?${qs}`);
        const columns = data.classes;
        if (!columns.length) {
            return ReportsPage._emptyGrid(data, 'class_ids', T('class'), T('classes'));
        }
        // Accounts down the side and a column per class, as QuickBooks
        // lays it out; an amount opens the transactions behind it, and a
        // class's heading opens its own P&L (#213), on the report's period.
        const args = (...xs) => xs.map(x => JSON.stringify(x)).join(',');
        const dates = { period, start_date: range.start, end_date: range.end };
        return `${ReportsPage._exportButtons('profit-loss-by-class', qs)}
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}. Click an amount for the transactions behind it, or a heading for that column's own report.
            </div>
            ${ReportsPage._pivotGrid({
                title: T('P&L by Class'),
                noun: T('class'),
                kind: 'class',
                columns: columns.map(c => ({ ...c, id: c.class_id, name: c.archived ? `${c.class_name} (archived)` : c.class_name })),
                accounts: data.accounts,
                totals: ReportsPage._gridTotals(data), hasOther: !!data.has_other,
                note: ReportsPage._filteredNote(data, columns, T('classes')),
                totalLabel: data.filtered ? 'Total (shown)' : 'Total',
                drill: (a, c) => `ReportsPage.openDrillDown(${args(a.account_id, a.account_path || a.account_name, range.start, range.end, c.class_id, c.class_name, 'profit-loss-by-class')})`,
                head: (c) => `ReportsPage.profitLossOfClass(${args(c.class_id, c.class_name, dates)}, null, this)`,
                sum: (c) => `ReportsPage.profitLossOfClass(${args(c.class_id, c.class_name, dates)}, null, this)`,
            })}`;
    }, "Dates", false, { reportType: 'profit_loss_by_class', view: 'profit-loss-by-class', params, prefill, toolbar, wide: true, afterRender: (_c, first) => { ReportsPage._gridFit(); if (first && prefill && prefill.jump) ReportsPage.gridJumpTo(prefill.jump); } });
};

// P&L by Job (R13, #242): the same grid with a column per job and "No job"
// first. An amount opens the lines behind it for that job; a job's heading
// opens the job page on the report's dates, with a way back to the report.
// "No job" holds untagged activity and the applied-cost credits behind Job
// Cost Entries, so the column totals equal the plain P&L.
ReportsPage.profitLossByJob = async function (prefill) {
    const params = ReportsPage._columnParams(prefill, 'job_ids');
    let jobs = [];
    try { jobs = await API.get('/jobs?include_inactive=true'); } catch (e) { jobs = []; }
    // One name for a job everywhere (chooser, picker, headings): the job's
    // own, with the customer in brackets only where two jobs share a name.
    const counts = {};
    jobs.forEach(j => { counts[j.name] = (counts[j.name] || 0) + 1; });
    const jobLabel = (name, customer) => (counts[name] > 1 && customer) ? `${name} (${customer})` : name;
    const items = [{ id: 0, name: Terms.text('No job'), inactive: false }].concat(
        jobs.map(j => ({ id: j.id, name: jobLabel(j.name, j.customer_name), inactive: j.is_active === false }))
    );
    const toolbar = ReportsPage._columnChooser({
        kind: 'job', key: 'job_ids', noun: T('job'), nouns: T('jobs'), Nouns: T('Jobs'), inactiveWord: 'inactive',
        items, params,
    });
    await ReportsPage.openPeriodModal(`${T('P&L')} by ${T('Job')}`, "this_year_to_date", async (period, range, p) => {
        const qs = `start_date=${range.start}&end_date=${range.end}${ReportsPage._columnQs(p, 'job_ids')}`;
        const data = await API.get(`/reports/profit-loss-by-job?${qs}`);
        const columns = data.jobs;
        if (!columns.length) {
            return ReportsPage._emptyGrid(data, 'job_ids', T('job'), T('jobs'));
        }
        const args = (...xs) => xs.map(x => JSON.stringify(x)).join(',');
        const jobKey = (c) => c.job_id === null || c.job_id === undefined ? 0 : c.job_id;
        const jobUrl = (c) => `#/jobs/${c.job_id}?start_date=${range.start}&end_date=${range.end}&from=profit-loss-by-job`;
        return `${ReportsPage._exportButtons('profit-loss-by-job', qs)}
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}. Click an amount for the transactions behind it, or a heading for the ${escapeHtml(T('job'))}'s page on these dates.
                ${Terms.text('"No job" holds untagged activity')} <em>and</em> ${Terms.text('the applied-cost credits behind Job Cost Entries, so its costs can be negative and the totals still match the P&L')}.
            </div>
            ${ReportsPage._pivotGrid({
                title: `${T('P&L')} by ${T('Job')}`,
                noun: T('job'),
                kind: 'job',
                columns: columns.map(c => ({ ...c, id: jobKey(c), name: `${jobKey(c) ? jobLabel(c.job_name, c.customer_name) : c.job_name}${c.inactive ? ' (inactive)' : ''}` })),
                accounts: data.accounts,
                totals: ReportsPage._gridTotals(data), hasOther: !!data.has_other,
                note: ReportsPage._filteredNote(data, columns, T('jobs')),
                totalLabel: data.filtered ? 'Total (shown)' : 'Total',
                drill: (a, c) => `ReportsPage.openDrillDown(${args(a.account_id, a.account_path || a.account_name, range.start, range.end, null, null, 'profit-loss-by-job', { job_id: jobKey(c), job_name: c.job_name })})`,
                head: (c) => c.job_id ? `ReportsPage._leaveFrom(this);App.navigate(${JSON.stringify(jobUrl(c))})` : null,
            })}`;
    }, "Dates", false, { reportType: 'profit_loss_by_job', view: 'profit-loss-by-job', params, prefill, toolbar, wide: true, afterRender: () => ReportsPage._gridFit() });
};

// One class's own P&L (#213): the P&L by Class column, account by account,
// each opening its transactions for that class. A report in its own right
// since R5 (#235): the period shell, a class picker with ‹ Prev / Next ›
// through the classes, Save and export. Its address is
// #/reports/profit-loss-class?class_id=…&start_date=…&end_date=… (R7):
// opened from it, the class's name comes from the server. `prefill` is the
// query (period, start_date, end_date) the view starts on.
ReportsPage.profitLossOfClass = async function (classId, className, prefill, from = null, row = null) {
    classId = parseInt(classId, 10);
    if (!classId) { toast(`No ${T('class')} on this column`, 'error'); return; }
    // The grid's heading or a fund's row that opened it (`row`, its link's
    // `this`) is noted now, while it is the row clicked: the view's own
    // push comes after the class list is fetched, when the click is over
    // and, in WebKit, focus was never on the link (W-7 review).
    if (row) ReportsPage._leaveFrom(row);
    prefill = prefill || {};
    // `from` names the view that opened it when it is not the by-class grid
    // (Fund Balances, #239): the "Back to …" button returns there.
    const back = from && from !== 'profit-loss-by-class' && ReportsPage._view(from) ? from : null;
    // Prev / Next and "n of m" walk the P&L by Class grid's columns for the
    // dates (the classes with activity, in the grid's order), so the numbers
    // agree with the grid's "Column 2 of 12"; the picker still lists every
    // class, so one with nothing in the period can be chosen by name.
    let walk = [], walkKey = null;
    ReportsPage._classWalk = null;
    // The picker lists every active class, and this one if it is archived
    // (history keeps it); the type-ahead attaches to it by its name.
    let classes = [];
    try { classes = await API.get('/classes?include_archived=true'); } catch (e) { classes = []; }
    const listed = classes.filter(c => !c.is_archived || c.id === classId);
    if (!listed.some(c => c.id === classId)) listed.unshift({ id: classId, name: className || `${T('Class')} ${classId}` });
    const pickOpts = listed.map(c =>
        `<option value="${c.id}" ${c.id === classId ? 'selected' : ''}>${escapeHtml(c.name)}${c.is_archived ? ' (archived)' : ''}</option>`
    ).join('');
    const noun = T('class');
    const toolbar = `
        <div class="form-group">
            <label for="class-pl-pick">${T('Class')}</label>
            <select id="class-pl-pick" name="class_id" onchange="ReportsPage.setParam('class_id', parseInt(this.value, 10))">${pickOpts}</select>
        </div>
        <div class="form-group grid-step full-width">
            <span class="grid-step__buttons">
                <button type="button" class="btn btn-sm btn-secondary" aria-label="Previous ${escapeHtml(noun)}" onclick="ReportsPage.stepClass(-1)">&lsaquo; Prev ${escapeHtml(noun)}</button>
                <button type="button" class="btn btn-sm btn-secondary" aria-label="Next ${escapeHtml(noun)}" onclick="ReportsPage.stepClass(1)">Next ${escapeHtml(noun)} &rsaquo;</button>
                <span id="class-pl-where" class="grid-live" role="status" aria-live="polite"></span>
            </span>
        </div>`;
    const params = { class_id: classId, from: back || undefined };
    await ReportsPage.openPeriodModal(`${T('Profit & Loss')} — ${className || T('Class')}`, "this_year_to_date", async (period, range, p) => {
        const id = parseInt(p.class_id, 10) || classId;
        const qs = `start_date=${range.start}&end_date=${range.end}&class_id=${id}`;
        const data = await API.get(`/reports/profit-loss?${qs}`);
        $('#modal-title').textContent = `${T('Profit & Loss')} — ${data.class_name}`;
        const pick = $('#class-pl-pick');
        if (pick && pick.value !== String(id)) { pick.value = String(id); if (pick._cbx) pick._cbx.sync(); }
        const gridKey = `${range.start}|${range.end}`;
        if (walkKey !== gridKey) {
            try {
                const g = await API.get(`/reports/profit-loss-by-class?start_date=${range.start}&end_date=${range.end}`);
                walk = (g.classes || []).map(c => ({ id: c.class_id !== undefined ? c.class_id : c.id, name: c.class_name || c.name }));
            } catch (e) { walk = []; }
            walkKey = gridKey;
        }
        ReportsPage._classWalk = walk.length ? walk.map(c => c.id) : null;
        const order = walk.length ? walk : listed;
        const at = order.findIndex(c => c.id === id);
        const where = $('#class-pl-where');
        if (where) where.textContent = at >= 0 ? `${data.class_name}: ${at + 1} of ${order.length}` : `${data.class_name}: nothing in this period`;
        const args = (...xs) => xs.map(x => JSON.stringify(x)).join(',');
        // sub-accounts beneath their parents, as on the P&L (#416)
        const section = (items, key) => {
            const rows = (data.tree || {})[key];
            if (!items.length && !(rows && rows.length)) return '<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>';
            return ReportsPage._treeRows(rows || items, {
                blank: '<td></td>',
                cells: (r) => `<td class="amount">${formatCurrency(r.amount)}</td>`,
                call: (r) => escapeHtml(`ReportsPage.openDrillDown(${args(r.account_id, r.account_name, range.start, range.end, id, data.class_name, 'profit-loss-class')})`),
            });
        };
        const subtotal = (title, amount) => `<tr style="font-weight:600; background:var(--gray-50);"><td>${title}</td><td class="amount">${formatCurrency(amount)}</td></tr>`;
        return `<div id="class-pl-body">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                ${ReportsPage._backButton(back || 'profit-loss-by-class', back ? { start_date: range.start, end_date: range.end } : { period, start_date: range.start, end_date: range.end })}
                ${ReportsPage._exportButtons('profit-loss', qs)}
            </div>
            <p style="margin-bottom:12px; color:var(--gray-500); font-size:12px;">${T('Class')}: <strong>${escapeHtml(data.class_name)}</strong> &middot; ${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)} &middot; this ${escapeHtml(noun)} only, not the company total</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>
                    <tr><td><strong>${T('Income')}</strong></td><td></td></tr>
                    ${section(data.income, 'income')}
                    ${subtotal(T('Total Income'), data.total_operating_income)}
                    <tr><td><strong>Cost of Goods Sold</strong></td><td></td></tr>
                    ${section(data.cogs, 'cogs')}
                    ${subtotal('Gross Profit', data.operating_gross_profit)}
                    <tr><td><strong>Expenses</strong></td><td></td></tr>
                    ${section(data.expenses, 'expenses')}
                    ${subtotal('Total Expenses', data.total_operating_expenses)}
                    ${ReportsPage._plOther(data, (label) => `<tr><td><strong>${label}</strong></td><td></td></tr>`, section, (label, key) => subtotal(label, data[key]))}
                    <tr style="font-weight:700; font-size:15px; background:var(--primary-light);"><td>${T('Net Income')}</td><td class="amount">${formatCurrency(data.net_income)}</td></tr>
                </tbody>
            </table></div></div>`;
    }, "Dates", false, { reportType: 'profit_loss_class', view: 'profit-loss-class', params, prefill, toolbar });
};


// P&L Unclassified (#235): the class P&L pointed at the system-default
// class, the end-of-month cleanup list. Its card's address stands in for
// the class view's (replaced, not pushed, so Back is one step).
ReportsPage.profitLossUnclassified = async function (prefill) {
    let classes = [];
    try { classes = await API.get('/classes'); } catch (err) { toast(err.message || `Could not load the ${T('classes')}`, 'error'); return; }
    const uncat = classes.find(c => c.is_system_default);
    if (!uncat) { toast(`There is no default ${T('class')} to report on`, 'error'); return; }
    if (App.parseHash(location.hash || '#/').path === '/reports/profit-loss-unclassified') {
        history.replaceState(history.state, '', ReportsPage.viewUrl('profit-loss-class', { class_id: uncat.id, ...(prefill || {}) }));
    }
    await ReportsPage.profitLossOfClass(uncat.id, uncat.name, prefill);
};


// ‹ Prev / Next › through the class picker's list.
ReportsPage.stepClass = function (delta) {
    const pick = $('#class-pl-pick');
    if (!pick || !pick.options.length) return;
    // Along the grid's columns when the current class is one of them,
    // else along the picker's list.
    const walk = ReportsPage._classWalk;
    const cur = parseInt(pick.value, 10);
    if (walk && walk.length && walk.includes(cur)) {
        const k = Math.max(0, Math.min(walk.length - 1, walk.indexOf(cur) + delta));
        if (walk[k] === cur) return;
        const opt = Array.from(pick.options).find(o => parseInt(o.value, 10) === walk[k]);
        if (opt) { pick.value = opt.value; if (pick._cbx) pick._cbx.sync(); }
        ReportsPage.setParam('class_id', walk[k]);
        return;
    }
    const i = Math.max(0, Math.min(pick.options.length - 1, pick.selectedIndex + delta));
    if (i === pick.selectedIndex) return;
    pick.selectedIndex = i;
    if (pick._cbx) pick._cbx.sync();
    ReportsPage.setParam('class_id', parseInt(pick.value, 10));
};

// Back to P&L by Class / the General Ledger, on the dates each was opened
// with (#213, #224): now ReportsPage._backButton, through the view's
// address and the browser's history (R7). Kept by name for callers.
ReportsPage._backToByClass = function (startDate, endDate) {
    return ReportsPage._backButton('profit-loss-by-class', { start_date: startDate, end_date: endDate });
};
ReportsPage._backToGeneralLedger = function (startDate, endDate) {
    return ReportsPage._backButton('general-ledger', { start_date: startDate, end_date: endDate });
};

// Fixed assets: register totals per type for GL reconciliation.
ReportsPage.fixedAssetReconciliation = async function () {
    ReportsPage.setAddress('fixed-asset-reconciliation', {});
    const data = await API.get('/fixed-assets/reports/reconciliation');
    const rows = data.types.map(t => `<tr>
        <td>${escapeHtml(t.asset_type)}</td>
        <td class="amount">${t.asset_count}</td>
        <td class="amount">${formatCurrency(t.cost)}</td>
        <td class="amount">${formatCurrency(t.accumulated_depreciation)}</td>
        <td class="amount">${formatCurrency(t.book_value)}</td>
    </tr>`).join('');
    openModal('Fixed Asset Reconciliation', `
        <div class="table-container"><table>
            <thead><tr><th scope="col">Asset Type</th><th scope="col" class="amount">Assets</th><th scope="col" class="amount">Cost</th>
            <th scope="col" class="amount">Accum. Depr.</th><th scope="col" class="amount">Book Value</th></tr></thead>
            <tbody>${rows.length ? rows : '<tr><td colspan="5">No registered assets</td></tr>'}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td>Total</td><td></td>
                <td class="amount">${formatCurrency(data.total_cost)}</td>
                <td class="amount">${formatCurrency(data.total_accumulated)}</td>
                <td class="amount">${formatCurrency(data.total_book_value)}</td>
            </tr></tfoot>
        </table></div>
        <div style="font-size:11px; color:var(--gray-500); margin-top:8px;">
            Compare against the mapped fixed-asset and accumulated-depreciation
            GL accounts — differences mean unposted acquisitions or manual GL edits.
        </div>`);
};

// Reconciliation Reports (#395): every completed bank reconciliation, by
// account and statement date, each to its on-screen report (Banking's) or
// its PDF. Nothing is made here: a list over GET /banking/reconciliations.
ReportsPage.reconciliationReports = async function () {
    ReportsPage.setAddress('reconciliation-reports', {});
    const rows = await API.get('/banking/reconciliations?status=completed');
    rows.sort((a, b) => (a.account_name || '').localeCompare(b.account_name || '')
        || String(b.statement_date).localeCompare(String(a.statement_date)));
    const body = rows.length ? `<div class="table-container"><table>
        <thead><tr><th scope="col">Account</th><th scope="col">Statement date</th>
            <th scope="col" class="amount">Statement balance</th><th scope="col" class="amount">Cleared</th>
            <th scope="col">Completed</th><th scope="col">Actions</th></tr></thead>
        <tbody>${rows.map(r => `<tr>
            <td>${escapeHtml(r.account_name || '')}</td>
            <td>${formatDate(r.statement_date)}</td>
            <td class="amount">${formatCurrency(r.statement_balance)}</td>
            <td class="amount">${r.cleared_total === null ? '' : formatCurrency(r.cleared_total)}</td>
            <td>${r.completed_at ? formatDate(r.completed_at) : ''}</td>
            <td class="actions" style="white-space:nowrap;">
                <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.showReconReport(${r.id}, ReportsPage.reconciliationReports)">View</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="window.open('/api/banking/reconciliations/${r.id}/pdf', '_blank')">Save PDF</button>
            </td>
        </tr>`).join('')}</tbody></table></div>`
        : '<p>No completed reconciliations yet. Finish one from Banking → Reconcile and it is listed here.</p>';
    openModal('Reconciliation Reports', `${body}
        <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
};

// Financial statements pack — one PDF with P&L, Balance Sheet, Trial Balance.
ReportsPage.financialStatementsPdf = async function (prefill) {
    await ReportsPage.openPeriodModal("Financial Statements Pack", "this_year_to_date", async (_period, range) => {
        window.open(`/api/reports/financial-statements/pdf?start_date=${range.start}&end_date=${range.end}`, '_blank');
        // Said for both: a browser opens a tab, the desktop app saves the PDF,
        // opens it in a window of its own and says where it saved it (F24).
        return `<div style="font-size:12px;">The statements pack has opened as a PDF —
            ${T('P&L')} and Trial Balance for ${escapeHtml(range.start)} — ${escapeHtml(range.end)},
            ${T('Balance Sheet')} as of ${escapeHtml(range.end)}. Paper size follows
            Settings → Report PDF Paper Size.</div>`;
    }, "Dates", false, { view: 'financial-statements', prefill });
};

// With customer_id (from the customer's page, #238) the API keeps that
// customer's jobs only: the report says so, its total is labelled as that
// customer's, and "all jobs" drops the filter in place (address replaced).
ReportsPage.jobProfitability = async function (prefill) {
    const params = { customer_id: parseInt((prefill || {}).customer_id, 10) || null };
    await ReportsPage.openPeriodModal(T("Job Profitability"), "this_year_to_date", async (_period, range) => {
        const filter = params.customer_id ? `&customer_id=${params.customer_id}` : '';
        const [data, customer] = await Promise.all([
            API.get(`/reports/job-profitability?start_date=${range.start}&end_date=${range.end}${filter}`),
            params.customer_id ? API.get(`/customers/${params.customer_id}`).catch(() => null) : null,
        ]);
        const who = customer ? customer.name : (params.customer_id ? `${T('customer')} #${params.customer_id}` : '');
        const filtered = params.customer_id
            ? `<div class="hint" style="margin-bottom:8px;" data-filtered="customer">
                ${escapeHtml(T('Jobs'))} of <strong>${escapeHtml(who)}</strong> only — the totals below are this ${escapeHtml(T('customer'))}'s, not the company's.
                <a href="javascript:void(0)" onclick="ReportsPage.setParam('customer_id', null)">Show all ${escapeHtml(T('jobs'))}</a>
              </div>`
            : '';
        const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(1)}%`;
        // A job's row opens the job's page on the report's dates, with the way
        // back (its own address, so Back returns here); the customer's name
        // opens the customer's page without taking the row's click (R9).
        // "No job" is no document: its row opens P&L by Job on its column,
        // the accounts behind the untagged activity (#245).
        const dates = { period: _period, start_date: data.start_date, end_date: data.end_date };
        // The row left is the one clicked: the job's link passes itself, the
        // row its job's link (the customer's is the row's other link), so
        // Back puts focus there after a mouse click too (W-7 review).
        const jobCall = (j, from) => j.job_id
            ? `ReportsPage._leaveFrom(${from});App.navigate(${JSON.stringify(`#/jobs/${j.job_id}?start_date=${data.start_date}&end_date=${data.end_date}&from=job-profitability`)})`
            : `ReportsPage._leaveFrom(${from});App.navigate(${JSON.stringify(ReportsPage.viewUrl('profit-loss-by-job', { ...dates, job_ids: '0' }))})`;
        const customerCell = (j) => j.customer_id
            ? ReportsPage._rowLink(`event.stopPropagation(); ReportsPage.openCustomer(${j.customer_id}, this)`, j.customer_name || '', `customer:${j.customer_id}`)
            : escapeHtml(j.customer_name || '');
        const rows = data.jobs.map(j => `<tr style="cursor:pointer" onclick="${escapeHtml(jobCall(j, "this.querySelector('[data-row-key^=job]')"))}">
            <td>${customerCell(j)}</td>
            <td>${ReportsPage._rowLink(`event.stopPropagation(); ${jobCall(j, 'this')}`, j.job_name, `job:${j.job_id || 0}`)}</td>
            <td class="amount">${j.contract_amount !== null && j.contract_amount !== undefined ? formatCurrency(j.contract_amount) : ''}</td>
            <td class="amount">${formatCurrency(j.income)}</td>
            <td class="amount">${formatCurrency(j.total_costs)}</td>
            <td class="amount" style="font-weight:700;">${formatCurrency(j.net_income)}</td>
            <td class="amount">${pct(j.margin_pct)}</td>
        </tr>`).join('');
        return `${filtered}
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}${params.customer_id ? '' : ` · ${Terms.text('"No job" holds untagged activity')} <em>and</em> ${Terms.text('the applied-cost credits behind Job Cost Entries (labor, equipment, overhead applied to jobs), so its costs can be negative and the totals still match the P&L')}`}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">${T('Job')}</th><th scope="col" class="amount">Contract</th><th scope="col" class="amount">${T('Income')}</th>
                <th scope="col" class="amount">Costs</th><th scope="col" class="amount">Net</th><th scope="col" class="amount">Margin</th></tr></thead>
                <tbody>${rows.length ? rows : '<tr><td colspan="7">No activity in this period</td></tr>'}</tbody>
                <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                    <td colspan="3">${params.customer_id ? `Total — ${escapeHtml(who)}` : 'Total'}</td>
                    <td class="amount">${formatCurrency(data.total_income)}</td>
                    <td class="amount">${formatCurrency(data.total_costs)}</td>
                    <td class="amount">${formatCurrency(data.total_net_income)}</td>
                    <td></td>
                </tr></tfoot>
            </table></div>`;
    // No row is spotlighted: with customer_id the server already filters
    // the rows to the customer's jobs, and the first of several would be
    // "current" for no reason (R8 review).
    }, "Dates", false, { view: 'job-profitability', params, prefill, spotlight: false });
};

ReportsPage.jobBudgetVsActual = async function (prefill) {
    await ReportsPage.openPeriodModal(T("Job Budget vs Actual"), "this_year_to_date", async (_period, range) => {
        const data = await API.get(`/jobs/budget-vs-actual?start_date=${range.start}&end_date=${range.end}`);
        const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(1)}%`;
        const t = { revised: 0, committed: 0, actual: 0, projected: 0, variance: 0, act_revenue: 0 };
        const rows = data.map(j => {
            for (const k of Object.keys(t)) t[k] += j[k] || 0;
            return `<tr style="cursor:pointer" onclick="App.navigate('#/jobs/${j.job_id}')">
            <td>${escapeHtml(j.customer_name || '')}</td>
            <td>${escapeHtml(j.job_name)}</td>
            <td class="amount">${formatCurrency(j.revised)}</td>
            <td class="amount">${formatCurrency(j.committed)}</td>
            <td class="amount">${formatCurrency(j.actual)}</td>
            <td class="amount">${formatCurrency(j.projected)}</td>
            <td class="amount" style="font-weight:700;color:${j.revised && j.variance < 0 ? 'var(--text-danger)' : 'inherit'}">${formatCurrency(j.variance)}</td>
            <td class="amount">${pct(j.pct_used)}</td>
            <td class="amount">${formatCurrency(j.act_revenue)}</td>
        </tr>`; }).join('');
        return `
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                Actuals for ${escapeHtml(range.start)} — ${escapeHtml(range.end)}; budgets and committed cost are job-to-date. Click a job to drill down.
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">${T('Job')}</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th>
                <th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th><th scope="col" class="amount">Revenue</th></tr></thead>
                <tbody>${rows.length ? rows : '<tr><td colspan="9">No jobs</td></tr>'}</tbody>
                <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                    <td colspan="2">Total</td>
                    <td class="amount">${formatCurrency(t.revised)}</td><td class="amount">${formatCurrency(t.committed)}</td>
                    <td class="amount">${formatCurrency(t.actual)}</td><td class="amount">${formatCurrency(t.projected)}</td>
                    <td class="amount">${formatCurrency(t.variance)}</td><td></td><td class="amount">${formatCurrency(t.act_revenue)}</td>
                </tr></tfoot>
            </table></div>`;
    }, "Dates", false, { view: 'job-budget-vs-actual', prefill });
};



// ---------------------------------------------------------------------------
// Nonprofit statements — shown in place of the P&L and Balance Sheet cards
// when Settings -> Company Type is nonprofit. Each reconciles to the plain
// P&L / balance sheet (the server computes both from the same lines).
// ---------------------------------------------------------------------------
ReportsPage._nonprofitCards = function () {
    const card = ReportsPage._pick;
    return [
        card('statement-of-activities', 'Statement of Activities', 'Revenue, releases and expenses, with and without donor restrictions'),
        card('statement-of-financial-position', 'Statement of Financial Position', 'Assets, liabilities, and net assets by restriction'),
        card('fund-balances', 'Fund Balances', 'Each restricted fund: beginning, contributions, spent, released, ending'),
        card('functional-expenses', 'Statement of Functional Expenses', 'Program / management / fundraising by expense account (Form 990 Part IX)'),
        card('pledges', 'Pledge Report', 'Promised, received, written off and outstanding by donor and campaign'),
        card('giving-statements', 'Year-End Giving Statements', 'One statement per donor for the tax year — print the stack or email them all'),
    ];
};

ReportsPage.givingStatements = function (prefill) {
    const y = new Date().getFullYear();
    const picked = parseInt((prefill || {}).year, 10);
    const chosen = [y, y - 1, y - 2].includes(picked) ? picked : y - 1;
    ReportsPage.setAddress('giving-statements', { year: chosen });
    const opts = [y, y - 1, y - 2].map(v => `<option value="${v}" ${v === chosen ? 'selected' : ''}>${v}</option>`).join('');
    openModal('Year-End Giving Statements', `
        <div class="form-grid">
            <div class="form-group"><label for="gs-year">Tax year</label><select id="gs-year" onchange="ReportsPage.setAddress('giving-statements', { year: this.value })">${opts}</select></div>
        </div>
        <p style="font-size:12px;color:var(--gray-500);margin:8px 0;">Every ${T('customer')} with a gift in the year gets a statement: cash contributions with the deductible portion, non-cash gifts described without a value. ${T('Customers')} who opted out in their record are skipped when emailing.</p>
        <div id="gs-result" style="font-size:12px;margin:8px 0;"></div>
        <div class="form-actions">
            <button class="btn btn-secondary" onclick="window.open('/api/donors/giving-statements/pdf?year=' + $('#gs-year').value, '_blank')">Download all (PDF)</button>
            <button class="btn btn-primary" onclick="ReportsPage.emailGivingStatements()">Email all</button>
            <button class="btn btn-secondary" onclick="closeModal()">Close</button>
        </div>`);
};

ReportsPage.emailGivingStatements = async function () {
    const year = parseInt($('#gs-year').value);
    if (!confirm(`Email ${year} giving statements to every ${T('customer')} with a gift that year?`)) return;
    const box = $('#gs-result');
    box.textContent = 'Sending…';
    try {
        const r = await API.post('/donors/giving-statements/batch-email', { year });
        box.innerHTML = `Sent ${r.sent}, failed ${r.failed}, skipped ${r.skipped}.` + (r.errors.length ? `<ul>${r.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : '');
    } catch (err) { box.textContent = err.message; }
};

ReportsPage.pledges = async function (prefill) {
    await ReportsPage.openPeriodModal("Pledge Report", "this_year_to_date", async (_period, range) => {
        const qs = `start_date=${range.start}&end_date=${range.end}`;
        const d = await API.get(`/reports/pledges?${qs}`);
        const cols = ['pledged', 'invoiced', 'not_yet_invoiced', 'received', 'written_off', 'outstanding'];
        const cells = (r) => cols.map(c => `<td class="amount">${formatCurrency(r[c])}</td>`).join('');
        const donors = d.by_donor.map(g => `<tr style="font-weight:600; background:var(--gray-50);"><td>${escapeHtml(g.customer_name)}</td>${cells(g)}</tr>`
            + g.pledges.map(p => `<tr><td style="padding-left:24px;">${escapeHtml(p.label)} <span style="color:var(--gray-500)">· ${escapeHtml(p.class_name)}</span></td>${cells(p)}</tr>`).join('')).join('');
        const classes = d.by_class.map(g => `<tr><td>${escapeHtml(g.class_name)}</td>${cells(g)}</tr>`).join('');
        const head = `<thead><tr><th scope="col"></th><th scope="col" class="amount">Pledged</th><th scope="col" class="amount">Invoiced</th><th scope="col" class="amount">Not yet invoiced</th><th scope="col" class="amount">Received</th><th scope="col" class="amount">Written off</th><th scope="col" class="amount">Outstanding</th></tr></thead>`;
        const foot = `<tfoot><tr style="font-weight:700; background:var(--gray-50);"><td>Total</td>${cells(d.totals)}</tr></tfoot>`;
        return `${ReportsPage._exportButtons('pledges', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}</p>
            <h4 style="margin:8px 0 4px;font-size:12px;">By ${T('customer')}</h4>
            <div class="table-container"><table>${head}<tbody>${donors || `<tr><td colspan="7" style="color:var(--gray-400);">No pledges in this period</td></tr>`}</tbody>${foot}</table></div>
            <h4 style="margin:12px 0 4px;font-size:12px;">By campaign (${T('class')})</h4>
            <div class="table-container"><table>${head}<tbody>${classes || `<tr><td colspan="7" style="color:var(--gray-400);">—</td></tr>`}</tbody></table></div>`;
    }, "Dates", false, { reportType: 'pledges', view: 'pledges', prefill });
};

// Possible Duplicates (#414): one bank or card account's entries with the
// same amount on the same side and the same or a similar payee a few days
// apart, grouped. Each entry opens, or voids through its own void route
// (the report names it: void_path), so a reconciled entry and the closing
// date follow the usual rules; "Not a duplicate" keeps a group off the
// report for good. Nothing is voided or hidden by the report itself.
ReportsPage._DUP_SOURCE = { imported: 'Imported', keyed: 'Keyed', migrated: 'Migrated' };
ReportsPage.possibleDuplicates = async function (prefill) {
    const p = prefill || {};
    let accounts = [];
    try { accounts = await API.get('/accounts?bank=1'); } catch (e) { accounts = []; }
    const params = {
        account_id: p.account_id || (accounts.length ? String(accounts[0].id) : ''),
        include_dismissed: ['1', 'true'].includes(String(p.include_dismissed)) ? '1' : '',
    };
    const label = (a) => `${a.account_number ? a.account_number + ' - ' : ''}${a.name}`;
    const options = accounts.map(a => `<option value="${a.id}" ${String(a.id) === String(params.account_id) ? 'selected' : ''}>${escapeHtml(label(a))}</option>`).join('');
    const box = 'font-weight:normal; text-transform:none; font-size:12px; display:inline-flex; gap:6px; align-items:center;';
    const toolbar = `
        <div class="form-group"><label for="dup-account">Account</label>
            <select id="dup-account" data-no-search onchange="ReportsPage.setParam('account_id', this.value)">${options}</select></div>
        <div class="form-group" style="justify-content:flex-end;">
            <label style="${box}"><input type="checkbox" id="dup-show-dismissed" ${params.include_dismissed ? 'checked' : ''}
                onchange="ReportsPage.setParam('include_dismissed', this.checked ? '1' : '')"> Show groups marked not a duplicate</label></div>`;
    await ReportsPage.openPeriodModal("Possible Duplicates", "this_year_to_date", async (_period, range, prm) => {
        if (!prm.account_id) return '<div class="empty-state"><p>There is no bank or credit-card account.</p></div>';
        const qs = `account_id=${encodeURIComponent(prm.account_id)}&start_date=${range.start}&end_date=${range.end}${prm.include_dismissed ? '&include_dismissed=true' : ''}`;
        const data = await API.get(`/reports/possible-duplicates?${qs}`);
        const groups = data.groups.map((g, n) => {
            const action = g.dismissed
                ? `<button class="btn btn-sm btn-secondary" onclick="ReportsPage.restoreDuplicate(${g.dismissal_id})">Put back</button>`
                : `<button class="btn btn-sm btn-secondary" data-dup-key="${escapeHtml(g.key)}" onclick="ReportsPage.dismissDuplicate(${data.account.id}, this.dataset.dupKey)">Not a duplicate</button>`;
            const head = `<tr class="dup-group" data-dup-group="${escapeHtml(g.key)}" style="font-weight:600; background:var(--gray-50);">
                <td colspan="7">Group ${n + 1} · ${escapeHtml(g.side_label)} ${formatCurrency(g.amount)} · ${g.entries.length} entries${g.any_reconciled ? ' · a reconciled entry' : ''}${g.dismissed ? ` · marked not a duplicate${g.dismissed_by ? ` by ${escapeHtml(g.dismissed_by)}` : ''}` : ''}</td>
                <td style="text-align:right; white-space:nowrap;">${action}</td></tr>`;
            const lines = g.entries.map(e => {
                const voidCall = escapeHtml(`ReportsPage.voidDuplicate(${JSON.stringify(e.void_path)})`);
                return `<tr data-dup-entry="${e.transaction_id}">
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td>${escapeHtml(e.memo || '')}</td>
                    <td>${escapeHtml(ReportsPage._DUP_SOURCE[e.source] || e.source)} <span style="color:var(--gray-500);">· ${escapeHtml(sourceWord(e.source_type || 'journal'))}</span></td>
                    <td>${e.reconciled ? 'Reconciled' : (e.cleared ? 'Cleared' : '')}</td>
                    <td class="amount">${formatCurrency(g.amount)}</td>
                    <td style="text-align:right; white-space:nowrap;">
                        <a class="btn btn-sm btn-secondary" href="${escapeHtml(e.link)}" data-row-key="dup:${e.transaction_id}" onclick="ReportsPage._leaveFrom(this)">Open</a>
                        ${e.voidable ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="${voidCall}">Void</button>` : ''}</td>
                </tr>`;
            }).join('');
            return head + lines;
        }).join('');
        // The count is the groups still open; the marked ones are counted
        // apart, shown or not, so the line never reads "0 groups" above a
        // marked group the box is showing (2.27.0 gate, W-32, #458).
        const hidden = !data.dismissed_count ? ''
            : data.include_dismissed
                ? ` · ${data.dismissed_count} marked not a duplicate (shown)`
                : ` · ${data.dismissed_count} marked not a duplicate, not shown`;
        return `${ReportsPage._exportButtons('possible-duplicates', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">${escapeHtml(data.account.number || '')} ${escapeHtml(data.account.name)} · ${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)} · ${data.count} ${data.count === 1 ? 'group' : 'groups'}${hidden}</p>
            <p style="margin-bottom:12px; font-size:12px; color:var(--gray-500);">Entries with the same amount on the same side and the same or a similar payee, at most ${data.window_days} days apart. Nothing here is voided unless you void it.</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Num</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">Source</th><th scope="col">Reconciled</th><th scope="col" class="amount">Amount</th><th scope="col"></th></tr></thead>
                <tbody>${groups || '<tr><td colspan="8" style="text-align:center; color:var(--gray-400);">No possible duplicates on this account in this period</td></tr>'}</tbody>
            </table></div>`;
    }, "Dates", false, { reportType: 'possible_duplicates', view: 'possible-duplicates', params, toolbar, wide: true, spotlight: false, prefill: p });
};

ReportsPage.voidDuplicate = async function (path) {
    if (!confirm('Void this entry? A reversing entry is posted; the original stays in the ledger.')) return;
    try {
        await API.post(path);
        toast('Voided');
        ReportsPage.refresh();
    } catch (err) { toast(err.message, 'error'); }
};

ReportsPage.dismissDuplicate = async function (accountId, key) {
    try {
        await API.post('/reports/possible-duplicates/dismiss', {
            account_id: accountId, transaction_ids: String(key).split(',').map(Number),
        });
        toast('Marked not a duplicate');
        ReportsPage.refresh();
    } catch (err) { toast(err.message, 'error'); }
};

ReportsPage.restoreDuplicate = async function (id) {
    try {
        await API.del(`/reports/possible-duplicates/dismissals/${id}`);
        toast('Put back on the report');
        ReportsPage.refresh();
    } catch (err) { toast(err.message, 'error'); }
};

// Save PDF and Save CSV for a report with both routes; a report with only
// a CSV (the agings, #393) passes kinds = ['csv'].
ReportsPage._exportButtons = function (path, qs, kinds = ['pdf', 'csv']) {
    const pdf = kinds.includes('pdf') ? `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/reports/${path}/pdf?${qs}','_blank')">Save PDF</button>` : '';
    const csv = kinds.includes('csv') ? `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/reports/${path}/csv?${qs}','_blank')">Save CSV</button>` : '';
    return `<div style="text-align:right; margin-bottom:6px; margin-left:auto;">
        ${pdf}
        ${csv}
    </div>`;
};

// Prior-year comparison on the Profit & Loss and the Balance Sheet: the
// params a view starts with (from its address or saved report), the two
// toolbar checkboxes, and the money cells — Amount alone, or Current |
// Prior Year, plus $ Change | % Change unless they are turned off.
ReportsPage._viewReturn = {};
ReportsPage._compareParams = function (prefill) {
    const p = prefill || {};
    const out = {};
    if (p.compare === 'prior_year') out.compare = 'prior_year';
    if (['1', 'true'].includes(String(p.hide_changes))) out.hide_changes = '1';
    return out;
};
// What Back from a drill-down returns to: the statement's own choices
// (sort, adjusting entries, comparison), not the drill-down's dates.
ReportsPage._cmpKeep = function (params) {
    const keep = {};
    for (const k of ['sort', 'exclude_adjusting', 'compare', 'hide_changes']) {
        if (params && params[k]) keep[k] = params[k];
    }
    return keep;
};
ReportsPage._compareToolbar = function (params, label) {
    const box = 'font-weight:normal; text-transform:none; font-size:12px; gap:6px; align-items:center;';
    return `<div class="form-group" style="justify-content:flex-end; gap:4px;">
        <label style="${box} display:inline-flex;"><input type="checkbox" id="cmp-prior" ${params.compare ? 'checked' : ''}
            onchange="ReportsPage.setParam('compare', this.checked ? 'prior_year' : '')"> ${escapeHtml(label)}</label>
        <label id="cmp-changes-wrap" style="${box} display:${params.compare ? 'inline-flex' : 'none'};"><input type="checkbox" id="cmp-changes" ${params.hide_changes ? '' : 'checked'}
            onchange="ReportsPage.setParam('hide_changes', this.checked ? '' : '1')"> Show $ and % change</label>
    </div>`;
};
// The change checkbox only means something while comparing, and so does
// the wide dialog: five columns need it, two do not, and a dialog that is
// wide for no reason reaches over the sidebar and takes the Open links from
// under the pointer (test_browser_open_tabs; the #367 review). The width
// follows the choice, and Back is re-measured against it.
ReportsPage._cmpSync = function (params) {
    const wrap = $('#cmp-changes-wrap');
    if (wrap) wrap.style.display = params.compare ? 'inline-flex' : 'none';
    const modal = $('#modal');
    if (modal) modal.classList.toggle('modal--wide', params.compare === 'prior_year');
    if (window.App && App.syncBack) App.syncBack();
};
ReportsPage._cmpQs = function (params) {
    if (!params || params.compare !== 'prior_year') return '';
    return `&compare=prior_year${params.hide_changes ? '&hide_changes=1' : ''}`;
};
ReportsPage._cmpCols = (prior, show) => (prior ? (show ? 5 : 3) : 2);
ReportsPage._cmpHead = function (prior, show) {
    if (!prior) return `<th scope="col" class="amount">Amount</th>`;
    return `<th scope="col" class="amount">Current</th><th scope="col" class="amount">Prior Year</th>`
        + (show ? `<th scope="col" class="amount">$ Change</th><th scope="col" class="amount">% Change</th>` : '');
};
ReportsPage._cmpPct = function (cur, before) {
    cur = Number(cur || 0); before = Number(before || 0);
    if (Math.abs(before) < 0.005) return '&mdash;';
    const v = (cur - before) / Math.abs(before) * 100;
    return `${v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
};
// priorDrill: an escaped onclick that opens last year's transactions for
// the line, or null for a total.
ReportsPage._cmpCells = function (cur, before, prior, show, priorDrill) {
    if (!prior) return `<td class="amount">${formatCurrency(cur)}</td>`;
    const priorCell = priorDrill
        ? `<a href="javascript:void(0)" style="color:inherit; text-decoration:none;" title="Prior-year transactions" onclick="${priorDrill}">${formatCurrency(before || 0)}</a>`
        : formatCurrency(before || 0);
    const cells = `<td class="amount">${formatCurrency(cur)}</td><td class="amount">${priorCell}</td>`;
    if (!show) return cells;
    const diff = Number(cur || 0) - Number(before || 0);
    return `${cells}<td class="amount">${formatCurrency(diff)}</td><td class="amount">${ReportsPage._cmpPct(cur, before)}</td>`;
};

// Prior-year comparison for the two statements a treasurer reads side by
// side. The choice is remembered for the session and rides on the export
// links, so the PDF and CSV carry the same columns as the screen.
ReportsPage._compare = { statement_of_activities: false, functional_expenses: false };
ReportsPage.compareToggleHtml = function (key) {
    return `<label style="font-weight:normal; font-size:12px; margin-right:auto;"><input type="checkbox" ${ReportsPage._compare[key] ? 'checked' : ''} onchange="ReportsPage._compare['${key}']=this.checked; $('#report-period-select').dispatchEvent(new Event('change'))"> Compare to prior year</label>`;
};

ReportsPage.statementOfActivities = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Activities", "this_year_to_date", async (_period, range) => {
        const cmp = ReportsPage._compare.statement_of_activities;
        const qs = `start_date=${range.start}&end_date=${range.end}${cmp ? '&compare=prior_year' : ''}`;
        const d = await API.get(`/reports/statement-of-activities?${qs}`);
        const t = d.totals;
        const pt = cmp && d.prior ? d.prior.totals : {};
        const cols = cmp ? 6 : 4;
        const line = (label, w, r, tot, prior, style = '') => `<tr style="${style}"><td>${label}</td><td class="amount">${formatCurrency(w)}</td><td class="amount">${formatCurrency(r)}</td><td class="amount">${formatCurrency(tot)}</td>${cmp ? `<td class="amount">${formatCurrency(prior || 0)}</td><td class="amount">${formatCurrency((tot || 0) - (prior || 0))}</td>` : ''}</tr>`;
        // sub-accounts beneath their parents, a total per family (#416)
        const rows = (items, key) => {
            const tree = (d.tree || {})[key];
            if (!items.length && !(tree && tree.length)) return `<tr><td colspan="${cols}" style="color:var(--gray-400);">None</td></tr>`;
            return (tree || items).map(r => {
                const pad = 24 + 18 * (r.depth || 0);
                const label = escapeHtml(r.label || r.account_name);
                if (r.kind === 'heading') return `<tr class="pl-family"><td style="padding-left:${pad}px; font-weight:600;">${label}</td>${'<td></td>'.repeat(cols - 1)}</tr>`;
                const text = r.kind === 'direct' ? `<em>${label}</em>` : label;
                return line(`<span style="padding-left:${pad}px; display:inline-block;">${text}</span>`, r.without, key === 'expenses' ? 0 : r.with, r.total, r.prior_total, r.kind === 'total' ? 'font-weight:600;' : '');
            }).join('');
        };
        const head = (label) => `<tr><td><strong>${label}</strong></td>${'<td></td>'.repeat(cols - 1)}</tr>`;
        return `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">${ReportsPage.compareToggleHtml('statement_of_activities')}${ReportsPage._exportButtons('statement-of-activities', qs)}</div>
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}${cmp && d.prior ? ` · prior year ${formatDate(d.prior.start_date)} &mdash; ${formatDate(d.prior.end_date)}` : ''}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col"></th><th scope="col" class="amount">Without Donor Restrictions</th><th scope="col" class="amount">With Donor Restrictions</th><th scope="col" class="amount">Total</th>${cmp ? '<th scope="col" class="amount">Prior year</th><th scope="col" class="amount">Change</th>' : ''}</tr></thead>
                <tbody>
                    ${head('Revenue &amp; Support')}
                    ${rows(d.revenue, 'revenue')}
                    ${line('Total Revenue &amp; Support', t.revenue_without, t.revenue_with, t.revenue, pt.revenue, 'font-weight:600; background:var(--gray-50);')}
                    ${line('Net assets released from restrictions', d.releases.without, d.releases.with, 0, 0)}
                    ${head('Expenses')}
                    ${rows(d.expenses, 'expenses')}
                    ${line('Total Expenses', t.expenses, 0, t.expenses, pt.expenses, 'font-weight:600; background:var(--gray-50);')}
                    ${line('Change in Net Assets', t.change_without, t.change_with, t.change_total, pt.change_total, 'font-weight:700; font-size:15px; background:var(--primary-light);')}
                </tbody>
            </table></div>`;
    }, "Dates", false, { reportType: 'statement_of_activities', view: 'statement-of-activities', prefill });
};

ReportsPage.statementOfFinancialPosition = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Financial Position", "this_year_to_date", async (_period, params) => {
        const qs = `as_of_date=${params.as_of_date}`;
        const d = await API.get(`/reports/statement-of-financial-position?${qs}`);
        const drillCall = (i) => escapeHtml(`ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_name)},null,${JSON.stringify(params.as_of_date)},null,null,'statement-of-financial-position')`);
        const section = (items, pad = 24) => items.map(i => `<tr><td style="padding-left:${pad}px;">
                ${i.account_id ? `<a href="javascript:void(0)" style="color:var(--text-link); text-decoration:none;" onclick="${drillCall(i)}">${escapeHtml(i.account_name)}</a>` : escapeHtml(i.account_name)}
                </td><td class="amount">${formatCurrency(i.amount)}</td></tr>`).join('') || `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
        const sub = (label, v) => `<tr style="font-weight:600; background:var(--gray-50);"><td>${label}</td><td class="amount">${formatCurrency(v)}</td></tr>`;
        // Current / Fixed / Other Assets, Current / Long Term Liabilities (#390)
        const grouped = (side) => {
            const groups = (d.groups || {})[side];
            if (!groups || !groups.length) return section(d[side]);
            return groups.map(g => `<tr class="bs-group"><td style="padding-left:16px; font-weight:600;">${escapeHtml(g.heading)}</td><td></td></tr>`
                + section(g.lines, 36)
                + `<tr style="font-weight:600;"><td style="padding-left:16px;">Total ${escapeHtml(g.heading)}</td><td class="amount">${formatCurrency(d[g.total_key])}</td></tr>`).join('');
        };
        return `${ReportsPage._exportButtons('statement-of-financial-position', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(d.as_of_date)}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>
                    <tr><td><strong>Assets</strong></td><td></td></tr>${grouped('assets')}${sub('Total Assets', d.total_assets)}
                    <tr><td><strong>Liabilities</strong></td><td></td></tr>${grouped('liabilities')}${sub('Total Liabilities', d.total_liabilities)}
                    <tr><td><strong>Net Assets</strong></td><td></td></tr>${section(d.net_assets)}
                    ${sub('Net Assets Without Donor Restrictions', d.net_assets_without)}
                    ${sub('Net Assets With Donor Restrictions', d.net_assets_with)}
                    ${sub('Total Net Assets', d.total_net_assets)}
                    <tr style="font-weight:700; font-size:15px; background:var(--primary-light);"><td>Liabilities + Net Assets</td><td class="amount">${formatCurrency(d.total_liabilities_and_net_assets)}</td></tr>
                </tbody>
            </table></div>`;
    }, "As of", true, { reportType: 'statement_of_financial_position', view: 'statement-of-financial-position', prefill });
};

ReportsPage.fundBalances = async function (prefill) {
    await ReportsPage.openPeriodModal("Fund Balances", "this_year_to_date", async (_period, range) => {
        const qs = `start_date=${range.start}&end_date=${range.end}`;
        const d = await API.get(`/reports/fund-balances?${qs}`);
        const keys = ['beginning', 'contributions', 'expenses', 'releases', 'ending', 'unreleased'];
        // A fund's name opens the fund's own P&L for the report's dates (R9),
        // with "Back to Fund Balances"; the Unassigned row has no class
        const name = (f) => f.class_id
            ? ReportsPage._rowLink(`ReportsPage.profitLossOfClass(${f.class_id},${JSON.stringify(f.class_name)},${JSON.stringify({ start_date: range.start, end_date: range.end })},'fund-balances', this)`, f.class_name, `class:${f.class_id}`)
            : escapeHtml(f.class_name);
        const row = (f, style = '') => `<tr style="${style}"><td>${name(f)}${f.donor_name ? `<div style="font-size:10px;color:var(--gray-500)">${escapeHtml(f.donor_name)}</div>` : ''}</td>${keys.map(k => `<td class="amount">${formatCurrency(f[k])}</td>`).join('')}</tr>`;
        const rows = d.funds.map(f => row(f)).join('') + (d.unassigned ? row(d.unassigned, 'font-style:italic') : '');
        return `${ReportsPage._exportButtons('fund-balances', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)} · restricted ${T('classes')} only</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Class')}</th><th scope="col" class="amount">Beginning</th><th scope="col" class="amount">Contributions</th><th scope="col" class="amount">Spent</th><th scope="col" class="amount">Released</th><th scope="col" class="amount">Ending</th><th scope="col" class="amount" title="Spent but not yet released">Unreleased</th></tr></thead>
                <tbody>${rows || `<tr><td colspan="7" style="color:var(--gray-400);">No restricted ${T('classes')} yet</td></tr>`}</tbody>
                <tfoot>${row({ class_name: 'Total', ...d.totals }, 'font-weight:700; background:var(--gray-50);')}</tfoot>
            </table></div>`;
    }, "Dates", false, { reportType: 'fund_balances', view: 'fund-balances', prefill });
};

ReportsPage.functionalExpenses = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Functional Expenses", "this_year_to_date", async (_period, range) => {
        const cmp = ReportsPage._compare.functional_expenses;
        const qs = `start_date=${range.start}&end_date=${range.end}${cmp ? '&compare=prior_year' : ''}`;
        const d = await API.get(`/reports/functional-expenses?${qs}`);
        const keys = ['total', 'program', 'management', 'fundraising', 'unassigned'];
        const pt = cmp && d.prior ? d.prior.totals : {};
        const row = (label, r, style = '', prior = null) => { const pv = prior != null ? prior : (r.prior_total || 0); return `<tr style="${style}"><td>${label}</td>${keys.map(k => `<td class="amount">${formatCurrency(r[k])}</td>`).join('')}${cmp ? `<td class="amount">${formatCurrency(pv)}</td><td class="amount">${formatCurrency(r.total - pv)}</td>` : ''}</tr>`; };
        const rows = d.rows.map(r => row(escapeHtml(`${r.account_number || ''} ${r.account_name}`.trim()), r)).join('');
        const programs = d.programs.length ? `<h4 style="margin:12px 0 4px;font-size:12px;">Program services by program</h4>
            <div class="table-container"><table><thead><tr><th scope="col">Program</th><th scope="col" class="amount">Amount</th></tr></thead>
            <tbody>${d.programs.map(p => `<tr><td>${escapeHtml(p.class_name)}</td><td class="amount">${formatCurrency(p.amount)}</td></tr>`).join('')}</tbody></table></div>` : '';
        return `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">${ReportsPage.compareToggleHtml('functional_expenses')}${ReportsPage._exportButtons('functional-expenses', qs)}</div>
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}${cmp && d.prior ? ` · prior year ${formatDate(d.prior.start_date)} &mdash; ${formatDate(d.prior.end_date)}` : ''}${d.totals.unassigned ? ` · <span style="color:var(--danger)">${formatCurrency(d.totals.unassigned)} still unassigned — run an allocation rule</span>` : ''}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Expense</th><th scope="col" class="amount">Total</th><th scope="col" class="amount">Program</th><th scope="col" class="amount">Management</th><th scope="col" class="amount">Fundraising</th><th scope="col" class="amount">Unassigned</th>${cmp ? '<th scope="col" class="amount">Prior year</th><th scope="col" class="amount">Change</th>' : ''}</tr></thead>
                <tbody>${rows || `<tr><td colspan="${cmp ? 8 : 6}" style="color:var(--gray-400);">No expenses in this period</td></tr>`}</tbody>
                <tfoot>${row('Total', d.totals, 'font-weight:700; background:var(--gray-50);', pt.total || 0)}</tfoot>
            </table></div>${programs}`;
    }, "Dates", false, { reportType: 'functional_expenses', view: 'functional-expenses', prefill });
};
