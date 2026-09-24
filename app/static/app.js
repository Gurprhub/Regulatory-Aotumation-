/**
 * Dashboard for the regulatory compliance registers.
 *
 * Field and column definitions live in REGISTERS so each register's table,
 * filters and create/edit form are generated from one description rather than
 * hand-written five times over.
 */

const STATE_LABELS = {
  valid: "Valid",
  expiring_soon: "Expiring soon",
  critical: "Critical",
  expired: "Expired",
  non_compliant: "Non-compliant",
  not_yet_effective: "Not yet effective",
};

const TILE_ORDER = [
  "critical",
  "expired",
  "non_compliant",
  "expiring_soon",
  "valid",
  "not_yet_effective",
];

/** Populated from /api/reference on load. */
let reference = null;
/** The signed-in account, from /api/auth/me. */
let currentUser = null;
/** Product list, cached for the product pickers. */
let productCache = [];

// --------------------------------------------------------------------------- //
// HTTP helpers
// --------------------------------------------------------------------------- //
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  // The session has gone (expired, revoked, or never existed): start again.
  if (response.status === 401) {
    redirectToSignIn();
    throw new Error("Your session has ended. Please sign in again.");
  }
  if (response.status === 204) return null;
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(formatApiError(body, response.status));
  }
  return body;
}

/** Turn a FastAPI error body into something a compliance officer can act on. */
function formatApiError(body, status) {
  if (!body) return `Request failed (HTTP ${status}).`;
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = (item.loc || []).filter((part) => part !== "body").join(".");
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join("\n");
  }
  return `Request failed (HTTP ${status}).`;
}

function redirectToSignIn() {
  const next = encodeURIComponent(location.pathname + location.search);
  location.replace(`/login?next=${next}`);
}

/** True when the signed-in account may change records. */
function canEdit() {
  return currentUser !== null && (currentUser.role === "editor" || currentUser.role === "admin");
}

function showBanner(message) {
  const banner = document.getElementById("banner");
  banner.textContent = message;
  banner.hidden = !message;
}

// --------------------------------------------------------------------------- //
// Rendering helpers
// --------------------------------------------------------------------------- //
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function stateBadge(state) {
  return el("span", { class: `badge ${state}` }, STATE_LABELS[state] || state);
}

function humanise(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value).replace(/_/g, " ");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function formatCountdown(days) {
  if (days === null || days === undefined) return "No expiry";
  if (days < 0) return `${Math.abs(days)} d overdue`;
  if (days === 0) return "Expires today";
  return `${days} d`;
}

function renderTable(container, columns, rows, emptyMessage) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(el("p", { class: "empty" }, emptyMessage));
    return;
  }
  const head = el(
    "thead",
    {},
    el(
      "tr",
      {},
      columns.map((column) => el("th", {}, column.header)),
    ),
  );
  const body = el(
    "tbody",
    {},
    rows.map((row) =>
      el(
        "tr",
        {},
        columns.map((column) => {
          const cell = el("td", { class: column.class || "" });
          const content = column.render(row);
          cell.append(content instanceof Node ? content : document.createTextNode(String(content)));
          return cell;
        }),
      ),
    ),
  );
  container.append(el("table", {}, [head, body]));
}

function optionList(values, { includeBlank = null } = {}) {
  const options = [];
  if (includeBlank !== null) options.push(el("option", { value: "" }, includeBlank));
  for (const value of values) {
    const [key, label] =
      typeof value === "object" ? [value.value, value.label] : [value, humanise(value)];
    options.push(el("option", { value: key }, label));
  }
  return options;
}

// --------------------------------------------------------------------------- //
// Field definitions
// --------------------------------------------------------------------------- //
const VALIDITY_FIELDS = () => [
  { name: "issue_date", label: "Issue date", type: "date" },
  { name: "valid_from", label: "Valid from", type: "date" },
  {
    name: "valid_until",
    label: "Valid until",
    type: "date",
    help: "Leave empty if it never expires.",
  },
  { name: "status", label: "Status", type: "select", options: () => reference.compliance_statuses },
  { name: "document_url", label: "Document URL", type: "url", wide: true },
  { name: "notes", label: "Notes", type: "textarea", wide: true },
];

const PRODUCT_FIELD = (required = true) => ({
  name: "product_id",
  label: "Product",
  type: "select",
  required,
  options: () => productCache.map((p) => ({ value: p.id, label: productLabel(p) })),
});

const REGISTRATION_FIELD = () => ({
  name: "registration_id",
  label: "Linked registration",
  type: "select",
  optional: true,
  options: async () => {
    const registrations = await api("/api/registrations?limit=500");
    return registrations.map((r) => ({
      value: r.id,
      label: `${r.registration_number} (${r.product_name || "—"})`,
    }));
  },
});

