/**
 * IIF Import/Export — QuickBooks Desktop Interoperability
 *
 * In QB2003: Import: File > Utilities > Import > IIF Files
 * Export: File > Utilities > Export > Lists to IIF Files
 *
 * The original DLL was a 342KB mess of fscanf() calls with no error
 * handling. A tab character in a company name would crash it. We do better.
 */
const IIFPage = {
    _selectedFile: null,
    _validated: false,

    async render() {
        return `
            <div class="page-header">
                <h2>Interoperability</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    IIF Import/Export &mdash; Works with QuickBooks Desktop
                </div>
            </div>

            <div class="iif-sections">
                <!-- Export Section -->
                <div class="iif-section">
                    <h3>&#9660; Export to IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Download Slowbooks data as .iif files for import into QuickBooks Desktop
                        via File &gt; Utilities &gt; Import &gt; IIF Files.
                    </p>

                    <div style="margin-bottom:10px;">
                        <button class="btn btn-primary" style="width:100%;" onclick="IIFPage.exportAll()">
                            Export All Data
                        </button>
                    </div>

                    <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                        Export Individual Sections
                    </div>
                    <div class="iif-export-grid">
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('accounts')">Accounts</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('customers')">Customers</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('vendors')">Vendors</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('items')">Items</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('estimates')">Estimates</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('classes')">Classes</button>
                    </div>

                    <div style="margin-top:12px; padding-top:10px; border-top:1px solid var(--panel-border);">
                        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                            Invoices &amp; Payments (with date range)
                        </div>
                        <div class="iif-date-range">
                            <label>From</label>
                            <input type="date" id="iif-date-from">
                            <label>To</label>
                            <input type="date" id="iif-date-to">
                        </div>
                        <div style="display:flex; gap:6px;">
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('invoices')">Invoices</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('payments')">Payments</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('sales-receipts')">Sales Receipts</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('bills')">Bills</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('deposits')">Deposits</button>
                        </div>
                        <div style="display:flex; gap:6px; margin-top:6px;">
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('register-entries')">Checks &amp; Card Charges</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('transfers')">Transfers</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('bill-payments')">Bill Payments</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('journal-entries')">Journal Entries</button>
                        </div>
                    </div>
                </div>

                <!-- Import Section -->
                <div class="iif-section" data-write>
                    <h3>&#9650; Import from IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Upload .iif files exported from QuickBooks Desktop
                        via File &gt; Utilities &gt; Export &gt; Lists to IIF Files. Transaction
                        blocks come in too: invoices, payments, sales receipts, estimates, bills,
                        deposits, checks, card charges, transfers, bill payments (as journal
                        entries on the bank and Accounts Payable) and general journal entries.
                        A block already here posts nothing twice.
                    </p>
                    <label style="display:flex; align-items:center; gap:6px; font-size:11px; margin-bottom:8px;">
                        <input type="checkbox" id="iif-adjusting">
                        This is an accountant's file: post its GENERAL JOURNAL blocks as adjusting entries
                    </label>

                    <div id="iif-dropzone" class="iif-dropzone"
                         onclick="document.getElementById('iif-file-input').click()"
                         ondragover="IIFPage.handleDragOver(event)"
                         ondragleave="IIFPage.handleDragLeave(event)"
                         ondrop="IIFPage.handleDrop(event)">
                        <div class="iif-dropzone-icon">&#9783;</div>
                        <div class="iif-dropzone-text">
                            <strong>Click to browse</strong> or drag &amp; drop an .iif file here
                        </div>
                    </div>
                    <input type="file" id="iif-file-input" accept=".iif" style="display:none;"
                           onchange="IIFPage.handleFileSelect(event)">

                    <div id="iif-file-info" style="display:none;"></div>

                    <div id="iif-import-actions" class="iif-import-actions" style="display:none;">
                        <button class="btn btn-secondary" onclick="IIFPage.validateFile()">Validate</button>
                        <button class="btn btn-primary" id="iif-import-btn" onclick="IIFPage.importFile()" disabled>Import</button>
                        <button class="btn" onclick="IIFPage.clearFile()" style="margin-left:auto;">Clear</button>
                    </div>

                    <div id="iif-validation-result"></div>
                    <div id="iif-import-result"></div>
                </div>

                <!-- QuickBooks Report CSV import -->
                <div class="iif-section" data-write>
                    <h3>&#9635; Import from Report CSV</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        QuickBooks Desktop can't export transactions to IIF — export a detail
                        <strong>report</strong> to CSV instead and upload it here. The report type is
                        detected automatically: <strong>Transaction Detail</strong> filtered to Sales
                        Receipt or Invoice (a receipt imports as a paid sale + payment; an invoice with
                        its lines, its Customer:Job and its number, open, partly paid or paid by the
                        report's <strong>Open Balance</strong> column — add that column, or every
                        invoice comes in open; upload again with it and the open ones here are marked
                        paid), <strong>Deposit Detail</strong>, or <strong>Check Detail</strong> (both
                        import as journal entries on your bank account), or <strong>Transaction Detail
                        by Account</strong>, one year of dates per file (every transaction of every type
                        as a journal entry; import the lists IIF first); or, for timesheets, <strong>Time by Job Detail</strong> (Reports > Jobs, Time &amp; Mileage; a Time by Name report grouped by job reads too), saved as CSV or Excel: every entry becomes an approved time entry under its employee, marked as QuickBooks' so no pay run picks it up. Keep the report's default
                        columns. Safe to re-upload — what is already here is skipped.
                        <strong>A categorized bank sheet</strong> (the Transaction Pro / SaasAnt layout: Bank Account, Transaction Date, Memo, Payee, Account, Amount) posts each row as the register entry it is, rows sharing the date, payee and memo as one split entry; payees not on the lists are added.
                    </p>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        A bank or credit card <strong>statement</strong> (CSV, OFX, QFX) is not imported here: it goes in from the account itself, under <a href="#/banking">Banking</a> → the account → <strong>Import file</strong>.
                    </p>
                    <label style="display:flex; align-items:center; gap:6px; font-size:11px; margin-bottom:8px;">
                        <input type="checkbox" id="qbcsv-outflow-positive">
                        In this sheet money out is positive (the usual way round is money out negative)
                    </label>
                    <input type="file" id="qbcsv-file-input" aria-label="QuickBooks report file (CSV or Excel)" accept=".csv,.xlsx" style="font-size:11px; margin-bottom:8px;">
                    <label style="display:flex; align-items:center; gap:6px; font-size:11px; margin-bottom:8px;">
                        <input type="checkbox" id="qbcsv-adjusting-only">
                        Adjusting entries only: an accountant's Transaction Detail by Account filtered to journal entries. Anything else in the file is refused, and what posts is marked adjusting.
                    </label>
                    <div>
                        <button class="btn btn-primary" id="qbcsv-import-btn" onclick="IIFPage.importQbReportCsv()">Import Report CSV</button>
                    </div>
                    <div id="qbcsv-import-result" style="margin-top:12px;"></div>
                </div>
            </div>`;
    },

    // What an import says when it's done (#197). One that came back with
    // errors said "Imported 0 records" in green and "Import complete", and the
    // red box below was the only sign of them.
    // The error lists of the last import, by box, for Save the list.
    _errors: {},

    // The whole list in the box, however long, with its count and a button
    // that saves it as a text file: the box showed twenty and "and 14,070
    // more", and the person asked where the rest were (discussion #349).
    _errorBox(errors, box) {
        IIFPage._errors[box] = errors;
        const n = errors.length;
        let html = '<div class="iif-errors">';
        if (n > 1) {
            html += `<div style="margin-bottom:4px;"><strong>${n.toLocaleString()} problems.</strong> `
                + `<button type="button" class="btn btn-sm btn-secondary" onclick="IIFPage.saveErrors('${box}')" title="Every line of this list, as a text file">Save the list</button></div>`;
        }
        errors.forEach(e => { html += `${escapeHtml(e)}<br>`; });
        return html + '</div>';
    },

    // The desktop app saves it to Documents/SlowBooks Pro/Reports and says
    // where; a browser downloads it.
    async saveErrors(box) {
        const errors = IIFPage._errors[box] || [];
        if (!errors.length) return;
        const day = todayISO();  // the local date: UTC is tomorrow on a US evening
        const filename = `Import-problems_${day}.txt`;
        const blob = new Blob([errors.join('\n') + '\n'], { type: 'text/plain' });
        const desktop = window.SlowbooksDesktop;
        if (!(desktop && await desktop.saveFile(blob, filename))) {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 30000);
            toast(`Saved ${filename}`);
        }
    },

    _reportImport(total, errors, unsupported = 0, words = null) {
        const records = words || `Imported ${total} record${total === 1 ? '' : 's'}`;
        const left = unsupported > 0 ? `, ${unsupported} not imported (type not supported)` : '';
        if (errors > 0) {
            toast(`${records}${left}, ${errors} error${errors === 1 ? '' : 's'}: see the list below`, 'error');
            App.setStatus('Interoperability — Import finished with errors');
        } else {
            toast(`${records}${left}`);
            App.setStatus('Interoperability — Import complete');
        }
    },

    async importQbReportCsv() {
        const input = $('#qbcsv-file-input');
        if (!input?.files[0]) { toast('Choose a file first', 'error'); return; }
        // kept for a file with problems, sent again with the answers (#663)
        IIFPage._qbReport = {
            file: input.files[0],
            adjusting: !!$('#qbcsv-adjusting-only')?.checked,
            outflow: !!$('#qbcsv-outflow-positive')?.checked,
        };
        await IIFPage._sendQbReport(null);
    },

    async _sendQbReport(answers) {
        const sent = IIFPage._qbReport;
        const formData = new FormData();
        formData.append('file', sent.file);
        formData.append('adjusting_only', sent.adjusting ? 'true' : 'false');
        formData.append('outflow_positive', sent.outflow ? 'true' : 'false');
        // a part of its own: a text field holds 1 MB, and thousands of
        // ticked transactions' keys can be more
        if (answers) formData.append('answers', new Blob([JSON.stringify(answers)], { type: 'application/json' }), 'answers.json');
        try {
            App.setStatus('Importing QuickBooks report CSV...');
            const res = await fetch('/api/csv/import/qb-report', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await API.responseError(res, 'Import failed'));
            const result = await res.json();
            if (result.problems?.length) {
                IIFPage._showProblems(result);
                return;
            }
            IIFPage._showQbResult(result);
        } catch (err) {
            const again = $('#qbr-import');
            if (again) again.disabled = false;
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    _showQbResult(result) {
        const kindLabel = { sales_receipts: 'Sales Receipts', invoices: 'Invoices', sales_and_invoices: 'Sales Receipts and Invoices', deposits: 'Deposits', checks: 'Checks', ledger: 'Transaction Detail by Account', journals: 'Journal entries', time: 'Time by Name by Job detail', time_entries: 'Time entries', categorized: 'Categorized bank sheet', posted: 'Register entries', splits: 'Split entries' };
        // a place for focus after a file's answers are imported (#663)
        let html = '<div class="iif-results"><h4 id="qbcsv-result-title" tabindex="-1">Results</h4>';
        if (result.detected) {
            html += `<div class="result-row"><span>Detected report</span><span class="result-count">${escapeHtml(result.report || kindLabel[result.detected] || result.detected)}</span></div>`;
        }
        for (const key of ['sales_receipts', 'invoices', 'deposits', 'checks', 'journals', 'time_entries', 'posted', 'splits']) {
            if (result[key] > 0) {
                const how = key === 'journals' && result.adjusting ? ' imported as adjusting entries' : ' imported';
                html += `<div class="result-row"><span>${kindLabel[key]}</span><span class="result-count">${result[key]}${how}</span></div>`;
            }
        }
        if (result.invoices > 0) {
            html += `<div class="result-row"><span style="font-size:11px;color:var(--gray-500)">${result.invoices_open || 0} open, ${result.invoices_partly_paid || 0} partly paid, ${result.invoices_paid || 0} paid</span></div>`;
        }
        if (result.invoices_updated > 0) {
            html += `<div class="result-row"><span>Already here, marked paid or partly paid from Open Balance</span><span class="result-count">${result.invoices_updated}</span></div>`;
        }
        html += IIFPage._answeredRows(result);
        if (result.duplicates_skipped > 0) {
            html += `<div class="result-row"><span>Duplicates skipped</span><span class="result-count">${result.duplicates_skipped}</span></div>`;
        }
        html += '</div>';
        if (result.warnings?.length) {
            // a warning sits in the warnings box, as the IIF import's do
            // (it sat in the error box with an orange border)
            html += '<div class="iif-warnings">';
            result.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
            html += '</div>';
        }
        if (result.errors?.length) {
            html += IIFPage._errorBox(result.errors.map(e => typeof e === 'string' ? e : JSON.stringify(e)), 'qbcsv');
        }
        IIFPage._wide(false);
        $('#qbcsv-import-result').innerHTML = html;
        const total = (result.sales_receipts || 0) + (result.invoices || 0) + (result.deposits || 0) + (result.checks || 0) + (result.journals || 0) + (result.time_entries || 0) + (result.posted || 0);  // + the categorized sheet's register entries (W-21)
        IIFPage._reportImport(total, (result.errors || []).length);
    },

    // ==== A ledger file's problems (#663) ====
    //
    // A Transaction Detail by Account file with problems posts nothing on
    // its first upload and answers with them. Each kind is a group here,
    // every problem in it ticked to accept it or left to leave it out, and
    // Import sends the same file again with the answers.
    _qbReport: null,

    _count(n, word) {
        return `${Number(n || 0).toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
    },

    _bulk(kind, words) {
        return `<div class="qbr-bulk" role="group" aria-label="${escapeHtml(words)}">
            <button type="button" class="btn btn-sm btn-secondary" onclick="IIFPage.reviewAll('${kind}', true)">Select all</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="IIFPage.reviewAll('${kind}', false)">Select none</button>
            <span class="qbr-count" data-count="${kind}" aria-live="polite"></span>
        </div>`;
    },

    // The types an account can be made with: QuickBooks' own, or, for a
    // sub-account, those the chart's rules let it take under its parent
    // (#663), starting on the parent's.
    _typeOptions(types, allowed, start) {
        const headings = { asset: 'Assets', liability: 'Liabilities', equity: T('Equity'),
            income: T('Income'), cogs: 'Cost of Goods Sold', expense: 'Expenses' };
        const ok = Array.isArray(allowed) ? new Set(allowed) : null;
        return '<option value="">Choose a type…</option>' + Object.keys(headings).map(t => {
            const opts = types.filter(d => d.account_type === t && (!ok || ok.has(d.value)))
                .map(d => `<option value="${escapeHtml(d.value)}"${d.value === start ? ' selected' : ''}>${escapeHtml(T(d.label))}</option>`).join('');
            return opts ? `<optgroup label="${escapeHtml(headings[t])}">${opts}</optgroup>` : '';
        }).join('');
    },

    _showProblems(result) {
        const problems = result.problems || [];
        const notes = result.problem_notes || {};
        const of = kind => problems.filter(p => p.kind === kind);
        const [cut] = of('cut_off');
        const [closed] = of('closed_period');
        const accounts = of('missing_account');
        const unbalanced = of('unbalanced');
        const replaces = of('replaces');
        const ready = result.ready || 0;
        const here = result.duplicates_skipped || 0;
        IIFPage._errors.qbr = problems.map(p => p.text);
        // the answers name the file the list was made for
        if (IIFPage._qbReport) IIFPage._qbReport.sha = result.file_sha256 || '';
        let html = `<div class="iif-results qbr" id="qbr" role="region" aria-labelledby="qbr-title">
            <h4 id="qbr-title">Choose what to import</h4>
            <p class="qbr-lead">Nothing is imported yet. ${IIFPage._count(ready, 'transaction')} ${ready === 1 ? 'is' : 'are'} ready${here ? `, and ${IIFPage._count(here, 'transaction')} already here ${here === 1 ? 'is' : 'are'} skipped` : ''}. The file also has the problems below: tick one to accept it, or leave it unticked to leave it out. Then press Import.</p>
            <div class="qbr-save"><button type="button" class="btn btn-sm btn-secondary" onclick="IIFPage.saveErrors('qbr')" title="Every problem in this list, as a text file">Save the list</button></div>`;
        if (cut) {
            html += `<section class="qbr-group" data-kind="cut_off" aria-labelledby="qbr-h-cut_off">
                <h5 id="qbr-h-cut_off">The file may be cut off</h5>
                <p class="qbr-note">${escapeHtml(notes.cut_off || '')}</p>
                <ul class="qbr-details">${(cut.details || []).map(d => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
                <label class="qbr-tick"><input type="checkbox" class="qbr-pick" data-kind="cut_off" data-key="file" onchange="IIFPage._reviewChanged()"> Import what is there</label>
            </section>`;
        }
        if (closed) {
            // a notice, no box: what the closing date keeps out (#663)
            html += `<section class="qbr-group" data-kind="closed_period" aria-labelledby="qbr-h-closed_period">
                <h5 id="qbr-h-closed_period">${IIFPage._count(closed.transactions, 'transaction')} in a closed period, left out</h5>
                <p class="qbr-note">${escapeHtml(notes.closed_period || '')}</p>
                <ul class="qbr-details"><li>${escapeHtml(closed.text || '')}</li></ul>
            </section>`;
        }
        if (accounts.length) {
            // One stop for Tab, as the other lists: the arrow keys move from
            // row to row and Tab goes on to the row's type, then out. A box
            // that cannot be ticked yet (no type) stays in the keyboard's
            // way through the rows (aria-disabled, not disabled).
            const rows = accounts.map((p, i) => {
                // a sub-account's types are its parent's; one that can take
                // none (an inactive parent) says why and cannot be made
                const start = p.type && (!p.types || p.types.includes(p.type)) ? p.type : '';
                const none = Array.isArray(p.types) && !p.types.length;
                const options = IIFPage._typeOptions(result.account_types || [], p.types, start);
                const tab = i ? -1 : 0;
                return `<tr>
                    <td class="qbr-pick-cell"><input type="checkbox" class="qbr-pick" data-kind="missing_account" data-key="${escapeHtml(p.key)}" tabindex="${tab}" aria-label="Make the account ${escapeHtml(p.path)}" aria-describedby="qbr-type-hint"${start ? '' : ' aria-disabled="true"'} onclick="IIFPage._pickClick(event)" onchange="IIFPage._reviewChanged()"></td>
                    <td>${escapeHtml(p.path)}${none && p.why ? `<br><span class="qbr-hint">${escapeHtml(p.why)}</span>` : ''}</td>
                    <td class="amount">${Number(p.transactions).toLocaleString()}</td>
                    <td><select class="qbr-type" data-no-search tabindex="${tab}" aria-label="Type for ${escapeHtml(p.path)}"${none ? ' disabled' : ''} onchange="IIFPage.reviewTyped(this)">${options}</select></td>
                </tr>`;
            }).join('');
            html += `<section class="qbr-group" data-kind="missing_account" aria-labelledby="qbr-h-missing_account">
                <h5 id="qbr-h-missing_account">${IIFPage._count(accounts.length, 'account')} not in the chart</h5>
                <p class="qbr-note">${escapeHtml(notes.missing_account || '')} <span id="qbr-type-hint">An account is ticked once it has a type.</span> <span id="qbr-keys-hint-accounts">In the list, the arrow keys move from row to row, and Tab goes on to the row's type and then out of the list.</span></p>
                ${IIFPage._bulk('missing_account', 'Accounts not in the chart')}
                <div class="qbr-scroll qbr-roving"><table aria-describedby="qbr-keys-hint-accounts">
                    <thead><tr><th scope="col"><span class="visually-hidden">Make it</span></th><th scope="col">Account</th><th scope="col" class="amount">Transactions</th><th scope="col">Type</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
            </section>`;
        }
        if (replaces.length) {
            // A transaction the books hold as a partial entry (its
            // difference on Import Differences) that this file has whole:
            // the file's copy beside the entry it would replace (#663).
            const rows = replaces.map((p, i) => {
                const what = `${p.type}${p.num ? ` ${p.num}` : ''} of ${formatDate(p.date)}${p.name ? `, ${p.name}` : ''}`;
                const books = (p.partial || []).map(e => `${formatDate(e.date)} · ${formatCurrency(Number(e.amount))}`);
                const plugs = (p.partial || []).map(e => IIFPage._postedWords(e.difference));
                const said = `Replace the partial entry: ${what}, ${formatCurrency(Number(p.amount))} in this file; in the books ${books.join(' and ')}, with ${plugs.map(w => `a ${w}`).join(' and ')} on Import Differences`;
                const held = p.voidable === false;
                return `<tr>
                    <td class="qbr-pick-cell"><input type="checkbox" class="qbr-pick" data-kind="replaces" data-key="${escapeHtml(p.key)}" tabindex="${i ? -1 : 0}" aria-label="${escapeHtml(said)}"${held ? ' aria-disabled="true" title="In a completed reconciliation: undo it to replace this entry"' : ''} onclick="IIFPage._pickClick(event)" onchange="IIFPage._reviewChanged()"></td>
                    <td class="qbr-nowrap">${formatDate(p.date)}</td>
                    <td>${escapeHtml(p.type)}</td>
                    <td>${escapeHtml(p.num || '')}</td>
                    <td>${escapeHtml((p.names || []).join(', ') || p.name || '')}</td>
                    <td class="amount qbr-nowrap">${formatCurrency(Number(p.amount))}${Number(p.off) ? `<br><span class="qbr-hint">${escapeHtml(IIFPage._offWords(p.off))}</span>` : ''}</td>
                    <td class="qbr-nowrap">${books.map(escapeHtml).join('<br>')}${held ? '<br><span class="qbr-hint">in a completed reconciliation</span>' : ''}</td>
                    <td class="amount qbr-nowrap">${plugs.map(escapeHtml).join('<br>')}</td>
                </tr>`;
            }).join('');
            html += `<section class="qbr-group" data-kind="replaces" aria-labelledby="qbr-h-replaces">
                <h5 id="qbr-h-replaces">${IIFPage._count(replaces.length, 'transaction')} that ${replaces.length === 1 ? 'replaces' : 'replace'} a partial entry in the books</h5>
                <p class="qbr-note">${escapeHtml(notes.replaces || '')} <span id="qbr-keys-hint-replaces">In the list, the arrow keys move from row to row and Tab leaves it.</span></p>
                ${IIFPage._bulk('replaces', 'Transactions that replace a partial entry')}
                <div class="qbr-scroll qbr-roving"><table aria-describedby="qbr-keys-hint-replaces">
                    <thead><tr><th scope="col"><span class="visually-hidden">Replace</span></th><th scope="col">Date</th><th scope="col">Type</th><th scope="col">Num</th><th scope="col">Name</th><th scope="col" class="amount">In this file</th><th scope="col">In the books now</th><th scope="col" class="amount">On Import Differences</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
            </section>`;
        }
        if (unbalanced.length) {
            // one stop for Tab however long the list: the arrow keys move in it
            const rows = unbalanced.map((p, i) => {
                const what = `${p.type}${p.num ? ` ${p.num}` : ''} of ${formatDate(p.date)}${p.name ? `, ${p.name}` : ''}`;
                // which side is over, not only how far (#663)
                const off = IIFPage._offWords(p.off);
                return `<tr>
                    <td class="qbr-pick-cell"><input type="checkbox" class="qbr-pick" data-kind="unbalanced" data-key="${escapeHtml(p.key)}" tabindex="${i ? -1 : 0}" aria-label="Import ${escapeHtml(what)}, off by ${escapeHtml(off)}" onchange="IIFPage._reviewChanged()"></td>
                    <td class="qbr-nowrap">${formatDate(p.date)}</td>
                    <td>${escapeHtml(p.type)}</td>
                    <td>${escapeHtml(p.num || '')}</td>
                    <td>${escapeHtml(p.name || '')}</td>
                    <td class="amount qbr-nowrap">${formatCurrency(Number(p.amount))}</td>
                    <td class="amount qbr-nowrap">${off}</td>
                </tr>`;
            }).join('');
            html += `<section class="qbr-group" data-kind="unbalanced" aria-labelledby="qbr-h-unbalanced">
                <h5 id="qbr-h-unbalanced">${IIFPage._count(unbalanced.length, 'transaction')} that ${unbalanced.length === 1 ? 'does' : 'do'} not balance</h5>
                <p class="qbr-note">${escapeHtml(notes.unbalanced || '')} <span id="qbr-keys-hint">In the list, the arrow keys move from row to row and Tab leaves it.</span></p>
                ${IIFPage._bulk('unbalanced', 'Transactions that do not balance')}
                <div class="qbr-scroll qbr-roving"><table aria-describedby="qbr-keys-hint">
                    <thead><tr><th scope="col"><span class="visually-hidden">Import</span></th><th scope="col">Date</th><th scope="col">Type</th><th scope="col">Num</th><th scope="col">Name</th><th scope="col" class="amount">Amount</th><th scope="col" class="amount">Off by</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
            </section>`;
        }
        html += `<div class="qbr-actions">
                <button type="button" class="btn btn-primary" id="qbr-import" aria-describedby="qbr-hint" onclick="IIFPage.importReviewed()">Import</button>
                <button type="button" class="btn btn-secondary" onclick="IIFPage.cancelReview()">Cancel</button>
                <span id="qbr-hint" class="qbr-hint" aria-live="polite"></span>
            </div></div>`;
        if (result.warnings?.length) {
            html += '<div class="iif-warnings">';
            result.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
            html += '</div>';
        }
        IIFPage._wide(true);
        $('#qbcsv-import-result').innerHTML = html;
        for (const list of document.querySelectorAll('#qbr .qbr-roving')) {
            // the row Tab comes back in at, kept so a move touches two rows
            list._row = list.querySelector('tbody tr');
            list.addEventListener('keydown', IIFPage._listKeys);
            list.addEventListener('focusin', IIFPage._listFocus);
            list.addEventListener('pointerdown', () => { delete list.dataset.keys; });
        }
        IIFPage._reviewChanged();
        toast(`Nothing imported yet: ${IIFPage._count(problems.length, 'problem')} to decide below`, 'warning');
        App.setStatus('Interoperability — Choose what to import');
    },

    // Up and Down, Home and End, between a long list's boxes: Tab enters
    // the list at the row last used and leaves it. Each move goes to the
    // next row, or the first or last, by the rows themselves: a list of
    // 15,000 was copied into an array on every key (#663).
    _listKeys(event) {
        const moves = { ArrowDown: 1, ArrowUp: -1, Home: 'first', End: 'last' };
        const from = event.target;
        if (!(event.key in moves) || !from.matches('input.qbr-pick')) return;
        event.preventDefault();
        const row = from.closest('tr');
        const body = row && row.parentElement;
        if (!body) return;
        const move = moves[event.key];
        const to = move === 'first' ? body.firstElementChild
            : move === 'last' ? body.lastElementChild
                : (move > 0 ? row.nextElementSibling : row.previousElementSibling) || row;
        const box = to && to.querySelector('input.qbr-pick');
        // the keys moved it, so the ring shows even after a click into the
        // list: WebKit keeps :focus-visible off for a focus() that follows
        // a pointer (2.31.2 gate, NEW-190); a pointer in the list clears it
        event.currentTarget.dataset.keys = '1';
        if (box && box !== from) box.focus({ focusVisible: true });
    },

    // A row's own controls, in the Tab order while it is the row in use.
    _ROW_CONTROLS: 'input.qbr-pick, select.qbr-type',

    // The row in use, by keyboard or mouse, is where Tab comes back in: its
    // controls take Tab, and the row before it gives it back. Two rows are
    // touched, not every row (every tabIndex was rewritten on each move,
    // 134 ms a key at 15,000, #663).
    _listFocus(event) {
        if (!event.target.matches(IIFPage._ROW_CONTROLS)) return;
        const row = event.target.closest('tr');
        const list = event.currentTarget;
        if (!row || list._row === row) return;
        if (list._row) {
            for (const c of list._row.querySelectorAll(IIFPage._ROW_CONTROLS)) c.tabIndex = -1;
        }
        for (const c of row.querySelectorAll(IIFPage._ROW_CONTROLS)) c.tabIndex = 0;
        list._row = row;
    },

    _picks(kind) {
        return [...document.querySelectorAll(`#qbr input.qbr-pick[data-kind="${kind}"]`)];
    },

    reviewAll(kind, on) {
        IIFPage._picks(kind).forEach(box => { if (!box.disabled && box.getAttribute('aria-disabled') !== 'true') box.checked = on; });
        IIFPage._reviewChanged();
    },

    // A box that cannot be ticked stays in the keyboard's way through the
    // list (aria-disabled, not disabled) and ignores the click or Space.
    _pickClick(event) {
        if (event.currentTarget.getAttribute('aria-disabled') === 'true') event.preventDefault();
    },

    // How far off, and which side is over: "$10.00 more in debits".
    _offWords(off) {
        const n = Number(off) || 0;
        return `${formatCurrency(Math.abs(n))} more in ${n > 0 ? 'debits' : 'credits'}`;
    },

    // What a partial entry put on Import Differences (#663): its difference
    // is its file's debits less its credits, evened out there, so a credit
    // of that much when the file had more debits and a debit when it had
    // more credits. "$100.00 more in credits on Import Differences" read as
    // Import Differences' side, the other way round.
    _postedWords(difference) {
        const n = Number(difference) || 0;
        return `${formatCurrency(Math.abs(n))} ${n > 0 ? 'credit' : 'debit'}`;
    },

    // a missing account's row is ticked once it has a type, and cannot be
    // ticked without one
    reviewTyped(select) {
        const box = select.closest('tr').querySelector('input.qbr-pick');
        if (select.value) box.removeAttribute('aria-disabled');
        else box.setAttribute('aria-disabled', 'true');
        box.checked = !!select.value;
        IIFPage._reviewChanged();
    },

    // The groups' counts, and Import held while the file's notice is
    // unticked: unticked, nothing would be imported.
    _reviewChanged() {
        for (const kind of ['missing_account', 'replaces', 'unbalanced']) {
            const count = document.querySelector(`#qbr [data-count="${kind}"]`);
            if (!count) continue;
            const picks = IIFPage._picks(kind);
            count.textContent = `${picks.filter(b => b.checked).length.toLocaleString()} of ${picks.length.toLocaleString()} ticked`;
        }
        const [cut] = IIFPage._picks('cut_off');
        const held = !!cut && !cut.checked;
        const button = $('#qbr-import');
        if (button) button.disabled = held;
        const hint = $('#qbr-hint');
        if (hint) hint.textContent = held ? 'Tick “Import what is there” to import from this file.' : '';
    },

    _answers() {
        const answers = { file_sha256: IIFPage._qbReport?.sha || '', unbalanced: [], replaces: [], accounts: {}, cut_off: false };
        for (const box of document.querySelectorAll('#qbr input.qbr-pick:checked')) {
            const kind = box.dataset.kind;
            if (box.getAttribute('aria-disabled') === 'true') continue;
            if (kind === 'unbalanced') answers.unbalanced.push(box.dataset.key);
            if (kind === 'replaces') answers.replaces.push(box.dataset.key);
            if (kind === 'cut_off') answers.cut_off = true;
            if (kind === 'missing_account') {
                const type = box.closest('tr').querySelector('select.qbr-type').value;
                if (type) answers.accounts[box.dataset.key] = type;
            }
        }
        return answers;
    },

    // Focus goes to the result after Import, or back to Import when the
    // answers were refused (the list stays), and to the button that began
    // it all after Cancel: it fell to the page itself (#663).
    async importReviewed() {
        if (!IIFPage._qbReport) return;
        const button = $('#qbr-import');
        if (button) button.disabled = true;
        await IIFPage._sendQbReport(IIFPage._answers());
        const done = $('#qbcsv-result-title');
        (done || $('#qbr-import'))?.focus();
    },

    cancelReview() {
        IIFPage._qbReport = null;
        IIFPage._wide(false);
        $('#qbcsv-import-result').innerHTML = '';
        App.setStatus('Interoperability — Ready');
        $('#qbcsv-import-btn')?.focus();
    },

    // the list takes the page's width while it is up: half of it squeezed
    // the dates and names onto three lines
    _wide(on) {
        const box = document.getElementById('qbcsv-import-result');
        const section = box && box.closest ? box.closest('.iif-section') : null;
        if (section) section.classList.toggle('iif-section--wide', on);
    },

    // What a file with problems imported on its second call: what was
    // accepted and what was left out.
    _answeredRows(result) {
        const row = (words, n) => `<div class="result-row"><span>${words}</span><span class="result-count">${n}</span></div>`;
        const accepted = result.accepted || {};
        const left = result.left_out || {};
        let html = '';
        if (accepted.unbalanced > 0) html += row(`Accepted, the difference on ${escapeHtml(result.differences_account || 'Import Differences')}`, accepted.unbalanced);
        if (accepted.accounts?.length) {
            html += row('Accounts made', accepted.accounts.length);
            html += `<div class="result-row result-row--line">${accepted.accounts.map(escapeHtml).join(', ')}</div>`;
        }
        if (accepted.replaced > 0) html += row('Replaced: partial entries voided, this file\'s posted in their place', accepted.replaced);
        if (accepted.cut_off) html += '<div class="result-row result-row--line">The file looked cut off; what was in it was imported.</div>';
        if (left.replaces > 0) html += row('Left out: a partial entry in the books kept', left.replaces);
        if (left.unbalanced > 0) html += row('Left out: did not balance', left.unbalanced);
        if (left.missing_account > 0) html += row('Left out: an account not in the chart', left.missing_account);
        if (left.closed_period > 0) html += row('Left out: in a closed period', left.closed_period);
        return html;
    },

    // ==== Export Functions ====

    exportAll() {
        IIFPage._download('/api/iif/export/all', 'slowbooks_export.iif');
    },

    exportSection(section) {
        let url = `/api/iif/export/${section}`;
        // Add date range for invoices/payments
        if (['invoices', 'payments', 'sales-receipts', 'bills', 'deposits', 'register-entries', 'transfers', 'bill-payments', 'journal-entries'].includes(section)) {
            const from = $('#iif-date-from')?.value;
            const to = $('#iif-date-to')?.value;
            const params = [];
            if (from) params.push(`date_from=${from}`);
            if (to) params.push(`date_to=${to}`);
            if (params.length) url += '?' + params.join('&');
        }
        IIFPage._download(url, `${section}.iif`);
    },

    async _download(url, fallbackName) {
        try {
            App.setStatus('Exporting IIF...');
            // Asked for inline: the desktop app's web view takes an
            // "attachment" answer to a page's own fetch() for a download and
            // never hands it back ("Failed to fetch"). This page saves the
            // file itself either way.
            const res = await fetch(url, { headers: { 'X-Slowbooks-Desktop': '1' } });
            if (!res.ok) throw new Error(await API.responseError(res, 'Export failed'));

            // Get filename from Content-Disposition header if available
            const disposition = res.headers.get('Content-Disposition');
            let filename = fallbackName;
            if (disposition) {
                const match = disposition.match(/filename="?([^"]+)"?/);
                if (match) filename = match[1];
            }

            const blob = await res.blob();
            // The desktop app saves it to Documents/SlowBooks Pro/Reports and
            // says where, as Save CSV does; a browser downloads it.
            const desktop = window.SlowbooksDesktop;
            if (!(desktop && await desktop.saveFile(blob, filename))) {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = filename;
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 30000);
                toast(`Exported ${filename}`);
            }
            App.setStatus('Interoperability — Ready');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Export failed');
        }
    },

    // ==== Import Functions ====

    handleDragOver(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.add('dragover');
    },

    handleDragLeave(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.remove('dragover');
    },

    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.remove('dragover');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.name.toLowerCase().endsWith('.iif')) {
                IIFPage._setFile(file);
            } else {
                toast('Please select an .iif file', 'error');
            }
        }
    },

    handleFileSelect(e) {
        const file = e.target.files[0];
        if (file) IIFPage._setFile(file);
    },

    _setFile(file) {
        IIFPage._selectedFile = file;
        IIFPage._validated = false;

        const size = formatFileSize(file.size);

        const info = $('#iif-file-info');
        info.style.display = '';
        info.innerHTML = `<div class="iif-file-info">
            <span class="filename">&#9783; ${escapeHtml(file.name)}</span>
            <span class="filesize">${size}</span>
        </div>`;

        $('#iif-import-actions').style.display = '';
        $('#iif-import-btn').disabled = true;
        $('#iif-validation-result').innerHTML = '';
        $('#iif-import-result').innerHTML = '';
    },

    clearFile() {
        IIFPage._selectedFile = null;
        IIFPage._validated = false;
        $('#iif-file-input').value = '';
        $('#iif-file-info').style.display = 'none';
        $('#iif-import-actions').style.display = 'none';
        $('#iif-validation-result').innerHTML = '';
        $('#iif-import-result').innerHTML = '';
    },

    async validateFile() {
        if (!IIFPage._selectedFile) return;

        const formData = new FormData();
        formData.append('file', IIFPage._selectedFile);

        try {
            App.setStatus('Validating IIF file...');
            const res = await fetch('/api/iif/validate', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await API.responseError(res, 'Validation failed'));

            const report = await res.json();
            IIFPage._showValidationReport(report);

            if (report.valid) {
                IIFPage._validated = true;
                $('#iif-import-btn').disabled = false;
                toast('Validation passed');
            } else {
                toast('Validation found errors', 'error');
            }
            App.setStatus('Interoperability — Ready');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Validation failed');
        }
    },

    _showValidationReport(report) {
        let html = '<div class="iif-results"><h4>Validation Report</h4>';

        html += `<div class="result-row">
            <span>Status</span>
            <span class="${report.valid ? 'iif-validation-ok' : 'iif-validation-err'}">
                ${report.valid ? 'PASS' : 'FAIL'}
            </span>
        </div>`;

        if (report.sections_found.length) {
            html += `<div class="result-row">
                <span>Sections Found</span>
                <span>${report.sections_found.join(', ')}</span>
            </div>`;
        }

        for (const [section, count] of Object.entries(report.record_counts || {})) {
            html += `<div class="result-row">
                <span>${section}</span>
                <span class="result-count">${count} records</span>
            </div>`;
        }

        // The blocks the import would not take, by type, before the person
        // decides (#288); the warning below names each block
        for (const [ttype, count] of Object.entries(report.unsupported_counts || {})) {
            html += `<div class="result-row">
                <span>${escapeHtml(ttype)} &mdash; not imported</span>
                <span class="result-count">${count} block${count === 1 ? '' : 's'}</span>
            </div>`;
        }

        html += '</div>';

        if (report.errors && report.errors.length) {
            html += '<div class="iif-errors">';
            report.errors.forEach(e => { html += `${escapeHtml(e)}<br>`; });
            html += '</div>';
        }

        if (report.warnings && report.warnings.length) {
            html += '<div class="iif-warnings">';
            report.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
            html += '</div>';
        }

        // ALL-CAPS names (#195): a box, off unless ticked, and a few of this
        // file's own names as the box would import them, so the choice is
        // made on the names that will change.
        if (report.valid && report.caps_names > 0) {
            const n = report.caps_names;
            const examples = (report.caps_name_examples || []).map(x =>
                `<li>${escapeHtml(x.name)} &rarr; ${escapeHtml(x.becomes)}</li>`).join('');
            html += `<div class="iif-retitle">
                <label><input type="checkbox" id="iif-retitle-names">
                    Change ${n} ALL-CAPS name${n === 1 ? '' : 's'} to normal capitalization</label>
                <ul>${examples}</ul>
                <p>Customer, vendor and account names; item names are kept as typed, and
                    names already in your books are not changed. A name that should stay
                    in capitals may not, so look over the examples first.</p>
            </div>`;
        }

        // "Customer:Job" names (#638): QuickBooks Desktop spells a job and a
        // sub-customer the same way, so the person importing says which the
        // file holds; off, each is a job under the customer, as before.
        if (report.valid && report.customer_job_names > 0) {
            const n = report.customer_job_names;
            html += `<div class="iif-retitle">
                <label><input type="checkbox" id="iif-sub-customers">
                    The ${n} Customer:Job name${n === 1 ? '' : 's'} in this file ${n === 1 ? 'is a sub-customer' : 'are sub-customers'}, not job${n === 1 ? '' : 's'}</label>
                <p>A property's tenants, say: each becomes a customer under its parent, with its own
                    contact details, and can be made inactive on its own. Left unticked, each
                    Customer:Job is a job under the customer.</p>
            </div>`;
        }

        $('#iif-validation-result').innerHTML = html;
    },

    async importFile() {
        if (!IIFPage._selectedFile) return;

        if (!IIFPage._validated) {
            toast('Please validate the file first', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('file', IIFPage._selectedFile);
        formData.append('retitle_names', $('#iif-retitle-names')?.checked ? 'true' : 'false');
        formData.append('adjusting', $('#iif-adjusting')?.checked ? 'true' : 'false');
        formData.append('sub_customers', $('#iif-sub-customers')?.checked ? 'true' : 'false');

        try {
            App.setStatus('Importing IIF file...');
            const res = await fetch('/api/iif/import', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await API.responseError(res, 'Import failed'));

            const result = await res.json();
            IIFPage._showImportResult(result);

            const total = (result.classes || 0) + (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0) +
                          (result.sales_receipts || 0) +
                          (result.estimates || 0) + (result.bills || 0) +
                          (result.deposits || 0);
            IIFPage._reportImport(total, (result.errors || []).length, result.unsupported || 0,
                IIFPage._importedWords(result));
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    // Transactions the import took, every type together.
    _transactionsImported(result) {
        return (result.invoices || 0) + (result.payments || 0) + (result.sales_receipts || 0)
            + (result.estimates || 0) + (result.bills || 0) + (result.deposits || 0)
            + (result.ledger_entries || 0);
    },

    // What the IIF import brought in, in the panel's own terms (2.23.1 gate,
    // NEW-106: "Imported 4 records" was a class and three transactions added
    // up, a total the panel never shows): "Imported 1 class and 3
    // transactions"; a file of records already here, "Nothing imported, 3
    // already here".
    _importedWords(result) {
        const parts = [];
        const say = (n, one, many) => { if (n > 0) parts.push(`${n} ${n === 1 ? one : many}`); };
        say(result.classes || 0, 'class', 'classes');
        say(result.accounts || 0, 'account', 'accounts');
        say(result.customers || 0, 'customer', 'customers');
        say(result.vendors || 0, 'vendor', 'vendors');
        say(result.items || 0, 'item', 'items');
        say(IIFPage._transactionsImported(result), 'transaction', 'transactions');
        if (!parts.length) {
            const here = result.duplicates_skipped || 0;
            return here > 0 ? `Nothing imported, ${here} already here` : 'Nothing imported';
        }
        const last = parts.pop();
        return `Imported ${parts.length ? `${parts.join(', ')} and ` : ''}${last}`;
    },

    _showImportResult(result) {
        const sections = [
            ['Classes', result.classes],
            ['Accounts', result.accounts],
            ['Customers', result.customers],
            ['Vendors', result.vendors],
            ['Items', result.items],
            ['Invoices', result.invoices],
            ['Payments', result.payments],
            ['Sales Receipts', result.sales_receipts],
            ['Estimates', result.estimates],
            ['Bills', result.bills],
            ['Deposits', result.deposits],
            ['Checks, card charges, transfers and bill payments', (result.ledger_entries || 0) - (result.journal_entries || 0)],
            ['Journal entries', result.journal_entries],
        ];

        let html = '<div class="iif-results"><h4>Import Results</h4>';
        for (const [name, count] of sections) {
            if (count > 0) {
                html += `<div class="result-row">
                    <span>${name}</span>
                    <span class="result-count">${count} imported</span>
                </div>`;
            }
        }
        // Not "imported": a record already here that the file named again
        // (both QA agents, 2.18.1 gate: "Duplicates skipped: 1 imported")
        if (result.duplicates_skipped > 0) {
            html += `<div class="result-row">
                <span>Already here, skipped</span>
                <span class="result-count">${result.duplicates_skipped}</span>
            </div>`;
        }
        // Blocks of a type the importer doesn't take (#288): counted and
        // named, never dropped without a word; the warnings say which types
        if (result.unsupported > 0) {
            const named = (result.unsupported_blocks || []).slice(0, 8).map(u =>
                `block ${u.block}: ${escapeHtml(u.type)}${u.docnum ? ' ' + escapeHtml(u.docnum) : ''}${u.date ? ', ' + escapeHtml(u.date) : ''}${u.name ? ', ' + escapeHtml(u.name) : ''}`).join('; ');
            html += `<div class="result-row">
                <span>Not imported (type not supported)</span>
                <span class="result-count">${result.unsupported}</span>
            </div>
            <div class="result-row"><span style="font-size:11px;color:var(--gray-500)">${named}${(result.unsupported_blocks || []).length > 8 ? '; …' : ''}</span></div>`;
        }
        // Every transaction block in the file accounted for, in one sentence
        // on a line of its own: as a label beside a count across the row it
        // wrapped after "the", the count's "3:" reading as a superscript
        // (2.23.1 gate, NEW-106)
        if (result.transaction_blocks > 0) {
            const n = result.transaction_blocks;
            const imported = IIFPage._transactionsImported(result);
            html += `<div class="result-row result-row--line">${n} transaction block${n === 1 ? '' : 's'} in the file: ${imported} imported, ${result.duplicates_skipped || 0} already here, ${result.unsupported || 0} not imported, ${(result.errors || []).length} failed</div>`;
        }
        if (result.names_changed > 0) {
            html += `<div class="result-row">
                <span>ALL-CAPS names changed to normal capitalization</span>
                <span class="result-count">${result.names_changed}</span>
            </div>`;
        }
        html += '</div>';

        if (result.warnings && result.warnings.length) {
            html += '<div class="iif-warnings">';
            html += `<strong>Warnings (${result.warnings.length}):</strong><br>`;
            result.warnings.forEach(w => {
                html += `${escapeHtml(w)}<br>`;
            });
            html += '</div>';
        }

        if (result.errors && result.errors.length) {
            html += IIFPage._errorBox(result.errors.map(e => typeof e === 'string' ? e : `Row ${e.row}: ${e.message}`), 'iif');
        }

        $('#iif-import-result').innerHTML = html;
    },
};
