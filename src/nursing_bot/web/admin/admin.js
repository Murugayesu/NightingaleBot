/*
 * admin.js: logic for the NursingBot admin panel (index.html, served at /ui/admin/).
 *
 * Flow:
 *   - Login: the admin API key is checked with GET /admin/health and kept in
 *     sessionStorage (cleared when the tab closes). Every request sends it as
 *     the X-Admin-API-Key header; any 401 sends the user back to the login form.
 *   - Dashboard tabs: Overview (health + stats), Documents (list, chunks,
 *     re-ingest, delete), Upload, Maintenance (re-index) and Evaluation.
 *
 * Safety: document names, chunk text and error messages come from uploads or
 * the server, so they are only ever written with textContent / text nodes,
 * never innerHTML. The page CSP forbids inline styles, so the UI changes by
 * toggling CSS classes and the `hidden` attribute.
 */

const API_BASE = "/api/v1";
const KEY_STORAGE = "nursingbot.adminKey";
const PAGE_SIZE = 20;
const CHUNK_PAGE_SIZE = 200;
const POLL_MS = 5000;
const TOAST_MS = 4000;
const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".tsv", ".xlsx", ".json"];
const TABS = ["overview", "documents", "upload", "maintenance", "evaluation"];

// Colour class for each status word used by documents, eval runs and health.
const STATUS_BADGES = new Map([
  ["ok", "badge--ok"],
  ["completed", "badge--ok"],
  ["degraded", "badge--warn"],
  ["processing", "badge--warn"],
  ["running", "badge--warn"],
  ["pending", "badge--info"],
  ["queued", "badge--info"],
  ["failed", "badge--bad"],
  ["disabled", ""],
]);

// Health fields that are component statuses (shown as coloured badges).
const HEALTH_STATUS_KEYS = new Set(["status", "database", "storage", "queue"]);
const HEALTH_LABELS = {
  status: "Overall",
  environment: "Environment",
  database: "Database",
  storage: "Storage",
  queue: "Queue",
  llm_model: "LLM model",
  embedding_model: "Embedding model",
  stale_embedding_chunks: "Stale embedding chunks",
};
const STATS_LABELS = {
  total_documents: "Documents",
  total_chunks: "Chunks",
  total_sessions: "Chat sessions",
  total_messages: "Messages",
  active_embedding_model: "Active embedding model",
};

const $ = (id) => document.getElementById(id);

/** An HTTP failure with a user-readable message (status 0 = network error). */
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let apiKey = readStoredKey();
let pollTimer = null; // one auto-refresh timer, owned by the visible tab
let toastTimer = null;
let docsRequestId = 0; // ignore responses from superseded requests
let evalRequestId = 0;

const state = {
  activeTab: "overview",
  docs: { offset: 0, total: 0, docType: "", status: "", byId: new Map() },
  chunks: { docId: null, offset: 0 },
  openRun: { id: null, status: null },
};

/* ------------------------------------------------------------------ */
/* Generic helpers                                                     */
/* ------------------------------------------------------------------ */

/** Create an element with an optional class and plain-text content. */
function createEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** A small button carrying data-action / data-id for delegated click handling. */
function actionButton(label, action, id, ariaLabel, extraClass = "btn--secondary") {
  const button = createEl("button", `btn btn--small ${extraClass}`, label);
  button.type = "button";
  button.dataset.action = action;
  button.dataset.id = id;
  button.setAttribute("aria-label", ariaLabel);
  return button;
}

/** Format a server timestamp in the user's local time. Offset-less values are treated as UTC. */
function formatDate(value) {
  if (typeof value !== "string" || value === "") return "—";
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const time = Date.parse(hasZone ? value : `${value}Z`);
  return Number.isNaN(time) ? value : new Date(time).toLocaleString();
}

function formatNumber(value) {
  return typeof value === "number" ? value.toLocaleString() : String(value ?? "—");
}