function productLabel(product) {
  return product.brand_name ? `${product.name} — ${product.brand_name}` : product.name;
}

function complianceColumns() {
  return [
    {
      header: "Valid until",
      class: "numeric",
      render: (row) => formatDate(row.valid_until),
    },
    {
      header: "Countdown",
      class: "numeric",
      render: (row) => formatCountdown(row.days_remaining),
    },
    { header: "Status", render: (row) => humanise(row.status) },
    { header: "Compliance", render: (row) => stateBadge(row.compliance_state) },
  ];
}

const REGISTERS = {
  products: {
    title: "Products",
    endpoint: "/api/products",
    singular: "product",
    columns: () => [
      { header: "Name", render: (row) => row.name },
      { header: "Brand", render: (row) => humanise(row.brand_name) },
      { header: "Active ingredient", render: (row) => row.active_ingredient },
      { header: "Conc.", render: (row) => humanise(row.concentration) },
      { header: "Category", render: (row) => humanise(row.category) },
      { header: "Form", render: (row) => row.formulation_type },
      { header: "CAS", class: "reference", render: (row) => humanise(row.cas_number) },
    ],
    filters: () => [
      { name: "q", label: "Search", type: "text", placeholder: "Name or ingredient" },
      { name: "category", label: "Category", type: "select", options: () => reference.product_categories },
      {
        name: "formulation_type",
        label: "Formulation",
        type: "select",
        options: () => reference.formulation_types,
      },
    ],
    fields: () => [
      { name: "name", label: "Name", type: "text", required: true, wide: true },
      { name: "brand_name", label: "Brand name", type: "text" },
      { name: "active_ingredient", label: "Active ingredient", type: "text", required: true },
      { name: "concentration", label: "Concentration", type: "text", placeholder: "17.8% w/w" },
      { name: "cas_number", label: "CAS number", type: "text" },
      {
        name: "category",
        label: "Category",
        type: "select",
        required: true,
        options: () => reference.product_categories,
      },
      {
        name: "formulation_type",
        label: "Formulation",
        type: "select",
        required: true,
        options: () => reference.formulation_types,
      },
      { name: "notes", label: "Notes", type: "textarea", wide: true },
    ],
  },

  registrations: {
    title: "Registrations",
    endpoint: "/api/registrations",
    singular: "registration",
    columns: () => [
      { header: "Registration no.", class: "reference", render: (row) => row.registration_number },
      { header: "Product", render: (row) => humanise(row.product_name) },
      { header: "Section", render: (row) => row.section },
      { header: "Purpose", render: (row) => humanise(row.purpose) },
      { header: "Registrant", render: (row) => row.registrant_name },
      ...complianceColumns(),
    ],
    filters: () => [
      { name: "section", label: "Section", type: "select", options: () => reference.registration_sections },
      { name: "status", label: "Status", type: "select", options: () => reference.compliance_statuses },
      {
        name: "compliance_state",
        label: "Compliance",
        type: "select",
        options: () => reference.compliance_states,
      },
    ],
    fields: () => [
      { name: "registration_number", label: "Registration number", type: "text", required: true, wide: true },
      PRODUCT_FIELD(),
      {
        name: "section",
        label: "Section",
        type: "select",
        required: true,
        options: () => reference.registration_sections,
      },
      {
        name: "purpose",
        label: "Purpose",
        type: "select",
        options: () => reference.registration_purposes,
      },
      { name: "registrant_name", label: "Registrant", type: "text", required: true },
      { name: "issuing_authority", label: "Issuing authority", type: "text" },
      ...VALIDITY_FIELDS(),
    ],
  },

  "sale-permissions": {
    title: "State sale permissions",
    endpoint: "/api/sale-permissions",
    singular: "sale permission",
    columns: () => [
      { header: "Permission no.", class: "reference", render: (row) => row.permission_number },
      { header: "State", render: (row) => row.state },
      { header: "Product", render: (row) => humanise(row.product_name) },
      { header: "Authority", render: (row) => humanise(row.licensing_authority) },
      ...complianceColumns(),
    ],
    filters: () => [
      { name: "state", label: "State", type: "select", options: () => reference.states },
      { name: "status", label: "Status", type: "select", options: () => reference.compliance_statuses },
      {
        name: "compliance_state",
        label: "Compliance",
        type: "select",
        options: () => reference.compliance_states,
      },
    ],
    fields: () => [
      { name: "permission_number", label: "Permission number", type: "text", required: true },
      { name: "state", label: "State", type: "select", required: true, options: () => reference.states },
      PRODUCT_FIELD(),
      REGISTRATION_FIELD(),
      { name: "licensing_authority", label: "Licensing authority", type: "text", wide: true },
      ...VALIDITY_FIELDS(),
    ],
  },

  licences: {
    title: "Licences",
    endpoint: "/api/licences",
    singular: "licence",
    columns: () => [
      { header: "Licence no.", class: "reference", render: (row) => row.licence_number },
      { header: "Type", render: (row) => humanise(row.licence_type) },
      { header: "Holder", render: (row) => row.holder_name },
      { header: "Site", render: (row) => humanise(row.site_name) },
      { header: "State", render: (row) => row.state },
      {
        header: "Products",
        class: "numeric",
        render: (row) => (row.product_ids.length ? String(row.product_ids.length) : "—"),
      },
      ...complianceColumns(),
    ],
    filters: () => [
      { name: "state", label: "State", type: "select", options: () => reference.states },
      { name: "licence_type", label: "Type", type: "select", options: () => reference.licence_types },
      {
        name: "compliance_state",
        label: "Compliance",
        type: "select",
        options: () => reference.compliance_states,
      },
    ],
    fields: () => [
      { name: "licence_number", label: "Licence number", type: "text", required: true },
      {
        name: "licence_type",
        label: "Licence type",
        type: "select",
        required: true,
        options: () => reference.licence_types,
      },
      { name: "holder_name", label: "Holder", type: "text", required: true },
      { name: "site_name", label: "Site", type: "text" },
      { name: "state", label: "State", type: "select", required: true, options: () => reference.states },
      { name: "issuing_authority", label: "Issuing authority", type: "text" },
      { name: "site_address", label: "Site address", type: "textarea", wide: true },
      {
        name: "product_ids",
        label: "Products covered",
        type: "multiselect",
        wide: true,
        help: "Ctrl/Cmd-click to select several.",
        options: () => productCache.map((p) => ({ value: p.id, label: productLabel(p) })),
      },
      ...VALIDITY_FIELDS(),
    ],
  },

  "label-approvals": {
    title: "Label approvals",
    endpoint: "/api/label-approvals",
    singular: "label approval",
    columns: () => [
      { header: "Approval no.", class: "reference", render: (row) => row.approval_number },
      { header: "Label ver.", render: (row) => row.label_version },
      { header: "Leaflet ver.", render: (row) => humanise(row.leaflet_version) },
      { header: "Product", render: (row) => humanise(row.product_name) },
      { header: "Languages", render: (row) => humanise(row.languages) },
      ...complianceColumns(),
    ],
    filters: () => [
      { name: "status", label: "Status", type: "select", options: () => reference.compliance_statuses },
      {
        name: "compliance_state",
        label: "Compliance",
        type: "select",
        options: () => reference.compliance_states,
      },
    ],
    fields: () => [
      { name: "approval_number", label: "Approval number", type: "text", required: true },
      { name: "label_version", label: "Label version", type: "text", placeholder: "1.0" },
      { name: "leaflet_version", label: "Leaflet version", type: "text" },
      PRODUCT_FIELD(),
      REGISTRATION_FIELD(),
      { name: "approving_authority", label: "Approving authority", type: "text" },
      { name: "languages", label: "Languages", type: "text", placeholder: "English, Hindi" },
      ...VALIDITY_FIELDS(),
    ],
  },
};

