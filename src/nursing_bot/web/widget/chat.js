/*
 * chat.js: logic for the NursingBot chat widget (chat.html).
 *
 * The page runs inside an iframe injected by embed.js. This script:
 *   1. loads the widget config (title, welcome text, limits) from the API;
 *   2. manages an anonymous chat session token in sessionStorage;
 *   3. sends questions to POST /api/v1/chat with stream: true and reads the
 *      Server-Sent Events (SSE) reply from a fetch() ReadableStream;
 *   4. renders answers safely: untrusted text (LLM output, document titles,
 *      snippets) only ever goes into text nodes, never into innerHTML;
 *   5. keeps the transcript in sessionStorage so it survives a page reload.
 *
 * The page CSP forbids inline styles, so all visual changes are made by
 * toggling CSS classes or the `hidden` attribute.
 */

const API_BASE = "/api/v1";
const SESSION_KEY = "nursingbot.session";
const TRANSCRIPT_KEY = "nursingbot.transcript";
const CONTEXT_CHAR_LIMIT = 2000; // contract: each recent_context content is at most 2000 chars
const MAX_STORED_TURNS = 50; // keeps sessionStorage small on very long chats
const SESSION_EXPIRY_MARGIN_MS = 30_000; // renew a little early to avoid racing the expiry
const FALLBACK_ANSWER = "Sorry, I couldn't produce an answer. Please try rephrasing your question.";

// Used until /widget/config answers (or if it fails).
const DEFAULT_CONFIG = {
  knowledge_base_name: "NursingBot",
  authority_name: "",
  contact_url: "",
  welcome_message: "Hello! Ask me about nursing registration, renewal, admissions and other council services.",
  require_session: true,
  max_query_chars: 4000,
  max_context_turns: 2,
};

const els = {
  title: document.getElementById("chat-title"),
  subtitle: document.getElementById("chat-subtitle"),
  messages: document.getElementById("messages"),
  srStatus: document.getElementById("sr-status"),
  form: document.getElementById("composer"),
  input: document.getElementById("query"),
  counter: document.getElementById("char-counter"),
  send: document.getElementById("send"),
  stop: document.getElementById("stop"),
  newChat: document.getElementById("new-chat"),
  close: document.getElementById("close-chat"),
};

/** An error whose message is safe and friendly enough to show in the chat. */
class ChatError extends Error {}

let config = { ...DEFAULT_CONFIG };
let turns = loadTranscript(); // [{ query, answer, citations, isGrounded }]
let session = readStored(SESSION_KEY); // { session_token, expires_at } or null
let activeController = null; // AbortController of the request in flight, if any

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/** Create an element with an optional class and plain-text content. */
function createEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Cut a string to `max` UTF-16 units without leaving half an emoji at the end. */
function truncate(text, max) {
  if (text.length <= max) return text;
  let cut = text.slice(0, max);
  if (/[\uD800-\uDBFF]$/.test(cut)) cut = cut.slice(0, -1);
  return cut;
}

/** Parse an ISO timestamp from the server. Timestamps without a timezone are treated as UTC. */
function parseServerDate(value) {
  if (typeof value !== "string" || value === "") return NaN;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return Date.parse(hasZone ? value : `${value}Z`);
}

/** Return the URL if it is a plain http(s) link, otherwise null (blocks javascript: URLs). */
function safeHttpUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

/** Read the `detail` field of an error response as a string ("" if there is none). */
async function readErrorDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    // 422 validation errors carry a list of { loc, msg, ... } objects.
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => (item && item.msg) || "").filter(Boolean).join("; ");
    }
  } catch {
    // Body was not JSON; fall through.
  }
  return "";
}

