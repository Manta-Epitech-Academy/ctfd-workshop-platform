/* Behaviour of the single-page workshop view (/workshop, see page.py).
 *
 * Everything happens in place: answering a step updates the steppers, fills in
 * whatever it unlocked and moves the participant to the next step, with no
 * page reload and no modal. Server state is re-read from
 * /api/v1/workshop/graph after each solve, so the page never guesses what a
 * solve unlocked — CTFd remains the source of truth.
 * All strings English (CLAUDE.md); i18n later.
 */
(function () {
  var ROOT = null;

  // Read once from the JSON islands the templates render. Both fall back to an
  // empty object, so a page served before they existed still runs and simply
  // shows the English source text.
  function island(id) {
    var el = document.getElementById(id);
    if (!el) return {};
    try {
      return JSON.parse(el.textContent) || {};
    } catch (e) {
      return {};
    }
  }
  var COVER = island("ws-page-data").cover || {};
  var I18N = island("ws-i18n");

  // Strings come from the server, already translated, through the JSON island —
  // there is no second translation mechanism in the browser. The fallback is
  // the English source text, so a page rendered before the island existed still
  // says something sensible rather than a key.
  function t(key, fallback) {
    return I18N[key] || fallback;
  }
  var TITLE = { done: "Completed", current: "In progress", todo: "Not started",
                locked: "Locked", info: "Just something to read" };
  var STATES = ["done", "current", "todo", "locked", "info"];

  function api(path, options) {
    return window.CTFd.fetch(path, Object.assign({
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
    }, options || {}));
  }

  /* ---------- answer collection ---------- */

  // Turn the controls into the grammar the server grades (see quiz.py):
  //   single "B" | multiple "A,D" | match "A-c,B-a" | freeform/flag: raw text
  function collectAnswer(form) {
    var kind = form.dataset.answerKind;
    // Reading acknowledgement: the click is the answer. The server accepts it
    // without grading (quiz.py), so this constant carries no secret.
    if (kind === "ack") return "read";
    // A checkpoint step in a self-serve instance: there is no instructor to
    // give out a code, so pressing the button is the completion. The server
    // accepts it only while the instance is self-serve (quiz.py, PLAN.md
    // §25.4), so this constant carries no secret either.
    if (kind === "done") return "done";
    // The closing rating: the feedback IS the submission, so the step cannot
    // be solved — and the bar cannot reach 100% — without it.
    if (kind === "rating") {
      var box = form.querySelector(".ws-rating");
      if (!box || !box.dataset.value) return "";
      var note = form.querySelector(".ws-rating-review");
      return JSON.stringify({
        value: parseInt(box.dataset.value, 10),
        review: note ? note.value : "",
      });
    }
    if (kind === "single") {
      var picked = form.querySelector("input[type=radio]:checked");
      return picked ? picked.value : "";
    }
    if (kind === "multiple") {
      return Array.from(form.querySelectorAll("input[type=checkbox]:checked"))
        .map(function (i) { return i.value; }).sort().join(",");
    }
    if (kind === "match") {
      return Array.from(form.querySelectorAll(".ws-match-select"))
        .filter(function (s) { return s.value; })
        .map(function (s) { return s.dataset.left + "-" + s.value; }).join(",");
    }
    var input = form.querySelector(".ws-answer");
    return input ? input.value : "";
  }

  function feedback(form, state, message) {
    var el = form.querySelector(".ws-feedback");
    if (!el) return;
    el.className = "ws-feedback ws-feedback-" + state;
    el.textContent = message;
  }

  async function submit(form) {
    var id = parseInt(form.dataset.challengeId, 10);
    var answer = collectAnswer(form);
    if (!answer.trim()) {
      feedback(form, "empty", form.dataset.answerKind === "rating"
        ? t("pickFirst", "Pick 👍 or 👎 first")
        : t("answerFirst", "Pick or type an answer first."));
      return;
    }
    var button = form.querySelector(".ws-submit-btn");
    button.disabled = true;
    feedback(form, "pending", t("checking", "Checking…"));
    try {
      var r = await api("/api/v1/challenges/attempt", {
        method: "POST",
        body: JSON.stringify({ challenge_id: id, submission: answer }),
      });
      var body = await r.json();
      var data = (body && body.data) || {};
      var status = data.status || "error";
      if (status === "correct" || status === "already_solved") {
        feedback(form, "correct", data.message || t("correct", "Correct"));
        // Everything the celebration needs is read BEFORE the refresh: it
        // replaces .ws-step-body's innerHTML, which contains this very form and
        // the button the float is anchored to. .ws-step-summary survives, which
        // is why the points are read from there.
        var stepEl = form.closest(".ws-step");
        var meta = stepEl && stepEl.querySelector(".ws-step-meta");
        var points = meta ? parseInt(meta.textContent, 10) : 0;
        // `correct` only, and here rather than anywhere else. This is the ONE
        // place in the page where a real solve and "you had already solved
        // this" are distinguishable: refresh() re-reads the graph and sees a
        // solved set with no provenance, updateCounters() sees only data-state.
        // It is also the only place that knows WHICH step — refresh()'s loop
        // flips every step whose state moved, which on one solve can be several.
        //
        // It fires BEFORE the refresh, and it is anchored to the button that
        // was just pressed rather than to the step's summary row: by the time a
        // validation code is being typed that row is most of a screen above the
        // fold, and the float used to land there, off screen.
        //
        // The reward and the move to the next step used to happen at the same
        // instant, and they diluted each other: two things asking for attention
        // at once, with the float — which is fixed — hanging still while the
        // whole page slid underneath it, so it read as a system overlay rather
        // than as a consequence of the click. They are a sequence now: you act,
        // you are paid, then the page moves. The celebration says how long its
        // own moment is (see celebrate.js) so the pause and the animation it is
        // waiting for cannot drift apart.
        var hold = 0;
        if (status === "correct" && window.wsCelebrate) {
          hold = window.wsCelebrate.step(button, points, I18N.points, COVER.mascot);
        }
        var counts = await refresh(id, hold ? performance.now() + hold : 0);
        if (status === "correct") celebrateCompletion(counts);
      } else {
        feedback(form, status, data.message || t("incorrect", "Incorrect"));
        button.disabled = false;
        // The other half of the loop. A red line of text is easy to miss when
        // you are looking at the keyboard; the field itself says it too, and
        // takes the cursor back so a retype is one gesture.
        var field = form.querySelector(".ws-answer");
        if (field) {
          field.classList.remove("ws-wrong");
          // Reading offsetWidth restarts the animation: without it a second
          // wrong code in a row would not replay it.
          void field.offsetWidth;
          field.classList.add("ws-wrong");
          field.addEventListener("animationend", function () {
            field.classList.remove("ws-wrong");
          }, { once: true });
          field.select();
        }
      }
    } catch (e) {
      feedback(form, "error", t("netError", "Could not reach the server — try again."));
      button.disabled = false;
    }
  }

  /* ---------- the moment after a solve ---------- */

  // Three intensities so the last one still counts: a figure floating off the
  // row (fired in submit, above), confetti when the part is done, confetti
  // twice when the whole workshop is. assets/celebrate.js owns the drawing;
  // this owns only which one fires.
  function celebrateCompletion(counts) {
    var api = window.wsCelebrate;
    if (!api || !counts || counts.total === 0) return;
    if (counts.done !== counts.total) return;
    // The server already decided what the completion block says;
    // `.ws-next-doc-done` is the class it puts there when there is no next
    // part, i.e. this was the last one.
    var block = ROOT.querySelector(".ws-next-doc");
    if (block && block.querySelector(".ws-next-doc-done")) api.workshop();
    else api.part();
  }

  /* ---------- ratings (CTFd's own per-challenge rating) ---------- */

  // Quiet by design: two buttons, one line, no dialog and no interruption of
  // the flow. This path is CTFd's own endpoint, which only accepts a rating
  // for a SOLVED challenge — fine here, since it only ever runs on solved
  // steps. The closing step rates through its submission instead (quiz.py).
  async function rate(box, value) {
    var id = parseInt(box.dataset.challengeId, 10);
    var thanks = box.querySelector(".ws-rating-thanks");
    var review = box.querySelector(".ws-rating-review");
    try {
      var r = await api("/api/v1/challenges/" + id + "/ratings", {
        method: "PUT",
        body: JSON.stringify({ value: value, review: review ? review.value : "" }),
      });
      if (!r.ok) throw new Error("rating refused");
      box.dataset.value = String(value);
      box.dataset.review = review ? review.value : "";
      markRating(box);
      if (thanks) thanks.textContent = t("thanks", "Thanks!");
      // Revealed by this very click: put the cursor where the reason goes.
      if (review && !review.value) review.focus();
    } catch (e) {
      if (thanks) thanks.textContent = t("saveError", "Could not save that — try again.");
    }
  }

  function markRating(box) {
    var chosen = box.dataset.value;
    box.querySelectorAll(".ws-rating-btn").forEach(function (b) {
      b.classList.toggle("ws-rating-chosen", b.dataset.rating === chosen);
    });
    // Progressive disclosure: the reason box shows up once there is a rating
    // to explain, and stays visible on later visits.
    var note = box.querySelector(".ws-rating-review");
    if (note && chosen) note.hidden = false;
  }

  // Inline rating only: save the reason on Enter or on leaving the field, so
  // no extra button clutters the completed line.
  function saveReview(box) {
    var note = box.querySelector(".ws-rating-review");
    if (!note || note.value === (box.dataset.review || "")) return;
    var value = parseInt(box.dataset.value, 10);
    if (value === 1 || value === -1) rate(box, value);
  }

  /* ---------- hints (native unlock, so usage stays tracked) ---------- */

  async function revealHint(details) {
    if (details.dataset.loaded) return;
    var id = parseInt(details.dataset.hintId, 10);
    var cost = parseInt(details.dataset.cost, 10) || 0;
    var body = details.querySelector(".ws-hint-body");
    if (cost > 0 &&
        !window.confirm(t("unlockHint", "Unlock this hint for {n} points?")
                        .replace("{n}", cost))) {
      details.open = false;
      return;
    }
    body.textContent = t("loading", "Loading…");
    try {
      // POST /unlocks records the reveal in CTFd's HintUnlocks table — this is
      // how hint usage is tracked (free hints included, deliberately).
      await api("/api/v1/unlocks", {
        method: "POST",
        body: JSON.stringify({ target: id, type: "hints" }),
      });
      var r = await api("/api/v1/hints/" + id);
      var payload = await r.json();
      var content = payload && payload.data && payload.data.html;
      body.innerHTML = content || (payload.data && payload.data.content) || "";
      details.dataset.loaded = "1";
    } catch (e) {
      body.textContent = t("hintError", "Could not load this hint.");
    }
  }

  /* ---------- syntax highlighting of injected bodies ---------- */

  /* The core theme highlights `pre code` once, on DOMContentLoaded
     (themes/core/assets/js/theme/highlight.js), and keeps lolight module-local.
     Every body this page fills in after a solve therefore arrived as plain
     text: measured on a real run, 0 `.ll-*` spans in each one — so from the
     second step of a subject onwards there was no highlighting at all, which is
     most of a workshop. The plugin carries its own pinned copy of the same
     library (tools/build_vendor.sh) rather than reaching into the core bundle,
     which would be a core edit. Absent, this is a no-op and the code is still
     readable, just monochrome. */

  /* Two things lolight's single, language-agnostic ruleset gets wrong on our
     subjects, both repaired here rather than by shipping a second highlighter.

     Its keyword list misses `local`, which is on nearly every line of beginner
     Lua, and Python's `None`, `True` and `False` — the list carries `null`,
     `true` and `false` and is case-sensitive. */
  /* Looked up with `=== 1`, not for truthiness: these are plain object literals,
     so a token spelled `constructor`, `toString` or `valueOf` would otherwise
     come back with a function off Object.prototype and be tagged a keyword.
     Same reason the language lookup below tests for a string. */
  var EXTRA_KEYWORDS = { local: 1, nonlocal: 1, pass: 1, None: 1, True: 1, False: 1 };

  /* And it only knows `//` and `#` line comments, so `-- deplace le fantome`
     tokenized as two operators followed by live code. That is not cosmetic
     here: one step of the Pac-Man subject exists to teach that `--` is a note
     to the reader and not code, and the page was colouring it as code.

     Keyed off the `language-*` class the markdown renderer puts on <code>, so
     this never fires on a shell block, where `--force` is a flag. */
  var LINE_COMMENT = { lua: "--", sql: "--", haskell: "--" };

  // lolight splits punctuation one character at a time, so a two-character
  // marker spans two tokens. Only punctuation can start one: a `--` inside a
  // string arrives as a single `str` token and is left alone.
  function startsComment(toks, i, marker) {
    var text = "";
    for (var j = i; j < toks.length && text.length < marker.length; j++) {
      if (toks[j][0] !== "pct") return false;
      text += toks[j][1];
    }
    return text === marker;
  }

  function span(parent, cls, text) {
    var el = document.createElement("span");
    el.className = "ll-" + cls;
    el.textContent = text;
    parent.appendChild(el);
  }

  /* Always re-tokenizes from textContent rather than skipping blocks that
     already carry spans. lolight rebuilds from textContent too, so this is
     idempotent, and it is what makes a block the core highlighted at load and
     a block this page fetched after a solve come out identical. Absent (the
     vendored file is a build artifact), it is a no-op and code stays
     monochrome but readable. */
  function highlight(scope) {
    if (!scope || !window.lolight || typeof window.lolight.tok !== "function") return;
    scope.querySelectorAll("pre code").forEach(function (block) {
      var lang = (block.className.match(/language-([\w+#-]+)/) || [])[1];
      var marker = typeof LINE_COMMENT[lang] === "string" ? LINE_COMMENT[lang] : "";
      var toks = window.lolight.tok(block.textContent);
      var out = document.createDocumentFragment();
      var inComment = false;
      for (var i = 0; i < toks.length; i++) {
        var cls = toks[i][0];
        var text = toks[i][1];
        if (inComment) {
          // The newline that ends the comment arrives inside a whitespace
          // token; everything before it belongs to the comment, the rest does
          // not and keeps its own class.
          var nl = text.indexOf("\n");
          if (nl < 0) { span(out, "com", text); continue; }
          if (nl > 0) span(out, "com", text.slice(0, nl));
          span(out, cls, text.slice(nl));
          inComment = false;
          continue;
        }
        if (marker && startsComment(toks, i, marker)) inComment = true;
        else if (cls === "nam" && EXTRA_KEYWORDS[text] === 1) cls = "key";
        span(out, inComment ? "com" : cls, text);
      }
      block.textContent = "";
      block.appendChild(out);
    });
  }

  /* ---------- state refresh after a solve ---------- */

  function setChipState(target, state) {
    ROOT.querySelectorAll('.ws-chip[href="#' + target + '"]').forEach(function (chip) {
      var was = null;
      STATES.forEach(function (s) {
        if (chip.classList.contains("ws-" + s)) was = s;
        chip.classList.remove("ws-" + s);
      });
      chip.classList.add("ws-" + state);
      chip.title = TITLE[state];
      // The glyph is a ::before keyed off the state class, so swapping the
      // class above is the whole update — nothing here writes an icon.
      if (state === "done" && was !== "done") {
        chip.classList.remove("ws-chip-pop");
        void chip.offsetWidth;
        chip.classList.add("ws-chip-pop");
        chip.addEventListener("animationend", function () {
          chip.classList.remove("ws-chip-pop");
        }, { once: true });
      }
      if (state === "current") revealChip(chip);
    });
  }

  /* The steps strip is one scrolling line, and it is the bar pinned to the top
     of the screen, so the chip it is about is the one chip that has to be on
     it. Scrolls the strip, never the page: `scrollIntoView` would have moved
     the document as well, fighting the scroll refresh() just performed.
     Measured from rects rather than offsetLeft, which is relative to whichever
     ancestor happens to be positioned. */
  function revealChip(chip) {
    var strip = chip.closest(".ws-stepper");
    if (!strip || strip.scrollWidth <= strip.clientWidth + 1) return;
    var cr = chip.getBoundingClientRect();
    var sr = strip.getBoundingClientRect();
    var delta = (cr.left - sr.left) - (sr.width - cr.width) / 2;
    if (Math.abs(delta) < 2) return;
    strip.scrollBy({ left: delta, behavior: "smooth" });
  }

  async function fillBody(step) {
    var r = await api("/api/v1/workshop/step/" + step.dataset.challengeId);
    var payload = await r.json();
    if (payload && payload.success) {
      step.querySelector(".ws-step-body").innerHTML = payload.data.html;
      // A step filled in place carries its own folds; give them the state the
      // participant last chose, like the ones rendered with the page.
      restoreFolds(step);
      highlight(step);
      var name = step.querySelector(".ws-step-name");
      if (name) name.textContent = payload.data.name;
      step.querySelectorAll(".ws-rating").forEach(markRating);
    }
  }

  /* `holdScrollUntil` is a performance.now() timestamp, or 0. Everything else
     in here lands immediately — counters, chips, bodies. Those corroborate the
     reward rather than compete with it, and they are not what steals the
     moment: moving the viewport is. */
  function until(timestamp) {
    var wait = timestamp ? timestamp - performance.now() : 0;
    if (wait <= 0) return Promise.resolve();
    return new Promise(function (done) { setTimeout(done, wait); });
  }

  async function refresh(justSolvedId, holdScrollUntil) {
    var r = await api("/api/v1/workshop/graph");
    var graph = (await r.json()).data;
    var solved = new Set(graph.solved);
    var byId = {};
    graph.nodes.forEach(function (n) { byId[n.id] = n; });

    var steps = Array.from(ROOT.querySelectorAll(".ws-step[data-challenge-id]"));
    var currentTaken = false;
    var pending = [];
    var next = null;

    steps.forEach(function (el) {
      var id = parseInt(el.dataset.challengeId, 10);
      var node = byId[id];
      if (!node) return;
      var was = el.dataset.state;
      var state;
      // A closing note stays a closing note — unless it was the outro and the
      // participant has now rated it, which does complete it.
      if (was === "info" && node.unlocked && !solved.has(id)) return;
      if (solved.has(id)) state = "done";
      else if (!node.unlocked) state = "locked";
      else if (!currentTaken) { state = "current"; currentTaken = true; }
      else state = "todo";

      if (state !== was) {
        STATES.forEach(function (s) { el.classList.remove("ws-" + s); });
        el.classList.add("ws-" + state);
        el.dataset.state = state;
        setChipState("step-" + id, state);
        // A step that just became answerable (or just got solved) needs its
        // body re-rendered: locked bodies hold a placeholder, solved ones
        // swap the form for the completed note.
        if (was === "locked" || state === "done") pending.push(fillBody(el));
      }
      if (state === "current" && !next) next = el;
    });

    // Counters read the states we just set, so they land immediately — the
    // bodies being fetched only affect the inside of steps.
    var counts = updateCounters(solved);
    await Promise.all(pending);
    if (next && next.dataset.challengeId !== String(justSolvedId)) {
      // Opened together with the scroll rather than before it: the next step is
      // below the fold either way, so revealing it early buys nothing and
      // splits one event into two.
      await until(holdScrollUntil);
      next.open = true;
      scrollTo(next);
    }
    return counts;
  }

  function updateCounters(solved) {
    var total = 0, done = 0;
    ROOT.querySelectorAll(".ws-part[data-part]").forEach(function (part) {
      // Whether a step counts is a property of the step, not of its state:
      // the outro still does not count once it has been rated.
      var steps = Array.prototype.filter.call(
        part.querySelectorAll(".ws-step[data-challenge-id]"),
        function (s) { return s.dataset.counts !== "0"; });
      var partDone = 0;
      steps.forEach(function (s) { if (s.dataset.state === "done") partDone += 1; });
      total += steps.length;
      done += partDone;

      var count = part.querySelector(".ws-part-count");
      if (count && steps.length) count.textContent = partDone + "/" + steps.length;

      var state = "todo";
      if (partDone === steps.length) state = "done";
      else if (part.querySelector('.ws-step[data-state="current"]')) state = "current";
      else if (!part.querySelector('.ws-step:not([data-state="locked"])')) state = "locked";
      setChipState("part-" + part.dataset.part, state);
      var badge = ROOT.querySelector(
        '.ws-chip[href="#part-' + part.dataset.part + '"] .ws-chip-badge');
      if (badge) badge.textContent = partDone + "/" + steps.length;
    });

    // The forward cue out of a finished document. Server-rendered on every
    // document-scoped view and hidden until it applies (templates/
    // workshop_page.html), because the solve that completes the document never
    // reloads the page — so the one moment it is needed is the one moment a
    // server-only section would miss. Same counters as the bar above: the
    // server decides what it says, this decides whether it shows.
    var nextDoc = ROOT.querySelector(".ws-next-doc");
    if (nextDoc) {
      var show = total > 0 && done === total;
      // Only on the transition. Setting the class whenever it is visible would
      // replay the entrance on every counter update, and on a page loaded with
      // the part already finished.
      if (show && nextDoc.hidden) {
        nextDoc.classList.add("ws-next-doc-in");
        nextDoc.addEventListener("animationend", function () {
          nextDoc.classList.remove("ws-next-doc-in");
        }, { once: true });
      }
      nextDoc.hidden = !show;
    }

    var overall = ROOT.querySelector(".ws-overall");
    if (overall) {
      overall.querySelector(".ws-overall-num").textContent = done + "/" + total;
      overall.querySelector(".ws-overall-fill").style.width =
        (total ? (100 * done) / total : 0) + "%";
    }
    // Handed back rather than only written into the DOM: submit() is the only
    // place that knows a solve just happened (see its comment), and it needs
    // these to decide between the three celebration tiers.
    return { total: total, done: done };
  }

  /* ---------- navigation ---------- */

  /* The app shell's header scrolls with the page (it is in normal flow, like
     jump's), so the only thing an anchor has to clear is the steps stepper of
     the part it lands in, which sticks to the top of the viewport on its own.
     Per part, because that is how the bar is scoped: measuring a different
     part's strip would leave the target under the real one. */
  function stickyOffset(el) {
    var part = el && el.closest ? el.closest(".ws-part") : null;
    var bar = (part || ROOT).querySelector(".ws-stepper-steps");
    return (bar ? bar.getBoundingClientRect().height : 0) + 16;
  }

  function scrollTo(el) {
    var top = el.getBoundingClientRect().top + window.scrollY - stickyOffset(el);
    // An explicit `behavior: "smooth"` WINS over the stylesheet's
    // `scroll-behavior: auto !important`: the CSS property is only consulted
    // when the JS behaviour is "auto". So the reduced-motion opt-out has to be
    // read here, or somebody who asked for no motion gets the one animation on
    // this page that moves their whole viewport.
    var smooth = true;
    try {
      smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) { /* no matchMedia: keep the default */ }
    window.scrollTo({ top: top, behavior: smooth ? "smooth" : "auto" });
  }

  /* `position: sticky` gives no state to style, and the usual
     IntersectionObserver trick does not apply here: it calls a bar "not fully
     intersecting" when the bar is off-screen entirely, and this page has one
     bar per part, so every bar below the fold would claim to be stuck.
     Measured — at scroll 0 the observer reported stuck on a bar sitting 1204px
     down the page.

     One rect read per bar, coalesced into a single frame, is both correct and
     cheaper than the observer plus the position test it would still need. */
  function watchStuck() {
    var bars = Array.prototype.slice.call(
      ROOT.querySelectorAll(".ws-stepper-steps"));
    if (!bars.length) return;
    var queued = false;
    var update = function () {
      queued = false;
      bars.forEach(function (bar) {
        // `top: 0` on the rule, so a pinned bar reports exactly 0; the epsilon
        // is only there for subpixel layout.
        bar.classList.toggle("ws-stuck", bar.getBoundingClientRect().top <= 0.5);
      });
    };
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(update);
    }, { passive: true });
    update();
  }

  function openFromHash() {
    if (!window.location.hash) return;
    var el = ROOT.querySelector(window.location.hash.replace(/[^#\w-]/g, ""));
    if (!el) return;
    var step = el.closest ? (el.matches(".ws-step") ? el : el.querySelector(".ws-step")) : null;
    if (step) step.open = true;
    setTimeout(function () { scrollTo(el); }, 50);
  }

  /* ---------- part introductions ---------- */

  // Open by default — PLAN.md §13, "do not make people click to read", and the
  // exercise after an introduction routinely says to reuse the code in it. The
  // fold is for the participant who has already read a long one; shutting it
  // sticks, so it does not spring open again on every page.
  // A step's short version (PLAN.md §25.7) folds the same way and for the same
  // reason, so both go through one key.
  var FOLDS = ".ws-part-lead[data-lead], .ws-summary[data-step]";

  function leadKey(lead) {
    var id = lead.dataset.lead || ("step" + lead.dataset.step);
    return "ws-lead:" + window.location.pathname + ":" + id;
  }

  function restoreFolds(scope) {
    (scope || ROOT).querySelectorAll(FOLDS).forEach(function (lead) {
      try {
        if (window.localStorage.getItem(leadKey(lead)) === "shut") lead.open = false;
      } catch (e) { /* storage refused (private window): it stays open */ }
    });
  }

  function rememberLeads() {
    restoreFolds(ROOT);
    // `toggle` does not bubble, hence the capture phase.
    ROOT.addEventListener("toggle", function (ev) {
      var lead = ev.target;
      if (!lead.dataset || !lead.matches || !lead.matches(FOLDS)) return;
      try {
        if (lead.open) window.localStorage.removeItem(leadKey(lead));
        else window.localStorage.setItem(leadKey(lead), "shut");
      } catch (e) { /* folding still works for this visit, it just won't stick */ }
    }, true);
  }

  function start() {
    ROOT = document.querySelector("#ws-workshop");
    if (!ROOT) return;
    ROOT.querySelectorAll(".ws-rating").forEach(markRating);
    rememberLeads();

    ROOT.addEventListener("click", function (ev) {
      var save = ev.target.closest(".ws-rating-save");
      if (save) {
        var saveBox = save.closest(".ws-rating");
        var current = parseInt(saveBox.dataset.value, 10);
        if (current === 1 || current === -1) rate(saveBox, current);
        return;
      }
      var btn = ev.target.closest(".ws-rating-btn");
      if (!btn) return;
      var box = btn.closest(".ws-rating");
      if (btn.closest(".ws-form")) {
        // Part of the closing step: choosing only selects. Sending it is what
        // submits the step, so the two can never come apart.
        box.dataset.value = btn.dataset.rating;
        markRating(box);
        return;
      }
      rate(box, parseInt(btn.dataset.rating, 10));   // already-solved step: CTFd's endpoint
    });

    ROOT.addEventListener("submit", function (ev) {
      var form = ev.target.closest(".ws-form");
      if (!form) return;
      ev.preventDefault();
      submit(form);
    });

    // A chip points at a step: open it as well as scroll to it.
    ROOT.addEventListener("click", function (ev) {
      var chip = ev.target.closest(".ws-chip");
      if (!chip) return;
      var target = ROOT.querySelector(chip.getAttribute("href").replace(/[^#\w-]/g, ""));
      if (!target) return;
      ev.preventDefault();
      var step = target.matches(".ws-step") ? target : target.querySelector(".ws-step");
      if (step) step.open = true;
      history.replaceState(null, "", chip.getAttribute("href"));
      scrollTo(target);
    });

    ROOT.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" || !ev.target.classList.contains("ws-rating-review")) return;
      var box = ev.target.closest(".ws-rating");
      ev.preventDefault();
      var btn = box.querySelector(".ws-rating-save");
      var form = box.closest(".ws-form");
      if (btn) btn.click();                 // closing step, already rated
      else if (form) form.requestSubmit();  // closing step, not yet sent
      else saveReview(box);                 // inline rating on a solved step
    });

    // Leaving the field saves it too — typing a reason and clicking away must
    // not silently discard it.
    ROOT.addEventListener("focusout", function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains("ws-rating-review")) return;
      var box = ev.target.closest(".ws-rating");
      if (box && !box.closest(".ws-form")) saveReview(box);
    });

    // `toggle` does not bubble, so listen in the capture phase.
    ROOT.addEventListener("toggle", function (ev) {
      var d = ev.target;
      if (!d.classList) return;
      if (d.classList.contains("ws-hint") && d.open) revealHint(d);
      // Reveal the content rather than blink it into place. Keyed off a real
      // toggle and not off `[open]`, so the folds that are already open when
      // the page loads do not all animate at once.
      if (d.open && d.tagName === "DETAILS") {
        d.classList.add("ws-fold-open");
        d.addEventListener("animationend", function () {
          d.classList.remove("ws-fold-open");
        }, { once: true });
      }
    }, true);

    // Twice, on purpose, and both passes are idempotent. The core's own
    // highlighting also runs on DOMContentLoaded and the order between the two
    // handlers is not ours to decide; if it lands second it rebuilds the block
    // from its text and drops the keyword re-tagging, so `load` — which is
    // strictly after every DOMContentLoaded handler — puts it back.
    highlight(ROOT);
    window.addEventListener("load", function () { highlight(ROOT); });
    watchStuck();
    // Where the participant is, brought onto the strip on arrival too, not only
    // when a solve moves it. A strip that opens on step 1 while the person is
    // on step 7 is a strip about somebody else.
    ROOT.querySelectorAll(".ws-stepper-steps .ws-chip.ws-current")
        .forEach(revealChip);

    openFromHash();
    // Back/forward between steps changes the hash without reloading.
    window.addEventListener("hashchange", openFromHash);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
