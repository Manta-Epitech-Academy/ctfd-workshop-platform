/* WRP adapter for the v86 runtime — an Alpine guest in the browser.
 * (docs/RUNTIME_PROTOCOL.md, PLAN.md §18.)
 *
 * Runs INSIDE the runtime frame, injected by the host (assets/runtime.js), so
 * the shell-rpg and v86-runner repositories stay untouched and keep working
 * standalone. The dist is built from the shell-rpg product, which is v86-runner
 * plus a welcome screen.
 *
 * This runtime needs very little from the protocol:
 *
 * - It announces `ready` once the terminal exists, so the host knows the pane
 *   is usable and stops showing it as loading.
 * - It handles `focus` by putting the caret in the terminal, which matters
 *   more here than anywhere else: everything the participant does is typing.
 * - It reports **no** storage keys. The runner only keeps debug flags in
 *   localStorage; the participant's actual work lives in the guest's 9p host
 *   share, which is in-memory and outside a v86 snapshot. Persisting that is a
 *   different collector for the same table (PLAN.md §16, §18.8) and is not
 *   built yet — claiming keys here would make the host save nothing, slowly.
 * - It never reports `result`. Nothing in a shell knows whether an exercise is
 *   done, and a runtime may not solve a step anyway (rule 0).
 *
 * The VM image itself is not the platform's business: the app fetches its
 * ~330 MB bundle from a CDN, a mirror, or the mirroring cache in
 * compose/mirror, and the participant picks it in the welcome screen.
 */
(function () {
  var PROTOCOL = 1;
  // One dist serves every v86 subject (Shell RPG, Shell 1, …); which machine
  // to boot arrives in `init.params`.
  var RUNTIME = "v86";

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  // xterm keeps a hidden textarea; focusing the container does nothing.
  function focusTerminal() {
    var el = document.querySelector(".xterm-helper-textarea")
          || document.querySelector(".xterm textarea")
          || document.querySelector("canvas");
    if (el && el.focus) el.focus({ preventScroll: true });
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "focus") focusTerminal();
    if (msg.type === "init" && msg.params) applyParams(msg.params);
    // `init` / `step` carry the active step. Nothing to do with them: the
    // guest is one long-lived machine, not a per-step workspace.
  });

  // Which VM to offer. One dist serves every v86 subject, and the subject says
  // which bundle belongs to it (PLAN.md §19.2), so the welcome screen's
  // download link is pointed here rather than baked in at build time.
  function applyParams(params) {
    var link = document.getElementById("official-bundle-download");
    if (link && params.bundle_url) {
      link.href = params.bundle_url;
      if (params.bundle_name) link.setAttribute("download", params.bundle_name);
    }
  }

  function announce() {
    send({ type: "ready", runtime: RUNTIME, protocol: PROTOCOL,
           capabilities: ["focus"] });
  }

  // The terminal appears only once the participant has supplied the bundle, so
  // announcing on its presence alone would leave the pane "loading" through
  // the whole welcome screen. Announce as soon as the app has painted
  // anything, and let the welcome screen be part of the runtime.
  function ready() {
    return document.querySelector(".xterm")
        || document.querySelector("[class*='welcome']")
        || (document.body && document.body.childElementCount > 0);
  }

  if (ready()) {
    announce();
  } else {
    var done = false;
    var finish = function () {
      if (done) return;
      done = true;
      observer.disconnect();
      announce();
    };
    var observer = new MutationObserver(function () { if (ready()) finish(); });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    // Belt and braces: a layout change upstream must not silently break the
    // handshake and leave the pane looking broken.
    setTimeout(finish, 5000);
  }
})();
