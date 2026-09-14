/* Host side of the workshop runtime (PLAN.md §14, §30, docs/RUNTIME_PROTOCOL.md).
 *
 * Shows an existing standalone web app (the "runtime") to a participant and
 * speaks a small postMessage protocol to it.
 *
 * Three rules from the design, visible in the code below:
 *   0. `result` is ADVISORY — nothing here ever submits on the runtime's word.
 *      Student code shares the page context in some runtimes, so an
 *      auto-submit would be forgeable.
 *   1. The runtime never calls the CTFd API; it asks the host.
 *   2. ONE iframe for the whole workshop, mounted lazily and kept alive —
 *      never one per step (booting a VM per accordion step is unacceptable).
 *
 * TWO PRESENTATIONS, ONE HOST.
 * ----------------------------------------------------------------------------
 * `split`  — a pane beside the subject. The default on a wide screen.
 * `window` — a tab of its own (templates/workshop_runtime.html). The default
 *            below PANE_MIN_WIDTH, because half of a small laptop's viewport is
 *            not enough room to work in.
 *
 * This file runs in three situations, and the difference between them is two
 * booleans, not two implementations:
 *
 *                     ownsFrame()  ownsPage()
 *   subject page, split    yes         yes
 *   subject page, window   no          yes
 *   the popped-out tab     yes         no
 *
 * `ownsFrame` is the protocol, the iframe and the work-in-progress snapshot;
 * exactly one window has it, which is what keeps §16's restore-before-boot
 * ordering true with no cross-window sequencing — whichever window creates the
 * frame is the one that restored first, and the only one that saves.
 * `ownsPage` is the step list, the advisory hint and the launcher.
 *
 * When they are not the same window they talk over a BroadcastChannel: `step`
 * down, `result`/`propose`/`mode` up. The channel rather than `window.opener`
 * because it survives a reload on either side and a subject page re-opened in
 * another tab, with no handle to keep. It is origin-scoped, and an instance
 * declares one runtime, so the channel needs no further namespacing.
 *
 * Nothing about that gives the runtime any new authority: `result` and
 * `propose` cross the channel and are still applied by the page exactly as
 * they were in one document, so rule 0 holds across two windows.
 */
