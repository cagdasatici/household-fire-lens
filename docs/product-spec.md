# Household FIRE Lens Product Spec

Household FIRE Lens is a local-first dashboard for household economics and FIRE planning. It imports yearly bank, card, and investment exports, preserves raw rows, normalizes transactions, classifies their economic meaning, and produces explainable monthly burn, savings, investment, balance, and data-health views.

## Priorities

1. FIRE-style tracking.
2. Savings maximization.
3. Better awareness.
4. Budgeting support.

The product optimizes for sustainable cost-of-living truth, not just pretty expense charts.

## Core Principles

- Raw imported data is immutable.
- Every number must drill down to transactions and rules.
- Transfers are not spending.
- Investment moves are wealth allocation.
- Booking.com reimbursements are pass-throughs, not income.
- Refunds reduce original spending categories.
- Cashflow and normalized FIRE views stay separate.
- Review only material uncertainty.
- Raw financial data stays local.

## Economic Classes

- `income`
- `household_spend`
- `wealth_allocation`
- `internal_transfer`
- `debt_service`
- `reimbursement_pass_through`
- `refund`
- `ignore_noise`
- `needs_review`

## MVP Scope

- Local web app with SQLite.
- CSV/TAB/PDF import for common ING, ABN AMRO, ING credit-card, Amex, IBKR, DeGiro, Wise, and generic exports.
- Account roles for checking, savings, investment, mortgage, and unknown accounts.
- Rule-based classification with confidence and explanations.
- Salary detection using recurring payer/source account, date window, amount similarity, February cash-bonus handling, and missing-income expectations.
- Booking.com reimbursement clearing against unknown/card spend at monthly level.
- Internal transfer, investment transfer, mortgage, credit-card settlement/detail pairing, RSU, and refund handling.
- Review queue that creates reusable rules.
- FIRE Snapshot, Optimization, Monthly Flow, Spending, Review, Data Health, and Imports views.
- Recurring merchant detection, controllability-ranked optimization queue, trend alerts, and approval-based amortization rules.

## Stage Roadmap

1. Ingestion foundation: parser contracts, raw rows, normalized rows, deduplication.
2. Economic classification: salary, reimbursements, transfers, investments, mortgage, refunds.
3. Review and rule learning.
4. Dashboard MVP.
5. Amortization and optimization insights.
6. Investment and mortgage refinement.
7. Credit-card statement import and settlement pairing.
8. Balance sheet from statement anchors and investment account observations.
9. Zero-toil monthly close loop.

## Build Rule

Build the truth engine before chart polish. A chart is only useful if its total is explainable and does not double-count transfers, reimbursements, or wealth allocation.
