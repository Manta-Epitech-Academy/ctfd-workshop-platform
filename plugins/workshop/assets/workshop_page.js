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

  // Read once from the JSON island the template renders. Falls back to an empty
  // object so a page that predates it (or an older cached template) still runs.
  var PAGE = (function () {
    var el = document.getElementById("ws-page-data");
    if (!el) return {};
    try {
      return JSON.parse(el.textContent) || {};
    } catch (e) {
      return {};
    }
  })();
  var COVER = PAGE.cover || {};
  var I18N = PAGE.i18n || {};

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
        // replaces .ws-step-body's innerHTML, which contains this very form.
        // .ws-step-summary survives, which is why the points come from there
        // and why the float is anchored to it.
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
        // The step's own moment fires BEFORE the refresh, not after: refresh()
        // opens the step that just unlocked and scrolls to it, so a float
        // created afterwards would pop on a row that is no longer on screen.
        // It pops in ~260ms and the refresh's own round trip is longer than
        // that, so it is seen and then the page moves on.
        if (status === "correct" && window.wsCelebrate) {
          window.wsCelebrate.step(stepEl, points, I18N.points, COVER.mascot);
        }
        var counts = await refresh(id);
        if (status === "correct") celebrateCompletion(counts);
      } else {
        feedback(form, status, data.message || t("incorrect", "Incorrect"));
        button.disabled = false;
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

  /* ---------- state refresh after a solve ---------- */

  function setChipState(target, state) {
    ROOT.querySelectorAll('.ws-chip[href="#' + target + '"]').forEach(function (chip) {
      STATES.forEach(function (s) { chip.classList.remove("ws-" + s); });
      chip.classList.add("ws-" + state);
      chip.title = TITLE[state];
      // The glyph is a ::before keyed off the state class, so swapping the
      // class above is the whole update — nothing here writes an icon.
    });
  }

  async function fillBody(step) {
    var r = await api("/api/v1/workshop/step/" + step.dataset.challengeId);
    var payload = await r.json();
    if (payload && payload.success) {
      step.querySelector(".ws-step-body").innerHTML = payload.data.html;
      // A step filled in place carries its own folds; give them the state the
      // participant last chose, like the ones rendered with the page.
      restoreFolds(step);
      var name = step.querySelector(".ws-step-name");
      if (name) name.textContent = payload.data.name;
      step.querySelectorAll(".ws-rating").forEach(markRating);
    }
  }

  async function refresh(justSolvedId) {
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
    if (nextDoc) nextDoc.hidden = !(total > 0 && done === total);

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
     jump's), so the only thing an anchor has to clear is the part stepper,
     which sticks to the top of the viewport on its own. */
  function stickyOffset() {
    var bar = ROOT.querySelector(".ws-stepper-parts");
    return (bar ? bar.getBoundingClientRect().height : 0) + 16;
  }

  function scrollTo(el) {
    var top = el.getBoundingClientRect().top + window.scrollY - stickyOffset();
    window.scrollTo({ top: top, behavior: "smooth" });
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
      if (d.classList && d.classList.contains("ws-hint") && d.open) revealHint(d);
    }, true);

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
