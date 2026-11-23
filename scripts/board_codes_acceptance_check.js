/* A plain board that keeps the code door, in a real browser (PLAN.md §71).
 *
 *     node scripts/board_codes_acceptance_check.js <base-url> <admin-pass> \
 *          [path-to-playwright] [screenshot-dir]
 *
 * scripts/board_codes_check.py holds the three states of the switch over
 * HTTP. This walks the middle one the way people will meet it:
 *
 *   - the admin picks "CTF with codes" on the switch page, and the admin
 *     menu keeps the switch and the codes and nothing else of the workshop;
 *   - a stranger gets the token door in French; the workshop pages and the
 *     supervisor join answer 404;
 *   - a registration code gives a personal code, and Continue lands on
 *     CTFd's own board;
 *   - the personal code opens the same account from another browser;
 *   - two codes are two cohorts, each listing its own account;
 *   - with the challenges closed to participants, a signed-in participant
 *     waits on a page that says the event is not open yet;
 *   - Off closes the door: the plain CTFd sign-in form is back.
 *
 * It changes the switch and the challenge visibility, and puts both back as
 * it found them. It makes two codes and two accounts and deletes all four,
 * also when a check throws. Needs Playwright and an instance served on https
 * or on localhost. Run it on a fresh local instance, not on one in use.
 */
const path = require("path");

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const ADMIN_PASS = process.argv[3];
if (!BASE || !ADMIN_PASS) {
  console.error("usage: node scripts/board_codes_acceptance_check.js <base-url> <admin-pass> [path-to-playwright] [screenshot-dir]");
  process.exit(2);
}
const { chromium } = require(process.argv[4] || "playwright");
const SHOTS = process.argv[5] || "";

const RUN = Math.random().toString(36).slice(2, 8).toUpperCase().replace(/[OIL]/g, "X");
const CODE_A = `BOARD-A-${RUN}`;
const CODE_B = `BOARD-B-${RUN}`;
const TOKEN = /^(?:[0-9A-HJKMNP-TV-Z]{4}-){6}[0-9A-HJKMNP-TV-Z]{4}$/;

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}
const note = (label) => console.log(`  [note] ${label}`);

