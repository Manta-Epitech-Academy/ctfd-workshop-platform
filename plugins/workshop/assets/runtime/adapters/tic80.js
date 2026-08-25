/* WRP adapter for tic80-web-editor (docs/RUNTIME_PROTOCOL.md).
 *
 * Runs INSIDE the runtime frame, injected by the host (assets/runtime.js).
 * Living here rather than in the tic80-web-editor repository is deliberate:
 * the runtime stays "runtime-only" and keeps working standalone, and adding a
 * new runtime to the platform means writing one file like this one.
 *
 * tic80-web-editor needs almost nothing: the participant types code from the
 * subject, and the editor already autosaves the whole cart to localStorage
 * (`tic80-web-editor-cart`). So this adapter announces itself, reports the
 * storage keys the host may snapshot later, and handles focus.
 *
 * It does NOT report `result`: nothing in this runtime knows whether a PyPong
 * exercise is done, and a runtime may never solve a step anyway (rule 0).
 */
(function () {
  var PROTOCOL = 1;
  var RUNTIME = "tic80";
  // Owned by src/bridge/cartStorage.ts and src/layout/types.ts upstream.
  var STORAGE_KEYS = ["tic80-web-editor-cart", "tic80-web-editor-layout"];

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  function focusEditor() {
    var el = document.querySelector(".monaco-editor textarea");
    if (el) el.focus({ preventScroll: true });
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "focus") focusEditor();
    // `init` / `step` carry the active step. Nothing to do with them yet: the
    // PyPong subject has the participant type the code themselves, so there is
    // no per-step starter cart to load. When a subject ships carts, load them
    // here via the cart bridge.
  });

  // The React app mounts asynchronously; announce once it is actually up so
  // the host does not talk to an empty document.
  function announce() {
    send({ type: "ready", runtime: RUNTIME, protocol: PROTOCOL,
           capabilities: ["focus", "storage"], storageKeys: STORAGE_KEYS });
  }

  var root = document.getElementById("root");
  if (root && root.childElementCount > 0) {
    announce();
  } else {
    var done = false;
    var observer = new MutationObserver(function () {
      if (done || !root || root.childElementCount === 0) return;
      done = true;
      observer.disconnect();
      announce();
    });
    if (root) observer.observe(root, { childList: true });
    // Belt and braces: announce anyway so a layout change upstream cannot
    // silently break the handshake.
    setTimeout(function () {
      if (done) return;
      done = true;
      observer.disconnect();
      announce();
    }, 5000);
  }
})();
