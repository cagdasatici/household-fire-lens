const money = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const preciseMoney = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });

const titles = {
  fire: ["FIRE Snapshot", "Normalized burn, savings rate, investment rate, and FI number."],
  optimization: ["Optimization", "Ranked levers, recurring spend, trends, and amortization decisions."],
  flow: ["Monthly Flow", "Month-by-month household economics."],
  spending: ["Spending", "Category, merchant, and transaction drilldown."],
  review: ["Review", "Material uncertainty that needs a decision."],
  health: ["Data Health", "Trust indicators for imported and classified data."],
  imports: ["Imports", "Upload files, set account roles, and inspect rules."],
};

const accountRoles = ["checking", "savings", "investment", "mortgage", "credit_card_proxy", "unknown"];
const state = { spending: null, fire: null, optimization: null };

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtMoney(value) {
  return money.format(Number(value || 0));
}

function fmtPrecise(value) {
  return preciseMoney.format(Number(value || 0));
}

function fmtPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Math.round(Number(value) * 100)}%`;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function renderTable(id, columns, rows) {
  const table = document.getElementById(id);
  if (!rows.length) {
    table.innerHTML = `<tbody><tr><td>No data yet</td></tr></tbody>`;
    return;
  }
  table.innerHTML = `
    <thead>
      <tr>${columns.map((column) => `<th class="${column.number ? "number" : ""}">${escapeHtml(column.label)}</th>`).join("")}</tr>
    </thead>
    <tbody>
      ${rows
        .map(
          (row) => `
            <tr>
              ${columns
                .map((column) => {
                  const value = column.render ? column.render(row) : row[column.key];
                  return `<td class="${column.number ? "number" : ""}">${column.html ? value : escapeHtml(value)}</td>`;
                })
                .join("")}
            </tr>
          `,
        )
        .join("")}
    </tbody>
  `;
}

async function loadFire() {
  const multiple = document.getElementById("fire-multiple").value || "25";
  const data = await api(`/api/dashboard/fire?multiple=${encodeURIComponent(multiple)}`);
  state.fire = data;
  const summary = data.summary;
  setText("monthly-burn", fmtMoney(summary.monthly_burn));
  setText("annual-burn", fmtMoney(summary.annualized_burn));
  setText("savings-rate", fmtPercent(summary.savings_rate));
  setText("fi-number", fmtMoney(summary.fi_number));
  setText("wealth-allocation", fmtMoney(summary.wealth_allocation));
  setText("investment-rate", fmtPercent(summary.investment_rate));
  setText("fi-multiple-label", `${summary.fire_multiple || multiple}x annual burn`);
  setText("runway-months", summary.runway_months ? `Runway ${Math.round(summary.runway_months)} months` : "Runway n/a");
  renderBurnChart(data.months || []);
  renderHealth(data.data_health || {});
  renderTrustList(data.data_health || {});
}

function renderBurnChart(months) {
  const chart = document.getElementById("burn-chart");
  if (!months.length) {
    chart.innerHTML = `<p class="empty">No imported data yet.</p>`;
    return;
  }
  const recent = months.slice(-12);
  const max = Math.max(...recent.map((row) => Number(row.household_spend_normalized || 0)), 1);
  chart.innerHTML = recent
    .map((row) => {
      const normalized = Number(row.household_spend_normalized || 0);
      const cashflow = Number(row.household_spend_cashflow || 0);
      const width = Math.max(2, Math.round((normalized / max) * 100));
      const cashWidth = Math.max(2, Math.round((cashflow / max) * 100));
      return `
        <div class="bar-row layered">
          <span>${escapeHtml(row.month)}</span>
          <div class="bar-stack">
            <div class="bar-track slim"><div class="bar-fill cashflow" style="width: ${cashWidth}%"></div></div>
            <div class="bar-track"><div class="bar-fill" style="width: ${width}%"></div></div>
          </div>
          <strong>${fmtMoney(normalized)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderTrustList(health) {
  const confidence = health.confidence_by_value || {};
  document.getElementById("trust-list").innerHTML = `
    ${signal("Classified value", `${fmtMoney((confidence.high || 0) + (confidence.medium || 0))}`, "High and medium confidence")}
    ${signal("Open review", `${health.open_review_items || 0}`, "Material unresolved items")}
    ${signal("Unknown card spend", fmtMoney(health.unknown_card_spend || 0), "Potential card-detail blind spot")}
    ${signal("Uncleared reimbursement", fmtMoney(health.reimbursement_uncleared || 0), "Booking.com clearing balance")}
  `;
}

function signal(label, value, detail) {
  return `
    <div class="signal">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

async function loadOptimization() {
  const data = await api("/api/dashboard/optimization");
  state.optimization = data;
  setText("opt-months", data.summary.months_loaded || 0);
  setText("opt-burden", fmtMoney(data.summary.total_burden || 0));
  setText("opt-recurring", data.summary.recurring_merchants || 0);
  setText("opt-amortizations", data.summary.suggested_amortizations || 0);
  renderOpportunities(data.opportunities || []);
  renderRecurring(data.recurring || []);
  renderTrends(data.trend_alerts || []);
  renderAmortizations(data.amortization_rules || []);
}

function renderOpportunities(items) {
  const target = document.getElementById("opportunity-list");
  if (!items.length) {
    target.innerHTML = `<p class="empty">No optimization signal yet. Import and classify more months first.</p>`;
    return;
  }
  target.innerHTML = items
    .map(
      (item) => `
        <article class="opportunity">
          <div>
            <span>${escapeHtml(item.economic_class)}</span>
            <h4>${escapeHtml(item.category)}${item.subcategory ? ` / ${escapeHtml(item.subcategory)}` : ""}</h4>
            <p>${escapeHtml(item.why)}</p>
          </div>
          <div class="opportunity-metrics">
            <strong>${fmtMoney(item.amount)}</strong>
            <small>${fmtPercent(item.share)} of burden</small>
            <small>control ${fmtPercent(item.controllability)}</small>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderRecurring(rows) {
  renderTable(
    "recurring-table",
    [
      { key: "merchant", label: "Merchant" },
      { key: "category", label: "Category" },
      { key: "cadence", label: "Cadence" },
      { key: "months_count", label: "Months", number: true },
      { key: "monthly_average", label: "Monthly avg", number: true, render: (row) => fmtPrecise(row.monthly_average) },
      { key: "annualized", label: "Annualized", number: true, render: (row) => fmtPrecise(row.annualized) },
      { key: "stability", label: "Stability", number: true, render: (row) => fmtPercent(row.stability) },
    ],
    rows,
  );
}

function renderTrends(rows) {
  const target = document.getElementById("trend-list");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">No trend alerts yet. Six months of data unlocks this view.</p>`;
    return;
  }
  target.innerHTML = rows
    .map((row) =>
      signal(
        row.category,
        `+${fmtMoney(row.monthly_delta)}/mo`,
        `${fmtPercent(row.change)} versus prior three-month average`,
      ),
    )
    .join("");
}

function renderAmortizations(rows) {
  const target = document.getElementById("amortization-list");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">No lumpy annual-cost candidates yet.</p>`;
    return;
  }
  target.innerHTML = rows
    .map(
      (rule) => `
        <article class="rule-item ${escapeHtml(rule.review_status)}">
          <div>
            <strong>${escapeHtml(rule.name)}</strong>
            <span>${escapeHtml(rule.category || "Uncategorized")} · ${fmtPrecise(rule.annual_amount)} annual · ${fmtPrecise(rule.monthly_amount)} monthly</span>
            <small>${escapeHtml(rule.start_month)} to ${escapeHtml(rule.end_month || "open")} · ${escapeHtml(rule.review_status)}</small>
          </div>
          <div class="review-actions">
            <button data-amortization="${rule.id}" data-status="approved">Approve</button>
            <button data-amortization="${rule.id}" data-status="disabled">Disable</button>
          </div>
        </article>
      `,
    )
    .join("");
}

