const money = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const preciseMoney = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });

const titles = {
  fire: ["FIRE Snapshot", "Normalized burn, savings rate, investment rate, and FI number."],
  optimization: ["Optimization", "Ranked levers, recurring spend, trends, and amortization decisions."],
  flow: ["Monthly Flow", "Month-by-month household economics."],
  spending: ["Spending", "Category, merchant, and transaction drilldown."],
  buckets: ["Buckets", "Every spending bucket summed by month and year."],
  insights: ["Insights", "Year-over-year spending analysis and key takeaways."],
  review: ["Review", "Material uncertainty that needs a decision."],
  health: ["Data Health", "Trust indicators for imported and classified data."],
  imports: ["Imports", "Upload files, set account roles, and inspect rules."],
};

const accountRoles = [
  "checking",
  "savings",
  "investment",
  "mortgage",
  "credit_card",
  "credit_card_proxy",
  "wise",
  "broker_proxy",
  "unknown",
];
const state = { spending: null, fire: null, optimization: null, period: "last13", auditMonth: null, auditSort: "confidence", auditCategoryFilter: null, auditBusy: false, auditRows: null, incomeMonth: null, incomeSort: "amount", incomeCategoryFilter: null, incomeRows: null };

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try {
      const parsed = JSON.parse(body);
      message = parsed.error || parsed.message || body;
    } catch (_error) {
      message = body;
    }
    throw new Error(`${response.status} ${message}`);
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