/** Render one metric value compactly: integers as-is, decimals to 3 places. */
function formatMetric(value) {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** A coloured status badge such as "completed" or "failed". */
function statusBadge(status) {
  const text = String(status ?? "unknown");
  const modifier = STATUS_BADGES.get(text.toLowerCase()) ?? "badge--bad";
  return createEl("span", `badge ${modifier}`.trim(), text);
}

/** Convert FastAPI's `detail` (string, or list for 422) into readable text. */
function detailToText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const where = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        return where ? `${where}: ${item.msg}` : String(item.msg);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "";
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function clearError(element) {
  element.textContent = "";
  element.hidden = true;
}

/** Show an API error next to a section. 401s are skipped: the login screen already explains. */
function reportError(element, error) {
  if (error.status === 401) return;
  showError(element, error.message);
}

/** Show a result box (`kind` = "ok" | "warn" | "error"). */
function showResult(element, message, kind) {
  element.replaceChildren(message);
  element.className = `result is-${kind}`;
  element.hidden = false;
}

/** Brief pop-up message in the corner, announced to screen readers. */
function showToast(message, isError = false) {
  // The toast element stays in the page (it is invisible while empty) so
  // screen readers reliably announce each new message.
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.textContent = "";
  }, TOAST_MS);
}

/**
 * Ask the user to confirm using the <dialog> in index.html.
 * Resolves true only if they press the confirm button (Esc or Cancel = false).
 */
