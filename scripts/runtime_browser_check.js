/* The runtime, in a real browser: both presentations, the advisory rules
 * across two windows, and the cue (PLAN.md §14, §30, §33).
 *
 *     node scripts/runtime_browser_check.js [base-url] [user] [password] \
 *          [path-to-playwright] [part-slug]
 *
 * Optional, and NOT part of scripts/phase2_validate.py, for the same reason
 * scripts/workspace_browser_check.js is not: it needs Playwright and a built
 * runtime dist, while the suite has to run anywhere. The suite asserts the
 * markup; what only a browser can prove is what this file covers — which of
 * the two launchers is on screen, that a named tab is reused rather than
 * rebooted, and that a runtime's advisory messages still cannot solve anything
 * once they have to cross a BroadcastChannel to be shown.
 *
 * Needs an instance with a subject synced, its runtime dist built
 * (tools/build_runtime.sh <id>), and an account whose first step is an `ack`
 * (the entry document's "J'ai lu"), because solving that is what makes a step
 * with an answer field current. It solves one step, so give it a throwaway
 * account.
 */
const PLAYWRIGHT = process.argv[5]
  || "/home/kc/.npm/_npx/e41f203b7505f1fb/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);

const BASE = process.argv[2] || "http://localhost:8080";
const USER = process.argv[3] || "attendee";
const PASS = process.argv[4] || "attendee";
const PART = "/workshop/" + (process.argv[6] || "atelier1");
// Must match PANE_MIN_WIDTH in plugins/workshop/assets/runtime.js: the point is
// to check the default on either side of it.
const PANE_MIN_WIDTH = 900;

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}

async function login(ctx, width, height) {
  const page = await ctx.newPage();
  await page.setViewportSize({ width, height });
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
  await page.fill('input[name="name"]', USER);
  await page.fill('input[name="password"]', PASS);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded" }),
    page.click("#_submit"),
  ]);
  return page;
}

const ready = (page) => page.waitForFunction(
  () => document.querySelector("#ws-runtime").dataset.runtimeReady === "1",
  null, { timeout: 60000 });

// Which launcher a participant can actually reach right now — on screen, not
// merely laid out, since the whole rule is that exactly one of the two is.
const launcher = (page) => page.evaluate(() => {
  const seen = (el) => {
    if (!el || el.hidden) return false;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
  };
  const cta = document.querySelector(".ws-runtime-cta");
  const handle = document.querySelector(".ws-runtime-handle");
  const pane = document.querySelector("#ws-runtime");
  return {
    cta: seen(cta), handle: seen(handle),
    label: cta ? cta.querySelector(".ws-runtime-label").textContent.trim() : null,
    paneOpen: !pane.hidden,
    mode: pane.dataset.runtimeMode,
  };
});