async function loadFlow() {
  const data = await api("/api/dashboard/monthly-flow");
  renderTable(
    "flow-table",
    [
      { key: "month", label: "Month" },
      { key: "real_income", label: "Income", number: true, render: (row) => fmtPrecise(row.real_income) },
      { key: "household_spend_cashflow", label: "Cashflow burn", number: true, render: (row) => fmtPrecise(row.household_spend_cashflow) },
      { key: "household_spend_normalized", label: "FIRE burn", number: true, render: (row) => fmtPrecise(row.household_spend_normalized) },
      { key: "mortgage_total", label: "Mortgage", number: true, render: (row) => fmtPrecise(row.mortgage_total) },
      { key: "wealth_allocation", label: "Invested", number: true, render: (row) => fmtPrecise(row.wealth_allocation) },
      { key: "reimbursements_cleared", label: "Reimb. cleared", number: true, render: (row) => fmtPrecise(row.reimbursements_cleared) },
      { key: "net_cash_change", label: "Net cash", number: true, render: (row) => fmtPrecise(row.net_cash_change) },
      { key: "savings_rate_fire", label: "FIRE savings", number: true, render: (row) => fmtPercent(row.savings_rate_fire) },
    ],
    data.months || [],
  );
}

async function loadSpending() {
  const data = await api("/api/dashboard/spending");
  state.spending = data;
  renderTable(
    "spending-table",
    [
      { key: "economic_class", label: "Economic class" },
      { key: "category", label: "Category" },
      { key: "subcategory", label: "Subcategory" },
      { key: "outflow", label: "Outflow", number: true, render: (row) => fmtPrecise(row.outflow) },
      { key: "inflow", label: "Inflow", number: true, render: (row) => fmtPrecise(row.inflow) },
      { key: "avg_confidence", label: "Confidence", number: true, render: (row) => fmtPercent(row.avg_confidence) },
      { key: "count", label: "Count", number: true },
    ],
    data.breakdown || [],
  );
  renderCategoryMonths(data.category_months || []);
  populateCategoryFilter(data.breakdown || []);
  await loadTransactions();
}

