/* « Reset my progress », in the account menu (PLAN.md §47).
 *
 * The entry is only rendered for an admin (templates/navbar.html) and the
 * endpoint refuses anybody else, so this file is the confirmation and nothing
 * more, plus the one half of the reset the server cannot do: the runtime's
 * code is also in this browser's localStorage (assets/runtime.js), and the
 * page would post it straight back.
 */
(function () {
  // The names runtime.js keeps its own bookkeeping under. Removing the owner
  // is what makes the next load treat this browser as holding nobody's work.
  var LS_KEYS = "ws-workspace-keys";
  var BOOKKEEPING = ["ws-workspace-owner", "ws-workspace-local"];
  // Heard by assets/runtime.js in every window of this browser, this one
  // included. See the listener there.
  var RESET_CHANNEL = "ws-reset";

  function tellRuntimes() {
    if (typeof BroadcastChannel !== "function") return;
    var channel = new BroadcastChannel(RESET_CHANNEL);
    channel.postMessage({ k: "reset" });
    channel.close();
  }

  function wipeRuntime(serverKeys) {
    // The frame first: a runtime may write its state when it is torn down, and
    // that has to happen before the keys are removed, not after.
    Array.prototype.forEach.call(
      document.querySelectorAll("iframe.ws-runtime-frame"),
      function (frame) { frame.remove(); });
    var keys = (serverKeys || []).slice();
    try {
      JSON.parse(localStorage.getItem(LS_KEYS) || "[]").forEach(function (k) {
        if (typeof k === "string" && keys.indexOf(k) < 0) keys.push(k);
      });
    } catch (e) { /* no cached list, the server's is enough */ }
    keys.concat(BOOKKEEPING).forEach(function (k) {
      try { localStorage.removeItem(k); } catch (e) { /* private mode */ }
    });
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("[data-ws-reset]");
    if (!link) return;
    event.preventDefault();
    if (!window.confirm(link.getAttribute("data-confirm"))) return;
    var nonce = (window.init && window.init.csrfNonce) || "";
    // Before the request: a runtime open in its own tab must have stopped
    // saving by the time the server erases what it saved.
    tellRuntimes();
    fetch("/api/v1/workshop/progress/reset", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "CSRF-Token": nonce },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (body) {
      wipeRuntime(body && body.data && body.data.runtime_keys);
      // Back to the start of the subject, which is where a reset leaves you.
      window.location.href = link.getAttribute("href");
    }).catch(function () {
      window.alert(link.getAttribute("data-failed"));
    });
  });
})();
