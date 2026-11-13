/* Saved code is encrypted in the browser (PLAN.md §68, issue #82).
 *
 *     node scripts/sealed_workspace_check.js <base-url> <admin-pass> \
 *          [path-to-playwright] [pane|window] [screenshot-dir]
 *
 * scripts/workspace_browser_check.js holds §16 to its promises with accounts
 * that have a password, whose work travels in clear. This one is about the
 * accounts made at the token door, whose work must never be readable on the
 * server:
 *
 *   - what the browser sends is one opaque string, and the text typed in the
 *     runtime is nowhere in it, nor in any request;
 *   - the server refuses such an account's work in clear, and caps the size
 *     of the string, which is all it can check;
 *   - on another machine the same token opens the same work, with no
 *     question asked: the door left the key;
 *   - a browser that lost the key while the session stayed asks for the
 *     personal code in the pane BEFORE the runtime boots, refuses a mistyped
 *     one and somebody else's without sending anything, and opens the work
 *     with the right one;
 *   - "continue without saving" boots the runtime, says that nothing is
 *     saved, and saves nothing;
 *   - a string the key does not open does not stop the runtime, and the next
 *     save replaces it;
 *   - an account with a password still saves in clear, and is refused a
 *     sealed string.
 *
 * Needs Playwright, a synced subject with a runtime whose dist is present,
 * and an instance served on https or on localhost (Web Crypto). It makes one
 * code and one account and deletes them, also when a check throws.
 */
const path = require("path");

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const ADMIN_PASS = process.argv[3];
if (!BASE || !ADMIN_PASS) {
  console.error("usage: node scripts/sealed_workspace_check.js <base-url> <admin-pass> [path-to-playwright] [pane|window] [screenshot-dir]");
  process.exit(2);
}
const { chromium } = require(process.argv[4] || "playwright");
const WHERE = process.argv[5] === "window" ? "window" : "pane";
const SHOTS = process.argv[6] || "";

const RUN = Math.random().toString(36).slice(2, 8).toUpperCase().replace(/[OIL]/g, "X");
const CODE = `SEAL-${RUN}`;
const WORK = `-- travail de A ${RUN}`;
const API = "/api/v1/workshop/workspace";
// A real token that is nobody's here: the test vector of the token door.
const OTHER = "000G-40R4-0M30-E209-185G-R38E-1WQS";

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}