async function wideScreen(browser) {
  console.log("== Wide viewport: the split, and one launcher at a time ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, 1280, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });
  await page.waitForSelector("#ws-runtime", { state: "attached" });
  await page.waitForTimeout(400);

  let v = await launcher(page);
  check(v.mode === "split", `${1280}px defaults to the split`);
  check(v.cta && !v.handle, "the hero button is the launcher on arrival, no edge tab");

  await page.evaluate(() => window.scrollTo({ top: 2500, behavior: "instant" }));
  await page.waitForTimeout(400);
  v = await launcher(page);
  check(!v.cta && v.handle, "once the hero has scrolled away, the edge tab takes over");

  const tucked = await page.evaluate(() => getComputedStyle(
    document.querySelector(".ws-runtime-handle")).transform);
  await page.hover(".ws-runtime-handle");
  await page.waitForTimeout(350);
  const flush = await page.evaluate(() => getComputedStyle(
    document.querySelector(".ws-runtime-handle")).transform);
  check(tucked !== flush, `the tab rests tucked and slides flush on hover (${tucked} -> ${flush})`);

  await page.click(".ws-runtime-handle");
  await ready(page);
  v = await launcher(page);
  check(v.paneOpen && !v.handle && !v.cta,
        "while the pane is open nothing competes with its own close button");
  const split = await page.evaluate(() => ({
    pane: Math.round(document.querySelector("#ws-runtime").getBoundingClientRect().width),
    subject: getComputedStyle(document.querySelector(".ws-page")).marginRight,
    hero: getComputedStyle(document.querySelector(".ws-hero")).marginRight,
  }));
  check(split.pane > 400 && split.subject !== "0px" && split.hero !== "0px",
        `a real split, subject and hero both make room (${JSON.stringify(split)})`);
  await ctx.close();
}

async function narrowScreen(browser) {
  console.log("== Narrow viewport: the pop-out is the default, never automatic ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, PANE_MIN_WIDTH - 100, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });
  await page.waitForTimeout(400);

  const v = await launcher(page);
  check(v.mode === "window", `${PANE_MIN_WIDTH - 100}px defaults to the pop-out`);
  check(!v.paneOpen && ctx.pages().length === 1,
        "and opens nothing at all until it is pressed");
  check(v.label !== null, `the launcher says where it goes ("${v.label}")`);

  const [popped] = await Promise.all([
    page.waitForEvent("popup"),
    page.click(".ws-runtime-cta"),
  ]);
  await popped.waitForURL(/\/runtime$/, { timeout: 20000 });
  await ready(popped);
  check(popped.url().endsWith(PART + "/runtime"), "the launcher opens the host page");
  const full = await popped.evaluate(() => ({
    frame: Math.round(document.querySelector(".ws-runtime-frame").getBoundingClientRect().width),
    vw: window.innerWidth,
    back: !!document.querySelector(".ws-runtime-back"),
    split: !!document.querySelector(".ws-runtime-split"),
  }));
  check(full.frame >= full.vw - 2, "the frame gets the whole width of its own page");
  check(full.back && full.split,
        "the tab offers the way back to the subject, and back to the split");

  // The reason the tab is named and reused: re-navigating it would reboot an
  // editor's unsaved buffer, or a 300 MB VM.
  const origin = await popped.evaluate(() => performance.timeOrigin);
  const before = ctx.pages().length;
  await page.click(".ws-runtime-cta");
  await page.waitForTimeout(800);
  check(ctx.pages().length === before, "a second press opens no second tab");
  check(await popped.evaluate(() => performance.timeOrigin) === origin,
        "and does not reload the one already open");

  await popped.click(".ws-runtime-split");
  await page.waitForTimeout(900);
  const back = await launcher(page);
  check(back.mode === "split" && back.paneOpen,
        "'Side by side' puts the pane back on the subject page");
  await ctx.close();
}

async function advisoryAcrossWindows(browser) {
  console.log("== Advisory only, across two windows (PLAN.md §14.3 rule 0) ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, 1280, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });

  // The entry document's "J'ai lu" needs no code, and solving it is what makes
  // a step with an answer field current — which is what `propose` fills.
  const ack = await page.$('.ws-form[data-answer-kind="ack"]');
  if (!ack) {
    console.log("  [skip] no unsolved `ack` step on this part — use a fresh account");
    await ctx.close();
    return;
  }
  const ackStep = await page.evaluate(() => {
    const el = document.querySelector('.ws-form[data-answer-kind="ack"]').closest(".ws-step");
    el.open = true;
    return "#" + el.id;
  });
  await page.waitForTimeout(250);
  await page.click(ackStep + " .ws-submit-btn");
  await page.waitForFunction(() => document.querySelector('.ws-step[data-state="current"]'),
                             null, { timeout: 20000 });
  await page.waitForTimeout(3500);        // the reward float holds the scroll

  const active = await page.evaluate(() => {
    const el = document.querySelector('.ws-step[data-state="current"]');
    el.open = true;
    return { id: el.dataset.challengeId, input: !!el.querySelector(".ws-answer") };
  });
  const solvedBefore = await page.evaluate(async () => {
    const r = await fetch("/api/v1/workshop/graph", { credentials: "same-origin" });
    return (await r.json()).data.solved.length;
  });

  // Real clicks throughout: `window.open` outside a user gesture is blocked,
  // which is what the fallback is for and not what is under test here. Opening
  // the pane first also exercises the split -> window switch, where this page's
  // frame goes and the tab's restores the work before booting.
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(400);
  await page.click(".ws-runtime-cta");
  await ready(page);
  const [popped] = await Promise.all([
    page.waitForEvent("popup"),
    page.click(".ws-runtime-pop"),
  ]);
  await popped.waitForURL(/\/runtime$/, { timeout: 20000 });
  await ready(popped);
  const frame = popped.frames().find((f) => f !== popped.mainFrame());
  check(!!frame, "the frame booted in the popped-out tab");

  // Exactly what an adapter does, from inside the frame.
  await frame.evaluate(() => parent.postMessage(
    { wrp: 1, type: "result", ok: true, detail: "browser-check-result" },
    location.origin));
  await page.waitForTimeout(600);
  const hint = await page.evaluate((id) => {
    const el = document.querySelector('.ws-step[data-challenge-id="' + id + '"]');
    const n = el.querySelector(".ws-runtime-hint");
    return { text: n ? n.textContent.trim() : null,
             ok: el.classList.contains("ws-runtime-ok"), state: el.dataset.state };
  }, active.id);
  check(hint.text === "browser-check-result",
        "`result` from the popped-out tab renders on the subject page");
  check(hint.ok, "and marks the step it names, not the one just solved");
  check(hint.state === "current", "and does not solve it");

  if (active.input) {
    await frame.evaluate(() => parent.postMessage(
      { wrp: 1, type: "propose", submission: "browser-check-answer" }, location.origin));
    await page.waitForTimeout(600);
    const filled = await page.evaluate((id) => {
      const el = document.querySelector('.ws-step[data-challenge-id="' + id + '"]');
      return { value: el.querySelector(".ws-answer").value, state: el.dataset.state };
    }, active.id);
    check(filled.value === "browser-check-answer",
          "`propose` fills the answer field on the subject page");
    check(filled.state === "current", "and still does not solve it");
  } else {
    console.log("  [skip] the current step has no answer field (self-serve mode)");
  }

  const solvedAfter = await page.evaluate(async () => {
    const r = await fetch("/api/v1/workshop/graph", { credentials: "same-origin" });
    return (await r.json()).data.solved.length;
  });
  check(solvedAfter === solvedBefore,
        `progress unchanged by either message (${solvedBefore} -> ${solvedAfter})`);
  await ctx.close();
}

/* Switching presentation reboots the runtime — a live iframe cannot move
 * between documents without reloading — so the only thing that makes the switch
 * safe is that the work is saved before the frame goes and restored before the
 * next one boots (PLAN.md §16, §30). Both directions, because they fail
 * differently: handing over without saving loses the work, and re-mounting
 * without re-reading the server boots against a stale browser bucket. */
async function workSurvivesTheHandover(browser) {
  console.log("== Work survives a switch in either direction ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, 1280, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });
  await page.waitForSelector("#ws-runtime", { state: "attached" });
  await page.click(".ws-runtime-cta");
  await ready(page);

  const keys = await page.evaluate(() => {
    try { return JSON.parse(localStorage.getItem("ws-workspace-keys") || "[]"); }
    catch (e) { return []; }
  });
  if (!keys.length) {
    console.log("  [skip] this runtime's adapter announces no storage keys");
    await ctx.close();
    return;
  }
  const key = keys[0];
  const mark = "ws-check-" + Date.now();
  await page.evaluate(({ k, v }) => localStorage.setItem(k, v), { k: key, v: mark });

  // split -> window. The pane's frame is the owner here, so this is the save
  // that used to be skipped because the mode had already moved.
  const [popped] = await Promise.all([
    page.waitForEvent("popup"),
    page.click(".ws-runtime-pop"),
  ]);
  await popped.waitForURL(/\/runtime$/, { timeout: 20000 });
  await ready(popped);
  const onServer = await popped.evaluate(async (k) => {
    const r = await fetch("/api/v1/workshop/workspace", { credentials: "same-origin" });
    return ((await r.json()).data.keys || {})[k] || null;
  }, key);
  check(onServer === mark, "the pane saved its work before handing the frame over");
  check(await popped.evaluate((k) => localStorage.getItem(k), key) === mark,
        "and the tab booted with it");

  // window -> split. What has to be true is that the re-mounted pane reads the
  // snapshot AGAIN: `restore()` memoises, so without resetting that memo when
  // the frame was handed over, `createFrame` runs off a resolved promise and
  // the pane boots against whatever this browser happens to hold — which on a
  // shared classroom machine is the previous student's work. Counted as a
  // request rather than compared as a value, because at this point the pane is
  // a live frame writing that same key and any value assertion would be racing
  // it.
  let snapshotGets = 0;
  page.on("request", (r) => {
    if (r.method() === "GET" && r.url().endsWith("/api/v1/workshop/workspace")) snapshotGets += 1;
  });
  await popped.click(".ws-runtime-split");
  await page.waitForFunction(
    () => document.querySelector("#ws-runtime").dataset.runtimeMode === "split"
          && !document.querySelector("#ws-runtime").hidden,
    null, { timeout: 20000 });
  await ready(page);
  await page.waitForTimeout(500);
  check(snapshotGets >= 1,
        `the re-mounted pane re-read the snapshot (${snapshotGets} GET) instead of` +
        " reusing a resolved restore");
  await ctx.close();
}

/* The cue (PLAN.md §33): the mark the author places, and the launcher that
 * answers it. Only a browser can check this one — the whole behaviour is an
 * IntersectionObserver reading a scroll position, and which launcher gets
 * painted depends on which one that scroll position leaves on screen.
 *
 * Skipped, not failed, on a subject that places no mark: the feature is opt-in
 * per subject and this file has to stay runnable against all of them.
 */
async function theCue(browser) {
  console.log("== The runtime cue: the mark, and the launcher it paints ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, 1280, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });
  await page.waitForSelector("#ws-runtime", { state: "attached" });
  await page.waitForTimeout(400);

  // Every step is a `<details>`, and a mark inside a closed one has no box for
  // the observer to see. Open them all: the participant reaching that line has
  // opened that step, which is the state being checked.
  await page.evaluate(() => document.querySelectorAll("details.ws-step")
    .forEach((d) => { d.open = true; }));
  await page.waitForTimeout(200);

  const marks = await page.evaluate(
    () => document.querySelectorAll('.ws-cue[data-cue="runtime"]').length);
  if (!marks) {
    console.log("  [skip] this subject places no ws:cue runtime mark");
    await ctx.close();
    return;
  }
  check(marks > 0, `${marks} cue mark(s) rendered from the content`);

  // The pane may have opened from a stored preference; the cue is for somebody
  // who has not opened it, so close it first.
  await page.evaluate(() => {
    const close = document.querySelector(".ws-runtime-close");
    if (close && !document.querySelector("#ws-runtime").hidden) close.click();
  });
  await page.waitForTimeout(300);

  const cued = () => page.evaluate(() => {
    const on = document.querySelector(".ws-runtime-toggle.ws-cue-on");
    return {
      any: !!on,
      cta: !!document.querySelector(".ws-runtime-cta.ws-cue-on"),
      handle: !!document.querySelector(".ws-runtime-handle.ws-cue-on"),
      ring: on ? getComputedStyle(on, "::after").animationName : null,
    };
  });

  check(!(await cued()).any, "nothing pulses before the mark is reached");

  // Into the middle band the observer watches. `center` is what a participant
  // scrolling to that line produces.
  await page.evaluate(() => document.querySelector('.ws-cue[data-cue="runtime"]')
    .scrollIntoView({ block: "center", behavior: "instant" }));
  await page.waitForTimeout(500);

  let v = await cued();
  check(v.any, "the launcher pulses once the mark reaches the middle of the screen");
  check(v.ring === "ws-cue-pulse", `the ring is the pulse animation (${v.ring})`);
  // Which one it is depends on where that mark sits; what must hold is that it
  // is the one the participant can actually see.
  const seen = await launcher(page);
  check((v.cta && seen.cta) || (v.handle && seen.handle),
        `it paints the launcher that is on screen (cta=${seen.cta} handle=${seen.handle})`);

  // It ends. Three pulses of 1.4s in workshop.css, cleared on a 4.4s timer in
  // runtime.js — so a little past that, nothing is left lit.
  await page.waitForTimeout(4600);
  check(!(await cued()).any, "and it stops on its own rather than pulsing forever");

  // Once each: a participant who scrolled past and carried on has had it.
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(300);
  await page.evaluate(() => document.querySelector('.ws-cue[data-cue="runtime"]')
    .scrollIntoView({ block: "center", behavior: "instant" }));
  await page.waitForTimeout(700);
  check(!(await cued()).any, "and does not fire again on a scroll-back");

  await ctx.close();
}

/* And the other half of the rule: with the runtime already open there is
 * nothing to point at, so the mark must stay quiet — while staying armed, since
 * the participant may close it and come back to that line.
 */
async function theCueWhenAlreadyOpen(browser) {
  console.log("== The cue with the runtime already open ==");
  const ctx = await browser.newContext();
  const page = await login(ctx, 1280, 900);
  await page.goto(BASE + PART, { waitUntil: "load" });
  await page.waitForSelector("#ws-runtime", { state: "attached" });
  await page.evaluate(() => document.querySelectorAll("details.ws-step")
    .forEach((d) => { d.open = true; }));
  await page.waitForTimeout(300);

  const marks = await page.evaluate(
    () => document.querySelectorAll('.ws-cue[data-cue="runtime"]').length);
  if (!marks) {
    console.log("  [skip] this subject places no ws:cue runtime mark");
    await ctx.close();
    return;
  }

  if ((await launcher(page)).paneOpen === false) {
    await page.evaluate(() => document.querySelector(".ws-runtime-toggle").click());
  }
  await ready(page);
  await page.evaluate(() => document.querySelector('.ws-cue[data-cue="runtime"]')
    .scrollIntoView({ block: "center", behavior: "instant" }));
  await page.waitForTimeout(700);
  const quiet = await page.evaluate(
    () => !document.querySelector(".ws-runtime-toggle.ws-cue-on"));
  check(quiet, "an open runtime is cue enough: nothing pulses");

  // Still armed. Close it, come back to the line, and the cue is there.
  await page.evaluate(() => document.querySelector(".ws-runtime-close").click());
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(300);
  await page.evaluate(() => document.querySelector('.ws-cue[data-cue="runtime"]')
    .scrollIntoView({ block: "center", behavior: "instant" }));
  await page.waitForTimeout(600);
  const armed = await page.evaluate(
    () => !!document.querySelector(".ws-runtime-toggle.ws-cue-on"));
  check(armed, "and the mark is still armed once the runtime is closed again");
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch(
    process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
  try {
    await wideScreen(browser);
    await narrowScreen(browser);
    await workSurvivesTheHandover(browser);
    await advisoryAcrossWindows(browser);
    await theCue(browser);
    await theCueWhenAlreadyOpen(browser);
  } finally {
    await browser.close();
  }
  console.log(fails.length
    ? `\n${fails.length} FAILED:\n  - ${fails.join("\n  - ")}`
    : "\nall ok");
  process.exit(fails.length ? 1 : 0);
})();
