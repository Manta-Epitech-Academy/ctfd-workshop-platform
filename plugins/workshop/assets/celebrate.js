/* Celebration moments for the workshop page.
 *
 * A workshop is 33 small wins in a row, and until now every one of them turned
 * a chip from grey to green and did nothing else. This is the layer that says
 * something happened, at three intensities so the last one still counts:
 *
 *   a step        a "+N pts" figure floating off the row that was just solved
 *   a part        confetti, over the completion block the page already renders
 *   the workshop  confetti, twice, and the block says it is over
 *
 * Ported from jump's talent space rather than invented: `XpFloat.svelte` (the
 * rising count-up figure), `lib/actions/confetti.ts` (the burst, including both
 * of its workarounds, below) and `rewardToast.ts`. Motion tokens and the
 * keyframes live in assets/workshop.css.
 *
 * All strings come from the page (see the i18n blob), so nothing here is copy.
 */
(function () {
  var CANVAS_ID = "ws-confetti";
  var instance = null;

  /* How long the step's moment lasts, i.e. how long the page owes it before
     moving on. Returned by `step()` rather than hard-coded on the caller's
     side, so the pause and the animation it is waiting for cannot drift apart.

     COUNT_UP is the count-up's own duration: the figure has reached its final
     value by then, which is the beat the moment is built around. With no number
     to count (a step worth no points) or under reduced motion (no count-up at
     all) there is only "has it been seen", which is shorter. */
  var COUNT_UP_MS = 900;
  var SEEN_MS = 450;

  /* Two things canvas-confetti gets wrong for us, both solved the way jump
     solves them:

     1. Its default canvas is a plain `position: fixed` element on <body>. The
        runtime pane is an iframe on its own compositing layer, and a plain
        canvas composites BEHIND it — so a celebration fired while the pane is
        open is invisible, which is exactly when it matters most. Our own canvas
        is promoted with translateZ(0) and sits above the pane's z-index (1030,
        see .ws-runtime in workshop.css); 2147483647 is what jump uses.
     2. `useWorker: true` builds its renderer from a blob: URL. Nothing blocks
        that here today, but a Content-Security-Policy would, and the failure is
        silent — the worker dies and nothing ever draws. Main thread. */
  function burst() {
    if (typeof window.confetti !== "function") return;
    if (!instance) {
      var canvas = document.createElement("canvas");
      canvas.id = CANVAS_ID;
      document.body.appendChild(canvas);
      instance = window.confetti.create(canvas, { resize: true, useWorker: false });
    }
    var total = 200;
    var common = { origin: { y: 0.7 } };
    function shot(ratio, opts) {
      instance(Object.assign({}, common, opts, {
        particleCount: Math.floor(total * ratio),
      }));
    }
    // The brand pair first, then the warm one: jump's own mix.
    shot(0.25, { spread: 26, startVelocity: 55, colors: ["#013afb", "#00ff97"] });
    shot(0.2, { spread: 60, colors: ["#ff5f3a", "#ffffff"] });
    shot(0.35, { spread: 100, decay: 0.91, scalar: 0.8, colors: ["#013afb", "#00ff97", "#ffffff"] });
    shot(0.1, { spread: 120, startVelocity: 25, decay: 0.92, scalar: 1.2, colors: ["#ff5f3a", "#00ff97"] });
  }

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  /* The step-level moment.

     Anchored to the control that was just pressed, in a fixed layer on <body>,
     and both halves of that are load-bearing:

     - to the CONTROL, not to the step's summary row. The summary is the top of
       a step whose body is a statement, a code block and often a hint, so by
       the time somebody is typing a validation code the row is far above the
       fold. Measured on a real solve before this changed: the float landed at
       top: -1479px in a 1000px viewport, a screen and a half above the
       participant. It was drawn every time and seen never.
     - in a FIXED layer, not appended to the control. fillBody() replaces the
       step body's innerHTML — the form, the button and anything inside it —
       and refresh() then scrolls to whatever just unlocked. A float parented to
       the button would be destroyed mid-animation, and one parented to the page
       would slide off with the scroll.

     Placed just above the control and rising, so it never covers the thing the
     eye is already on. Clamped below the sticky stepper: a step near the top of
     the viewport would otherwise throw it off-screen, which is the bug this is
     fixing. */
  var TOP_MARGIN = 84;

  function floatReward(anchor, amount, label, mascot) {
    if (!anchor || !anchor.getBoundingClientRect) return 0;
    var rect = anchor.getBoundingClientRect();
    var host = document.createElement("div");
    host.className = "ws-reward";
    host.setAttribute("aria-hidden", "true");
    host.style.left = Math.round(rect.left + rect.width / 2) + "px";
    host.style.top = Math.round(Math.max(TOP_MARGIN, rect.top - 14)) + "px";

    // The subject's sprite when it declared one, and nothing at all when it did
    // not. A stand-in glyph was tried and removed: a generic icon beside the
    // figure makes it read as a notification badge, which is the one thing this
    // must not look like.
    if (mascot) {
      var img = document.createElement("img");
      img.className = "ws-reward-mascot";
      img.src = mascot;
      img.alt = "";
      host.appendChild(img);
    }
    var figure = document.createElement("span");
    figure.className = "ws-reward-figure";
    figure.textContent = amount ? "+0" : "\u2713";
    host.appendChild(figure);
    if (label && amount) {
      var unit = document.createElement("span");
      unit.className = "ws-reward-unit";
      unit.textContent = label;
      host.appendChild(unit);
    }

    document.body.appendChild(host);
    // The element removes itself on animationend. Under prefers-reduced-motion
    // the stylesheet swaps the rise for a plain fade of the same length rather
    // than shortening it to nothing: reduced motion means gentler, not absent,
    // and this is the only confirmation a solve gives.
    host.addEventListener("animationend", function () { host.remove(); });

    if (amount && !reducedMotion()) {
      // The count-up is the satisfying beat in jump's XpFloat, kept at jump's
      // own 900ms and its own ease-out, so the number decelerates into place as
      // the figure settles out of its overshoot.
      var start = performance.now();
      var tick = function (now) {
        var t = Math.min(1, (now - start) / COUNT_UP_MS);
        figure.textContent = "+" + Math.round((1 - Math.pow(1 - t, 3)) * amount);
        if (t < 1 && host.isConnected) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      return COUNT_UP_MS;
    }
    if (amount) figure.textContent = "+" + amount;
    return SEEN_MS;
  }

  window.wsCelebrate = {
    /* `anchor` is the control that was pressed — see floatReward. Returns how
       many milliseconds this moment needs before the page may move, or 0 if
       nothing was drawn. The caller decides what to do with that; this only
       knows how long its own animation takes. */
    step: function (anchor, points, label, mascot) {
      return floatReward(anchor, points, label, mascot);
    },
    part: function () {
      if (!reducedMotion()) burst();
    },
    workshop: function () {
      if (reducedMotion()) return;
      burst();
      // A second volley a beat later, so finishing the whole thing does not
      // land the same as finishing a part.
      setTimeout(burst, 700);
    },
  };
})();
