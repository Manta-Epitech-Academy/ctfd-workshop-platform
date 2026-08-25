/* Host side of the workshop runtime pane (PLAN.md §14, docs/RUNTIME_PROTOCOL.md).
 *
 * Shows an existing standalone web app (the "runtime") beside the workshop
 * instructions, and speaks a small postMessage protocol to it.
 *
 * Three rules from the design, visible in the code below:
 *   0. `result` is ADVISORY — nothing here ever submits on the runtime's word.
 *      Student code shares the page context in some runtimes, so an
 *      auto-submit would be forgeable.
 *   1. The runtime never calls the CTFd API; it asks the host.
 *   2. ONE iframe for the whole workshop, mounted lazily and kept alive —
 *      never one per step (booting a VM per accordion step is unacceptable).
 * All strings English (CLAUDE.md); i18n later.
 */
(function () {
  var PROTOCOL = 1;
  var LS_OPEN = "ws-runtime-open";
  var LS_WIDTH = "ws-runtime-width";

  var pane, cfg, frame = null, ready = false;
  var handle = null;

  /* ---------- work in progress, kept server-side (PLAN.md §16) ----------
   *
   * The frame is same-origin, so its localStorage IS this page's localStorage.
   * The host therefore reads and writes the runtime's own keys directly: no
   * message, no cooperation, nothing added to the runtime's repository.
   * Without this, work lives in the browser — two students sharing a
   * classroom PC share a cart.
   */
  var WS_API = "/api/v1/workshop/workspace";
  var LS_OWNER = "ws-workspace-owner";   // "<user id>:<runtime>" — whose bucket this is
  var LS_LOCAL = "ws-workspace-local";   // epoch ms of the last local change seen
  var LS_KEYS = "ws-workspace-keys";     // last known key list, for the shared-machine wipe
  var POLL_MS = 5000;
  var MIN_SAVE_MS = 15000;

  var watched = [];          // keys the runtime declared in its `ready`
  var snapshotPromise = null;
  var restoreDone = null;    // resolves once the local bucket has been reconciled
  var restored = false;      // NOTHING may be saved before that: an empty bucket
                             // must never overwrite a good snapshot
  var lastPrint = null, lastSaved = 0, saving = false, giveUp = false;

  function ls(name) {
    try { return localStorage.getItem(name); } catch (e) { return null; }
  }

  function lsSet(name, value) {
    try { localStorage.setItem(name, value); } catch (e) { /* private mode, quota */ }
  }

  function owner() {
    var id = (window.init && window.init.userId) || 0;
    return id + ":" + (cfg ? cfg.id : "");
  }

  function knownKeys() {
    // The union of what the runtime told us, what we cached locally, and what
    // the server holds. The cache is what lets us wipe a *previous* student's
    // work when the server has nothing to restore.
    var out = watched.slice();
    var cached = ls(LS_KEYS);
    if (cached) {
      try {
        JSON.parse(cached).forEach(function (k) {
          if (out.indexOf(k) < 0) out.push(k);
        });
      } catch (e) { /* corrupt cache, ignore */ }
    }
    return out;
  }

  function collect(keys) {
    var out = {};
    keys.forEach(function (k) {
      var v = ls(k);
      if (v !== null) out[k] = v;
    });
    return out;
  }

  function fetchSnapshot() {
    snapshotPromise = fetch(WS_API, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (body) { return (body && body.data) || null; })
      .catch(function () { return null; });
    return snapshotPromise;
  }

  // Which copy wins — the table in PLAN.md §16.2. Runs BEFORE the frame is
  // created, because a runtime reads its storage once, at boot.
  function reconcile(snapshot) {
    var mine = ls(LS_OWNER) === owner();
    var serverKeys = (snapshot && snapshot.keys) || {};
    var serverAt = snapshot && snapshot.updated ? Date.parse(snapshot.updated) : 0;
    var localAt = parseInt(ls(LS_LOCAL), 10) || 0;

    if (mine && (!serverAt || localAt >= serverAt)) {
      // Unsaved work on this machine outranks a stale snapshot.
      return;
    }
    if (!mine) {
      // Somebody else's browser bucket (or nobody's). Wipe what they left
      // behind, even when the server has nothing to put in its place — that
      // second half is what actually fixes the shared classroom PC.
      knownKeys().forEach(function (k) {
        if (!(k in serverKeys)) {
          try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
        }
      });
    }
    Object.keys(serverKeys).forEach(function (k) { lsSet(k, serverKeys[k]); });
    lsSet(LS_OWNER, owner());
    lsSet(LS_LOCAL, String(serverAt || Date.now()));
  }

  function restore() {
    if (restoreDone) return restoreDone;
    restoreDone = (snapshotPromise || fetchSnapshot()).then(function (snapshot) {
      try { reconcile(snapshot); } catch (e) {
        console.warn("workshop: could not restore your work in progress", e);
      }
      lastPrint = JSON.stringify(collect(knownKeys()));
      restored = true;
    });
    return restoreDone;
  }

  function save(keepalive) {
    if (!restored || giveUp || saving || !watched.length) return;
    var keys = collect(watched);
    saving = true;
    lastSaved = Date.now();
    fetch(WS_API, {
      method: "POST",
      credentials: "same-origin",
      keepalive: !!keepalive,
      headers: {
        "Content-Type": "application/json",
        "CSRF-Token": (window.init && window.init.csrfNonce) || "",
      },
      body: JSON.stringify({ runtime: cfg.id, keys: keys }),
    }).then(function (r) {
      saving = false;
      if (r.status === 413) {
        // Too big to store. Retrying forever would just hammer the server.
        giveUp = true;
        console.warn("workshop: your work is too large to save on the server");
        return;
      }
      if (!r.ok) return;              // transient: the next tick tries again
      lastPrint = JSON.stringify(keys);
      lsSet(LS_OWNER, owner());
      lsSet(LS_LOCAL, String(Date.now()));
    }).catch(function () { saving = false; });
  }

  // A poll rather than a message: localStorage fires no event in the document
  // that wrote it, and the whole point is to need nothing from the runtime.
  function watchStorage() {
    setInterval(function () {
      if (!restored || !watched.length) return;
      var print = JSON.stringify(collect(watched));
      if (print === lastPrint) return;
      lsSet(LS_LOCAL, String(Date.now()));
      if (Date.now() - lastSaved >= MIN_SAVE_MS) save(false);
    }, POLL_MS);

    function flush() {
      if (!restored || !watched.length) return;
      if (JSON.stringify(collect(watched)) === lastPrint) return;
      save(true);
    }
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") flush();
    });
  }

  function post(msg) {
    if (!frame || !frame.contentWindow) return;
    msg.wrp = PROTOCOL;
    frame.contentWindow.postMessage(msg, window.location.origin);
  }

  /* ---------- which step the participant is on ---------- */

  var lastOpened = null;

  function activeStep() {
    // The step they are working on. Several can be open at once, and document
    // order is the wrong answer then — the one they opened LAST is the one
    // they are looking at.
    if (lastOpened && lastOpened.open && lastOpened.isConnected) return lastOpened;
    return document.querySelector('.ws-step[data-challenge-id][open]')
        || document.querySelector('.ws-step[data-state="current"]');
  }

  function stepPayload() {
    var el = activeStep();
    if (!el) return null;
    var name = el.querySelector(".ws-step-name");
    return {
      id: parseInt(el.dataset.challengeId, 10),
      name: name ? name.textContent.trim() : "",
      state: el.dataset.state,
    };
  }

  function sendStep(type) {
    var step = stepPayload();
    if (step) post({ type: type || "step", step: step });
  }

  /* ---------- advisory signals from the runtime ---------- */

  function stepElement(id) {
    return document.querySelector('.ws-step[data-challenge-id="' + id + '"]');
  }

  // The runtime believes a step is done. This is a HINT: it highlights the
  // step, it never solves it. Solving stays the participant's action.
  function onResult(msg) {
    var el = stepElement(msg.step && msg.step.id) || activeStep();
    if (!el) return;
    el.classList.toggle("ws-runtime-ok", msg.ok !== false);
    var note = el.querySelector(".ws-runtime-hint");
    if (!note) {
      note = document.createElement("div");
      note.className = "ws-runtime-hint";
      var form = el.querySelector(".ws-form");
      if (form) form.insertBefore(note, form.firstChild);
    }
    note.textContent = msg.ok === false
      ? (msg.detail || "The runtime says this is not done yet.")
      : (msg.detail || "The runtime says this looks done — submit when you agree.");
  }

  // The runtime produced something answer-shaped (a flag its guest printed,
  // for instance). Fill it in; the participant still presses Submit.
  function onPropose(msg) {
    var el = stepElement(msg.step && msg.step.id) || activeStep();
    var input = el && el.querySelector(".ws-answer");
    if (!input || typeof msg.submission !== "string") return;
    input.value = msg.submission;
    input.focus();
    onResult({ step: msg.step, ok: true,
               detail: "The runtime filled in an answer — check it, then submit." });
  }

  function onMessage(ev) {
    if (!frame || ev.source !== frame.contentWindow) return;
    if (ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    switch (msg.type) {
      case "ready":
        ready = true;
        pane.dataset.runtimeReady = "1";
        // The runtime names its own storage keys, so the host stays blind to
        // every runtime's schema (docs/RUNTIME_PROTOCOL.md §2).
        if (Array.isArray(msg.storageKeys)) {
          watched = msg.storageKeys.filter(function (k) { return typeof k === "string"; });
          lsSet(LS_KEYS, JSON.stringify(watched));
          lastPrint = JSON.stringify(collect(watched));
        }
        post({ type: "init", protocol: PROTOCOL, runtime: cfg.id,
               params: cfg.params, step: stepPayload() });
        break;
      case "result":
        onResult(msg);
        break;
      case "propose":
        onPropose(msg);
        break;
    }
  }

  /* ---------- pane ---------- */

  var mounting = false;

  // Restore first, create the frame second: a runtime reads its storage once,
  // at boot, so putting the participant's work back afterwards would be too
  // late (PLAN.md §16.2).
  function mount() {
    if (frame || mounting) return;
    mounting = true;
    restore().then(createFrame, createFrame);
  }

  function createFrame() {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.className = "ws-runtime-frame";
    frame.title = cfg.title;
    // Same-origin, so no sandbox: sandboxing would need allow-same-origin to
    // keep localStorage working, which defeats the point. The rule that makes
    // this safe is "only embed runtimes we control" (PLAN.md §14.2).
    frame.setAttribute("allow", "clipboard-read; clipboard-write; fullscreen; gamepad");
    frame.src = cfg.src;
    frame.addEventListener("load", injectAdapter);
    pane.querySelector(".ws-runtime-body").appendChild(frame);
  }

  // The adapter is host-side and injected into the frame: a runtime's own
  // repository stays untouched, and wiring a new runtime in means writing one
  // small file here. Only possible because the frame is same-origin.
  function injectAdapter() {
    try {
      var doc = frame.contentDocument;
      if (!doc || doc.querySelector("[data-wrp-adapter]")) return;
      var s = doc.createElement("script");
      s.src = cfg.adapter;
      s.setAttribute("data-wrp-adapter", cfg.id);
      (doc.body || doc.documentElement).appendChild(s);
    } catch (e) {
      // A runtime served from another origin cannot be injected into; it must
      // ship the adapter itself. Nothing else breaks.
      console.warn("workshop: could not inject the runtime adapter", e);
    }
  }

  function setWidth(pct) {
    pct = Math.min(80, Math.max(20, pct));
    document.documentElement.style.setProperty("--ws-pane-width", pct + "%");
    try { localStorage.setItem(LS_WIDTH, String(pct)); } catch (e) { /* private mode */ }
    return pct;
  }

  // The handle is offered whenever the pane is closed; while it is open the
  // pane's own close button is the control.
  function updateHandle() {
    if (handle) handle.hidden = !pane.hidden;
  }

  function setOpen(open) {
    pane.hidden = !open;
    document.body.classList.toggle("ws-has-pane", open);
    document.querySelectorAll(".ws-runtime-toggle").forEach(function (b) {
      b.setAttribute("aria-expanded", String(open));
      b.classList.toggle("active", open);
    });
    try { localStorage.setItem(LS_OPEN, open ? "1" : "0"); } catch (e) { /* private mode */ }
    updateHandle();
    if (open) {
      mount();
      if (ready) sendStep("step");
    } else if (restored && watched.length) {
      // Closing the pane usually means "done with this for now" — a good
      // moment to make sure the server has what the editor holds.
      save(false);
    }
  }

  function initResizer() {
    var grip = pane.querySelector(".ws-runtime-resizer");
    if (!grip) return;
    var dragging = false;
    grip.addEventListener("mousedown", function (ev) {
      dragging = true;
      // The iframe swallows mousemove once the pointer crosses into it.
      pane.classList.add("ws-resizing");
      ev.preventDefault();
    });
    window.addEventListener("mousemove", function (ev) {
      if (!dragging) return;
      setWidth(((window.innerWidth - ev.clientX) / window.innerWidth) * 100);
    });
    window.addEventListener("mouseup", function () {
      dragging = false;
      pane.classList.remove("ws-resizing");
    });
    grip.addEventListener("keydown", function (ev) {
      var cur = parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue("--ws-pane-width")) || cfg.size;
      if (ev.key === "ArrowLeft") setWidth(cur + 5);
      else if (ev.key === "ArrowRight") setWidth(cur - 5);
    });
  }

  function start() {
    pane = document.querySelector("#ws-runtime");
    if (!pane) return;
    cfg = {
      id: pane.dataset.runtimeId,
      src: pane.dataset.runtimeSrc,
      adapter: pane.dataset.runtimeAdapter,
      title: pane.dataset.runtimeTitle,
      size: parseInt(pane.dataset.runtimeSize, 10) || 55,
      // Subject-specific data for the runtime (which VM bundle, typically).
      // One dist can serve several subjects; this is what differs.
      params: (function () {
        try { return JSON.parse(pane.dataset.runtimeParams || "{}"); }
        catch (e) { return {}; }
      })(),
    };

    var stored = null;
    try {
      stored = localStorage.getItem(LS_OPEN);
      setWidth(parseFloat(localStorage.getItem(LS_WIDTH)) || cfg.size);
    } catch (e) {
      setWidth(cfg.size);
    }

    document.querySelectorAll(".ws-runtime-toggle").forEach(function (b) {
      b.addEventListener("click", function () { setOpen(pane.hidden); });
    });

    handle = document.querySelector(".ws-runtime-handle");
    var close = pane.querySelector(".ws-runtime-close");
    if (close) close.addEventListener("click", function () { setOpen(false); });

    initResizer();
    // Fetch the participant's snapshot right away, so opening the pane does
    // not wait on a round trip.
    fetchSnapshot();
    watchStorage();
    window.addEventListener("message", onMessage);
    // Step changes: the participant opened another step, or solved one.
    document.addEventListener("toggle", function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains("ws-step")) return;
      if (!ev.target.open || !ev.target.dataset.challengeId) return;
      lastOpened = ev.target;
      sendStep("step");
    }, true);
    window.addEventListener("ws:steps-changed", function () { sendStep("step"); });

    setOpen(stored === null ? pane.dataset.runtimeOpen === "1" : stored === "1");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