// --------------------------------------------------------------------------- //
// Dashboard view
// --------------------------------------------------------------------------- //
async function renderDashboard() {
  const summary = await api("/api/dashboard?upcoming_limit=100");

  const tiles = document.getElementById("tiles");
  tiles.replaceChildren(
    ...TILE_ORDER.map((state) =>
      el("div", { class: `tile ${state}` }, [
        el("div", { class: "value" }, summary.totals[state] ?? 0),
        el("div", { class: "label" }, STATE_LABELS[state]),
      ]),
    ),
  );

  renderTable(
    document.getElementById("register-breakdown"),
    [
      { header: "Register", render: (row) => row.register_label },
      { header: "Total", class: "numeric", render: (row) => row.total },
      { header: "Needs action", class: "numeric", render: (row) => row.actionable },
      ...["critical", "expired", "non_compliant", "expiring_soon", "valid"].map((state) => ({
        header: STATE_LABELS[state],
        class: "numeric",
        render: (row) => row.by_state[state] ?? 0,
      })),
    ],
    summary.registers,
    "Nothing recorded yet.",
  );

  await renderQueue();
}

async function renderQueue() {
  const horizon = document.getElementById("queue-horizon").value;
  const register = document.getElementById("queue-register").value;
  const state = document.getElementById("queue-state").value;

  const params = new URLSearchParams({ within_days: horizon });
  if (register) params.set("register", register);
  if (state) params.set("state", state);

  document.getElementById("queue-csv").href = `/api/alerts.csv?${params}`;

  const items = await api(`/api/alerts?${params}`);
  renderTable(
    document.getElementById("queue-table"),
    [
      { header: "Priority", render: (row) => stateBadge(row.compliance_state) },
      { header: "Register", render: (row) => row.register_label },
      { header: "Reference", class: "reference", render: (row) => row.reference_number },
      { header: "Item", render: (row) => row.title },
      { header: "State", render: (row) => humanise(row.state_name) },
      { header: "Valid until", class: "numeric", render: (row) => formatDate(row.valid_until) },
      { header: "Countdown", class: "numeric", render: (row) => formatCountdown(row.days_remaining) },
      { header: "Status", render: (row) => humanise(row.status) },
    ],
    items,
    "Nothing needs attention within this horizon.",
  );
}