async function loadMetadata() {
  const metadata = await api("/api/metadata");
  setText("app-git", `git ${metadata.git_hash || "unknown"}`);
  setText("app-db", metadata.database || "db unknown");
  setText("app-classified-at", metadata.classified_at ? `classified ${metadata.classified_at}` : "classified never");
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

function periodQuery() {
  const selector = document.getElementById("period-selector");
  state.period = selector ? selector.value : state.period || "last13";
  return encodeURIComponent(state.period);
}

async function loadFire() {
  const multiple = document.getElementById("fire-multiple").value || "25";
  const data = await api(`/api/dashboard/fire?multiple=${encodeURIComponent(multiple)}&period=${periodQuery()}`);
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
  renderPeriodOptions(data.available_years || []);
  renderBurnChart(data.months || []);
  renderYearlyTable(data.years || []);
  renderHealth(data.data_health || {});
  renderTrustList(data.data_health || {});
}

function renderPeriodOptions(years) {
  const selector = document.getElementById("period-selector");
  if (!selector || selector.dataset.loadedYears === years.join(",")) return;
  const current = selector.value || state.period || "last13";
  selector.innerHTML = `
    <option value="last13">Last 13 months</option>
    <option value="ytd">YTD</option>
    ${years.map((year) => `<option value="year:${escapeHtml(year)}">${escapeHtml(year)}</option>`).join("")}
    <option value="all">All</option>
  `;
  selector.value = [...selector.options].some((option) => option.value === current) ? current : "last13";
  selector.dataset.loadedYears = years.join(",");
}

function percentile(values, pct) {
  const sorted = values.filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
  if (!sorted.length) return 1;
  const index = Math.min(sorted.length - 1, Math.ceil(sorted.length * pct) - 1);
  return sorted[index];
}

function renderBurnChart(months) {
  const chart = document.getElementById("burn-chart");
  if (!months.length) {
    chart.innerHTML = `<p class="empty">No imported data yet.</p>`;
    return;
  }
  chart.classList.toggle("scroll-list", state.period === "all");
  const scaleReference = Math.max(
    percentile(months.flatMap((row) => [Number(row.regular_income || 0), Math.max(0, Number(row.household_spend_cashflow || 0))]), 0.9) * 1.2,
    1,
  );
  chart.innerHTML = months
    .map((row) => {
      const regular = Number(row.regular_income || 0);
      const variable = Number(row.variable_income || 0);
      const income = Number(row.real_income || 0);
      const outflow = Number(row.household_spend_cashflow || 0);
      const net = Number(row.household_net_pnl ?? income - outflow);
      const cashMovement = Number(row.net_cash_change || 0);
      const regularWidth = Math.min(100, Math.round((regular / scaleReference) * 100));
      const variableWidth = regular >= scaleReference ? 0 : Math.min(100 - regularWidth, Math.round((variable / scaleReference) * 100));
      const outflowWidth = outflow > 0 ? Math.min(100, Math.max(2, Math.round((outflow / scaleReference) * 100))) : 0;
      const incomeOverflow = income > scaleReference;
      const outflowOverflow = outflow > scaleReference;
      return `
        <div class="bar-row monthly-flow-row" title="${escapeHtml(monthTooltip(row))}">
          <button class="month-link" data-month="${escapeHtml(row.month)}">${escapeHtml(row.month)}</button>
          <div class="bar-stack">
            <button class="flow-bar-line drilldown-line" data-month-income="${escapeHtml(row.month)}" aria-label="Drill into ${escapeHtml(row.month)} in transactions">
              <small>In</small>
              <div class="bar-track segmented">
                <div class="bar-fill income regular" style="width: ${regularWidth}%"></div>
                <div class="bar-fill income variable" style="width: ${variableWidth}%"></div>
                ${incomeOverflow ? `<span class="overflow-marker">›</span>` : ""}
              </div>
              <strong>${fmtMoney(income)}</strong>
            </button>
            <button class="flow-bar-line drilldown-line" data-month="${escapeHtml(row.month)}" aria-label="Drill into ${escapeHtml(row.month)} out transactions">
              <small>Out</small>
              <div class="bar-track"><div class="bar-fill outflow" style="width: ${outflowWidth}%"></div>${outflowOverflow ? `<span class="overflow-marker">›</span>` : ""}</div>
              <strong>${fmtMoney(outflow)}</strong>
            </button>
          </div>
          <div class="flow-net">
            <strong class="${net >= 0 ? "positive" : "negative"}">${fmtMoney(net)}</strong>
            <small>cash ${fmtMoney(cashMovement)}</small>
          </div>
        </div>
      `;
    })
    .join("");
}

function monthTooltip(row) {
  const income = Number(row.real_income || 0);
  const regular = Number(row.regular_income || 0);
  const variable = Number(row.variable_income || 0);
  const outflow = Number(row.household_spend_cashflow || 0);
  const grossOutflow = Number(row.household_outflow_gross || 0);
  const refunds = Number(row.refunds || 0);
  const reimbursements = Number(row.reimbursements_cleared || 0);
  const linked = Number(row.linked_reimbursements || 0);
  const net = Number(row.household_net_pnl ?? income - outflow);
  const cashMovement = Number(row.net_cash_change || 0);
  return [
    `${row.month}`,
    `IN ${fmtPrecise(income)} = regular ${fmtPrecise(regular)} + variable ${fmtPrecise(variable)}`,
    `OUT ${fmtPrecise(outflow)} = gross household ${fmtPrecise(grossOutflow)} - refunds ${fmtPrecise(refunds)} - reimbursements ${fmtPrecise(reimbursements)}`,
    `Linked reimbursements ${fmtPrecise(linked)}`,
    `NET ${fmtPrecise(net)} = IN - OUT`,
    `Cash movement incl. transfers/investing ${fmtPrecise(cashMovement)}`,
    `Invested ${fmtPrecise(row.wealth_allocation || 0)}; internal transfers ${fmtPrecise(row.internal_transfers || 0)}`,
  ].join("\n");
}

function renderYearlyTable(years) {
  renderTable(
    "yearly-table",
    [
      { key: "year", label: "Year" },
      { key: "months", label: "Months", number: true },
      { key: "regular_income", label: "Regular in", number: true, render: (row) => fmtPrecise(row.regular_income) },
      { key: "variable_income", label: "Variable in", number: true, render: (row) => fmtPrecise(row.variable_income) },
      { key: "real_income", label: "Total in", number: true, render: (row) => fmtPrecise(row.real_income) },
      { key: "household_spend_cashflow", label: "Out", number: true, render: (row) => fmtPrecise(row.household_spend_cashflow) },
      { key: "household_net_pnl", label: "Net", number: true, render: (row) => fmtPrecise(row.household_net_pnl) },
      { key: "net_cash_change", label: "Cash", number: true, render: (row) => fmtPrecise(row.net_cash_change) },
    ],
    years,
  );
}

async function renderMonthAudit(month) {
  const panel = document.getElementById("month-audit-panel");
  const list = document.getElementById("month-audit-list");
  if (state.auditBusy) {
    setAuditStatus("Still saving the last change. One moment...", "info");
    return;
  }
  state.auditMonth = month;
  state.auditCategoryFilter = null;
  state.auditRows = null;
  document.getElementById("month-audit-title").textContent = `${month} OUT Drilldown`;
  panel.classList.remove("hidden");
  setAuditStatus("Loading OUT transactions...");
  list.innerHTML = `<p class="empty">Loading month audit...</p>`;
  updateSortButtonStates();
  let data;
  try {
    data = await api(`/api/month-audit?month=${encodeURIComponent(month)}&sort=${encodeURIComponent(state.auditSort)}`);
  } catch (error) {
    setAuditStatus(error.message, "error");
    list.innerHTML = `<p class="empty">Could not load this month.</p>`;
    return;
  }
  if (!data.rows.length) {
    setAuditStatus("No household OUT transactions in this month.");
    list.innerHTML = `<p class="empty">No household outflow rows for this month.</p>`;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const rows = data.rows || [];
  state.auditRows = rows;
  const grossTotal = rows.reduce((sum, row) => sum + Math.abs(Number(row.amount || 0)), 0);
  const linkedTotal = rows.reduce((sum, row) => sum + Number(row.linked_reimbursement || 0), 0);
  const netTotal = rows.reduce((sum, row) => sum + Number(row.net_outflow || 0), 0);
  const categoryTotals = rows.reduce((totals, row) => {
    const key = row.category || "Uncategorized";
    totals.set(key, (totals.get(key) || 0) + Number(row.net_outflow || 0));
    return totals;
  }, new Map());
  list.innerHTML = rows.length
    ? `
      <div class="audit-summary">
        ${signal("Net OUT", fmtPrecise(netTotal), "After linked reimbursements")}
        ${signal("Gross OUT", fmtPrecise(grossTotal), "Before refund/reimbursement netting")}
        ${signal("Linked back", fmtPrecise(linkedTotal), "Cleared from company/refunds")}
      </div>
      <div class="audit-category-strip">
        ${[...categoryTotals.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([category, value]) => `<button class="category-filter-btn" data-category="${escapeHtml(category)}"><strong>${escapeHtml(category)}</strong>${fmtPrecise(value)}</button>`)
          .join("")}
      </div>
    `
    : "";
  renderAuditTransactions(rows);
  setAuditStatus(`${rows.length} OUT transactions loaded. Click a row action to correct and recalculate.`);
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderAuditTransactions(rows) {
  const list = document.getElementById("month-audit-list");
  const filteredRows = state.auditCategoryFilter
    ? rows.filter((row) => row.category === state.auditCategoryFilter)
    : rows;
  const strip = list.querySelector(".audit-category-strip");
  if (strip) {
    strip.querySelectorAll(".category-filter-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.category === state.auditCategoryFilter);
    });
  }
  const container = document.createElement("div");
  container.innerHTML = filteredRows
    .map((row) => `
      <article class="audit-row ${row.link_state}">
        <div>
          <strong>${escapeHtml(row.normalized_merchant || row.counterparty_name || "Unknown")}</strong>
          <span>${escapeHtml(row.transaction_date)} · ${fmtPrecise(Math.abs(row.amount))} gross · ${fmtPrecise(row.net_outflow)} net</span>
          <small>${escapeHtml(row.account_name || "Account")} · ${escapeHtml(row.category || "Uncategorized")} · ${escapeHtml(row.subcategory || "")} · ${escapeHtml(row.link_state)} · confidence ${fmtPercent(row.confidence)}</small>
          <p>${escapeHtml(row.description || "")}</p>
          <p class="audit-why">${escapeHtml(row.explanation || "")}</p>
        </div>
        <div class="audit-actions">
          <span class="audit-state ${row.link_state}">${row.link_state === "done" ? "Done" : fmtPercent(row.confidence)}</span>
          <button data-audit-action="link-inflow" data-transaction="${row.id}">Link inflow</button>
          <button data-audit-action="tag-business" data-transaction="${row.id}">Business</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Groceries" data-subcategory="" data-transaction="${row.id}">Groceries</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Eating Out" data-subcategory="" data-transaction="${row.id}">Eating out</button>
          <button data-audit-action="classify" data-category="Education" data-subcategory="Professional Education" data-transaction="${row.id}">Education</button>
          <button data-audit-action="classify" data-category="Holiday" data-subcategory="" data-transaction="${row.id}">Holiday</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Transportation" data-subcategory="" data-transaction="${row.id}">Transport</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Subscriptions" data-subcategory="" data-transaction="${row.id}">Subscriptions</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Health" data-subcategory="" data-transaction="${row.id}">Health</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Housing" data-subcategory="" data-transaction="${row.id}">Housing</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Pet Care" data-subcategory="" data-transaction="${row.id}">Pet care</button>
          <button data-audit-action="classify" data-class="household_spend" data-category="Home and Furniture" data-subcategory="" data-transaction="${row.id}">Home</button>
          <button data-audit-action="classify" data-category="Shopping" data-subcategory="" data-transaction="${row.id}">Shopping</button>
          <button data-audit-action="classify" data-category="Other" data-subcategory="" data-transaction="${row.id}">Other</button>
          <button data-audit-action="classify" data-class="debt_service" data-category="Housing" data-subcategory="Mortgage" data-transaction="${row.id}">Mortgage</button>
          <button data-audit-action="classify" data-class="internal_transfer" data-category="Inter-account Transfers" data-subcategory="" data-transaction="${row.id}">Not OUT</button>
          <button data-audit-action="classify" data-class="wealth_allocation" data-category="Investments" data-subcategory="" data-transaction="${row.id}">Invest</button>
          <button data-audit-action="classify" data-class="reimbursement_pass_through" data-category="Reimbursements" data-subcategory="Company Expense" data-transaction="${row.id}">Reimb.</button>
          <button data-audit-action="custom-bucket" data-transaction="${row.id}" data-merchant="${escapeHtml(row.normalized_merchant || row.counterparty_name || "this merchant")}">Custom</button>
        </div>
      </article>
    `)
    .join("");
  while (list.lastChild && list.lastChild !== strip) {
    list.removeChild(list.lastChild);
  }
  list.appendChild(container);
  setAuditStatus(`${filteredRows.length} of ${rows.length} OUT transactions${state.auditCategoryFilter ? ` (${state.auditCategoryFilter})` : ""}. Click a row action to correct and recalculate.`);
}

async function renderMonthIncome(month) {
  const panel = document.getElementById("month-income-panel");
  const list = document.getElementById("month-income-list");
  if (state.auditBusy) {
    setIncomeStatus("Still saving the last change. One moment...", "info");
    return;
  }
  state.incomeMonth = month;
  state.incomeCategoryFilter = null;
  state.incomeRows = null;
  document.getElementById("month-income-title").textContent = `${month} IN Drilldown`;
  panel.classList.remove("hidden");
  setIncomeStatus("Loading IN transactions...");
  list.innerHTML = `<p class="empty">Loading month income...</p>`;
  updateIncomeSortButtonStates();
  let data;
  try {
    data = await api(`/api/month-income?month=${encodeURIComponent(month)}&sort=${encodeURIComponent(state.incomeSort)}`);
  } catch (error) {
    setIncomeStatus(error.message, "error");
    list.innerHTML = `<p class="empty">Could not load this month.</p>`;
    return;
  }
  if (!data.rows.length) {
    setIncomeStatus("No income transactions in this month.");
    list.innerHTML = `<p class="empty">No income rows for this month.</p>`;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const rows = data.rows || [];
  state.incomeRows = rows;
  const totalIncome = rows.reduce((sum, row) => sum + Number(row.amount || 0), 0);
  const categoryTotals = rows.reduce((totals, row) => {
    const key = row.category || "Uncategorized";
    totals.set(key, (totals.get(key) || 0) + Number(row.amount || 0));
    return totals;
  }, new Map());
  list.innerHTML = rows.length
    ? `
      <div class="audit-summary">
        ${signal("Total IN", fmtPrecise(totalIncome), "Gross income")}
      </div>
      <div class="audit-category-strip">
        ${[...categoryTotals.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([category, value]) => `<button class="category-filter-btn" data-category="${escapeHtml(category)}"><strong>${escapeHtml(category)}</strong>${fmtPrecise(value)}</button>`)
          .join("")}
      </div>
    `
    : "";
  renderIncomeTransactions(rows);
  setIncomeStatus(`${rows.length} IN transactions loaded.`);
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderIncomeTransactions(rows) {
  const list = document.getElementById("month-income-list");
  const filteredRows = state.incomeCategoryFilter
    ? rows.filter((row) => row.category === state.incomeCategoryFilter)
    : rows;
  const strip = list.querySelector(".audit-category-strip");
  if (strip) {
    strip.querySelectorAll(".category-filter-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.category === state.incomeCategoryFilter);
    });
  }
  const container = document.createElement("div");
  container.innerHTML = filteredRows
    .map((row) => `
      <article class="audit-row">
        <div>
          <strong>${escapeHtml(row.normalized_merchant || row.counterparty_name || "Unknown")}</strong>
          <span>${escapeHtml(row.transaction_date)} · ${fmtPrecise(row.amount)}</span>
          <small>${escapeHtml(row.account_name || "Account")} · ${escapeHtml(row.category || "Uncategorized")} · ${escapeHtml(row.subcategory || "")} · confidence ${fmtPercent(row.confidence)}</small>
          <p>${escapeHtml(row.description || "")}</p>
          <p class="audit-why">${escapeHtml(row.explanation || "")}</p>
        </div>
      </article>
    `)
    .join("");
  while (list.lastChild && list.lastChild !== strip) {
    list.removeChild(list.lastChild);
  }
  list.appendChild(container);
  setIncomeStatus(`${filteredRows.length} of ${rows.length} IN transactions${state.incomeCategoryFilter ? ` (${state.incomeCategoryFilter})` : ""}.`);
}

function setAuditStatus(message, tone = "info") {
  const status = document.getElementById("month-audit-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.tone = tone;
}

function updateSortButtonStates() {
  document.querySelectorAll(".sort-button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.sort === state.auditSort);
  });
}

async function handleSortClick(event) {
  const button = event.target;
  if (!button.classList.contains("sort-button")) return;
  state.auditSort = button.dataset.sort;
  updateSortButtonStates();
  if (state.auditMonth) {
    await renderMonthAudit(state.auditMonth);
  }
}

function handleCategoryFilterClick(button) {
  const category = button.dataset.category;
  if (state.auditCategoryFilter === category) {
    state.auditCategoryFilter = null;
  } else {
    state.auditCategoryFilter = category;
  }
  if (state.auditRows) {
    renderAuditTransactions(state.auditRows);
  }
}

function setIncomeStatus(message, tone = "info") {
  const status = document.getElementById("month-income-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.tone = tone;
}

function updateIncomeSortButtonStates() {
  document.querySelectorAll(".sort-income-button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.sort === state.incomeSort);
  });
}

async function handleIncomeSortClick(event) {
  const button = event.target;
  if (!button.classList.contains("sort-income-button")) return;
  state.incomeSort = button.dataset.sort;
  updateIncomeSortButtonStates();
  if (state.incomeMonth) {
    await renderMonthIncome(state.incomeMonth);
  }
}

function handleIncomeCategoryFilterClick(button) {
  const category = button.dataset.category;
  if (state.incomeCategoryFilter === category) {
    state.incomeCategoryFilter = null;
  } else {
    state.incomeCategoryFilter = category;
  }
  if (state.incomeRows) {
    renderIncomeTransactions(state.incomeRows);
  }
}

async function handleAuditAction(button) {
  if (state.auditBusy) {
    setAuditStatus("Still saving the last change. One moment...", "info");
    return;
  }
  let action = button.dataset.auditAction;
  const txId = button.dataset.transaction;
  const body = {};
  if (action === "custom-bucket") {
    const category = window.prompt("New bucket name", "");
    if (!category || !category.trim()) {
      setAuditStatus("Custom bucket cancelled.");
      return;
    }
    const subcategory = window.prompt("Optional sub-bucket", "") || "";
    const scope = window.confirm(`Apply '${category.trim()}' to all transactions from ${button.dataset.merchant}?`)
      ? "merchant"
      : "transaction";
    action = "classify";
    Object.assign(body, {
      economic_class: "household_spend",
      category: category.trim(),
      subcategory: subcategory.trim(),
      scope,
    });
  } else if (action === "classify") {
    Object.assign(body, {
      economic_class: button.dataset.class || "household_spend",
      category: button.dataset.category,
      subcategory: button.dataset.subcategory || "",
      scope: button.dataset.scope || "transaction",
    });
  }
  const originalText = button.textContent;
  state.auditBusy = true;
  button.disabled = true;
  button.textContent = "Saving...";
  setAuditStatus(`Saving ${originalText} for transaction ${txId}...`);
  try {
    await api(`/api/transactions/${txId}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setAuditStatus("Saved. Recalculating this month...");
    await loadFire();
    await loadBuckets();
    if (document.getElementById("spending").classList.contains("active")) {
      await loadSpending();
    }
    state.auditBusy = false;
    if (state.auditMonth) await renderMonthAudit(state.auditMonth);
    const savedMsg = body.category
      ? `Saved. '${body.category}' now shows in the Buckets tab.`
      : "Saved and recalculated.";
    setAuditStatus(savedMsg, "success");
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    setAuditStatus(error.message, "error");
    state.auditBusy = false;
  }
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
  const data = await api(`/api/dashboard/monthly-flow?period=${periodQuery()}`);
  renderTable(
    "flow-table",
    [
      { key: "month", label: "Month" },
      { key: "real_income", label: "Income", number: true, render: (row) => fmtPrecise(row.real_income) },
      { key: "household_spend_cashflow", label: "Household out", number: true, render: (row) => fmtPrecise(row.household_spend_cashflow) },
      { key: "household_net_pnl", label: "Household net", number: true, render: (row) => fmtPrecise(row.household_net_pnl) },
      { key: "household_spend_normalized", label: "FIRE burn", number: true, render: (row) => fmtPrecise(row.household_spend_normalized) },
      { key: "mortgage_total", label: "Mortgage", number: true, render: (row) => fmtPrecise(row.mortgage_total) },
      { key: "wealth_allocation", label: "Invested", number: true, render: (row) => fmtPrecise(row.wealth_allocation) },
      { key: "reimbursements_cleared", label: "Reimb. cleared", number: true, render: (row) => fmtPrecise(row.reimbursements_cleared) },
      { key: "net_cash_change", label: "Cash movement", number: true, render: (row) => fmtPrecise(row.net_cash_change) },
      { key: "savings_rate_fire", label: "FIRE savings", number: true, render: (row) => fmtPercent(row.savings_rate_fire) },
    ],
    data.months || [],
  );
}

async function loadSpending() {
  const data = await api(`/api/dashboard/spending?period=${periodQuery()}`);
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
  await loadBuckets();
  await loadTransactions();
}

async function loadBuckets() {
  const data = await api(`/api/dashboard/buckets?period=${periodQuery()}`);
  renderBucketYearTotals(data.years || []);
  renderBucketMonthTotals(data.months || []);
}

async function loadInsights() {
  const data = await api("/api/dashboard/insights");
  renderInsights(data);
}

function renderInsights(data) {
  const target = document.getElementById("insights-content");
  if (!data.categories || data.categories.length === 0) {
    target.innerHTML = `<p class="empty">No spending data yet.</p>`;
    return;
  }

  let html = `
    <div class="insights-panel">
      <h4>Yearly Totals</h4>
      <div class="insights-totals">
  `;

  for (const [year, yearData] of Object.entries(data.yearly_totals || {})) {
    html += `<div class="insight-metric"><span>${year}</span><strong>${yearData.total}</strong></div>`;
  }

  html += `</div></div>`;

  html += `
    <div class="insights-panel">
      <h4>Spending by Category</h4>
      <div class="table-wrap">
        <table class="insights-table">
          <thead>
            <tr>
              <th>Category</th>
  `;

  const years = Object.keys(data.yearly_totals || {}).sort();
  for (const year of years) {
    html += `<th>${year}</th>`;
  }
  html += `</tr></thead><tbody>`;

  for (const cat of data.categories) {
    html += `<tr><td>${escapeHtml(cat.category)}</td>`;
    for (const year of years) {
      const amount = cat.years[year] || "€0.00";
      html += `<td>${amount}</td>`;
    }
    html += `</tr>`;
  }

  html += `</tbody></table></div></div>`;

  if (data.key_takeaways && data.key_takeaways.length > 0) {
    html += `<div class="insights-panel"><h4>Key Takeaways</h4>`;
    for (const takeaway of data.key_takeaways) {
      html += `<div class="insight-takeaway"><strong>${escapeHtml(takeaway.title)}</strong>`;
      if (takeaway.items && takeaway.items.length > 0) {
        html += `<ul>`;
        for (const item of takeaway.items) {
          if (item.category) {
            html += `<li><strong>${escapeHtml(item.category)}</strong>: ${item.amount_2023 || "€0"} → ${item.amount_2024 || "€0"} <span class="change">${item.change}</span> (${item.delta})</li>`;
          }
        }
        html += `</ul>`;
      }
      html += `</div>`;
    }
    html += `</div>`;
  }

  target.innerHTML = html;
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

function bucketLabel(row) {
  return `${row.category || "Uncategorized"}${row.subcategory ? ` / ${row.subcategory}` : ""}`;
}

function renderBucketYearTotals(rows) {
  renderTable(
    "bucket-years-table",
    [
      { key: "year", label: "Year" },
      { key: "bucket", label: "Bucket", render: bucketLabel },
      { key: "outflow", label: "Out", number: true, render: (row) => fmtPrecise(row.outflow) },
      { key: "count", label: "Rows", number: true },
      { key: "avg_confidence", label: "Conf.", number: true, render: (row) => fmtPercent(row.avg_confidence) },
    ],
    rows,
  );
}

function renderBucketMonthTotals(rows) {
  const target = document.getElementById("bucket-months");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">No bucket totals yet.</p>`;
    return;
  }
  const months = [...new Set(rows.map((row) => row.month))].sort();
  const buckets = [...new Set(rows.map(bucketLabel))].sort();
  const lookup = new Map(rows.map((row) => [`${bucketLabel(row)}|${row.month}`, Number(row.outflow || 0)]));
  target.innerHTML = `
    <div class="matrix-row bucket-header" style="--month-count:${months.length}"><span>Bucket</span>${months.map((month) => `<span>${escapeHtml(month)}</span>`).join("")}<span>Total</span></div>
    ${buckets
      .map((bucket) => {
        const total = months.reduce((sum, month) => sum + (lookup.get(`${bucket}|${month}`) || 0), 0);
        return `
          <div class="matrix-row bucket-row" style="--month-count:${months.length}">
            <strong>${escapeHtml(bucket)}</strong>
            ${months.map((month) => `<span>${lookup.get(`${bucket}|${month}`) ? fmtMoney(lookup.get(`${bucket}|${month}`)) : ""}</span>`).join("")}
            <strong>${fmtMoney(total)}</strong>
          </div>
        `;
      })
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
      { key: "digest_tier", label: "Tier" },
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
      (item) => {
        const groupCount = Number(item.suggested_action?.group_count || 1);
        const groupText =
          groupCount > 1
            ? ` · ${groupCount} similar · ${fmtPrecise(item.suggested_action.group_materiality)} total`
            : "";
        return `
        <article class="review-item">
          <div>
            <strong>${escapeHtml(item.normalized_merchant || item.description || "Transaction")}</strong>
            <span>${escapeHtml(item.transaction_date)} · ${fmtPrecise(item.amount)}${escapeHtml(groupText)} · ${escapeHtml(item.reason || "Needs classification")}</span>
            <small>${escapeHtml(item.account_name || "Account")} · ${escapeHtml(item.account_role || "unknown")} · Current: ${escapeHtml(item.economic_class || "unknown")} / ${escapeHtml(item.category || "Uncategorized")}</small>
          </div>
          <div class="review-actions">
            <button data-review-details="${item.id}">Details</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Groceries">Groceries</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Eating Out">Eating out</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Holiday">Holiday</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Shopping">Shopping</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="household_spend" data-category="Other">Other</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="debt_service" data-category="Housing" data-subcategory="Mortgage">Mortgage</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="internal_transfer" data-category="Inter-account Transfers">Inter-acct</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="wealth_allocation" data-category="Investments">Investment</button>
            <button data-review="${item.id}" data-transaction="${item.transaction_id}" data-class="reimbursement_pass_through" data-category="Reimbursements" data-subcategory="Company Expense">Reimb.</button>
          </div>
          <div id="review-details-${item.id}" class="review-details" hidden></div>
        </article>
      `;
      },
    )
    .join("");
}

async function toggleReviewDetails(button) {
  const panel = document.getElementById(`review-details-${button.dataset.reviewDetails}`);
  if (!panel) return;
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  if (panel.dataset.loaded === "true") {
    panel.hidden = false;
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Loading...";
  try {
    const data = await api(`/api/review-items/${button.dataset.reviewDetails}/transactions`);
    panel.innerHTML = renderReviewDetails(data.transactions || []);
    panel.dataset.loaded = "true";
    panel.hidden = false;
  } catch (error) {
    panel.innerHTML = `<p class="inline-status">${escapeHtml(error.message)}</p>`;
    panel.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function renderReviewDetails(rows) {
  if (!rows.length) return `<p class="empty">No grouped transactions found.</p>`;
  const body = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.transaction_date)}</td>
          <td class="numeric">${fmtPrecise(row.amount)}</td>
          <td>${escapeHtml(row.from_account || row.account_name || "")}</td>
          <td>${escapeHtml(row.to_account || row.counterparty_name || "")}</td>
          <td>${escapeHtml(row.description || row.normalized_merchant || "")}</td>
          <td>${escapeHtml(`${row.economic_class || ""} / ${row.category || ""}${row.subcategory ? ` / ${row.subcategory}` : ""}`)}</td>
        </tr>
      `,
    )
    .join("");
  return `
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Amount</th>
          <th>From</th>
          <th>To</th>
          <th>Description</th>
          <th>Current</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

async function resolveReview(button) {
  const result = document.getElementById("review-result");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Saving...";
  if (result) result.textContent = "Saving review decision...";
  try {
    await api(`/api/review-items/${button.dataset.review}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transaction_id: Number(button.dataset.transaction),
        economic_class: button.dataset.class,
        category: button.dataset.category,
        subcategory: button.dataset.subcategory || "",
        create_rule: true,
      }),
    });
    if (result) result.textContent = "Review decision saved.";
    await refreshAll();
  } catch (error) {
    if (result) result.textContent = error.message;
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function setAmortizationStatus(button) {
  await api(`/api/amortization-rules/${button.dataset.amortization}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: button.dataset.status }),
  });
  await refreshAll();
}

async function runEntityEnrichment() {
  const result = document.getElementById("entity-enrichment-result");
  const button = document.getElementById("entity-enrichment-button");
  button.disabled = true;
  result.textContent = "Looking up...";
  try {
    const data = await api("/api/entity-enrichment/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 10 }),
    });
    const summary = data.enrichment || {};
    result.textContent = `${summary.resolved || 0} resolved · ${summary.unresolved || 0} unresolved · ${summary.error || 0} errors`;
    await refreshAll();
  } catch (error) {
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
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
  const [imports, accounts, rules, audit] = await Promise.all([
    api("/api/imports"),
    api("/api/accounts"),
    api("/api/rules"),
    api("/api/rule-audit"),
  ]);
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
  renderRuleAudit(audit.rules || []);
}

function shortJson(value) {
  const text = JSON.stringify(value || {});
  return text.length > 140 ? `${text.slice(0, 137)}...` : text;
}

function renderRuleAudit(rows) {
  renderTable(
    "rule-audit-table",
    [
      { key: "id", label: "ID", number: true },
      { key: "name", label: "Rule" },
      { key: "created_by", label: "By" },
      { key: "matched_count", label: "Matches", number: true },
      { key: "matched_value", label: "Value", number: true, render: (row) => fmtPrecise(row.matched_value) },
      { key: "enabled", label: "On", render: (row) => (row.enabled ? "yes" : "no") },
      { key: "conditions", label: "Scope", render: (row) => shortJson(row.conditions) },
      { key: "actions", label: "Action", render: (row) => shortJson(row.actions) },
    ],
    rows,
  );
}

function renderAccounts(rows) {
  renderTable(
    "accounts-table",
    [
      { key: "display_name", label: "Account" },
      { key: "institution", label: "Institution" },
      { key: "owner", label: "Owner" },
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
  await loadMetadata();
  await loadFire();
  await Promise.all([loadOptimization(), loadFlow(), loadSpending(), loadBuckets(), loadInsights(), loadReview(), loadImports()]);
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
  const target = event.target;
  if (!(target instanceof Element)) return;
  const reviewDetailsButton = target.closest("[data-review-details]");
  const reviewButton = target.closest("[data-review]");
  const amortizationButton = target.closest("[data-amortization]");
  const monthButton = target.closest("[data-month]");
  const monthIncomeButton = target.closest("[data-month-income]");
  const sortButton = target.closest(".sort-button");
  const incomeSortButton = target.closest(".sort-income-button");
  const categoryFilterButton = target.closest(".category-filter-btn");
  const auditActionButton = target.closest("[data-audit-action]");
  if (reviewDetailsButton) {
    event.preventDefault();
    toggleReviewDetails(reviewDetailsButton);
  }
  if (reviewButton) {
    event.preventDefault();
    resolveReview(reviewButton);
  }
  if (amortizationButton) {
    event.preventDefault();
    setAmortizationStatus(amortizationButton);
  }
  if (monthButton) {
    event.preventDefault();
    renderMonthAudit(monthButton.dataset.month);
  }
  if (monthIncomeButton) {
    event.preventDefault();
    renderMonthIncome(monthIncomeButton.dataset.monthIncome);
  }
  if (sortButton) {
    event.preventDefault();
    handleSortClick(event);
  }
  if (incomeSortButton) {
    event.preventDefault();
    handleIncomeSortClick(event);
  }
  if (categoryFilterButton && !target.closest("#month-income-panel")) {
    event.preventDefault();
    handleCategoryFilterClick(categoryFilterButton);
  }
  if (categoryFilterButton && target.closest("#month-income-panel")) {
    event.preventDefault();
    handleIncomeCategoryFilterClick(categoryFilterButton);
  }
  if (auditActionButton) {
    event.preventDefault();
    handleAuditAction(auditActionButton);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches(".role-select")) {
    updateAccountRole(event.target);
  }
});

document.getElementById("refresh-button").addEventListener("click", refreshAll);
document.getElementById("entity-enrichment-button").addEventListener("click", runEntityEnrichment);
document.getElementById("fire-multiple").addEventListener("change", loadFire);
document.getElementById("period-selector").addEventListener("change", refreshAll);
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