function populateCategoryFilter(rows) {
  const select = document.getElementById("transaction-category");
  const current = select.value;
  const categories = [...new Set(rows.map((row) => row.category).filter(Boolean))].sort();
  select.innerHTML = `<option value="">All categories</option>${categories
    .map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`)
    .join("")}`;
  select.value = categories.includes(current) ? current : "";
}

function renderCategoryMonths(rows) {
  const target = document.getElementById("category-months");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">No spending by month yet.</p>`;
    return;
  }
  const months = [...new Set(rows.map((row) => row.month))].sort().slice(-8);
  const categories = [...new Set(rows.map((row) => row.category))].sort();
  const lookup = new Map(rows.map((row) => [`${row.category}|${row.month}`, Number(row.outflow || 0)]));
  const max = Math.max(...rows.map((row) => Number(row.outflow || 0)), 1);
  target.innerHTML = `
    <div class="matrix-row header"><span>Category</span>${months.map((month) => `<span>${escapeHtml(month)}</span>`).join("")}</div>
    ${categories
      .map(
        (category) => `
          <div class="matrix-row">
            <strong>${escapeHtml(category)}</strong>
            ${months
              .map((month) => {
                const value = lookup.get(`${category}|${month}`) || 0;
                const intensity = Math.min(0.95, Math.max(0.08, value / max));
                return `<span class="heat" style="--intensity:${intensity}">${value ? fmtMoney(value) : ""}</span>`;
              })
              .join("")}
          </div>
        `,
      )
      .join("")}
  `;
}

async function loadTransactions() {
  const params = new URLSearchParams();
  params.set("limit", "350");
  const q = document.getElementById("transaction-search").value;
  const klass = document.getElementById("transaction-class").value;
  const category = document.getElementById("transaction-category").value;
  const confidence = document.getElementById("transaction-confidence").value;
  if (q) params.set("q", q);
  if (klass) params.set("economic_class", klass);
  if (category) params.set("category", category);
  if (confidence) params.set("confidence", confidence);
  const data = await api(`/api/transactions?${params.toString()}`);
  renderTable(
    "transactions-table",
    [
      { key: "transaction_date", label: "Date" },
      { key: "amount", label: "Amount", number: true, render: (row) => fmtPrecise(row.amount) },
      { key: "normalized_merchant", label: "Merchant" },
      { key: "economic_class", label: "Class" },
      { key: "category", label: "Category" },
      { key: "account_name", label: "Account" },
      { key: "confidence", label: "Conf.", number: true, render: (row) => fmtPercent(row.confidence) },
      { key: "explanation", label: "Why" },
    ],
    data.transactions || [],
  );
}

async function loadReview() {
  const data = await api("/api/review-items");
  const list = document.getElementById("review-list");
  if (!data.review_items.length) {
    list.innerHTML = `<p class="empty">No material review needed.</p>`;
    return;
  }
  list.innerHTML = data.review_items
    .map(
      (item) => `
        <article class="review-item">
          <div>
            <strong>${escapeHtml(item.normalized_merchant || item.description || "Transaction")}</strong>
            <span>${escapeHtml(item.transaction_date)} · ${fmtPrecise(item.amount)} · ${escapeHtml(item.reason || "Needs classification")}</span>
            <small>Current: ${escapeHtml(item.economic_class || "unknown")} / ${escapeHtml(item.category || "Uncategorized")}</small>
          </div>
          <div class="review-actions">
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Groceries">Groceries</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Eating Out">Eating out</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Shopping">Shopping</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="internal_transfer" data-category="Transfers">Transfer</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="wealth_allocation" data-category="Investments">Investment</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="reimbursement_pass_through" data-category="Reimbursements">Reimb.</button>
          </div>
        </article>
      `,
    )
    .join("");
}

async function resolveReview(button) {
  await api(`/api/review-items/${button.dataset.review}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      transaction_id: Number(button.dataset.transaction),
      economic_class: button.dataset.class,
      category: button.dataset.category,
      create_rule: true,
    }),
  });
  await refreshAll();
}

async function setAmortizationStatus(button) {
  await api(`/api/amortization-rules/${button.dataset.amortization}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: button.dataset.status }),
  });
  await refreshAll();
}

