/**
 * Payroll — NZ placeholder until PAYE and payslips are implemented.
 */
const PayrollPage = {
    async render() {
        return `
            <div class="page-header">
                <h2>Payroll</h2>
            </div>
            <div style="background:#e0f2fe;border:1px solid #7dd3fc;padding:10px 12px;margin-bottom:12px;font-size:12px;color:#0c4a6e;">
                <strong>NZ payroll setup is ready.</strong> PAYE calculations, KiwiSaver deductions, student loan deductions, ESCT, and NZ payslips are coming in the next slices.
            </div>
            <div class="empty-state">
                <p>Use the Employees page to maintain IRD number, tax code, KiwiSaver, student loan, child support, ESCT, and pay frequency details.</p>
            </div>`;
    },
};