/** Turn a non-2xx response into a friendly message for the chat. */
async function describeHttpError(response) {
  const detail = await readErrorDetail(response);
  if (response.status === 429) {
    return `Too many requests, please wait a moment and try again.${detail ? ` (${detail})` : ""}`;
  }
  if (response.status === 422) {
    return detail
      ? `Your question couldn't be sent: ${detail}`
      : "Your question couldn't be sent. Please shorten or rephrase it.";
  }
  if (response.status === 401) {
    return "Your chat session could not be verified. Please reload the page and try again.";
  }
  if (response.status >= 500) {
    return "The assistant is temporarily unavailable. Please try again in a few minutes.";
  }
  return detail || `Something went wrong (error ${response.status}). Please try again.`;
}

/* ------------------------------------------------------------------ */
/* sessionStorage (wrapped: storage can be blocked inside iframes)     */
/* ------------------------------------------------------------------ */

function readStored(key) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null; // storage blocked or corrupt value
  }
}

function writeStored(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable: the chat still works, it just won't survive a reload.
  }
}

function loadTranscript() {
  const stored = readStored(TRANSCRIPT_KEY);
  if (!Array.isArray(stored)) return [];
  return stored.filter((t) => t && typeof t.query === "string" && typeof t.answer === "string");
}

function saveTranscript() {
  turns = turns.slice(-MAX_STORED_TURNS);
  writeStored(TRANSCRIPT_KEY, turns.length ? turns : null);
}

/* ------------------------------------------------------------------ */
/* SSE parser                                                          */
/* ------------------------------------------------------------------ */

/**
 * Incremental Server-Sent Events parser.
 *
 * Feed it decoded text with push() as it arrives; it calls
 * onEvent(eventName, dataString) for every complete event. Network reads can
 * split an event (or even a "\r\n" pair) anywhere, so incomplete lines are
 * kept in `buffer` until the rest arrives. Lines may end in "\r\n", "\n" or "\r".
 * Call finish() when the stream ends to flush a final event that had no
 * trailing blank line.
 */
function createSseParser(onEvent) {
  let buffer = "";
  let eventName = "";
  let dataLines = [];

  function dispatch() {
    if (dataLines.length > 0) onEvent(eventName || "message", dataLines.join("\n"));
    eventName = "";
    dataLines = [];
  }

  function processLine(line) {
    if (line === "") {
      dispatch(); // a blank line ends the event
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive ping
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    // "id" and "retry" fields are not needed here and are ignored.
  }

  return {
    push(text) {
      let pending = buffer + text;
      // A trailing "\r" may be the first half of a "\r\n" split across reads.
      // Hold it back so it isn't counted as two line endings.
      const heldCr = pending.endsWith("\r");
      if (heldCr) pending = pending.slice(0, -1);
      const lines = pending.split(/\r\n|\r|\n/);
      buffer = lines.pop() + (heldCr ? "\r" : ""); // last piece is an unfinished line
      lines.forEach(processLine);
    },
    finish() {
      const rest = buffer.replace(/\r$/, "");
      buffer = "";
      if (rest !== "") processLine(rest);
      dispatch();
    },
  };
}

/* ------------------------------------------------------------------ */
/* Safe answer formatting (DOM nodes only, no HTML strings)            */
/* ------------------------------------------------------------------ */

// Matches **bold** or a [Source N] tag.
const INLINE_PATTERN = /\*\*(.+?)\*\*|\[Source\s+(\d+)\]/g;

/**
 * Render answer text into `container` with minimal formatting:
 * paragraphs, line breaks, "- " / "* " / "1. " lists, **bold** and
 * [Source N] buttons. Text is inserted with append(string), which creates
 * text nodes, so any HTML in the answer is shown literally (that is the
 * "escaping" step) and can never run.
 *
 * `sourceNumbers` is the Set of citation numbers that exist; [Source N] tags
 * for unknown numbers are left as plain text.
 */
function renderFormatted(container, text, sourceNumbers) {
  container.replaceChildren();
  const blocks = text.replace(/\r\n?/g, "\n").trim().split(/\n\s*\n/);

  for (const block of blocks) {
    let paragraph = null;
    let list = null;

    for (const line of block.split("\n")) {
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);

      if (bullet || numbered) {
        const listTag = bullet ? "UL" : "OL";
        if (!list || list.tagName !== listTag) {
          list = document.createElement(listTag);
          if (numbered) list.start = Number(numbered[1]);
          container.append(list);
        }
        paragraph = null;
        const item = document.createElement("li");
        appendInline(item, bullet ? bullet[1] : numbered[2], sourceNumbers);
        list.append(item);
      } else if (line.trim() === "") {
        continue;
      } else if (list && /^\s+\S/.test(line)) {
        // Indented line right after a list item: continuation of that item.
        list.lastElementChild.append(document.createElement("br"));
        appendInline(list.lastElementChild, line.trim(), sourceNumbers);
      } else {
        list = null;
        if (paragraph) {
          paragraph.append(document.createElement("br"));
        } else {
          paragraph = document.createElement("p");
          container.append(paragraph);
        }
        appendInline(paragraph, line, sourceNumbers);
      }
    }
  }
}

