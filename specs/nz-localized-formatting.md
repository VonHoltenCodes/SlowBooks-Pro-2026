# NZ Localized Formatting

## Problem Statement

SlowBooks Pro 2026 still displays user-facing currency and dates with US defaults in several places. The NZ localization foundation now stores `locale=en-NZ`, `currency=NZD`, and `timezone=Pacific/Auckland`, but formatting helpers and rendered documents must consume those settings before later GST, address, reporting, and payroll work can present correct NZ-facing output.

## Scope

- Add shared Python formatting helpers for currency and dates.
- Wire PDF Jinja filters to use company settings from the render context.
- Wire invoice email rendering to use the same currency/date filters.
- Add a global frontend settings cache so existing UI formatting calls can use localization settings without threading settings through every page renderer.
- Keep explicit helper arguments available for tests and future special cases.
- Update the localization plan document to reflect the synced upstream branch and the current formatting design.

## Non-Scope

- No GST calculation changes.
- No line-level GST model or schema changes.
- No address label changes.
- No payroll localization.
- No full multi-currency accounting, exchange rates, gains/losses, or account currency handling.
- No visual redesign of document templates.

## Acceptance Criteria

- `format_currency(value, settings)` formats NZD values using the configured currency and stable two-decimal accounting output.
- `format_date(value, settings)` formats NZ dates in day-month-year order for `en-NZ`.
- PDF invoice, estimate, and statement templates that use `currency` or `fdate` filters receive company settings and render localized output.
- Invoice email dates and amount due use the same localized formatting path.
- Frontend `formatCurrency()` and `formatDate()` use `App.settings` by default.
- `App.init()` loads `/settings` before first route render so existing UI calls use the cached settings.
- Saving settings updates the global cache immediately.
- Existing callers remain compatible when no settings are loaded, falling back to the previous US defaults.

## Affected Files And Modules

- `app/services/formatting.py`
- `app/services/pdf_service.py`
- `app/services/email_service.py`
- `app/templates/invoice_email.html`
- `app/static/js/app.js`
- `app/static/js/settings.js`
- `app/static/js/utils.js`
- `docs/localization_summary.md`
- `tests/test_localized_formatting.py`
- `tests/test_pdf_service_formatting.py`
- `tests/test_email_formatting.py`
- `tests/js_formatting.test.js`
- `tests/js_settings_cache.test.js`
- `tests/js_app_init_settings.test.js`

## Test Plan

- Add failing Python tests for localized currency and date helpers.
- Add failing Python tests proving invoice, estimate, and statement PDF render paths use `company.locale` and `company.currency`.
- Add failing Python tests proving invoice email uses localized filters for invoice date, due date, and amount due.
- Add failing Node tests proving frontend helpers use explicit settings and the global `App.settings` cache.
- Add failing Node tests proving `SettingsPage.save()` refreshes `App.settings` from the API response.
- Add failing Node tests proving `App.init()` waits for settings before first navigation and tolerates invalid locale input.
- Verify all Python tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

- Verify frontend helper tests with:

```bash
node tests/js_formatting.test.js
node tests/js_settings_cache.test.js
node tests/js_app_init_settings.test.js
```

- Verify JavaScript syntax with:

```bash
node --check app/static/js/app.js
node --check app/static/js/settings.js
node --check app/static/js/utils.js
```

- Verify whitespace with:

```bash
git diff --check
```

## Risks

- Frontend formatting depends on `App.settings` being loaded before the first route render. `App.init()` must await settings before navigation.
- If `/settings` fails, the UI intentionally falls back to existing US defaults so the app remains usable.
- This is display formatting only. It must not be treated as GST compliance or full multi-currency support.
