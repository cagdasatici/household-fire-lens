const money = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const preciseMoney = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });

const titles = {
  fire: ["FIRE Snapshot", "Normalized burn, savings rate, investment rate, and FI number."],
  flow: ["Monthly Flow", "Month-by-month household economics."],
  spending: ["Spending", "Category, merchant, and transaction drilldown."],
  review: ["Review", "Material uncertainty that needs a decision."],
  health: ["Data Health", "Trust indicators for imported and classified data."],
  imports: ["Imports", "Upload files and set account roles."],
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
  return response.json();
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
  document.getElementById(id).textContent = value;
}

function renderTable(id, columns, rows) {
  const table = document.getElementById(id);
  if (!rows.length) {
    table.innerHTML = `<tbody><tr><td>No data yet</td></tr></tbody>`;
    return;
  }
  table.innerHTML = `
    <thead>
      <tr>${columns.map((column) => `<th class="${column.number ? "number" : ""}">${column.label}</th>`).join("")}</tr>
    </thead>
    <tbody>
      ${rows
        .map(
          (row) => `
            <tr>
              ${columns
                .map((column) => {
                  const value = column.render ? column.render(row) : row[column.key];
                  return `<td class="${column.number ? "number" : ""}">${value ?? ""}</td>`;
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
  const summary = data.summary;
  setText("monthly-burn", fmtMoney(summary.monthly_burn));
  setText("annual-burn", fmtMoney(summary.annualized_burn));
  setText("savings-rate", fmtPercent(summary.savings_rate));
  setText("fi-number", fmtMoney(summary.fi_number));
  setText("wealth-allocation", fmtMoney(summary.wealth_allocation));
  setText("investment-rate", fmtPercent(summary.investment_rate));
  renderBurnChart(data.months || []);
  renderHealth(data.data_health || {});
}

function renderBurnChart(months) {
  const chart = document.getElementById("burn-chart");
  if (!months.length) {
    chart.innerHTML = "<p>No imported data yet.</p>";
    return;
  }
  const max = Math.max(...months.map((row) => Number(row.household_spend_normalized || 0)), 1);
  chart.innerHTML = months
    .slice(-12)
    .map((row) => {
      const value = Number(row.household_spend_normalized || 0);
      const width = Math.max(2, Math.round((value / max) * 100));
      return `
        <div class="bar-row">
          <span>${row.month}</span>
          <div class="bar-track"><div class="bar-fill" style="width: ${width}%"></div></div>
          <strong>${fmtMoney(value)}</strong>
        </div>
      `;
    })
    .join("");
}

async function loadFlow() {
  const data = await api("/api/dashboard/monthly-flow");
  renderTable(
    "flow-table",
    [
      { key: "month", label: "Month" },
      { key: "real_income", label: "Income", number: true, render: (row) => fmtPrecise(row.real_income) },
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
  renderTable(
    "spending-table",
    [
      { key: "economic_class", label: "Economic class" },
      { key: "category", label: "Category" },
      { key: "subcategory", label: "Subcategory" },
      { key: "outflow", label: "Outflow", number: true, render: (row) => fmtPrecise(row.outflow) },
      { key: "inflow", label: "Inflow", number: true, render: (row) => fmtPrecise(row.inflow) },
      { key: "count", label: "Count", number: true },
    ],
    data.breakdown || [],
  );
  await loadTransactions();
}

async function loadTransactions() {
  const q = document.getElementById("transaction-search").value;
  const data = await api(`/api/transactions?limit=250&q=${encodeURIComponent(q)}`);
  renderTable(
    "transactions-table",
    [
      { key: "transaction_date", label: "Date" },
      { key: "amount", label: "Amount", number: true, render: (row) => fmtPrecise(row.amount) },
      { key: "normalized_merchant", label: "Merchant" },
      { key: "economic_class", label: "Class" },
      { key: "category", label: "Category" },
      { key: "account_name", label: "Account" },
      { key: "confidence", label: "Conf.", number: true, render: (row) => `${Math.round(Number(row.confidence || 0) * 100)}%` },
    ],
    data.transactions || [],
  );
}

async function loadReview() {
  const data = await api("/api/review-items");
  const list = document.getElementById("review-list");
  if (!data.review_items.length) {
    list.innerHTML = "<p>No material review needed.</p>";
    return;
  }
  list.innerHTML = data.review_items
    .map(
      (item) => `
        <article class="review-item">
          <div>
            <strong>${item.normalized_merchant || item.description || "Transaction"}</strong>
            <span>${item.transaction_date} · ${fmtPrecise(item.amount)} · ${item.reason || "Needs classification"}</span>
          </div>
          <div class="review-actions">
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Uncategorized">Spend</button>
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

function renderHealth(health) {
  setText("health-transactions", health.transactions || 0);
  setText("health-review", health.open_review_items || 0);
  setText("health-duplicates", health.duplicates || 0);
  setText("health-unknown", fmtMoney(health.needs_review_amount || 0));
}

async function loadImports() {
  const [imports, accounts] = await Promise.all([api("/api/imports"), api("/api/accounts")]);
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
  renderTable(
    "accounts-table",
    [
      { key: "display_name", label: "Account" },
      { key: "institution", label: "Institution" },
      { key: "role", label: "Role" },
      { key: "currency", label: "Currency" },
    ],
    accounts.accounts || [],
  );
}

async function refreshAll() {
  await loadFire();
  await Promise.all([loadFlow(), loadSpending(), loadReview(), loadImports()]);
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
});

document.getElementById("refresh-button").addEventListener("click", refreshAll);
document.getElementById("fire-multiple").addEventListener("change", loadFire);
document.getElementById("transaction-search-button").addEventListener("click", loadTransactions);
document.getElementById("transaction-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadTransactions();
});

bindNavigation();
bindImport();
refreshAll().catch((error) => {
  console.error(error);
  alert(error.message);
});