function confirmAction(message, confirmLabel) {
  const dialog = $("confirm-dialog");
  $("confirm-message").textContent = message;
  $("confirm-ok").textContent = confirmLabel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

/* ------------------------------------------------------------------ */
/* API access                                                          */
/* ------------------------------------------------------------------ */

function readStoredKey() {
  try {
    return sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

function storeKey(key) {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    // Storage blocked: the key lives in memory until the page is reloaded.
  }
}

/** Read an error response's `detail` as text. */
async function readDetail(response) {
  try {
    const text = detailToText((await response.json()).detail);
    if (text) return text;
  } catch {
    // Not JSON.
  }
  return `Request failed (HTTP ${response.status}).`;
}

/**
 * Call the admin API. Throws ApiError on failure.
 *   json:          object to send as a JSON body
 *   key:           override the stored key (used by the login check)
 *   redirectOn401: false during login, so a bad key just shows an error
 */
async function api(path, { method = "GET", json, key = apiKey, redirectOn401 = true } = {}) {
  const headers = { "X-Admin-API-Key": key || "", Accept: "application/json" };
  const init = { method, headers };
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "Network error: could not reach the server.");
  }

  if (response.status === 401 && redirectOn401) {
    logout("Your admin key was rejected. Please sign in again.");
  }
  if (!response.ok) throw new ApiError(response.status, await readDetail(response));
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/* ------------------------------------------------------------------ */
/* Login / logout                                                      */
/* ------------------------------------------------------------------ */

function showLogin(message) {
  $("dashboard-view").hidden = true;
  $("logout").hidden = true;
  $("login-view").hidden = false;
  $("api-key").value = "";
  if (message) showError($("login-error"), message);
  else clearError($("login-error"));
  $("api-key").focus();
}

function showDashboard() {
  $("login-view").hidden = true;
  $("dashboard-view").hidden = false;
  $("logout").hidden = false;
  selectTab("overview");
}

function logout(message) {
  if (!apiKey) return; // already logged out (e.g. two requests got 401 at once)
  apiKey = null;
  storeKey(null);
  stopPolling();
  state.activeTab = ""; // stops in-flight loads from scheduling another refresh
  closeChunks();
  closeRunDetail();
  showLogin(message);
}

async function onLoginSubmit(event) {
  event.preventDefault();
  const key = $("api-key").value.trim();
  const errorBox = $("login-error");
  if (!key) {
    showError(errorBox, "Please enter the admin API key.");
    return;
  }

  const submit = $("login-submit");
  submit.disabled = true;
  submit.textContent = "Checking…";
  try {
    await api("/admin/health", { key, redirectOn401: false });
    apiKey = key;
    storeKey(key);
    showDashboard();
  } catch (error) {
    showError(errorBox, error.status === 401 ? "That key was not accepted. Check it and try again." : error.message);
  } finally {
    submit.disabled = false;
    submit.textContent = "Sign in";
  }
}

/* ------------------------------------------------------------------ */
/* Tabs                                                                */
/* ------------------------------------------------------------------ */

/** Show one tab panel, load its data, and (optionally) focus its tab. */
function selectTab(name, { focus = false } = {}) {
  state.activeTab = name;
  for (const tab of TABS) {
    const selected = tab === name;
    const tabButton = $(`tab-${tab}`);
    tabButton.setAttribute("aria-selected", String(selected));
    tabButton.tabIndex = selected ? 0 : -1;
    $(`panel-${tab}`).hidden = !selected;
  }
  if (focus) $(`tab-${name}`).focus();

  stopPolling(); // each tab starts its own polling if it needs to
  if (name === "overview") loadOverview();
  else if (name === "documents") loadDocuments();
  else if (name === "evaluation") loadEvalRuns();
}

/** Arrow keys / Home / End move between tabs (WAI-ARIA tabs pattern). */
function onTabKeydown(event) {
  const index = TABS.indexOf(state.activeTab);
  const moves = {
    ArrowRight: (index + 1) % TABS.length,
    ArrowLeft: (index - 1 + TABS.length) % TABS.length,
    Home: 0,
    End: TABS.length - 1,
  };
  if (!(event.key in moves)) return;
  event.preventDefault();
  selectTab(TABS[moves[event.key]], { focus: true });
}

/* ------------------------------------------------------------------ */
/* Overview                                                            */
/* ------------------------------------------------------------------ */

async function loadOverview() {
  const errorBox = $("overview-error");
  try {
    const [health, stats] = await Promise.all([api("/admin/health"), api("/admin/stats")]);
    clearError(errorBox);
    renderHealth(health);
    renderStats(stats);
  } catch (error) {
    reportError(errorBox, error);
  }
}

/** Append a <dt>/<dd> pair to a definition list. `value` may be a string or a node. */
function addKv(list, label, value) {
  const dd = createEl("dd");
  dd.append(value);
  list.append(createEl("dt", "", label), dd);
}

/** A badge for a health value: "ok" green, "disabled" grey, anything else red/amber. */
function healthBadge(value) {
  const text = String(value);
  const lower = text.toLowerCase();
  let modifier = "badge--bad";
  if (lower === "ok") modifier = "badge--ok";
  else if (lower === "disabled") modifier = "";
  else if (lower.startsWith("degraded")) modifier = "badge--warn";
  const badge = createEl("span", `badge ${modifier}`.trim(), text);
  if (text.length > 40) badge.title = text; // long "unhealthy: ..." messages
  return badge;
}

function renderHealth(health) {
  const list = $("health-list");
  list.replaceChildren();
  for (const [key, value] of Object.entries(health || {})) {
    const label = HEALTH_LABELS[key] || key;
    if (key === "stale_embedding_chunks") {
      const count = Number(value) || 0;
      const text = count === 0 ? "0" : `${count.toLocaleString()} (re-index recommended)`;
      addKv(list, label, createEl("span", `badge ${count === 0 ? "badge--ok" : "badge--warn"}`, text));
    } else if (HEALTH_STATUS_KEYS.has(key) || /^(ok|degraded|unhealthy)\b/i.test(String(value))) {
      addKv(list, label, healthBadge(value));
    } else {
      addKv(list, label, formatMetric(value));
    }
  }
}

function renderStats(stats) {
  const list = $("stats-list");
  list.replaceChildren();
  const data = stats || {};
  for (const [key, value] of Object.entries(data)) {
    if (key === "documents_by_status") continue;
    addKv(list, STATS_LABELS[key] || key, typeof value === "number" ? formatNumber(value) : formatMetric(value));
  }

  const byStatus = $("stats-by-status");
  byStatus.replaceChildren();
  const entries = Object.entries(data.documents_by_status || {});
  if (entries.length === 0) {
    byStatus.append(createEl("li", "muted", "No documents yet."));
  }
  for (const [status, count] of entries) {
    const item = createEl("li");
    const badge = statusBadge(status);
    badge.textContent = `${status}: ${formatNumber(count)}`;
    item.append(badge);
    byStatus.append(item);
  }
}

/* ------------------------------------------------------------------ */
/* Documents                                                           */
/* ------------------------------------------------------------------ */

async function loadDocuments() {
  stopPolling();
  const requestId = ++docsRequestId;
  const { offset, docType, status } = state.docs;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);

  try {
    const data = await api(`/documents?${params}`);
    if (requestId !== docsRequestId) return; // a newer request has started
    const documents = Array.isArray(data?.documents) ? data.documents : [];
    const total = Number(data?.total) || 0;

    // Deleting the last row of the last page leaves it empty: step back one page.
    if (documents.length === 0 && offset > 0 && total > 0) {
      state.docs.offset = Math.max(0, Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE);
      loadDocuments();
      return;
    }

    state.docs.total = total;
    clearError($("docs-error"));
    renderDocuments(documents);

    const busy = documents.some((doc) => doc.status === "pending" || doc.status === "processing");
    if (busy && state.activeTab === "documents") pollTimer = setTimeout(loadDocuments, POLL_MS);
  } catch (error) {
    if (requestId === docsRequestId) reportError($("docs-error"), error);
  }
}

