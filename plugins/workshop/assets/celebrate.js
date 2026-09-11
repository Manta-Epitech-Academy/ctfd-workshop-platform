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

  /* The step-level moment: a figure that pops, hovers and floats away, anchored
     to the row that was just solved rather than centred on the viewport. jump
     centres its XP float because it celebrates the session; this celebrates one
     line of a list, and a full-screen overlay 33 times would be a punishment.

     The count-up is the satisfying beat in jump's original and it is kept, with
     the same ease-out so the number decelerates into place as the badge pops. */
  function floatReward(anchor, amount, label, mascot) {
    if (!anchor) return;
    var host = document.createElement("div");
    host.className = "ws-reward";
    host.setAttribute("aria-hidden", "true");

    if (mascot) {
      var img = document.createElement("img");
      img.className = "ws-reward-mascot";
      img.src = mascot;
      img.alt = "";
      host.appendChild(img);
    }
    var figure = document.createElement("span");
    figure.className = "ws-reward-figure";
    figure.textContent = amount ? "+0" : "✓";
    host.appendChild(figure);
    if (label) {
      var unit = document.createElement("span");
      unit.className = "ws-reward-unit";
      unit.textContent = label;
      host.appendChild(unit);
    }

    anchor.appendChild(host);
    // The element removes itself on animationend. The reduced-motion rule in
    // the stylesheet shortens the animation to ~0ms rather than removing it,
    // which is precisely so this still fires and nothing leaks.
    host.addEventListener("animationend", function () { host.remove(); });

    if (amount && !reducedMotion()) {
      var start = performance.now();
      var tick = function (now) {
        var t = Math.min(1, (now - start) / 900);
        figure.textContent = "+" + Math.round((1 - Math.pow(1 - t, 3)) * amount);
        if (t < 1 && host.isConnected) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    } else if (amount) {
      figure.textContent = "+" + amount;
    }
  }

  window.wsCelebrate = {
    step: function (stepEl, points, label, mascot) {
      var row = stepEl && stepEl.querySelector(".ws-step-summary");
      floatReward(row, points, label, mascot);
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
