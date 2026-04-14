/**
 * Legacy tax page — retained only to explain that the US Schedule C workflow
 * is disabled on the NZ branch.
 */
const TaxPage = {
    async render() {
        return `
            <div class="page-header">
                <h2>Tax Reports — Disabled for SlowBooks NZ</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Use Reports → GST Return for the supported NZ tax workflow.
                </div>
            </div>
            <div style="background:#fef3c7;border:1px solid #fbbf24;padding:10px 12px;margin-bottom:12px;font-size:10px;color:#92400e;">
                <strong>Disabled:</strong> The old US income-tax export page is not part of the NZ product surface.
            </div>`;
    },

    async generate() {
        throw new Error('TaxPage.generate is disabled for SlowBooks NZ');
    },

    exportCSV() {
        throw new Error('TaxPage.exportCSV is disabled for SlowBooks NZ');
    },
};
