/* The crux of PLAN.md §16, in a real browser: one machine, two students.
 *
 *     node scripts/workspace_browser_check.js [base-url] [path-to-playwright]
 *
 * Optional, and NOT part of scripts/phase2_validate.py: it needs Playwright and
 * a real runtime dist, while the suite must run anywhere. The suite covers the
 * endpoints; what only a browser can prove is the client-side rule that decides
 * WHICH copy wins, so that is what this exercises.
 *
 * One browser context throughout — that IS the shared classroom PC, since the
 * point is that localStorage follows the machine, not the account. Needs an
 * instance with a subject synced and its runtime dist built; it registers two
 * throwaway accounts, so give it a disposable instance or delete them after.
 */
const PLAYWRIGHT = process.argv[3]
  || "/home/kc/.npm/_npx/e41f203b7505f1fb/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);

const BASE = process.argv[2] || "http://localhost:8082";
const CART = "tic80-web-editor-cart";   // the key adapters/tic80.js announces
const tag = Math.random().toString(36).slice(2, 8);
const A = { name: "alice" + tag, pw: "pwalice" };
const B = { name: "bob" + tag, pw: "pwbob" };

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}

async function nonce(page, path) {
  await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
  return page.evaluate(() => {
    const m = document.querySelector('input[name="nonce"]');
    if (m) return m.value;
    return window.init && window.init.csrfNonce;
  });
}

async function register(page, who) {
  const n = await nonce(page, "/register");
  await page.evaluate(async ({ who, n, BASE }) => {
    const body = new URLSearchParams({
      name: who.name, email: who.name + "@example.com",
      password: who.pw, nonce: n, _submit: "Submit",
    });
    await fetch(BASE + "/register", { method: "POST", body, credentials: "same-origin" });
  }, { who, n, BASE });
}

async function login(page, who) {
  const n = await nonce(page, "/login");
  await page.evaluate(async ({ who, n, BASE }) => {
    const body = new URLSearchParams({
      name: who.name, password: who.pw, nonce: n, _submit: "Submit",
    });
    await fetch(BASE + "/login", { method: "POST", body, credentials: "same-origin" });
  }, { who, n, BASE });
}

async function logout(page) {
  await page.goto(BASE + "/logout", { waitUntil: "domcontentloaded" });
}

// Open the workshop page, open the runtime pane, and wait for the adapter's
// handshake — that is when the host knows which storage keys to watch.
async function openPane(page) {
  await page.goto(BASE + "/workshop/starter1", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#ws-runtime", { state: "attached" });
  const open = await page.evaluate(() => !document.querySelector("#ws-runtime").hidden);
  if (!open) await page.click(".ws-runtime-toggle");
  await page.waitForFunction(
    () => document.querySelector("#ws-runtime").dataset.runtimeReady === "1",
    null, { timeout: 60000 });
}

const cartOf = (page) => page.evaluate((k) => localStorage.getItem(k), CART);
const setCart = (page, text) => page.evaluate(
  ({ k, text }) => localStorage.setItem(k, JSON.stringify(
    { version: 1, text, ext: "lua", savedAt: Date.now() })),
  { k: CART, text });

async function serverCart(page) {
  return page.evaluate(async (BASE) => {
    const r = await fetch(BASE + "/api/v1/workshop/workspace", { credentials: "same-origin" });
    const body = await r.json();
    return body.data.keys["tic80-web-editor-cart"] || null;
  }, BASE);
}

// The host saves on a 5 s poll; give it a couple of cycles.
async function waitForSave(page, needle, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const stored = await serverCart(page);
    if (stored && stored.includes(needle)) return true;
    await page.waitForTimeout(2000);
  }
  return false;
}

(async () => {
  const browser = await chromium.launch();
  // ONE context for the whole run: one machine, one browser profile.
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") console.log("    (console error) " + m.text().slice(0, 120));
  });

  console.log("== student A works on the shared PC ==");
  await register(page, A);
  await login(page, A);
  await openPane(page);
  const watched = await page.evaluate(() => localStorage.getItem("ws-workspace-keys"));
  check(!!watched && watched.includes(CART),
        "the host learned the runtime's storage keys from the handshake");
  await setCart(page, "-- travail de A");
  check(await waitForSave(page, "travail de A"),
        "A's work reaches the server without A doing anything");
  const ownerA = await page.evaluate(() => localStorage.getItem("ws-workspace-owner"));
  check(!!ownerA, "the browser bucket is stamped with an owner");

  console.log("\n== student B sits down at the same machine ==");
  await logout(page);
  await register(page, B);
  await login(page, B);
  await openPane(page);
  const seenByB = await cartOf(page);
  check(!seenByB || !seenByB.includes("travail de A"),
        "B does NOT inherit A's cart — the shared classroom PC, fixed");
  check((await page.evaluate(() => localStorage.getItem("ws-workspace-owner"))) !== ownerA,
        "the bucket now belongs to B");
  await setCart(page, "-- travail de B");
  check(await waitForSave(page, "travail de B"), "B's own work saves under B");

  console.log("\n== A comes back, same machine ==");
  await logout(page);
  await login(page, A);
  await openPane(page);
  const backA = await cartOf(page);
  check(!!backA && backA.includes("travail de A"),
        "A's work is restored from the server, not B's");
  check(!backA.includes("travail de B"), "and B's work is gone from A's editor");

  console.log("\n== A on a different machine ==");
  const fresh = await browser.newContext();       // empty localStorage: another PC
  const page2 = await fresh.newPage();
  await login(page2, A);
  await openPane(page2);
  const elsewhere = await cartOf(page2);
  check(!!elsewhere && elsewhere.includes("travail de A"),
        "the same work follows A to a machine that has never seen them");

  console.log("\n== local work outranks a stale snapshot ==");
  await setCart(page2, "-- tout nouveau");
  await page2.evaluate(() => localStorage.setItem("ws-workspace-local", String(Date.now())));
  await page2.reload({ waitUntil: "domcontentloaded" });
  await openPane(page2);
  const kept = await cartOf(page2);
  check(!!kept && kept.includes("tout nouveau"),
        "a reload before the next save does not clobber unsaved work");

  await browser.close();
  console.log("\n" + (fails.length ? "FAILURES: " + JSON.stringify(fails) : "ALL GREEN"));
  process.exit(fails.length ? 1 : 0);
})();