(async () => {
  const browser = await chromium.launch();
  const options = { ignoreHTTPSErrors: true, baseURL: BASE };

  // ---- the admin: one code in, and everything out at the end -------------
  const adminContext = await browser.newContext(options);
  const admin = await adminContext.newPage();
  await admin.goto("/login?staff=1");
  await admin.fill('input[name="name"]', "admin");
  await admin.fill('input[name="password"]', ADMIN_PASS);
  await Promise.all([
    admin.waitForURL((u) => !u.pathname.startsWith("/login")),
    admin.click('form [type="submit"]'),
  ]);
  await admin.goto("/admin/workshop/codes");
  const adminNonce = await admin.evaluate(() => window.init.csrfNonce);
  const codesPage = (form) => adminContext.request.post("/admin/workshop/codes", {
    form: { nonce: adminNonce, ...form }, maxRedirects: 0,
  });
  async function codeId() {
    const html = await (await adminContext.request.get("/admin/workshop/codes")).text();
    const body = html.split("<tbody>")[1].split("</tbody>")[0];
    for (const chunk of body.split("<tr>").slice(1)) {
      if (!chunk.includes(`<code>${CODE}</code>`)) continue;
      const id = chunk.match(/name="id" value="(\d+)"/);
      return id && id[1];
    }
    return null;
  }

  const madeUsers = new Set();
  let adminSaved = false;
  async function cleanup() {
    for (const id of madeUsers) {
      await adminContext.request.delete(`/api/v1/users/${id}`, {
        headers: { "CSRF-Token": adminNonce, "Content-Type": "application/json" },
      });
    }
    const id = await codeId();
    if (id) await codesPage({ action: "delete", id });
    if (adminSaved) await write(admin, { keys: {} });
  }

  const created = await codesPage({ action: "create", code: CODE, campus: `Seal ${RUN}` });
  if (created.status() !== 302) {
    console.error(`could not create ${CODE}: ${created.status()}`);
    await browser.close();
    process.exit(2);
  }

  // ---- a machine: a browser profile, and a record of everything it sends --
  const sent = [];
  async function machine() {
    const context = await browser.newContext(options);
    context.on("request", (request) => {
      sent.push({ url: request.url(), method: request.method(),
                  body: request.postData() || "", headers: JSON.stringify(request.headers()) });
    });
    return context;
  }
  const saves = () => sent.filter((r) => r.method === "POST" && r.url.endsWith(API));

  const me = (page) => page.evaluate(() =>
    fetch("/api/v1/users/me", { credentials: "same-origin" })
      .then((r) => r.json()).then((j) => (j && j.data) || null).catch(() => null));
  const snapshot = (page) => page.evaluate((api) =>
    fetch(api, { credentials: "same-origin" }).then((r) => r.json()).then((j) => j.data), API);
  // A save sent by hand, as the page's session: what the server answers.
  const write = (page, body) => page.evaluate(async ({ api, body }) => {
    const current = await fetch(api, { credentials: "same-origin" }).then((r) => r.json());
    const r = await fetch(api, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "CSRF-Token": window.init.csrfNonce },
      body: JSON.stringify({ runtime: current.data.runtime, ...body }),
    });
    return r.status;
  }, { api: API, body });

  // The part whose page holds the runtime: the first one the index links to.
  let doc = null;
  async function findDoc(page) {
    await page.goto("/workshop", { waitUntil: "domcontentloaded" });
    const slugs = await page.evaluate(() => Array.from(document.querySelectorAll("a[href]"))
      .map((a) => (new URL(a.href).pathname.match(/^\/workshop\/([A-Za-z0-9_-]+)$/) || [])[1])
      .filter(Boolean));
    for (const slug of [...new Set(slugs)]) {
      const html = await (await page.context().request.get(`/workshop/${slug}`)).text();
      if (html.includes('id="ws-runtime"')) return slug;
    }
    return null;
  }

  // Put the runtime's host on screen. Does not wait for it to boot: whether
  // it boots is what is being checked.
  async function show(page) {
    if (WHERE === "window") {
      await page.goto(`/workshop/${doc}/runtime`, { waitUntil: "domcontentloaded" });
    } else {
      await page.goto(`/workshop/${doc}`, { waitUntil: "domcontentloaded" });
      await page.waitForSelector("#ws-runtime", { state: "attached" });
      await page.evaluate(() => localStorage.setItem("ws-runtime-mode", "split"));
      const open = await page.evaluate(() => !document.querySelector("#ws-runtime").hidden);
      if (!open) await page.evaluate(() => document.querySelector(".ws-runtime-toggle").click());
    }
  }
  const booted = (page, timeout = 60000) => page.waitForFunction(
    () => document.querySelector("#ws-runtime").dataset.runtimeReady === "1",
    null, { timeout }).then(() => true, () => false);
  const locked = (page) => page.waitForSelector(".ws-runtime-lock", { state: "visible", timeout: 20000 })
    .then(() => true, () => false);
  const hasFrame = (page) => page.evaluate(() => !!document.querySelector("iframe.ws-runtime-frame"));

  let KEY = null;
  const work = (page) => page.evaluate((k) => localStorage.getItem(k), KEY);
  const setWork = (page, text) => page.evaluate(
    ({ k, text }) => localStorage.setItem(k, text), { k: KEY, text });
  async function waitSealed(page, differentFrom, timeout = 40000) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const got = await snapshot(page);
      if (got.sealed && got.sealed !== differentFrom) return got;
      await page.waitForTimeout(2000);
    }
    return snapshot(page);
  }

  async function signIn(page, typed) {
    await page.goto("/login");
    await page.fill("#ws-door-code", typed);
    await page.click("[data-ws-door-form] [type=submit]");
  }
  // Site data gone, session kept: what a "clear browsing data" with the
  // cookies box unticked leaves behind. Done from a page with no runtime on
  // it: on a live one the poll would see the work vanish and save that.
  const loseKey = async (page) => {
    await page.goto("/workshop", { waitUntil: "domcontentloaded" });
    await page.evaluate(() => new Promise((resolve) => {
      localStorage.clear();
      const gone = indexedDB.deleteDatabase("ws-door");
      gone.onsuccess = gone.onerror = gone.onblocked = () => resolve();
    }));
  };
  const keptKey = (page) => page.evaluate(() => window.wsToken.kept()
    .then((found) => ({ key: found.key instanceof CryptoKey, owner: found.owner })));
  async function typeCode(page, typed) {
    await page.fill("#ws-runtime-lock-code", typed);
    await page.click(".ws-runtime-lock [type=submit]");
  }
  const lockError = async (page) => {
    const p = page.locator(".ws-runtime-lock-error");
    await p.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
    return (await p.isVisible()) ? (await p.textContent()) : null;
  };

  let token = null;
  try {
    console.log(`== A comes in through the token door and works (${WHERE}) ==`);
    const first = await machine();
    const page = await first.newPage();
    page.on("pageerror", (e) => console.log("    (page error) " + String(e).slice(0, 160)));
    await signIn(page, CODE);
    await page.waitForSelector("[data-ws-door-handout]", { state: "visible" });
    token = await page.getAttribute("[data-ws-door-token]", "data-token");
    await page.check("[data-ws-door-saved]");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 }),
      page.click("[data-ws-door-enter]"),
    ]);
    const account = await me(page);
    if (account) madeUsers.add(account.id);
    doc = await findDoc(page);
    if (!doc) throw new Error("no part of this instance shows a runtime: sync a subject that has one, with its dist");

    await show(page);
    check(await booted(page), "the runtime boots with no question: the door left the key");
    KEY = JSON.parse(await page.evaluate(() => localStorage.getItem("ws-workspace-keys")) || "[]")[0];
    if (!KEY) throw new Error("the runtime announced no storage key");
    const empty = await snapshot(page);
    check(/^[0-9a-f]{16}$/.test(empty.seal || ""), "the server says this account seals, and whose key");
    const kept = await keptKey(page);
    check(kept.key && kept.owner === empty.seal, "the key in this browser is that one");

    await setWork(page, WORK);
    const sealed = await waitSealed(page, null);
    check(typeof sealed.sealed === "string" && /^v1\.[\w-]+\.[\w-]+$/.test(sealed.sealed),
          "A's work reaches the server as one sealed string");
    check(JSON.stringify(sealed.keys) === "{}", "and no key of it is given back in clear");
    const marker = [WORK, Buffer.from(WORK).toString("base64").slice(0, -4),
                    Buffer.from(WORK).toString("base64url").slice(0, -4)];
    check(!marker.some((m) => (sealed.sealed || "").includes(m)),
          "what was typed is not readable in what is stored");
    check(saves().length > 0 && saves().every((r) => !/"keys"/.test(r.body)
          && !marker.some((m) => r.body.includes(m)) && !r.body.includes(KEY)),
          "no save carries the text, or even the name of the key it is kept under");

    console.log("== the server holds its half ==");
    check((await write(page, { keys: { [KEY]: "en clair" } })) === 400,
          "a token account's work in clear is refused");
    check((await write(page, { sealed: 7 })) === 400, "so is anything that is not a string");
    check((await write(page, { sealed: "x".repeat(512 * 1024 * 4 / 3 + 1025) })) === 413,
          "and a string over the cap");
    check((await snapshot(page)).sealed === sealed.sealed, "none of them replaced the work");

    console.log("== A on another machine ==");
    const second = await machine();
    const page2 = await second.newPage();
    await signIn(page2, token);
    await page2.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    await show(page2);
    check(await booted(page2), "the runtime boots with no question there either");
    check((await work(page2)) === WORK, "and the work is there, opened with the token's key");

    console.log("== the browser lost the key, the session stayed ==");
    await loseKey(page2);
    const before = sent.length;
    await show(page2);
    check(await locked(page2), "the pane asks for the personal code");
    check(!(await hasFrame(page2)), "and the runtime has not booted behind it");
    if (SHOTS) await page2.screenshot({ path: path.join(SHOTS, `sealed-lock-${WHERE}.png`) });
    const wrong = await page2.getAttribute(".ws-runtime-lock", "data-label-wrong");
    await typeCode(page2, "ABCD-EFGH");
    check(!!wrong && (await lockError(page2)) === wrong, "a mistyped code is refused");
    await typeCode(page2, OTHER);
    check((await lockError(page2)) === wrong && !(await hasFrame(page2)),
          "a real code that is somebody else's is refused too");
    if (SHOTS) await page2.screenshot({ path: path.join(SHOTS, `sealed-lock-wrong-${WHERE}.png`) });
    check(!(await keptKey(page2)).key, "and its key is not kept");
    await typeCode(page2, token.toLowerCase().replace(/-/g, " "));
    check(await booted(page2), "the right code boots the runtime");
    check((await work(page2)) === WORK, "with the work back");
    check((await keptKey(page2)).key, "and the key waits in this browser again");
    const bare = (t) => t.replace(/-/g, "");
    check(!sent.slice(before).some((r) => [token, OTHER].some((t) =>
            (r.url + r.body + r.headers).toUpperCase().includes(t)
            || (r.url + r.body + r.headers).toUpperCase().includes(bare(t)))),
          "no code typed there was sent anywhere");

    console.log("== continue without saving ==");
    await loseKey(page2);
    await show(page2);
    await locked(page2);
    await page2.click(".ws-runtime-lock-skip");
    check(await booted(page2), "the runtime boots");
    const unsaved = await page2.getAttribute("#ws-runtime", "data-label-unsaved");
    check(!!unsaved && (await page2.textContent(".ws-runtime-note")) === unsaved
          && await page2.isVisible(".ws-runtime-note"), "the pane says nothing is being saved");
    check((await work(page2)) === null, "nothing was opened");
    const savesBefore = saves().length;
    await setWork(page2, "-- jamais sauvegarde");
    await page2.waitForTimeout(12000);
    check(saves().length === savesBefore && (await snapshot(page2)).sealed === sealed.sealed,
          "and nothing is saved: the work on the server is untouched");

    console.log("== a string the key does not open ==");
    check((await write(page, { sealed: "v1.AAAA.BBBB" })) === 200, "the server stores it: it cannot tell");
    const third = await machine();
    const page3 = await third.newPage();
    await signIn(page3, token);
    await page3.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    await show(page3);
    check(await booted(page3), "the runtime boots all the same");
    await setWork(page3, WORK + " bis");
    const again = await waitSealed(page3, "v1.AAAA.BBBB");
    check(/^v1\.[\w-]{40,}\.[\w-]{20,}$/.test(again.sealed || ""), "and the next save replaces it");

    console.log("== an account with a password is as before ==");
    await show(admin);
    check(await booted(admin), "the runtime boots for the admin");
    check((await snapshot(admin)).seal === null, "the server says this account does not seal");
    adminSaved = true;
    check((await write(admin, { keys: { [KEY]: "-- admin" } })) === 200, "its work is stored in clear");
    check(((await snapshot(admin)).keys || {})[KEY] === "-- admin", "and read back in clear");
    check((await write(admin, { sealed: "v1.AAAA.BBBB" })) === 400, "a sealed string is refused");
  } catch (e) {
    check(false, "the script ran to its end: " + (e && e.message));
  } finally {
    await cleanup().catch((e) => console.log("    (cleanup) " + e.message));
    await browser.close();
  }
  console.log("\n" + (fails.length ? "FAILURES: " + JSON.stringify(fails) : "ALL GREEN"));
  process.exit(fails.length ? 1 : 0);
})();