function renderDocuments(documents) {
  const body = $("docs-body");

  // Auto-refresh rebuilds the rows; remember which action button had focus
  // so keyboard users don't lose their place every 5 seconds.
  const focused = body.contains(document.activeElement) ? document.activeElement : null;
  const focusKey = focused ? { id: focused.dataset.id, action: focused.dataset.action } : null;

  state.docs.byId = new Map(documents.map((doc) => [String(doc.id), doc]));
  body.replaceChildren(...documents.map(renderDocumentRow));

  if (documents.length === 0) {
    const row = createEl("tr");
    const cell = createEl("td", "empty", "No documents match these filters.");
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
  }

  if (focusKey && focusKey.id) {
    const selector = `button[data-id="${CSS.escape(focusKey.id)}"][data-action="${CSS.escape(focusKey.action)}"]`;
    body.querySelector(selector)?.focus();
  }

  // Pager
  const { offset, total } = state.docs;
  const first = documents.length ? offset + 1 : 0;
  $("docs-page-info").textContent = total
    ? `Showing ${first}–${offset + documents.length} of ${total}`
    : "No documents";
  $("docs-prev").disabled = offset === 0;
  $("docs-next").disabled = offset + PAGE_SIZE >= total;
}

function renderDocumentRow(doc) {
  const id = String(doc.id);
  const name = String(doc.filename ?? "");
  const row = createEl("tr");

  const filename = createEl("td", "cell-filename", name);
  const errorText = doc.error_message ? String(doc.error_message) : "";
  const errorCell = createEl("td", "cell-error", errorText);
  if (errorText) errorCell.title = errorText; // full text on hover

  const statusCell = createEl("td");
  statusCell.append(statusBadge(doc.status));

  const actions = createEl("td", "cell-actions");
  actions.append(
    actionButton("Chunks", "chunks", id, `View chunks of ${name}`),
    actionButton("Re-ingest", "reingest", id, `Re-ingest ${name}`),
    actionButton("Delete", "delete", id, `Delete ${name}`, "btn--danger"),
  );

  row.append(
    filename,
    createEl("td", "", doc.doc_type),
    createEl("td", "", doc.content_type),
    statusCell,
    createEl("td", "num", formatNumber(doc.chunk_count ?? 0)),
    createEl("td", "", formatDate(doc.updated_at)),
    errorCell,
    actions,
  );
  return row;
}

/** One click handler for every row button (event delegation). */
function onDocumentAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const doc = state.docs.byId.get(button.dataset.id);
  if (!doc) return;
  if (button.dataset.action === "chunks") openChunks(doc);
  else if (button.dataset.action === "reingest") reingestDocument(doc, button);
  else if (button.dataset.action === "delete") deleteDocument(doc);
}

