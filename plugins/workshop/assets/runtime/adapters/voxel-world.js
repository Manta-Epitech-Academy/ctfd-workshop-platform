/* WRP adapter for World Generator (voxel-world_runtime, docs/RUNTIME_PROTOCOL.md).
 *
 * Runs INSIDE the runtime frame, injected by the host (assets/runtime.js).
 *
 * The runtime grades its own steps: every step has a list of checks
 * (« défis ») run on each « ▶ Générer », and when all of them are green the
 * runtime's embed.js shows a token derived from this instance's secret. The
 * sync computed the same value and set it as the step's flag (TOKEN_SCHEMES in
 * tools/sync_subject.py). So this adapter does what miniasm's does:
 *
 * 1. hand the runtime the secret, and switch it to platform mode, which hides
 *    its own copy of the workshop text, hints and step list;
 * 2. follow the step the participant is reading: the sync wrote which runtime
 *    step each challenge is (`params.exercises`, challenge id -> "1".."7");
 * 3. when the runtime reveals a token, `propose` it to that step's answer
 *    field. Advisory as always (rule 0): the participant still submits.
 *
 * Student Lua runs in wasmoon with no access to JavaScript, so it can neither
 * read the secret nor emit these events.
 */
(function () {
  var PROTOCOL = 1;
  var RUNTIME = "voxel-world";
  // The app's own autosave (`Gu` in the bundle prefixes every key with mwg.).
  // `mwg.etape` is left out: which step is shown is the host's call.
  var STORAGE_KEYS = ["mwg.code", "mwg.graine", "mwg.reussites", "mwg.console",
                      "mwg.tailles.monde-sujet"];
  var params = {};

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  function exercises() { return params.exercises || {}; }

  function challengeFor(step) {
    var map = exercises();
    for (var id in map) {
      if (String(map[id]) === String(step)) return parseInt(id, 10);
    }
    return null;
  }

  function followStep(step) {
    if (!step || !window.WorldGenerator) return;
    var n = parseInt(exercises()[String(step.id)], 10);
    if (n >= 1 && n !== window.WorldGenerator.etape() + 1) {
      window.WorldGenerator.allerA(n - 1);
    }
  }

  function focusEditor() {
    var el = document.querySelector("#editeur textarea");
    if (el) el.focus({ preventScroll: true });
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "focus") focusEditor();
    if (msg.type === "init" && msg.params) {
      params = msg.params;
      if (window.VoxelWorld) {
        window.VoxelWorld.setSecret(params.token_secret || null);
        window.VoxelWorld.enable();
      }
    }
    if (msg.type === "init" || msg.type === "step") followStep(msg.step);
  });

  window.addEventListener("wg:jeton", function (ev) {
    var d = ev.detail || {};
    var id = challengeFor(d.etape);
    if (id === null || typeof d.jeton !== "string") return;
    send({ type: "propose", step: { id: id }, submission: d.jeton });
  });

  function announce() {
    send({ type: "ready", runtime: RUNTIME, protocol: PROTOCOL,
           capabilities: ["focus", "result", "propose", "storage"],
           storageKeys: STORAGE_KEYS });
  }

  // The app builds the editor synchronously in its inline script, so by the
  // time the editor exists the step API and embed.js are there too.
  var waited = 0;
  var timer = setInterval(function () {
    waited += 100;
    if ((window.WorldGenerator && document.querySelector("#editeur .monaco-editor"))
        || waited >= 15000) {
      clearInterval(timer);
      announce();
    }
  }, 100);
})();