// --------------------------------------------------------------------------- //
// Register views
// --------------------------------------------------------------------------- //
const filterState = {};

async function renderRegister(key) {
  const config = REGISTERS[key];
  document.getElementById("register-title").textContent = config.title;

  const controls = document.getElementById("register-controls");
  if (controls.dataset.register !== key) {
    controls.dataset.register = key;
    controls.replaceChildren();
    for (const filter of config.filters()) {
      const id = `filter-${key}-${filter.name}`;
      let input;
      if (filter.type === "select") {
        input = el("select", { id }, optionList(filter.options(), { includeBlank: "All" }));
      } else {
        input = el("input", { type: "text", id, placeholder: filter.placeholder || "" });
      }
      input.value = filterState[`${key}:${filter.name}`] || "";
      input.addEventListener("change", () => {
        filterState[`${key}:${filter.name}`] = input.value;
        loadRegisterRows(key).catch((error) => showBanner(error.message));
      });
      if (filter.type !== "select") {
        input.addEventListener("keyup", (event) => {
          if (event.key === "Enter") input.dispatchEvent(new Event("change"));
        });
      }
      controls.append(el("label", {}, [filter.label, input]));
    }
    if (canEdit()) {
      controls.append(
        el(
          "button",
          {
            class: "button primary",
            onclick: () => openDialog(key, null).catch((error) => showBanner(error.message)),
          },
          `Add ${config.singular}`,
        ),
      );
    }
  }

  await loadRegisterRows(key);
}

async function loadRegisterRows(key) {
  const config = REGISTERS[key];
  const params = new URLSearchParams({ limit: "500" });
  for (const filter of config.filters()) {
    const value = filterState[`${key}:${filter.name}`];
    if (value) params.set(filter.name, value);
  }

  const rows = await api(`${config.endpoint}?${params}`);
  document.getElementById("register-count").textContent =
    `${rows.length} record${rows.length === 1 ? "" : "s"}`;

  const columns = [...config.columns()];
  // A viewer gets a read-only table rather than buttons that would 403.
  if (canEdit()) {
    columns.push({
      header: "",
      render: (row) =>
        el("div", {}, [
          el(
            "button",
            {
              class: "button link",
              onclick: () => openDialog(key, row).catch((error) => showBanner(error.message)),
            },
            "Edit",
          ),
          el(
            "button",
            {
              class: "button link danger",
              onclick: () => deleteRecord(key, row).catch((error) => showBanner(error.message)),
            },
            "Delete",
          ),
        ]),
    });
  }
  renderTable(document.getElementById("register-table"), columns, rows, "No records yet.");

  if (key === "products") productCache = rows;
}

async function deleteRecord(key, row) {
  const config = REGISTERS[key];
  const extra =
    key === "products"
      ? "\n\nIts registrations, sale permissions and label approvals will go with it."
      : "";
  if (!window.confirm(`Delete this ${config.singular}?${extra}`)) return;
  await api(`${config.endpoint}/${row.id}`, { method: "DELETE" });
  showBanner("");
  await loadRegisterRows(key);
  if (key === "products") await refreshProducts();
}

// --------------------------------------------------------------------------- //
// Create / edit dialog
// --------------------------------------------------------------------------- //
const dialog = document.getElementById("record-dialog");
let dialogContext = null;