async function reingestDocument(doc, button) {
  button.disabled = true;
  try {
    await api(`/documents/${encodeURIComponent(doc.id)}/reingest`, { method: "POST" });
    showToast(`Re-ingest queued for "${doc.filename}".`);
    loadDocuments();
  } catch (error) {
    if (error.status !== 401) showToast(`Re-ingest failed: ${error.message}`, true);
    button.disabled = false;
  }
}

async function deleteDocument(doc) {
  const confirmed = await confirmAction(
    `Delete "${doc.filename}"? This removes the document and all of its chunks. It cannot be undone.`,
    "Delete",
  );
  if (!confirmed) return;
  try {
    await api(`/documents/${encodeURIComponent(doc.id)}`, { method: "DELETE" });
    showToast(`Deleted "${doc.filename}".`);
    if (state.chunks.docId === doc.id) closeChunks();
    loadDocuments();
  } catch (error) {
    if (error.status !== 401) showToast(`Delete failed: ${error.message}`, true);
  }
}

function onFiltersSubmit(event) {
  event.preventDefault();
  state.docs.docType = $("filter-doc-type").value.trim();
  state.docs.status = $("filter-status").value;
  state.docs.offset = 0;
  loadDocuments();
}

function changePage(direction) {
  state.docs.offset = Math.max(0, state.docs.offset + direction * PAGE_SIZE);
  loadDocuments();
}

/* ---------- Chunks panel ---------- */

function openChunks(doc) {
  state.chunks = { docId: doc.id, offset: 0 };
  $("chunks-title").textContent = `Chunks: ${doc.filename}`;
  $("chunks-list").replaceChildren();
  $("chunks-more").hidden = true;
  $("chunks-status").textContent = "Loading…";
  const panel = $("chunks-panel");
  panel.hidden = false;
  panel.scrollIntoView({ block: "start" });
  panel.focus({ preventScroll: true });
  loadChunks();
}

async function loadChunks() {
  const { docId, offset } = state.chunks;
  const more = $("chunks-more");
  more.disabled = true;
  try {
    const data = await api(`/documents/${encodeURIComponent(docId)}/chunks?limit=${CHUNK_PAGE_SIZE}&offset=${offset}`);
    if (state.chunks.docId !== docId) return; // user opened another document meanwhile
    const chunks = Array.isArray(data) ? data : [];
    $("chunks-list").append(...chunks.map(renderChunk));
    state.chunks.offset += chunks.length;
    more.hidden = chunks.length < CHUNK_PAGE_SIZE;
    $("chunks-status").textContent = state.chunks.offset
      ? `Showing ${state.chunks.offset} chunk(s).`
      : "This document has no chunks.";
  } catch (error) {
    if (error.status !== 401 && state.chunks.docId === docId) $("chunks-status").textContent = error.message;
  } finally {
    more.disabled = false;
  }
}

function renderChunk(chunk) {
  const item = createEl("li", "chunk");
  const meta = createEl("div", "chunk-meta");
  meta.append(createEl("span", "badge badge--info", `#${chunk.chunk_index}`));
  if (chunk.page_number !== null && chunk.page_number !== undefined) {
    meta.append(createEl("span", "badge", `p. ${chunk.page_number}`));
  }
  meta.append(createEl("span", "badge", `${formatNumber(chunk.token_count ?? 0)} tokens`));
  if (chunk.status && chunk.status !== "active") meta.append(statusBadge(chunk.status));
  item.append(meta);

  if (Array.isArray(chunk.heading_hierarchy) && chunk.heading_hierarchy.length) {
    item.append(createEl("p", "chunk-heading", chunk.heading_hierarchy.join(" › ")));
  }
  item.append(createEl("pre", "chunk-content", chunk.content ?? ""));
  return item;
}

function closeChunks() {
  state.chunks = { docId: null, offset: 0 };
  $("chunks-panel").hidden = true;
  $("chunks-list").replaceChildren();
}

