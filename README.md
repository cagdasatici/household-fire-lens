# Household FIRE Lens

Local-first household economics for FIRE planning. Import messy yearly bank and investment CSVs, classify the true economic meaning of each transaction, and see where household income goes without counting transfers, reimbursements, or investment moves as spending.

## What It Does

- Imports CSV exports from household accounts.
- Keeps raw imported rows immutable and stores cleaned transactions separately.
- Classifies transactions into economic classes such as income, household spend, wealth allocation, internal transfer, mortgage/debt service, refunds, and reimbursement pass-throughs.
- Detects salary using recurring payer, date window, and amount similarity.
- Clears Booking.com reimbursements without requiring credit card statement imports.
- Separates Cashflow and Normalized FIRE views.
- Shows FIRE burn, savings rate, investment rate, optimization opportunities, recurring spend, data-health warnings, review items, and drillable transaction tables.
- Suggests amortization rules for lumpy annual expenses and applies them only after approval.

The first implementation is deliberately local and dependency-light: Python standard library, SQLite, and a static dashboard.

## Quick Start

```bash
python3 -m household_fire_lens.server
```

Then open:

```text
http://127.0.0.1:8765
```

By default the app creates a local SQLite database at:

```text
.household-fire-lens/household-fire-lens.sqlite3
```

This path is ignored by git.

## Tests

```bash
python3 -m unittest
```

## Privacy

This repo is designed so personal files do not get committed accidentally. The `.gitignore` excludes bank exports, investment statements, PDFs, spreadsheets, upload folders, local databases, secrets, and common ABN/ING/DeGiro/IBKR/Booking.com filename patterns.

Raw data stays local. The app does not call external services.

## Project Status

Implemented MVP:

- SQLite schema
- CSV parser profiles for common ING, ABN AMRO, IBKR, DeGiro, and generic exports
- import and deduplication pipeline
- account roles
- classification engine
- salary, transfer, investment, mortgage, Booking.com reimbursement, card settlement, refund, and merchant category handling
- monthly aggregation
- FIRE dashboard APIs
- dark local dashboard UI
- optimization queue, recurring merchant detection, trend alerts, and amortization approvals
- review queue and reusable classification rules
- focused domain tests with synthetic data

Future stages are documented in [docs/product-spec.md](docs/product-spec.md).
