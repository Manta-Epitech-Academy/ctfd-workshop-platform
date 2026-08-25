/* WRP adapter for MiniASM / WDR+E (docs/RUNTIME_PROTOCOL.md, PLAN.md §21).
 *
 * Runs INSIDE the runtime frame, injected by the host (assets/runtime.js).
 *
 * This is the first runtime that knows whether a step is done: every exercise
 * carries input/expected pairs and the app runs them. So this adapter finally
 * uses the advisory `result` message the protocol has specified since §14.6 —
 * and uses it as specified, which is to say **advisory**. The app declares a
 * pass, the host highlights the step, the participant still submits.
 *
 * Two jobs beyond the handshake:
 *
 * 1. **Hand the app the session's token secret.** The port reveals a
 *    completion token on a passing run, derived from that secret and the
 *    exercise id, and the platform computes the same value as the flag. With
 *    no secret the app is exactly upstream and reveals nothing.
 * 2. **Watch progress.** `localStorage['miniasm-progress']` is `{completed:
 *    [ids]}`, written when the tests pass. Same origin, so this needs no change
 *    in the app: poll it, and report ids as they appear.
 *
 * Storage keys are reported so §16 keeps the participant's work: progress and
 * the per-exercise source the editor saves.
 */
(function () {
  var PROTOCOL = 1;
  var RUNTIME = "miniasm";
  var PROGRESS_KEY = "miniasm-progress";
  var POLL_MS = 2000;
  var params = {};

  // Open the exercise the participant is reading. The host names the step by
  // challenge id; the sync wrote which runtime exercise that is, because only
  // it knows both (PLAN.md §21).
  function followStep(step) {
    if (!step || !window.MiniASMEmbed) return;
    var map = params.exercises || {};
    var exercise = map[String(step.id)];
    if (exercise === undefined || exercise === null) return;
    window.MiniASMEmbed.selectExercise(Number(exercise));
  }

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  function completedIds() {
    try {
      var raw = localStorage.getItem(PROGRESS_KEY);
      var data = raw ? JSON.parse(raw) : null;
      return (data && data.completed) || [];
    } catch (e) {
      return [];
    }
  }

  // Every key the app writes: progress, plus one source blob per exercise. The
  // exercise keys are not enumerable up front, so take whatever is there —
  // this runs same-origin, so `localStorage` is the app's own.
  function storageKeys() {
    var keys = [PROGRESS_KEY];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key && key.indexOf("miniasm") === 0 && keys.indexOf(key) < 0) {
          keys.push(key);
        }
      }
    } catch (e) { /* private mode */ }
    return keys;
  }

  function focusEditor() {
    var el = document.querySelector(".monaco-editor textarea")
          || document.querySelector("textarea");
    if (el) el.focus({ preventScroll: true });
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "focus") focusEditor();
    if (msg.type === "init" && msg.params) {
      params = msg.params;
      // Before the participant can pass anything, so the first success already
      // has a token to show.
      if (params.token_secret) window.MiniASMTokenSecret = params.token_secret;
      // The platform owns the statement, the language and the exercise list;
      // the app hides its own controls for those and follows.
      if (window.MiniASMEmbed) {
        window.MiniASMEmbed.enable({ language: params.language || "fr" });
      }
    }
    if (msg.type === "init" || msg.type === "step") followStep(msg.step);
  });

  // The app writes progress on a passing run; nothing else does. Comparing
  // against what we last saw turns that into "exercise N just passed", which
  // is what the host wants to hear about.
  function watchProgress() {
    var seen = completedIds().slice();
    setInterval(function () {
      var now = completedIds();
      for (var i = 0; i < now.length; i++) {
        if (seen.indexOf(now[i]) >= 0) continue;
        seen.push(now[i]);
        // Advisory, always: the host may highlight the step and light up the
        // submit control, and it may never solve anything on this word.
        send({ type: "result", ok: true, exercise: now[i],
               detail: "MiniASM says the tests pass — submit the token it shows." });
      }
    }, POLL_MS);
  }

  function announce() {
    send({ type: "ready", runtime: RUNTIME, protocol: PROTOCOL,
           capabilities: ["focus", "result", "storage"],
           storageKeys: storageKeys() });
    watchProgress();
  }

  if (document.getElementById("test-results")) {
    announce();
  } else {
    var done = false;
    var finish = function () {
      if (done) return;
      done = true;
      observer.disconnect();
      announce();
    };
    var observer = new MutationObserver(function () {
      if (document.getElementById("test-results")) finish();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(finish, 5000);
  }
})();