/* ------------------------------------------------------------------ */
/* Upload                                                              */
/* ------------------------------------------------------------------ */

/**
 * POST the form with XMLHttpRequest, because fetch() cannot report upload
 * progress. Resolves with { status, body } for any HTTP status.
 */
function uploadWithProgress(formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/documents/upload`);
    xhr.setRequestHeader("X-Admin-API-Key", apiKey || "");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "json"; // body is null if the server did not send JSON
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    xhr.addEventListener("load", () => resolve({ status: xhr.status, body: xhr.response }));
    xhr.addEventListener("error", () => reject(new ApiError(0, "Network error: the upload did not reach the server.")));
    xhr.send(formData);
  });
}

/** Validate the metadata textarea. Returns the parsed object, null if empty, or undefined if invalid. */
function readMetadata() {
  const input = $("upload-metadata");
  const errorBox = $("upload-metadata-error");
  input.removeAttribute("aria-invalid");
  clearError(errorBox);

  const raw = input.value.trim();
  if (!raw) return null;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    showError(errorBox, `Not valid JSON: ${error.message}`);
  }
  if (parsed !== undefined && (parsed === null || typeof parsed !== "object" || Array.isArray(parsed))) {
    showError(errorBox, 'Metadata must be a JSON object, for example {"year": 2025}.');
    parsed = undefined;
  }
  if (parsed === undefined) input.setAttribute("aria-invalid", "true");
  return parsed;
}

async function onUploadSubmit(event) {
  event.preventDefault();
  const result = $("upload-result");
  result.hidden = true;

  const file = $("upload-file").files[0];
  if (!file) {
    showResult(result, "Choose a file to upload.", "error");
    return;
  }
  const dot = file.name.lastIndexOf(".");
  const extension = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  if (!SUPPORTED_EXTENSIONS.includes(extension)) {
    showResult(result, `Unsupported file type "${extension || "(none)"}". Supported: ${SUPPORTED_EXTENSIONS.join(" ")}`, "error");
    return;
  }
  const metadata = readMetadata();
  if (metadata === undefined) return;

  const formData = new FormData();
  formData.append("file", file);
  formData.append("doc_type", $("upload-doc-type").value.trim() || "general");
  if (metadata) formData.append("metadata_json", JSON.stringify(metadata));

  const submit = $("upload-submit");
  const progress = $("upload-progress");
  submit.disabled = true;
  submit.textContent = "Uploading…";
  progress.value = 0;
  progress.hidden = false;

  try {
    const { status, body } = await uploadWithProgress(formData, (percent) => {
      progress.value = percent;
      progress.textContent = `${percent}%`;
      if (percent >= 100) {
        // Bytes are sent; the server may still be parsing the file.
        submit.textContent = "Processing…";
        progress.removeAttribute("value"); // indeterminate bar
      }
    });
    handleUploadResponse(status, body, file.name);
  } catch (error) {
    showResult(result, error.message, "error");
  } finally {
    submit.disabled = false;
    submit.textContent = "Upload";
    progress.hidden = true;
  }
}

function handleUploadResponse(status, body, fileName) {
  const result = $("upload-result");
  if (status === 401) {
    logout("Your admin key was rejected. Please sign in again.");
    return;
  }

  if (status === 201 || status === 200) {
    const name = body?.filename || fileName;
    const message =
      status === 201
        ? `Uploaded "${name}". Status: ${body?.status ?? "pending"}.`
        : `"${name}" was already uploaded (duplicate). Nothing was changed.`;
    showResult(result, message, status === 201 ? "ok" : "warn");
    const view = createEl("button", "btn btn--small btn--secondary", "View documents");
    view.type = "button";
    view.addEventListener("click", () => selectTab("documents", { focus: true }));
    result.append(" ", view);
    $("upload-file").value = "";
    $("upload-metadata").value = "";
    return;
  }

  const fallback = {
    400: "The file is empty or the metadata is invalid.",
    413: "The file is too large.",
    415: "This file type is not supported.",
  };
  const detail = detailToText(body?.detail);
  showResult(result, `Upload failed: ${detail || fallback[status] || `HTTP ${status}`}`, "error");
}

/* ------------------------------------------------------------------ */
/* Maintenance                                                         */
/* ------------------------------------------------------------------ */

async function onReindexClick() {
  const confirmed = await confirmAction(
    "Re-index all documents? Every document is re-ingested in the background, and answers may be incomplete until it finishes.",
    "Re-index",
  );
  if (!confirmed) return;

  const button = $("reindex");
  const result = $("reindex-result");
  button.disabled = true;
  try {
    const data = await api("/admin/reindex", { method: "POST" });
    showResult(result, `Queued ${formatNumber(data?.queued ?? 0)} document(s) for re-indexing.`, "ok");
  } catch (error) {
    if (error.status !== 401) showResult(result, `Re-index failed: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* Evaluation                                                          */
/* ------------------------------------------------------------------ */

const isRunActive = (run) => run.status === "queued" || run.status === "running";

/** A compact <ul> of "name: value" pairs, built generically from any metrics object. */
function renderMetrics(metrics) {
  const entries = metrics && typeof metrics === "object" ? Object.entries(metrics) : [];
  if (entries.length === 0) return createEl("span", "muted", "—");
  const list = createEl("ul", "metrics");
  for (const [name, value] of entries) {
    const item = createEl("li");
    item.append(createEl("strong", "", `${name}: `), formatMetric(value));
    list.append(item);
  }
  return list;
}

async function startEval(mode, button) {
  button.disabled = true;
  try {
    await api("/admin/eval/runs", { method: "POST", json: { mode } });
    showToast(`${mode === "answer" ? "Answer" : "Retrieval"} evaluation queued.`);
    loadEvalRuns();
  } catch (error) {
    reportError($("eval-error"), error);
  } finally {
    button.disabled = false;
  }
}

async function loadEvalRuns() {
  stopPolling();
  const requestId = ++evalRequestId;
  try {
    const data = await api("/admin/eval/runs?limit=20");
    if (requestId !== evalRequestId) return;
    const runs = Array.isArray(data) ? data : [];
    clearError($("eval-error"));
    renderRuns(runs);

    // Keep an open run's results in step with the list.
    const open = runs.find((run) => String(run.id) === state.openRun.id);
    if (open && (isRunActive(open) || open.status !== state.openRun.status)) {
      loadRunDetail(state.openRun.id, { focus: false });
    }

    if (runs.some(isRunActive) && state.activeTab === "evaluation") pollTimer = setTimeout(loadEvalRuns, POLL_MS);
  } catch (error) {
    if (requestId === evalRequestId) reportError($("eval-error"), error);
  }
}

function renderRuns(runs) {
  const body = $("eval-body");
  body.replaceChildren();
  if (runs.length === 0) {
    const row = createEl("tr");
    const cell = createEl("td", "empty", "No evaluation runs yet.");
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const run of runs) {
    const row = createEl("tr");
    const statusCell = createEl("td");
    statusCell.append(statusBadge(run.status));
    if (run.error) {
      const error = createEl("div", "cell-error", run.error);
      error.title = String(run.error);
      statusCell.append(error);
    }
    const metricsCell = createEl("td");
    metricsCell.append(renderMetrics(run.metrics));
    const actionCell = createEl("td");
    actionCell.append(actionButton("View", "view-run", String(run.id), `View results of ${run.mode} run from ${formatDate(run.created_at)}`));
    row.append(createEl("td", "", formatDate(run.created_at)), createEl("td", "", run.mode), statusCell, metricsCell, actionCell);
    body.append(row);
  }
}

function onRunAction(event) {
  const button = event.target.closest('button[data-action="view-run"]');
  if (button) loadRunDetail(button.dataset.id, { focus: true });
}

async function loadRunDetail(runId, { focus }) {
  const panel = $("eval-detail");
  const summary = $("eval-detail-summary");
  const body = $("eval-detail-body");
  if (state.openRun.id !== runId) {
    // A different run: clear the old content while loading.
    summary.replaceChildren(createEl("p", "muted", "Loading…"));
    body.replaceChildren();
  }
  state.openRun.id = runId;
  panel.hidden = false;
  if (focus) {
    panel.scrollIntoView({ block: "start" });
    panel.focus({ preventScroll: true });
  }

  try {
    const run = await api(`/admin/eval/runs/${encodeURIComponent(runId)}`);
    if (state.openRun.id !== runId) return; // user opened another run meanwhile
    state.openRun.status = run.status;
    renderRunDetail(run);
  } catch (error) {
    if (error.status !== 401 && state.openRun.id === runId) {
      summary.replaceChildren(createEl("p", "form-error", error.message));
    }
  }
}

function renderRunDetail(run) {
  $("eval-detail-title").textContent = `Results: ${run.mode} run`;

  const summary = $("eval-detail-summary");
  const line = createEl("p", "", `Created ${formatDate(run.created_at)} · finished ${formatDate(run.finished_at)} · `);
  line.append(statusBadge(run.status));
  summary.replaceChildren(line);
  if (run.error) summary.append(createEl("p", "form-error", `Error: ${run.error}`));
  summary.append(renderMetrics(run.metrics));

  const results = Array.isArray(run.results) ? [...run.results] : [];
  // Failures first; Array.sort is stable, so the server's order is otherwise kept.
  results.sort((a, b) => Number(a.passed === true) - Number(b.passed === true));

  const body = $("eval-detail-body");
  body.replaceChildren();
  if (results.length === 0) {
    const row = createEl("tr");
    const cell = createEl("td", "empty", isRunActive(run) ? "The run has not finished yet." : "No per-case results.");
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const result of results) {
    const passed = result.passed === true;
    const row = createEl("tr", passed ? "" : "row-fail");
    const passCell = createEl("td", passed ? "pass-yes" : "pass-no", passed ? "✓ Pass" : "✗ Fail");
    const detail = typeof result.detail === "string" ? result.detail : formatMetric(result.detail);
    row.append(
      createEl("td", "", result.case_id),
      createEl("td", "", result.category),
      createEl("td", "", result.question),
      passCell,
      createEl("td", "", detail),
    );
    body.append(row);
  }
}

function closeRunDetail() {
  state.openRun = { id: null, status: null };
  $("eval-detail").hidden = true;
}

/* ------------------------------------------------------------------ */
/* Start-up                                                            */
/* ------------------------------------------------------------------ */

function wireEvents() {
  $("login-form").addEventListener("submit", onLoginSubmit);
  $("logout").addEventListener("click", () => logout());

  const tablist = document.querySelector('[role="tablist"]');
  tablist.addEventListener("click", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (tab) selectTab(tab.id.replace("tab-", ""));
  });
  tablist.addEventListener("keydown", onTabKeydown);

  $("overview-refresh").addEventListener("click", loadOverview);

  $("docs-refresh").addEventListener("click", loadDocuments);
  $("docs-filters").addEventListener("submit", onFiltersSubmit);
  $("docs-prev").addEventListener("click", () => changePage(-1));
  $("docs-next").addEventListener("click", () => changePage(1));
  $("docs-body").addEventListener("click", onDocumentAction);
  $("chunks-close").addEventListener("click", closeChunks);
  $("chunks-more").addEventListener("click", loadChunks);

  $("upload-form").addEventListener("submit", onUploadSubmit);

  $("reindex").addEventListener("click", onReindexClick);

  $("eval-retrieval").addEventListener("click", (event) => startEval("retrieval", event.currentTarget));
  $("eval-answer").addEventListener("click", (event) => startEval("answer", event.currentTarget));
  $("eval-refresh").addEventListener("click", loadEvalRuns);
  $("eval-body").addEventListener("click", onRunAction);
  $("eval-detail-close").addEventListener("click", closeRunDetail);
}

wireEvents();
if (apiKey) showDashboard();
else showLogin();