/** Append one line of text, turning **bold** and [Source N] into elements. */
function appendInline(parent, text, sourceNumbers) {
  let last = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    if (match.index > last) parent.append(text.slice(last, match.index));
    if (match[1] !== undefined) {
      const strong = document.createElement("strong");
      appendInline(strong, match[1], sourceNumbers); // bold text may contain [Source N]
      parent.append(strong);
    } else {
      parent.append(createSourceRef(Number(match[2]), match[0], sourceNumbers));
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

/** A superscript button for [Source N], or the raw tag text if source N doesn't exist. */
function createSourceRef(number, rawText, sourceNumbers) {
  if (!sourceNumbers.has(number)) return rawText;
  const sup = document.createElement("sup");
  const button = createEl("button", "cite-ref", String(number));
  button.type = "button";
  button.dataset.source = String(number);
  button.setAttribute("aria-label", `Show source ${number}`);
  sup.append(button);
  return sup;
}

/* ------------------------------------------------------------------ */
/* Message rendering                                                   */
/* ------------------------------------------------------------------ */

/** Run `update`, then keep the list scrolled to the bottom if it already was. */
function withAutoScroll(update) {
  const box = els.messages;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  update();
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function addUserMessage(text) {
  const article = createEl("article", "msg msg--user");
  article.append(createEl("span", "visually-hidden", "You said: "), text);
  els.messages.append(article);
  els.messages.scrollTop = els.messages.scrollHeight;
}

/**
 * Create an empty bot bubble with a typing indicator. Returns a "view"
 * object that the streaming code fills in.
 */
function createBotView() {
  const article = createEl("article", "msg msg--bot");
  const body = createEl("div", "msg-body");
  const typing = createEl("span", "typing");
  typing.setAttribute("aria-hidden", "true");
  typing.append(createEl("span"), createEl("span"), createEl("span"));
  const sources = createEl("div", "sources");
  sources.hidden = true;
  article.append(createEl("span", "visually-hidden", "NursingBot said: "), body, typing, sources);
  withAutoScroll(() => els.messages.append(article));
  return { article, body, typing, sources, sourceNumbers: new Set(), draft: "", done: false, renderQueued: false };
}

/** Render the citation list under a bot message. */
function renderSources(view, citations) {
  const valid = citations.filter((c) => c && Number.isFinite(Number(c.source_index)));
  view.sourceNumbers = new Set(valid.map((c) => Number(c.source_index)));
  view.sources.replaceChildren();
  view.sources.hidden = valid.length === 0;
  if (valid.length === 0) return;

  const list = createEl("ol", "sources-list");
  list.setAttribute("aria-label", "Sources");
  for (const citation of valid) {
    const item = createEl("li", "source");
    item.dataset.source = String(Number(citation.source_index));
    item.tabIndex = -1; // focus target when a [Source N] button is clicked
    item.append(
      createEl("span", "source-num", `${citation.source_index}.`),
      " ",
      createEl("span", "source-title", String(citation.title || "Untitled document")),
    );
    if (citation.page_number !== null && citation.page_number !== undefined) {
      item.append(createEl("span", "source-page", ` · p. ${citation.page_number}`));
    }
    if (citation.snippet) {
      const details = document.createElement("details");
      details.append(createEl("summary", "", "Show excerpt"), createEl("p", "source-snippet", String(citation.snippet)));
      item.append(details);
    }
    list.append(item);
  }
  view.sources.append(createEl("p", "sources-title", "Sources"), list);
}

/** Re-render the streamed draft at most once per animation frame. */
function scheduleDraftRender(view) {
  if (view.renderQueued) return;
  view.renderQueued = true;
  requestAnimationFrame(() => {
    view.renderQueued = false;
    if (view.done) return; // the final answer has already replaced the draft
    withAutoScroll(() => renderFormatted(view.body, view.draft, view.sourceNumbers));
  });
}

/** Show the verified final answer (replacing any streamed draft). */
function finishBotMessage(view, result) {
  view.done = true;
  withAutoScroll(() => {
    view.typing.remove();
    renderSources(view, result.citations);
    renderFormatted(view.body, result.answer, view.sourceNumbers);
    if (!result.isGrounded) markAbstention(view);
  });
}

/** Style a message as "could not answer" and add the council contact link. */
function markAbstention(view) {
  view.article.classList.add("msg--abstain");
  const url = safeHttpUrl(config.contact_url);
  if (!url) return;
  const note = createEl("p", "msg-contact", "For official help, please ");
  const link = createEl("a", "", `contact the ${config.authority_name || "council"}`);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  note.append(link, ".");
  view.article.append(note);
}

/** Replace a bot bubble's content with an error (or "stopped") notice. */
function showBotError(view, message, kind = "error") {
  view.done = true;
  withAutoScroll(() => {
    view.typing.remove();
    view.sources.replaceChildren();
    view.sources.hidden = true;
    view.body.replaceChildren(createEl("p", "", message));
    view.article.classList.add(kind === "stopped" ? "msg--stopped" : "msg--error");
  });
}

/** Clear the list and draw the welcome message plus the saved transcript. */
function renderConversation() {
  els.messages.replaceChildren();
  finishBotMessage(createBotView(), { answer: config.welcome_message, citations: [], isGrounded: true });
  for (const turn of turns) {
    addUserMessage(turn.query);
    finishBotMessage(createBotView(), {
      answer: turn.answer,
      citations: Array.isArray(turn.citations) ? turn.citations : [],
      isGrounded: turn.isGrounded !== false,
    });
  }
  els.messages.scrollTop = els.messages.scrollHeight;
}

/** Scroll to, open and briefly highlight citation N of the clicked message. */
function highlightSource(button) {
  const article = button.closest(".msg");
  // data-source only ever holds digits we wrote ourselves, so the selector is safe.
  const item = article && article.querySelector(`.source[data-source="${button.dataset.source}"]`);
  if (!item) return;
  const details = item.querySelector("details");
  if (details) details.open = true;
  item.classList.remove("is-highlighted");
  void item.offsetWidth; // force a reflow so the CSS animation restarts
  item.classList.add("is-highlighted");
  setTimeout(() => item.classList.remove("is-highlighted"), 2000);
  item.scrollIntoView({ block: "nearest" });
  item.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ */
/* Config and session                                                  */
/* ------------------------------------------------------------------ */

/** Fetch /widget/config. Returns false (and keeps defaults) if it fails. */
async function loadConfig() {
  try {
    const response = await fetch(`${API_BASE}/widget/config`, { headers: { Accept: "application/json" } });
    if (!response.ok) return false;
    const data = await response.json();
    config = { ...DEFAULT_CONFIG, ...data };
    // Guard against missing or odd numeric values.
    if (!(Number.isInteger(config.max_query_chars) && config.max_query_chars > 0)) {
      config.max_query_chars = DEFAULT_CONFIG.max_query_chars;
    }
    if (!(Number.isInteger(config.max_context_turns) && config.max_context_turns >= 0)) {
      config.max_context_turns = DEFAULT_CONFIG.max_context_turns;
    }
    return true;
  } catch {
    return false;
  }
}

function applyConfig() {
  const title = String(config.knowledge_base_name || DEFAULT_CONFIG.knowledge_base_name);
  els.title.textContent = title;
  document.title = title;
  els.subtitle.textContent = String(config.authority_name || "");
  els.subtitle.hidden = !config.authority_name;
  els.input.maxLength = config.max_query_chars;
  updateCounter();
}

/** True if we hold a token that is not about to expire. */
function sessionIsValid() {
  if (!session || typeof session.session_token !== "string") return false;
  const expiry = parseServerDate(session.expires_at);
  // No readable expiry: keep using the token until the server answers 401.
  return Number.isNaN(expiry) || expiry - SESSION_EXPIRY_MARGIN_MS > Date.now();
}

/** POST /chat/session and remember the new token. */
async function createSession(signal) {
  const response = await fetch(`${API_BASE}/chat/session`, {
    method: "POST",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new ChatError(await describeHttpError(response));
  const data = await response.json();
  if (!data || typeof data.session_token !== "string") {
    throw new ChatError("Couldn't start a chat session. Please try again.");
  }
  session = { session_token: data.session_token, expires_at: data.expires_at };
  writeStored(SESSION_KEY, session);
  return session.session_token;
}

/** The token to send, creating a session first if the server requires one. */
async function getSessionToken(signal) {
  if (sessionIsValid()) return session.session_token;
  if (!config.require_session) return null;
  return createSession(signal);
}

/* ------------------------------------------------------------------ */
/* Sending a question and reading the streamed answer                  */
/* ------------------------------------------------------------------ */

/** Last `max_context_turns` question/answer pairs, oldest first. */
function buildRecentContext() {
  if (config.max_context_turns <= 0) return [];
  return turns.slice(-config.max_context_turns).flatMap((turn) => [
    { role: "user", content: truncate(turn.query, CONTEXT_CHAR_LIMIT) },
    { role: "assistant", content: truncate(turn.answer, CONTEXT_CHAR_LIMIT) },
  ]);
}

/** POST /chat. On a 401, start a fresh session and retry exactly once. */
async function postChat(query, signal) {
  const body = JSON.stringify({ query, stream: true, recent_context: buildRecentContext(), filters: null });
  const send = (token) => {
    const headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
    if (token) headers["X-Session-Token"] = token;
    return fetch(`${API_BASE}/chat`, { method: "POST", headers, body, signal });
  };

  let response = await send(await getSessionToken(signal));
  if (response.status === 401) {
    response.body?.cancel().catch(() => {});
    response = await send(await createSession(signal));
  }
  return response;
}

/** Normalise an answer payload into { answer, citations, isGrounded }. */
function toResult(answer, citations, isGrounded) {
  return {
    answer: typeof answer === "string" && answer.trim() ? answer : FALLBACK_ANSWER,
    citations: Array.isArray(citations) ? citations : [],
    isGrounded: isGrounded !== false,
  };
}

/**
 * Read the reply. Tokens are shown live as a draft; the `end` event's
 * final_answer is the only text we keep (the contract requires replacing
 * the draft, which may have failed citation checks).
 */
async function readAnswer(response, view) {
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    // The server chose not to stream; the body has the non-streaming shape.
    const data = await response.json();
    return toResult(data.answer, data.citations, data.is_grounded);
  }
  if (!response.body) throw new ChatError(FALLBACK_ANSWER);

  let result = null;
  let streamError = null;
  const parser = createSseParser((event, data) => {
    if (result || streamError) return; // ignore anything after end/error
    let payload;
    try {
      payload = JSON.parse(data);
    } catch {
      return; // skip a malformed event rather than failing the whole answer
    }
    if (event === "citations" && Array.isArray(payload)) {
      renderSources(view, payload);
      scheduleDraftRender(view);
    } else if (event === "token" && typeof payload === "string") {
      view.draft += payload;
      scheduleDraftRender(view);
    } else if (event === "end" && payload && typeof payload === "object") {
      result = toResult(payload.final_answer, payload.citations, payload.is_grounded);
    } else if (event === "error") {
      streamError = new ChatError("Sorry, something went wrong while writing the answer. Please try again.");
    }
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder(); // {stream: true} below copes with UTF-8 characters split across reads
  while (!result && !streamError) {
    const { value, done } = await reader.read();
    if (done) {
      parser.push(decoder.decode());
      parser.finish();
      break;
    }
    parser.push(decoder.decode(value, { stream: true }));
  }
  reader.cancel().catch(() => {}); // release the connection if we stopped early

  if (streamError) throw streamError;
  if (!result) throw new ChatError("The answer was interrupted before it finished. Please try again.");
  return result;
}

/** Send one question and show the answer. */
async function sendQuery(query) {
  addUserMessage(query);
  const view = createBotView();
  const controller = new AbortController();
  activeController = controller;
  setBusy(true);
  els.srStatus.textContent = "NursingBot is writing an answer.";

  try {
    const response = await postChat(query, controller.signal);
    if (!response.ok) {
      showBotError(view, await describeHttpError(response));
      return;
    }
    const result = await readAnswer(response, view);
    finishBotMessage(view, result);
    turns.push({ query, ...result });
    saveTranscript();
  } catch (error) {
    if (error.name === "AbortError") showBotError(view, "Stopped. Ask again whenever you're ready.", "stopped");
    else if (error instanceof ChatError) showBotError(view, error.message);
    else showBotError(view, "Couldn't reach the assistant. Please check your connection and try again.");
  } finally {
    if (activeController === controller) {
      activeController = null;
      setBusy(false);
    }
    els.srStatus.textContent = "";
  }
}

/* ------------------------------------------------------------------ */
/* UI wiring                                                           */
/* ------------------------------------------------------------------ */

/**
 * Lock the composer while a reply is in flight. The textarea is made
 * read-only (rather than disabled) so keyboard focus is not lost and the
 * Stop button is one Tab away.
 */
function setBusy(busy) {
  els.input.readOnly = busy;
  els.input.setAttribute("aria-disabled", String(busy));
  els.send.disabled = busy;
  els.stop.hidden = !busy;
  els.messages.setAttribute("aria-busy", String(busy));
  if (!busy) els.input.focus();
}

function updateCounter() {
  const used = els.input.value.length;
  const max = config.max_query_chars;
  els.counter.textContent = `${used} / ${max} characters`;
  els.counter.classList.toggle("is-near-limit", used >= max * 0.9);
}

function submitQuery() {
  if (activeController) return; // a reply is already in flight
  const query = els.input.value.trim();
  if (!query) return;
  els.input.value = "";
  updateCounter();
  sendQuery(query.slice(0, config.max_query_chars));
}

function wireEvents() {
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuery();
  });

  els.input.addEventListener("keydown", (event) => {
    // Enter sends, Shift+Enter inserts a newline. isComposing protects IME
    // input (e.g. Tamil keyboards), where Enter confirms a character.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitQuery();
    }
  });

  els.input.addEventListener("input", updateCounter);

  els.stop.addEventListener("click", () => activeController?.abort());

  els.newChat.addEventListener("click", () => {
    activeController?.abort();
    turns = [];
    saveTranscript();
    renderConversation();
    els.input.focus();
  });

  // One delegated listener handles every [Source N] button.
  els.messages.addEventListener("click", (event) => {
    const button = event.target.closest(".cite-ref");
    if (button) highlightSource(button);
  });

  // The close button only makes sense inside the embed iframe. It asks the
  // parent page (embed.js) to hide us; the message carries no data.
  if (window.parent !== window) {
    els.close.hidden = false;
    els.close.addEventListener("click", () => window.parent.postMessage({ type: "nursingbot:close" }, "*"));
  }
}

async function init() {
  wireEvents();
  const loaded = await loadConfig();
  applyConfig();
  renderConversation();
  if (!loaded) {
    showBotError(
      createBotView(),
      "Couldn't load the assistant's settings. You can still try asking a question.",
    );
  }
}

init();
