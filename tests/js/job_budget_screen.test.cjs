// fix/budget-seed: the job budget screen shows one line per cost code that
// stands for every non-change row on the code (estimate rows carry the
// code's cost type), leaves out an old Save Budget twin, and names a truthful
// source. The save compares what is sent against the same sum, so these
// figures are what keeps an untouched Save Budget from changing anything.
// Drives the real JobsPage.budgetHtml().
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function budgetContext(rows) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: {
            get: async (p) => {
                if (p === '/jobs/1/budgets') return rows;
                if (p === '/cost-codes') return [
                    { id: 10, code: '03 30 00', name: 'Concrete', cost_type: 'material' },
                    { id: 11, code: '06 10 00', name: 'Framing', cost_type: 'material' },
                    { id: 12, code: '09 90 00', name: 'Painting', cost_type: 'labor' },
                ];
                if (p === '/cost-types') return [{ code: 'labor', name: 'Labor' }];
                return [];
            },
        },
        Terms: { mode: 'construction', isConstruction() { return true; }, text: (s) => s },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        hint: () => '',
        formatCurrency: (v) => '$' + Number(v || 0).toFixed(2),
        reversedRangeRefused: () => false,
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.TradePackages = TradePackages;\nthis.CostTypes = CostTypes;',
        context,
    );
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/jobs.js'), 'utf8') +
            '\nthis.JobsPage = JobsPage;',
        context,
    );
    context.JobsPage._job = { id: 1, customer_id: 2 };
    return context;
}

// {cost, revenue, source} as rendered on one data-budget-code row
function shown(html, codeId) {
    const m = html.match(new RegExp(`<tr data-budget-code="${codeId}">([\\s\\S]*?)</tr>`));
    assert.ok(m, `row for code ${codeId}`);
    const vals = [...m[1].matchAll(/value="([^"]*)"/g)].map(x => x[1]);
    const cells = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(x => x[1].trim());
    return { cost: vals[0], revenue: vals[1], source: cells[3] };
}

const row = (o) => ({ cost_code_id: null, cost_type: null, amount: '0', revenue_amount: '0', source: 'manual', locked: false, ...o });

test('a code shows the sum of its estimate rows, leaves out a twin and a change row', async () => {
    const html = await budgetContext([
        row({ cost_code_id: 10, cost_type: 'material', amount: '600.00', revenue_amount: '1000.00', source: 'estimate' }),
        row({ cost_code_id: 10, cost_type: 'labor', amount: '400.00', revenue_amount: '800.00', source: 'estimate' }),
        // the twin an older Save Budget wrote: equal to the code's other rows
        row({ cost_code_id: 10, amount: '1000.00', revenue_amount: '1800.00' }),
        // a change order's row is its own and stays out
        row({ cost_code_id: 10, cost_type: 'material', amount: '250.00', source: 'change', locked: true }),
        row({ cost_code_id: 11, cost_type: 'material', amount: '300.00', revenue_amount: '500.00', source: 'estimate', locked: true }),
    ]).JobsPage.budgetHtml();
    assert.deepEqual({ ...shown(html, 10) }, { cost: '1000.00', revenue: '1800.00', source: 'estimate' });
    assert.deepEqual({ ...shown(html, 11) }, { cost: '300.00', revenue: '500.00', source: 'estimate · locked' });
    assert.deepEqual({ ...shown(html, 12) }, { cost: '', revenue: '', source: '' });
});

test('a manual row that is not a twin is counted, and the source says mixed', async () => {
    const html = await budgetContext([
        row({ cost_code_id: 10, cost_type: 'material', amount: '600.00', revenue_amount: '600.00', source: 'estimate', locked: true }),
        row({ cost_code_id: 10, amount: '1.00', revenue_amount: '0.00' }),
        row({ cost_code_id: 11, amount: '700.00', revenue_amount: '0.00' }),
    ]).JobsPage.budgetHtml();
    assert.deepEqual({ ...shown(html, 10) }, { cost: '601.00', revenue: '600.00', source: 'mixed · locked' });
    assert.deepEqual({ ...shown(html, 11) }, { cost: '700.00', revenue: '0.00', source: 'manual' });
});
