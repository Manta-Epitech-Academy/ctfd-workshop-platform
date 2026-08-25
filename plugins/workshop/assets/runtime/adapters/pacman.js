/* WRP adapter for pacman-ghost-ai (docs/RUNTIME_PROTOCOL.md).
 *
 * Runs INSIDE the runtime frame, injected by the host (assets/runtime.js).
 * The pacman-ghost-ai repository is not modified — same rule as tic80.
 *
 * Beyond the handshake it reports the storage key: student code is Lua,
 * autosaved by js/workshop_loader.js to localStorage on CTFd's origin, which is
 * what lets the host snapshot it per user later (PLAN.md §14.6).
 *
 * It also hides the runtime's instructions pane. pacman-ghost-ai_runtime has no
 * such pane — the text lives in the subject repo now (§14 phase D) — so this is
 * inert against a current dist. It matters only for one built from the
 * pre-split pacman-ghost-ai, where the pane would otherwise show a second, and
 * eventually divergent, copy of the instructions.
 *
 * It does NOT report `result`. The game knows when the ghost behaves, so it
 * could — but student Lua runs in this page's JS context through Fengari
 * (js/workshop/lua_runtime.js), so a `result` here is reachable from student
 * code. Advisory-only is what makes that harmless (rule 0); reporting nothing
 * keeps it simpler still.
 */
(function () {
  var PROTOCOL = 1;
  var RUNTIME = "pacman";
  // Owned by js/workshop/config.js upstream (WORKSHOP_MODE.storageKey).
  var STORAGE_KEYS = ["mini_pacman_ghost_ai_lua_v1"];

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  // .column-left is `grid-template-rows: 1fr 1fr` (game, instructions), so
  // hiding the pane alone would leave the bottom half of the column empty.
  function hideInstructions() {
    var style = document.createElement("style");
    style.textContent =
      "#panel-instructions { display: none !important; }" +
      ".column-left { grid-template-rows: 1fr !important; }";
    document.head.appendChild(style);
  }

  function focusEditor() {
    var el = document.querySelector("#editor-host textarea");
    if (el) el.focus({ preventScroll: true });
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "focus") focusEditor();
    // `init` / `step` carry the active step. Nothing to do with them: this
    // subject has one Lua file that grows across every step, so there is no
    // per-step starter code to load.
  });

  function announce() {
    send({ type: "ready", runtime: RUNTIME, protocol: PROTOCOL,
           capabilities: ["focus", "storage"], storageKeys: STORAGE_KEYS });
  }

  hideInstructions();

  // Monaco is loaded by an AMD loader after boot(), so the frame is usable only
  // once the editor exists. Poll rather than observe: the editor host is
  // replaced wholesale, and a MutationObserver on it would miss that.
  var waited = 0;
  var timer = setInterval(function () {
    waited += 100;
    if (document.querySelector("#editor-host .monaco-editor") || waited >= 15000) {
      clearInterval(timer);
      announce();
    }
  }, 100);
})();