(function () {
  var PROTOCOL = 1;
  var LS_OPEN = "ws-runtime-open";
  var LS_WIDTH = "ws-runtime-width";
  var LS_MODE = "ws-runtime-mode";

  var MODE_SPLIT = "split";
  var MODE_WINDOW = "window";
  // Below this, a split is two columns too narrow to use and the pop-out is the
  // default instead. Must match the `max-width` in workshop.css's
  // "Narrow screens" rule, which is the fallback for somebody who chooses the
  // split here anyway.
  var PANE_MIN_WIDTH = 900;
  // One named tab, reused. Re-opening a live runtime would reboot an editor's
  // unsaved buffer or a 300 MB VM, so the name is what makes a second click a
  // focus instead.
  var WINDOW_NAME = "ws-runtime";
  var CHANNEL = "ws-runtime";

  var pane, cfg, frame = null, ready = false;
  var handle = null, cta = null;
  var mode = MODE_SPLIT;
  var channel = null;
  var popup = null;          // the popped-out tab, while this page opened it
  var popupLive = false;     // ...or while one announced itself on the channel
  var remoteStep = null;     // the active step, when this window has no DOM

  /* ---------- who this window is ---------- */

  function ownsFrame() {
    return cfg.role === "window" || mode === MODE_SPLIT;
  }

  function ownsPage() {
    return cfg.role === "pane";
  }

  function canPopOut() {
    // No route to pop out to: a subject synced before per-document routes
    // existed has no `<doc_slug>` and therefore no host page (page.py
    // `workshop_runtime`). The split is then the only presentation, and no
    // control claims otherwise.
    return !!cfg.windowUrl;
  }

  /* ---------- the two windows talk ---------- */

  function send(msg) {
    if (channel) channel.postMessage(msg);
  }

  function onChannel(ev) {
    var msg = ev.data;
    if (!msg || !msg.k) return;
    if (ownsPage()) {
      switch (msg.k) {
        case "who":
          // Only a page asks, and only a tab answers; a page hearing this is
          // another page and has nothing to say.
          break;
        case "hello":
          // A tab is alive. It has no step list of its own, so answer with the
          // one the participant is on; it asks again whenever it reloads.
          //
          // A live tab outranks this page's stored preference, because it IS
          // the runtime right now and two frames on one origin would be two
          // editors writing one cart. The rare cost is a wasted boot: a page
          // that loaded in split mode may have started mounting before this
          // arrived, and `dropFrame` then saves that frame's work and throws it
          // away. Correct either way, and only reachable when a tab the browser
          // refused to close is still open.
          popupLive = true;
          setMode(MODE_WINDOW, { silent: true });
          updateLauncher();
          send({ k: "step", step: stepPayload() });
          break;
        case "bye":
          popupLive = false;
          popup = null;
          updateLauncher();
          break;
        case "result":
          onResult(msg.msg);
          break;
        case "propose":
          onPropose(msg.msg);
          break;
        case "split":
          popupLive = false;
          popup = null;
          setMode(MODE_SPLIT);
          setOpen(true);
          break;
      }
    } else if (msg.k === "step") {
      remoteStep = msg.step || null;
      if (ready) sendStep("step");
    } else if (msg.k === "who") {
      // A subject page loaded (or reloaded) while this tab was already open. It
      // cannot have heard the `hello` sent when this tab started, so say it
      // again — otherwise the page offers to open a runtime that is already
      // running two windows away.
      send({ k: "hello" });
    }
  }

  function openChannel() {
    if (typeof BroadcastChannel !== "function") return;
    channel = new BroadcastChannel(CHANNEL);
    channel.addEventListener("message", onChannel);
  }

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
    // Only the window holding the frame saves. After a switch to the popped-out
    // tab this page still knows the keys, and two windows POSTing the same
    // bucket would race each other for no gain.
    if (!ownsFrame()) return;
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
      if (!ownsFrame() || !restored || !watched.length) return;
      var print = JSON.stringify(collect(watched));
      if (print === lastPrint) return;
      lsSet(LS_LOCAL, String(Date.now()));
      if (Date.now() - lastSaved >= MIN_SAVE_MS) save(false);
    }, POLL_MS);

    function flush() {
      if (!ownsFrame() || !restored || !watched.length) return;
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
    // The step they are working on. Several can be open at once, so the one
    // they opened LAST is the one they are looking at — but only while it is
    // still something to work on. A step they have just SOLVED is never the
    // answer, and it used to be: `<details>` fires no `toggle` when it was
    // already open, and page.py's `_open_states` renders a locked step open
    // when its part has nothing else to show, so `refresh()`'s `next.open =
    // true` on the freshly unlocked step changed nothing and `lastOpened`
    // stayed on the step just finished. An advisory `result` then landed on the
    // solved card, where there is no form to put it in, and the runtime was
    // told the wrong step id in `init`.
    //
    // Document order cannot be the fallback for the same reason — it finds an
    // open locked step, or the solved one above it. The page's own answer to
    // "where are you" is `data-state="current"`, so that is the fallback.
    if (lastOpened && lastOpened.open && lastOpened.isConnected
        && lastOpened.dataset.state !== "done") return lastOpened;
    return document.querySelector('.ws-step[data-state="current"]')
        || document.querySelector('.ws-step[data-challenge-id][open]');
  }

  function stepPayload() {
    // In the popped-out tab there is no step list to read: the subject page
    // sends it down the channel and this is the last thing it said.
    if (!ownsPage()) return remoteStep;
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
    // Advisory, and it is the PAGE that shows it — so in the popped-out tab
    // this hands the message back to the subject page rather than looking for a
    // step card that is not here. Still nothing that can solve anything
    // (PLAN.md §14.3 rule 0); it crosses a window boundary, not a trust one.
    if (!ownsPage()) { send({ k: "result", msg: msg }); return; }
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
    if (!ownsPage()) { send({ k: "propose", msg: msg }); return; }
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
    if (!ownsFrame() || frame || mounting) return;
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

  /* ---------- the launcher ---------- */

  var ctaVisible = true;    // is the in-flow button on screen?

  function note(text) {
    var el = pane.querySelector(".ws-runtime-note");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }

  // Two controls, one action, never both on screen: the labelled button in the
  // hero while it is in view, the edge tab once it has scrolled away. That is
  // what `.ws-runtime-handle`'s comment in workshop.css promised from the first
  // commit; the second control it referred to did not exist until now, so the
  // tab was simply always there and the hero had nothing.
  function updateLauncher() {
    var toWindow = mode === MODE_WINDOW;
    document.querySelectorAll(".ws-runtime-toggle").forEach(function (b) {
      var label = toWindow ? b.dataset.labelWindow : b.dataset.labelSplit;
      if (label) {
        b.title = label;
        b.setAttribute("aria-label", label);
        var span = b.querySelector(".ws-runtime-label");
        if (span) span.textContent = label;
      }
      if (toWindow) {
        // It no longer controls anything in this document, so claiming to
        // expand an element here would be a lie to a screen reader.
        b.removeAttribute("aria-expanded");
        b.removeAttribute("aria-controls");
        b.classList.toggle("active", popupLive);
      } else {
        b.setAttribute("aria-controls", "ws-runtime");
        b.setAttribute("aria-expanded", String(!pane.hidden));
        b.classList.toggle("active", !pane.hidden);
      }
    });
    if (handle) handle.hidden = !(pane.hidden && !ctaVisible);
    var pop = pane.querySelector(".ws-runtime-pop");
    if (pop) pop.hidden = !canPopOut();
  }

  // The hero button is the anchor for "has the launcher scrolled away": one
  // observer, rather than a scroll listener measuring a rect on every frame.
  function watchCta() {
    if (!cta) { ctaVisible = false; return; }
    if (typeof IntersectionObserver !== "function") { ctaVisible = false; return; }
    new IntersectionObserver(function (entries) {
      ctaVisible = entries[entries.length - 1].isIntersecting;
      updateLauncher();
    }).observe(cta);
  }

  /* ---------- split or its own tab ---------- */

  function setMode(next, opts) {
    var silent = !!(opts && opts.silent);
    var want = next === MODE_WINDOW && canPopOut() ? MODE_WINDOW : MODE_SPLIT;
    if (!silent) lsSet(LS_MODE, want);
    if (want === mode && pane.dataset.runtimeMode) { updateLauncher(); return; }
    // Before `mode` moves, while this window is still the frame's owner: `save`
    // and `restore` are both gated on `ownsFrame()`, so flipping first would
    // make the handover silently lose the work it exists to carry.
    if (want === MODE_WINDOW) dropFrame();
    mode = want;
    // On the element, not only in a variable: it is what a stylesheet and the
    // regression suite can see, and the stored preference deliberately records
    // only an explicit choice — so `localStorage` is silent about a default.
    pane.dataset.runtimeMode = mode;
    if (mode === MODE_WINDOW) setOpen(false);
    updateLauncher();
  }

  /* Hand the runtime over to the other window.
   *
   * The frame goes, and that reboots the runtime — no amount of care avoids it,
   * a live iframe cannot be adopted by another document without reloading. So
   * the work is pushed to the server first and the next frame, wherever it is
   * created, restores it before booting (PLAN.md §16). That is the whole reason
   * that mechanism exists.
   *
   * The restore state is reset with it, or `restore()`'s memo would hand the
   * next mount a resolved promise and `createFrame` would run without ever
   * re-reading the server — so a pane re-opened after a session in the tab
   * would boot against whatever was in this browser. */
  function dropFrame() {
    if (!frame) return;
    save(true);
    frame.remove();
    frame = null;
    ready = false;
    mounting = false;
    pane.removeAttribute("data-runtime-ready");
    snapshotPromise = null;
    restoreDone = null;
    restored = false;
    lastPrint = null;
  }

  // Synchronous `window.open`, always: an asynchronous one has lost the user
  // gesture by the time it runs and every browser blocks it. The empty URL is
  // what makes a named tab be REUSED rather than re-navigated — re-navigating
  // would reboot an editor's unsaved buffer, or a 300 MB VM.
  function openWindow() {
    var w = null;
    try { w = window.open("", WINDOW_NAME); } catch (e) { w = null; }
    if (!w) {
      // Blocked. Falling back to the split is better than a control that looks
      // like it did nothing, and the note says which of the two happened.
      setMode(MODE_SPLIT);
      setOpen(true);
      note(pane.dataset.labelBlocked || "");
      return;
    }
    popup = w;
    var here = "";
    try { here = w.location.href || ""; } catch (e) { here = ""; }
    // A fresh tab is about:blank; a tab somebody navigated elsewhere is not our
    // host either. Anything already on the host page is left strictly alone.
    if (here.indexOf(cfg.windowUrl) === -1) w.location.href = cfg.windowUrl;
    else { try { w.focus(); } catch (e) { /* the browser may refuse */ } }
  }

  // What a launcher press does, in either presentation.
  function activate() {
    if (mode === MODE_WINDOW) { openWindow(); return; }
    setOpen(pane.hidden);
  }

  function setOpen(open) {
    pane.hidden = !open;
    document.body.classList.toggle("ws-has-pane", open);
    try { localStorage.setItem(LS_OPEN, open ? "1" : "0"); } catch (e) { /* private mode */ }
    if (open) note("");
    updateLauncher();
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
      // "pane" on the subject page, "window" on the popped-out host
      // (templates/workshop_runtime.html). Everything else in this file is the
      // same code in both.
      role: pane.dataset.runtimeRole || "pane",
      windowUrl: pane.dataset.runtimeWindow || "",
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

    openChannel();
    window.addEventListener("message", onMessage);

    if (cfg.role === "window") { startWindow(); return; }
    startPane();
  }

  /* The popped-out tab. It owns the frame and nothing else: no launcher, no
     mode, no step list. */
  function startWindow() {
    // Fetch the participant's snapshot right away — the frame must not boot
    // before it is back in `localStorage` (PLAN.md §16.2).
    fetchSnapshot();
    watchStorage();
    mount();
    // Ask the subject page which step they are on. It answers on the channel,
    // and it also learns from this that a tab is live, so its launcher stops
    // offering to open a second one.
    send({ k: "hello" });
    window.addEventListener("pagehide", function () { send({ k: "bye" }); });

    var split = pane.querySelector(".ws-runtime-split");
    if (split) split.addEventListener("click", function () {
      send({ k: "split" });
      // Give the subject page a moment to open its pane before this tab goes,
      // so the participant never sees neither.
      setTimeout(function () {
        window.close();
        // Still running: a browser refuses `close()` on a tab a script did not
        // open, which is what a reloaded or bookmarked tab is. Say so instead
        // of appearing to do nothing.
        note(pane.dataset.labelClose || "");
      }, 200);
    });
  }

  /* The subject page. It owns the step list always, and the frame only while
     the runtime is shown as a pane. */
  function startPane() {
    var stored = null, storedMode = null;
    try {
      stored = localStorage.getItem(LS_OPEN);
      storedMode = localStorage.getItem(LS_MODE);
      setWidth(parseFloat(localStorage.getItem(LS_WIDTH)) || cfg.size);
    } catch (e) {
      setWidth(cfg.size);
    }

    cta = document.querySelector(".ws-runtime-cta");
    handle = document.querySelector(".ws-runtime-handle");
    document.querySelectorAll(".ws-runtime-toggle").forEach(function (b) {
      b.addEventListener("click", activate);
    });
    var close = pane.querySelector(".ws-runtime-close");
    if (close) close.addEventListener("click", function () { setOpen(false); });
    var pop = pane.querySelector(".ws-runtime-pop");
    if (pop) pop.addEventListener("click", function () {
      setMode(MODE_WINDOW);
      openWindow();
    });

    initResizer();
    watchCta();
    fetchSnapshot();
    watchStorage();
    // Is a popped-out tab already open? It announces itself when it starts, so
    // a page that started later never heard it.
    send({ k: "who" });
    // Step changes: the participant opened another step, or solved one.
    document.addEventListener("toggle", function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains("ws-step")) return;
      if (!ev.target.open || !ev.target.dataset.challengeId) return;
      lastOpened = ev.target;
      sendStep("step");
    }, true);
    window.addEventListener("ws:steps-changed", function () { sendStep("step"); });

    /* Which presentation, on arrival.
     *
     * A stored choice wins — somebody on a 13" laptop sets it once. With none,
     * the viewport decides: below PANE_MIN_WIDTH a split is two columns too
     * narrow to work in, so the launcher offers the tab instead.
     *
     * What is automatic is the DEFAULT, never the tab itself. Opening a window
     * nobody asked for is hostile, and outside a click every browser blocks it
     * anyway — so the participant still presses the button, it just says what
     * it is going to do. */
    if (storedMode === MODE_WINDOW || storedMode === MODE_SPLIT) {
      setMode(storedMode, { silent: true });
    } else {
      setMode(window.innerWidth < PANE_MIN_WIDTH ? MODE_WINDOW : MODE_SPLIT,
              { silent: true });
    }
    if (mode === MODE_SPLIT) {
      setOpen(stored === null ? pane.dataset.runtimeOpen === "1" : stored === "1");
    } else {
      updateLauncher();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