function renderHealth(health) {
  setText("health-transactions", health.transactions || 0);
  setText("health-review", health.open_review_items || 0);
  setText("health-duplicates", health.duplicates || 0);
  setText("health-unknown", fmtMoney(health.needs_review_amount || 0));
  setText("health-card", fmtMoney(health.unknown_card_spend || 0));
  setText("health-reimbursement", fmtMoney(health.reimbursement_uncleared || 0));
  renderConfidenceBars(health.confidence_by_value || {});
}

function renderConfidenceBars(confidence) {
  const rows = [
    ["High", confidence.high || 0],
    ["Medium", confidence.medium || 0],
    ["Low", confidence.low || 0],
  ];
  const max = Math.max(...rows.map(([, amount]) => Number(amount)), 1);
  document.getElementById("confidence-bars").innerHTML = rows
    .map(([label, amount]) => {
      const width = Math.max(2, Math.round((Number(amount) / max) * 100));
      return `
        <div class="bar-row">
          <span>${label}</span>
          <div class="bar-track"><div class="bar-fill ${label.toLowerCase()}" style="width:${width}%"></div></div>
          <strong>${fmtMoney(amount)}</strong>
        </div>
      `;
    })
    .join("");
}

async function loadImports() {
  const [imports, accounts, rules] = await Promise.all([api("/api/imports"), api("/api/accounts"), api("/api/rules")]);
  renderTable(
    "imports-table",
    [
      { key: "filename", label: "File" },
      { key: "institution", label: "Institution" },
      { key: "statement_year", label: "Year" },
      { key: "row_count", label: "Rows", number: true },
      { key: "imported_at", label: "Imported" },
    ],
    imports.imports || [],
  );
  renderAccounts(accounts.accounts || []);
  renderTable(
    "rules-table",
    [
      { key: "name", label: "Rule" },
      { key: "created_by", label: "Source" },
      { key: "priority", label: "Priority", number: true },
      { key: "confidence", label: "Confidence", number: true, render: (row) => fmtPercent(row.confidence) },
      { key: "enabled", label: "Enabled", render: (row) => (row.enabled ? "yes" : "no") },
    ],
    rules.rules || [],
  );
}

function renderAccounts(rows) {
  renderTable(
    "accounts-table",
    [
      { key: "display_name", label: "Account" },
      { key: "institution", label: "Institution" },
      {
        key: "role",
        label: "Role",
        html: true,
        render: (row) => `
          <select class="role-select" data-account="${row.id}">
            ${accountRoles
              .map((role) => `<option value="${role}" ${role === row.role ? "selected" : ""}>${role}</option>`)
              .join("")}
          </select>
        `,
      },
      { key: "currency", label: "Currency" },
    ],
    rows,
  );
}

async function updateAccountRole(select) {
  await api(`/api/accounts/${select.dataset.account}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: select.value }),
  });
  await refreshAll();
}

async function refreshAll() {
  setText("side-db-status", "refreshing");
  await loadFire();
  await Promise.all([loadOptimization(), loadFlow(), loadSpending(), loadReview(), loadImports()]);
  setText("side-db-status", "ready");
}

function bindNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(button.dataset.view).classList.add("active");
      const [title, subtitle] = titles[button.dataset.view];
      setText("view-title", title);
      setText("view-subtitle", subtitle);
    });
  });
}

function bindImport() {
  document.getElementById("import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = document.getElementById("import-result");
    result.textContent = "Importing...";
    try {
      const data = await api("/api/imports", { method: "POST", body: new FormData(form) });
      result.textContent = JSON.stringify(data, null, 2);
      form.reset();
      await refreshAll();
    } catch (error) {
      result.textContent = error.message;
    }
  });
}

document.addEventListener("click", (event) => {
  if (event.target.matches("[data-review]")) {
    resolveReview(event.target);
  }
  if (event.target.matches("[data-amortization]")) {
    setAmortizationStatus(event.target);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches(".role-select")) {
    updateAccountRole(event.target);
  }
});

document.getElementById("refresh-button").addEventListener("click", refreshAll);
document.getElementById("fire-multiple").addEventListener("change", loadFire);
document.getElementById("transaction-search-button").addEventListener("click", loadTransactions);
["transaction-class", "transaction-category", "transaction-confidence"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadTransactions);
});
document.getElementById("transaction-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadTransactions();
});

bindNavigation();
bindImport();
refreshAll().catch((error) => {
  console.error(error);
  setText("side-db-status", "error");
  alert(error.message);
});