async function openDialog(key, row) {
  const config = REGISTERS[key];
  dialogContext = { key, row };

  document.getElementById("dialog-title").textContent = row
    ? `Edit ${config.singular}`
    : `New ${config.singular}`;
  const errorBox = document.getElementById("dialog-error");
  errorBox.hidden = true;
  errorBox.textContent = "";

  const grid = document.getElementById("dialog-fields");
  grid.replaceChildren();

  for (const field of config.fields()) {
    const id = `field-${field.name}`;
    let input;

    if (field.type === "select" || field.type === "multiselect") {
      const options = await Promise.resolve(field.options());
      const multiple = field.type === "multiselect";
      input = el(
        "select",
        multiple ? { id, multiple: "multiple", size: "5" } : { id },
        optionList(options, { includeBlank: multiple ? null : field.required ? "Select…" : "—" }),
      );
    } else if (field.type === "textarea") {
      input = el("textarea", { id, rows: "2" });
    } else {
      input = el("input", { type: field.type, id, placeholder: field.placeholder || "" });
    }

    if (row) {
      const value = row[field.name];
      if (field.type === "multiselect") {
        const selected = new Set((value || []).map(String));
        for (const option of input.options) option.selected = selected.has(option.value);
      } else if (value !== null && value !== undefined) {
        input.value = value;
      }
    }

    grid.append(
      el("div", { class: `field${field.wide ? " wide" : ""}` }, [
        el("label", { for: id }, [
          field.label,
          field.required ? el("span", { class: "required" }, " *") : null,
        ]),
        input,
        field.help ? el("span", { class: "help" }, field.help) : null,
      ]),
    );
  }

  dialog.showModal();
}

function collectPayload(key) {
  const payload = {};
  for (const field of REGISTERS[key].fields()) {
    const input = document.getElementById(`field-${field.name}`);
    if (!input) continue;

    if (field.type === "multiselect") {
      payload[field.name] = Array.from(input.selectedOptions).map((option) =>
        Number(option.value),
      );
      continue;
    }

    const raw = input.value.trim();
    if (raw === "") {
      // Send an explicit null when editing, so a value can be cleared.
      if (dialogContext.row && !field.required) payload[field.name] = null;
      continue;
    }
    payload[field.name] = field.name.endsWith("_id") ? Number(raw) : raw;
  }
  return payload;
}

document.getElementById("record-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const { key, row } = dialogContext;
  const config = REGISTERS[key];
  const errorBox = document.getElementById("dialog-error");
  const saveButton = document.getElementById("dialog-save");

  saveButton.disabled = true;
  try {
    const payload = collectPayload(key);
    await api(row ? `${config.endpoint}/${row.id}` : config.endpoint, {
      method: row ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    dialog.close();
    showBanner("");
    await loadRegisterRows(key);
    if (key === "products") await refreshProducts();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
});

document.getElementById("dialog-cancel").addEventListener("click", () => dialog.close());

// --------------------------------------------------------------------------- //
// Navigation and bootstrap
// --------------------------------------------------------------------------- //
async function refreshProducts() {
  productCache = await api("/api/products?limit=500");
}

function showView(view) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.view === view);
  }
  const isDashboard = view === "dashboard";
  document.getElementById("view-dashboard").hidden = !isDashboard;
  document.getElementById("view-register").hidden = isDashboard;

  const work = isDashboard ? renderDashboard() : renderRegister(view);
  work.catch((error) => showBanner(error.message));
}

function renderUserChip() {
  const holder = document.getElementById("user-chip");
  holder.replaceChildren(
    el("span", {}, currentUser.full_name),
    el("span", { class: "role" }, currentUser.role),
    el(
      "button",
      {
        class: "button ghost",
        onclick: async () => {
          try {
            await api("/api/auth/logout", { method: "POST" });
          } finally {
            location.replace("/login");
          }
        },
      },
      "Sign out",
    ),
  );
}

async function bootstrap() {
  try {
    currentUser = await api("/api/auth/me");
  } catch {
    // api() has already redirected to the sign-in page on a 401.
    return;
  }
  renderUserChip();

  try {
    reference = await api("/api/reference");
  } catch (error) {
    showBanner(`Could not reach the API: ${error.message}`);
    return;
  }

  document.getElementById("thresholds").textContent =
    `Critical ≤ ${reference.thresholds.critical_days} d · warning ≤ ${reference.thresholds.warning_days} d`;

  const registerSelect = document.getElementById("queue-register");
  registerSelect.append(
    ...optionList(reference.registers.map((r) => ({ value: r.key, label: r.label }))),
  );
  document.getElementById("queue-state").append(...optionList(reference.states));

  for (const control of ["queue-horizon", "queue-register", "queue-state"]) {
    document
      .getElementById(control)
      .addEventListener("change", () => renderQueue().catch((error) => showBanner(error.message)));
  }

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }

  await refreshProducts();
  showView("dashboard");
}

bootstrap();
