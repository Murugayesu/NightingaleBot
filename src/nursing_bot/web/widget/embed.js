/*
 * embed.js: one-line loader for the NursingBot chat widget.
 *
 * The council pastes this into any page of its website:
 *   <script src="https://<host>/ui/widget/embed.js" async></script>
 *
 * Optional attributes on that script tag:
 *   data-position="right" | "left"   which bottom corner to use (default right)
 *   data-button-label="Ask us"       text on the launcher button
 *
 * It adds a fixed launcher button and, on first click, an iframe pointing at
 * <origin of this script>/ui/widget/chat.html. Clicking the button again (or
 * the close button inside the chat) hides the iframe.
 *
 * This file runs on the HOST page, under the host's CSP and CSS, so:
 *   - it is a classic script wrapped in a function (no globals leak);
 *   - every id and message type is prefixed with "nursingbot";
 *   - styles are set inline, starting from `all: initial` so the host
 *     page's CSS (e.g. a global `button {}` rule) can't restyle the widget.
 */
(function () {
  "use strict";

  var LAUNCHER_ID = "nursingbot-launcher";
  var FRAME_ID = "nursingbot-frame";
  var MOBILE_QUERY = "(max-width: 480px)";

  // Already loaded (e.g. the snippet was pasted twice): do nothing.
  if (document.getElementById(LAUNCHER_ID)) return;

  // Find our own <script> tag. document.currentScript works for async classic
  // scripts while they execute; the querySelector is a fallback.
  var script =
    document.currentScript ||
    document.querySelector('script[src*="/ui/widget/embed.js"]');
  if (!script || !script.src) return;

  var widgetOrigin = new URL(script.src, window.location.href).origin;
  var chatUrl = widgetOrigin + "/ui/widget/chat.html";
  var side = script.getAttribute("data-position") === "left" ? "left" : "right";
  var openLabel = script.getAttribute("data-button-label") || "Ask NursingBot";
  var closeLabel = "Close chat";

  var launcher = null;
  var frame = null; // created lazily on first open, so idle visitors cost nothing
  var isOpen = false;
  var mobile = window.matchMedia(MOBILE_QUERY);

  /** Build the launcher button with a small chat-bubble icon. */
  function createLauncher() {
    var button = document.createElement("button");
    button.id = LAUNCHER_ID;
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", FRAME_ID);
    button.style.cssText =
      "all: initial; position: fixed; bottom: 20px; " + side + ": 20px; z-index: 2147483000;" +
      "display: inline-flex; align-items: center; gap: 8px; box-sizing: border-box;" +
      "padding: 12px 18px; border-radius: 999px; background: #0f766e; color: #ffffff;" +
      "font: 600 15px/1.2 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;" +
      "box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25); cursor: pointer;";

    // Icon drawn with SVG DOM calls (no innerHTML needed).
    var svgNs = "http://www.w3.org/2000/svg";
    var icon = document.createElementNS(svgNs, "svg");
    icon.setAttribute("width", "20");
    icon.setAttribute("height", "20");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
    var path = document.createElementNS(svgNs, "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute("d", "M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z");
    icon.appendChild(path);

    var text = document.createElement("span");
    text.textContent = openLabel;

    button.appendChild(icon);
    button.appendChild(text);

    // Visible keyboard focus ring (host CSS was reset by `all: initial`).
    button.addEventListener("focus", function () {
      button.style.outline = "3px solid #f59e0b";
      button.style.outlineOffset = "2px";
    });
    button.addEventListener("blur", function () {
      button.style.outline = "none";
    });
    button.addEventListener("click", function () {
      setOpen(!isOpen);
    });
    return button;
  }

  /** Build the iframe that hosts chat.html. */
  function createFrame() {
    var iframe = document.createElement("iframe");
    iframe.id = FRAME_ID;
    iframe.src = chatUrl;
    iframe.title = "NursingBot chat";
    applyFrameLayout(iframe);
    return iframe;
  }

  /** Floating panel on desktop, full screen on phones. */
  function applyFrameLayout(iframe) {
    var base =
      "all: initial; position: fixed; z-index: 2147483001; border: 0; background: #ffffff;" +
      "box-sizing: border-box; display: " + (isOpen ? "block" : "none") + ";";
    if (mobile.matches) {
      iframe.style.cssText = base + "top: 0; left: 0; width: 100%; height: 100%; border-radius: 0;";
    } else {
      iframe.style.cssText =
        base +
        "bottom: 84px; " + side + ": 20px; width: 400px; height: 600px;" +
        "max-width: calc(100vw - 40px); max-height: calc(100vh - 104px);" +
        "border-radius: 12px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);";
    }
  }

  /** Show or hide the chat and keep the launcher in sync. */
  function setOpen(open) {
    isOpen = open;
    if (open && !frame) {
      frame = createFrame();
      document.body.appendChild(frame);
    }
    if (frame) applyFrameLayout(frame);

    launcher.setAttribute("aria-expanded", String(open));
    launcher.lastChild.textContent = open ? closeLabel : openLabel;
    // On phones the chat covers the screen; its own close button takes over.
    launcher.style.display = open && mobile.matches ? "none" : "inline-flex";

    if (open) frame.focus();
    else launcher.focus();
  }

  /** The chat page asks to be closed via postMessage. Accept it only from our iframe. */
  function onMessage(event) {
    if (!frame || event.source !== frame.contentWindow || event.origin !== widgetOrigin) return;
    if (event.data && event.data.type === "nursingbot:close") setOpen(false);
  }

  function mount() {
    launcher = createLauncher();
    document.body.appendChild(launcher);
    window.addEventListener("message", onMessage);
    // Re-layout when crossing the phone breakpoint (e.g. rotating a tablet).
    mobile.addEventListener("change", function () {
      if (frame) applyFrameLayout(frame);
      launcher.style.display = isOpen && mobile.matches ? "none" : "inline-flex";
    });
  }

  // `async` scripts can run before <body> exists.
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