(async () => {
  const browser = await chromium.launch();
  const options = { ignoreHTTPSErrors: true, baseURL: BASE, viewport: { width: 1280, height: 800 } };
  const shot = async (page, name, full) => {
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name), fullPage: !!full });
  };

  // ---- the admin ---------------------------------------------------------
  const adminContext = await browser.newContext(options);
  const admin = await adminContext.newPage();
  await admin.goto("/login?staff=1");
  await admin.fill('input[name="name"]', "admin");
  await admin.fill('input[name="password"]', ADMIN_PASS);
  await Promise.all([
    admin.waitForURL((u) => !u.pathname.startsWith("/login")),
    admin.click('form [type="submit"]'),
  ]);
  console.log("== the admin signs in ==");
  check(!new URL(admin.url()).pathname.startsWith("/login"), `the staff form lets admin in (${new URL(admin.url()).pathname})`);

  await admin.goto("/admin/workshop/plugin");
  const adminNonce = await admin.evaluate(() => window.init.csrfNonce);
  const api = { "CSRF-Token": adminNonce, "Content-Type": "application/json" };
  const stateNow = () => admin.locator('input[name="state"]:checked').getAttribute("value");
  async function setState(state) {
    await admin.goto("/admin/workshop/plugin");
    await admin.check(`#ws-state-${state}`);
    await Promise.all([
      admin.waitForNavigation(),
      admin.click('form:has(#ws-state-board) [type="submit"]'),
    ]);
    await admin.goto("/admin/workshop/plugin");
  }
  async function visibility(value) {
    if (value === undefined) {
      const r = await adminContext.request.get("/api/v1/configs/challenge_visibility", { headers: api });
      return r.ok() ? (await r.json()).data.value : null;
    }
    return adminContext.request.patch("/api/v1/configs", { headers: api, data: { challenge_visibility: value } });
  }
  const codesPage = (form) => adminContext.request.post("/admin/workshop/codes", {
    form: { nonce: adminNonce, ...form }, maxRedirects: 0,
  });
  async function codeRow(code) {
    const html = await (await adminContext.request.get("/admin/workshop/codes")).text();
    const body = (html.split("<tbody>")[1] || "").split("</tbody>")[0];
    for (const chunk of body.split("<tr>").slice(1)) {
      if (!chunk.includes(`<code>${code}</code>`)) continue;
      const id = chunk.match(/name="id" value="(\d+)"/);
      const count = chunk.match(/<summary>\s*(\d+)/);
      return { id: id && id[1], accounts: count ? Number(count[1]) : 0, html: chunk };
    }
    return null;
  }

  const stateBefore = await stateNow();
  const visibleBefore = await visibility();
  note(`found: switch on "${stateBefore}", challenge visibility "${visibleBefore}"`);

  const madeUsers = new Set();
  async function cleanup() {
    await visibility(visibleBefore || "private");
    await setState(stateBefore || "workshop");
    for (const id of madeUsers) {
      await adminContext.request.delete(`/api/v1/users/${id}`, { headers: api });
    }
    for (const code of [CODE_A, CODE_B]) {
      const row = await codeRow(code);
      if (row && row.id) await codesPage({ action: "delete", id: row.id });
    }
  }
  const visitor = () => browser.newContext(options);
  const me = (page) => page.evaluate(() =>
    fetch("/api/v1/users/me", { credentials: "same-origin" })
      .then((r) => (r.redirected ? null : r.json()))
      .then((j) => (j && j.data ? j.data : null)).catch(() => null));
  // Through the door with a registration code: the personal code, the tick, Continue.
  async function enter(page, code) {
    await page.goto("/login");
    await page.fill("#ws-door-code", code);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("[data-ws-door-handout]:not([hidden])");
    const token = await page.getAttribute("[data-ws-door-token]", "data-token");
    await page.check("[data-ws-door-saved]");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.startsWith("/login")),
      page.click("[data-ws-door-enter]"),
    ]);
    return token;
  }

  try {
    // ---- the switch -------------------------------------------------------
    console.log("== the admin picks CTF with codes ==");
    await visibility("private");
    await setState("board");
    check((await stateNow()) === "board", "the switch page now has CTF with codes ticked");
    check((await admin.locator("h2 .badge").innerText()).trim() === "CTF with codes", "and says so in its heading");
    check(await admin.locator("#ws-state-workshop").count() === 1 && await admin.locator("#ws-state-off").count() === 1,
          "the two other states are still offered");
    const menu = await admin.evaluate(() => [...document.querySelectorAll('a[href^="/admin/workshop"]')]
      .map((a) => new URL(a.href).pathname));
    note(`workshop links in the admin menu: ${[...new Set(menu)].join(", ")}`);
    check(menu.includes("/admin/workshop/plugin") && menu.includes("/admin/workshop/codes"), "the admin menu keeps the switch and the codes");
    check(![...new Set(menu)].some((p) => !["/admin/workshop/plugin", "/admin/workshop/codes"].includes(p)),
          "and no other workshop page");
    await shot(admin, "1-switch.png", true);

    const a = await codesPage({ action: "create", code: CODE_A, campus: "Campus A", event: "Recette A", event_date: "2026-11-23" });
    const b = await codesPage({ action: "create", code: CODE_B, campus: "Campus B", event: "Recette B", event_date: "2026-11-24" });
    check(a.status() === 302 && b.status() === 302, "two test codes are made on the codes page");

    // ---- nobody in particular --------------------------------------------
    console.log("== a stranger sees the token door, and no workshop ==");
    const anonymous = await visitor();
    const page = await anonymous.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    await page.goto("/");
    check(new URL(page.url()).pathname === "/login", "/ lands on /login");
    const doorText = (await page.locator(".epi-login-main").innerText()).replace(/\s+/g, " ");
    check(/ton code/i.test(doorText) && /Continuer/.test(doorText), "the door speaks French (Ton code, Continuer)");
    check((await page.locator(".epi-login-main input:visible").count()) === 1, "one box: no name, no e-mail, no password");
    await shot(page, "2-door.png");
    for (const gone of ["/workshop", "/toolbox", "/supervisor/join"]) {
      const r = await anonymous.request.get(gone, { maxRedirects: 0 });
      check(r.status() === 404, `${gone} answers 404 (${r.status()})`);
    }
    const reg = await anonymous.request.get("/register", { maxRedirects: 0 });
    check(reg.status() !== 200, `core's /register is not offered (${reg.status()})`);
    const ch = await anonymous.request.get("/challenges", { maxRedirects: 0 });
    check(ch.status() === 302 && /\/login/.test(ch.headers()["location"] || ""), "the board sends a stranger to the door");

    // ---- code A: in, on the board ----------------------------------------
    console.log("== a registration code leads to the board ==");
    const token = await enter(page, CODE_A.toLowerCase());
    check(TOKEN.test(token || ""), "a personal code is handed out: seven groups of four characters");
    const landed = new URL(page.url()).pathname;
    check(landed === "/challenges", `the tick and Continue land on ${landed}`);
    const account = await me(page);
    if (account) madeUsers.add(account.id);
    check(!!account && /^[A-ZÉ][a-zéèêï]+-[A-ZÉ][a-zéèê]+-\d{2,4}$/.test(account.name), `signed in as ${account && account.name}, a name nobody typed`);
    await page.waitForLoadState("networkidle");
    check(!/workshop/i.test(await page.locator("nav").first().innerText()), "the navigation bar has no workshop link");
    await shot(page, "3-board.png");
    check(pageErrors.length === 0, `no script error on the way (${pageErrors.join(" | ").slice(0, 200)})`);

    // ---- back with the token ---------------------------------------------
    console.log("== out, and back in from another browser ==");
    await page.goto("/logout");
    const other = await visitor();
    const back = await other.newPage();
    await back.goto("/login");
    await back.fill("#ws-door-code", token);
    await back.click("[data-ws-door-form] [type=submit]");
    await back.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    const again = await me(back);
    check(!!again && !!account && again.id === account.id, "the personal code opens the same account");
    check(new URL(back.url()).pathname === "/challenges", "and lands on the board too");

    // ---- two cohorts -----------------------------------------------------
    console.log("== two codes, two cohorts ==");
    const third = await visitor();
    const second = await third.newPage();
    await enter(second, CODE_B);
    const accountB = await me(second);
    if (accountB) madeUsers.add(accountB.id);
    const rowA = await codeRow(CODE_A), rowB = await codeRow(CODE_B);
    check(rowA.accounts === 1 && rowB.accounts === 1, "each code lists one account");
    check(!!account && !!accountB && rowA.html.includes(account.name) && !rowA.html.includes(accountB.name)
          && rowB.html.includes(accountB.name) && !rowB.html.includes(account.name), "its own, and not the other's");
    check(rowA.html.includes("Campus A") && rowB.html.includes("Campus B"), "each carries its campus");
    await admin.goto("/admin/workshop/codes");
    for (const s of await admin.locator("details summary").all()) await s.click().catch(() => {});
    if (SHOTS) await admin.locator("table").first().screenshot({ path: path.join(SHOTS, "4-codes.png") });

    // ---- not open yet ----------------------------------------------------
    console.log("== the board closed to participants: a waiting page ==");
    await visibility("admins");
    const fourth = await visitor();
    const early = await fourth.newPage();
    await early.goto("/login");
    await early.fill("#ws-door-code", token);
    await early.click("[data-ws-door-form] [type=submit]");
    await early.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    check(new URL(early.url()).pathname === "/door/waiting", `a participant lands on ${new URL(early.url()).pathname}`);
    const waiting = (await early.locator("body").innerText()).replace(/\s+/g, " ");
    check(/L'événement n'est pas encore ouvert/i.test(waiting) && /Reviens quand il sera ouvert/.test(waiting),
          "it says, in French, that the event is not open yet");
    check(!/atelier/i.test(waiting), "and does not speak of a workshop");
    await shot(early, "5-waiting.png");
    await visibility("private");

    // ---- off -------------------------------------------------------------
    console.log("== Off closes the door ==");
    await setState("off");
    check((await stateNow()) === "off", "the switch page now has Off ticked");
    const plain = await (await visitor()).newPage();
    await plain.goto("/login");
    check(await plain.locator("#ws-door-code").count() === 0 && await plain.locator('input[name="password"]').count() === 1,
          "/login is CTFd's own form again: a name and a password, no code box");
    await shot(plain, "6-off.png");
    const codesOff = await adminContext.request.get("/admin/workshop/codes", { maxRedirects: 0 });
    check(codesOff.status() === 404, `the codes page answers 404 (${codesOff.status()})`);
  } catch (e) {
    console.log(`  [FAIL] the pass stopped: ${e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e}`);
    fails.push("stopped: " + e);
  } finally {
    let clean = false;
    try {
      await cleanup();
      const left = [await codeRow(CODE_A), await codeRow(CODE_B)];
      clean = left.every((r) => r === null) && (await stateNow()) === (stateBefore || "workshop")
        && (await visibility()) === (visibleBefore || "private");
    } catch (e) {
      note(`cleanup stopped: ${e}`);
    }
    console.log(`  [${clean ? "ok" : "FAIL"}] the codes and accounts are deleted, the switch and the visibility are as found`);
    if (!clean) fails.push("cleanup");
    await browser.close();
  }
  console.log(fails.length ? `\nFAILURES: ${JSON.stringify(fails)}` : "\nALL GREEN");
  process.exit(fails.length ? 1 : 0);
})();
