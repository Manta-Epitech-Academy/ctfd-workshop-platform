/* A standalone instance, end to end, in a real browser (PLAN.md §62).
 *
 *     node scripts/standalone_acceptance_check.js <base-url> <admin-pass> \
 *          [runtime-id|none] [path-to-playwright] [screenshot-dir]
 *
 * scripts/token_door_browser_check.js holds the door itself. This holds what
 * is around it on an instance somebody is about to use, with its real content
 * and behind its real proxies:
 *
 *   - a stranger gets the token door in French, no anonymous address leaves
 *     the instance, and core's /register is closed;
 *   - a registration code leads to the workshop, a first step can be done,
 *     the steps show their XP, and the runtime named on the command line
 *     boots in its pane, served by the instance (`none` for a subject that
 *     has no runtime);
 *   - the personal code opens the same account, with its progress, from
 *     another browser, and one nobody kept opens nothing;
 *   - two codes are two cohorts: each carries its campus, event and date and
 *     lists its own account;
 *   - no request carries a personal code, and an export of the database
 *     holds neither a personal code nor a sign-in value.
 *
 * Changes no configuration, so it can run on an instance whose settings are
 * preset. It makes two codes and two accounts and deletes all four, also
 * when a check throws. Needs Playwright and an instance served on https or
 * on localhost, with a subject synced whose first step is an `ack`.
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const ADMIN_PASS = process.argv[3];
if (!BASE || !ADMIN_PASS) {
  console.error("usage: node scripts/standalone_acceptance_check.js <base-url> <admin-pass> [runtime-id|none] [path-to-playwright] [screenshot-dir]");
  process.exit(2);
}
const RUNTIME = process.argv[4] || "none";
const { chromium } = require(process.argv[5] || "playwright");
const SHOTS = process.argv[6] || "";
const HOST = new URL(BASE).host;

const RUN = Math.random().toString(36).slice(2, 8).toUpperCase().replace(/[OIL]/g, "X");
const CODE_A = `ACCEPT-A-${RUN}`;
const CODE_B = `ACCEPT-B-${RUN}`;
const TOKEN = /^(?:[0-9A-HJKMNP-TV-Z]{4}-){6}[0-9A-HJKMNP-TV-Z]{4}$/;

function signinValue(token) {
  const A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  let bits = 0n;
  for (const c of token.replace(/-/g, "").slice(0, 26)) bits = (bits << 5n) | BigInt(A.indexOf(c));
  const raw = Buffer.from((bits >> 2n).toString(16).padStart(32, "0"), "hex");
  return Buffer.from(crypto.hkdfSync("sha256", raw, "ctfd-workshop-token-door-v1", "sign-in", 32))
    .toString("hex");
}

// Just enough of a zip reader for CTFd's export: central directory, stored or deflated.
function unzip(buf) {
  const out = {};
  let eocd = buf.length - 22;
  while (eocd >= 0 && buf.readUInt32LE(eocd) !== 0x06054b50) eocd--;
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  for (let i = 0; i < count; i++) {
    const method = buf.readUInt16LE(p + 10);
    const csize = buf.readUInt32LE(p + 20);
    const nlen = buf.readUInt16LE(p + 28), elen = buf.readUInt16LE(p + 30), clen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.slice(p + 46, p + 46 + nlen).toString();
    const lnlen = buf.readUInt16LE(local + 26), lelen = buf.readUInt16LE(local + 28);
    const data = buf.slice(local + 30 + lnlen + lelen, local + 30 + lnlen + lelen + csize);
    out[name] = method === 0 ? data : zlib.inflateRawSync(data);
    p += 46 + nlen + elen + clen;
  }
  return out;
}

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}
const note = (label) => console.log(`  [note] ${label}`);

(async () => {
  const browser = await chromium.launch();
  const options = { ignoreHTTPSErrors: true, baseURL: BASE };
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
  await admin.goto("/admin/workshop/codes");
  const adminNonce = await admin.evaluate(() => window.init.csrfNonce);
  const codesPage = (form) => adminContext.request.post("/admin/workshop/codes", {
    form: { nonce: adminNonce, ...form }, maxRedirects: 0,
  });
  async function codeRow(code) {
    const html = await (await adminContext.request.get("/admin/workshop/codes")).text();
    const body = html.split("<tbody>")[1].split("</tbody>")[0];
    for (const chunk of body.split("<tr>").slice(1)) {
      if (!chunk.includes(`<code>${code}</code>`)) continue;
      const id = chunk.match(/name="id" value="(\d+)"/);
      const count = chunk.match(/<summary>\s*(\d+)/);
      return { id: id && id[1], accounts: count ? Number(count[1]) : 0, html: chunk };
    }
    return null;
  }
  const madeUsers = new Set();
  async function cleanup() {
    for (const id of madeUsers) {
      await adminContext.request.delete(`/api/v1/users/${id}`, {
        headers: { "CSRF-Token": adminNonce, "Content-Type": "application/json" },
      });
    }
    for (const code of [CODE_A, CODE_B]) {
      const row = await codeRow(code);
      if (row && row.id) await codesPage({ action: "delete", id: row.id });
    }
  }

  const sent = [];
  async function visitor(extra) {
    const context = await browser.newContext({ ...options, ...extra });
    context.on("request", (request) => {
      sent.push({ url: request.url(), body: request.postData() || "",
                  headers: JSON.stringify(request.headers()) });
    });
    return context;
  }
  const me = (page) => page.evaluate(() =>
    fetch("/api/v1/users/me", { credentials: "same-origin" })
      .then((r) => (r.redirected ? null : r.json()))
      .then((j) => (j && j.data ? j.data : null)).catch(() => null));
  const doorError = async (page) => {
    const p = page.locator("#ws-door-error");
    return (await p.isVisible()) ? (await p.textContent()).trim() : null;
  };

  try {
    const a = await codesPage({ action: "create", code: CODE_A, campus: "Campus A", event: "Recette A", event_date: "2026-11-10" });
    const b = await codesPage({ action: "create", code: CODE_B, campus: "Campus B", event: "Recette B", event_date: "2026-11-11" });
    check(a.status() === 302 && b.status() === 302, "two test codes are made on the codes page");

    // ---- nobody in particular --------------------------------------------
    console.log("== a stranger sees the token door and nothing else ==");
    const anonymous = await visitor();
    const page = await anonymous.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    await page.goto("/");
    check(new URL(page.url()).pathname === "/login" && new URL(page.url()).host === HOST, "/ lands on /login, on this instance");
    const doorText = (await page.locator(".epi-login-main").innerText()).replace(/\s+/g, " ");
    note(`door text: ${doorText.slice(0, 200)}`);
    check(/ton code/i.test(doorText) && /Continuer/.test(doorText) && !/Your code|Continue\b|Sign in/.test(doorText), "the door speaks French (Ton code, Continuer)");
    check((await page.locator(".epi-login-main input:visible").count()) === 1, "one box: no name, no e-mail, no password");
    check(!/jump/i.test(await page.locator("body").innerText()), "the word Jump is nowhere on the page");
    const away = await page.evaluate(() => [...document.querySelectorAll("a[href], form[action]")]
      .map((e) => new URL(e.getAttribute("href") || e.getAttribute("action"), location.href))
      .filter((u) => /^https?:$/.test(u.protocol) && u.host !== location.host).map((u) => u.host));
    check(!away.some((h) => /jump/i.test(h)), `and no link leaves for Jump (other hosts linked: ${[...new Set(away)].join(", ") || "none"})`);
    await shot(page, "1-door.png");

    for (const where of ["/workshop", "/challenges", "/scoreboard", "/users", "/settings", "/toolbox", "/workshop/no-such-part"]) {
      const r = await anonymous.request.get(where, { maxRedirects: 0 });
      const to = r.headers()["location"] || "";
      const same = !to || to.startsWith("/") || new URL(to, BASE).host === HOST;
      check(same && [200, 302, 403, 404].includes(r.status()), `anonymous ${where}: ${r.status()} ${to || ""} stays on this instance`);
    }
    const reg = await anonymous.request.get("/register", { maxRedirects: 0 });
    check(reg.status() === 404, `core's /register is closed (${reg.status()})`);
    const regPost = await anonymous.request.post("/register", { form: { name: "x", email: "x@x.fr", password: "xxxxxxxx" }, maxRedirects: 0 });
    check(![200, 302].includes(regPost.status()), `and posting to it makes nothing (${regPost.status()})`);
    for (const gone of ["/jump", "/jump/login", "/auth/jump"]) {
      const r = await anonymous.request.get(gone, { maxRedirects: 0 });
      const to = r.headers()["location"] || "";
      check(!to || new URL(to, BASE).host === HOST, `anonymous ${gone}: ${r.status()} goes nowhere else`);
    }

    // ---- a wrong code -----------------------------------------------------
    console.log("== a wrong code ==");
    await page.goto("/login");
    await page.fill("#ws-door-code", `NOPE-${RUN}`);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("#ws-door-error:not([hidden])");
    const refusal = await doorError(page);
    note(`refusal: ${refusal}`);
    check(!!refusal && /[éèàç]|code/i.test(refusal) && !/invalid|wrong|error/i.test(refusal), "a French refusal");
    check(await page.locator("[data-ws-door-handout]").isHidden(), "no personal code is shown");

    // ---- code A: in -------------------------------------------------------
    console.log("== a registration code gives a personal code, once ==");
    await page.fill("#ws-door-code", CODE_A.toLowerCase());
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("[data-ws-door-handout]:not([hidden])");
    const token = await page.getAttribute("[data-ws-door-token]", "data-token");
    check(TOKEN.test(token || ""), "seven groups of four characters");
    check(await page.locator("[data-ws-door-enter]").isDisabled(), "Continue waits for the tick");
    check((await codeRow(CODE_A)).accounts === 0, "no account exists before the tick");
    check(await page.locator("[data-ws-door-qr]").isVisible() && await page.locator("[data-ws-door-download]").isVisible()
          && await page.locator("[data-ws-door-copy]").isVisible(), "a file, a copy button and a QR code to keep it");
    const handout = (await page.locator("[data-ws-door-handout]").innerText()).replace(/\s+/g, " ");
    note(`handout text: ${handout.slice(0, 260)}`);
    await shot(page, "2-handout.png", true);
    await page.check("[data-ws-door-saved]");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.startsWith("/login")),
      page.click("[data-ws-door-enter]"),
    ]);
    const landed = new URL(page.url()).pathname;
    check(landed === "/workshop" || /^\/workshop\/[a-z0-9-]+$/.test(landed), `the tick and Continue land on ${landed}`);
    const account = await me(page);
    if (account) madeUsers.add(account.id);
    check(!!account && /^[A-ZÉ][a-zéèêï]+-[A-ZÉ][a-zéèê]+-\d{2,4}$/.test(account.name), `signed in as ${account && account.name}, a name nobody typed`);
    check(!!account && /@token\.invalid$/.test(account.email || ""), "its e-mail field is a placeholder that reaches nobody");
    check((await codeRow(CODE_A)).accounts === 1, "the code now has one account");

    // ---- the content ------------------------------------------------------
    console.log("== the workshop ==");
    const title = await page.title();
    const cards = await page.locator(".ws-card").count();
    note(`title: ${title}; ${cards} part card(s)`);
    const single = landed !== "/workshop";
    if (single) note("a subject with one document: the workshop opens on that document, there is no index of parts");
    check(!!title && (single || cards >= 1), single ? "the page shows the subject" : "the index shows the subject and its parts");
    const navText = (await page.locator("nav").first().innerText()).replace(/\s+/g, " ");
    check(/Atelier/.test(navText) && !/Settings|Paramètres|Register|Inscription/i.test(navText), `the navbar: ${navText.slice(0, 80)}`);
    await shot(page, "3-index.png", true);

    const solves0 = await page.evaluate(() => fetch("/api/v1/users/me/solves").then((r) => r.json()).then((j) => j.data.length));
    const first = page.locator(".ws-step.ws-current .ws-form").first();
    const kind = await first.getAttribute("data-answer-kind");
    note(`first step answer kind: ${kind}`);
    await first.locator(".ws-submit-btn").click();
    await page.waitForFunction((n) => fetch("/api/v1/users/me/solves").then((r) => r.json()).then((j) => j.data.length > n), solves0, { timeout: 15000 });
    const after = await me(page);
    check(true, "the first step (a read-and-confirm one) is done with one click");
    note(`score after one step: ${after.score}`);
    await page.reload();
    const overall = (await page.locator(".ws-overall").first().innerText()).replace(/\s+/g, " ");
    note(`progress badge: ${overall}`);
    const partLink = single ? landed : await page.evaluate(() => {
      const links = [...document.querySelectorAll(".ws-card a[href^='/workshop/'], a.ws-card[href^='/workshop/']")];
      return links.length ? links[0].getAttribute("href").split("#")[0] : null;
    });
    check(!!partLink, `a part opens: ${partLink}`);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(partLink);
    const steps = await page.locator(".ws-stepper-steps .ws-step, .ws-part .ws-step").count();
    const lockedNow = await page.locator(".ws-step.ws-locked").count();
    note(`${partLink}: ${steps} step(s) on the page, ${lockedNow} locked`);
    check(steps >= 3, "the part page lists its steps");
    check(await page.locator(".ws-step.ws-current").count() >= 1, "one step is the current one");
    const xp = await page.locator(".ws-step-meta").allTextContents();
    note(`XP labels: ${xp.map((t) => t.trim()).join(", ")}`);
    check(xp.length >= 3 && xp.every((t) => /^\d+ XP$/.test(t.trim())), `the steps show their XP (${xp.length} labels)`);
    await shot(page, "4-part.png");

    // ---- the runtime ------------------------------------------------------
    if (RUNTIME !== "none") {
      console.log(`== the runtime (${RUNTIME}) ==`);
      check(await page.locator("#ws-runtime").count() === 1, "the page has a runtime pane");
      await page.click(".ws-runtime-cta");
      await page.waitForFunction(
        () => document.querySelector("#ws-runtime").dataset.runtimeReady === "1", null, { timeout: 90000 });
      check(true, "the pane opens and the runtime says it is ready");
      const src = await page.getAttribute("iframe.ws-runtime-frame", "src");
      const full = new URL(src, BASE);
      check(full.host === HOST && full.pathname.startsWith(`/runtime/${RUNTIME}/`), `its frame is served by this instance: ${full.pathname}`);
      const frame = page.frames().find((f) => f.url().includes(`/runtime/${RUNTIME}/`));
      const inside = frame ? await frame.evaluate(() => ({
        canvas: document.querySelectorAll("canvas").length,
        nodes: document.body.querySelectorAll("*").length,
      })) : null;
      note(`inside the frame: ${JSON.stringify(inside)}`);
      check(!!inside && inside.nodes > 10, "and it has drawn its interface");
      await page.waitForTimeout(1500);
      await shot(page, "5-runtime.png");
      const pop = await anonymous.request.get(`${partLink}/runtime`, { maxRedirects: 0 });
      check(pop.status() === 200, `the pop-out page answers (${pop.status()})`);
    } else {
      console.log("== no runtime on this subject ==");
      check(await page.locator(".ws-runtime-cta").count() === 0, "the page offers no runtime button");
    }
    check(pageErrors.length === 0, `no script error on the way (${pageErrors.join(" | ").slice(0, 200)})`);

    // ---- out, and back with the token ------------------------------------
    console.log("== out, and back in from another browser ==");
    await page.goto("/logout");
    await page.goto("/workshop");
    check(new URL(page.url()).pathname === "/login", "signed out: the workshop sends back to the door");

    const other = await visitor();
    const back = await other.newPage();
    await back.goto("/login");
    await back.fill("#ws-door-code", token);
    await back.click("[data-ws-door-form] [type=submit]");
    await back.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    const again = await me(back);
    check(!!again && again.id === account.id, "the personal code opens the same account");
    check(!!again && again.score === after.score, `with its progress (${again && again.score} points)`);
    const solvesBack = await back.evaluate(() => fetch("/api/v1/users/me/solves").then((r) => r.json()).then((j) => j.data.length));
    check(solvesBack === solves0 + 1, "the step done earlier is still done");

    // ---- a token nobody holds --------------------------------------------
    console.log("== a personal code that opens nothing ==");
    const third = await visitor();
    const stray = await third.newPage();
    await stray.goto("/login");
    await stray.fill("#ws-door-code", CODE_B);
    await stray.click("[data-ws-door-form] [type=submit]");
    await stray.waitForSelector("[data-ws-door-handout]:not([hidden])");
    const unused = await stray.getAttribute("[data-ws-door-token]", "data-token");
    await stray.goto("/login");
    check((await codeRow(CODE_B)).accounts === 0, "leaving the personal code screen made no account");
    await stray.fill("#ws-door-code", unused);
    await stray.click("[data-ws-door-form] [type=submit]");
    await stray.waitForSelector("#ws-door-error:not([hidden])");
    check((await doorError(stray)) === refusal, "a personal code nobody kept gets the same refusal");
    check((await me(stray)) === null, "and signs nobody in");

    // ---- code B: another cohort ------------------------------------------
    console.log("== two codes, two cohorts ==");
    await stray.fill("#ws-door-code", CODE_B);
    await stray.click("[data-ws-door-form] [type=submit]");
    await stray.waitForSelector("[data-ws-door-handout]:not([hidden])");
    const tokenB = await stray.getAttribute("[data-ws-door-token]", "data-token");
    await stray.check("[data-ws-door-saved]");
    await Promise.all([
      stray.waitForURL((u) => !u.pathname.startsWith("/login")),
      stray.click("[data-ws-door-enter]"),
    ]);
    const accountB = await me(stray);
    if (accountB) madeUsers.add(accountB.id);
    check(!!accountB && accountB.id !== account.id && tokenB !== token, `a second account, ${accountB && accountB.name}`);
    const rowA = await codeRow(CODE_A), rowB = await codeRow(CODE_B);
    check(rowA.accounts === 1 && rowB.accounts === 1, "each code counts its own account");
    check(rowA.html.includes("Campus A") && rowA.html.includes("Recette A") && rowA.html.includes("2026-11-10"),
          "code A carries its campus, event and date");
    check(rowB.html.includes("Campus B") && rowB.html.includes("Recette B") && rowB.html.includes("2026-11-11"),
          "code B carries its own");
    check(rowA.html.includes(account.name) && !rowA.html.includes(accountB.name)
          && rowB.html.includes(accountB.name) && !rowB.html.includes(account.name),
          "and each lists its own account, not the other's");
    await admin.goto("/admin/workshop/codes");
    await shot(admin, "6-codes-admin.png", true);

    // ---- what the token touched ------------------------------------------
    console.log("== the personal code never leaves the browser ==");
    const bare = token.replace(/-/g, ""), bareB = tokenB.replace(/-/g, "");
    const leaked = sent.filter((r) => [token, bare, tokenB, bareB].some((t) =>
      r.url.includes(t) || r.body.includes(t) || r.headers.includes(t)));
    check(leaked.length === 0, `${sent.length} requests recorded, none carries a personal code`);

    console.log("== what an export of the database holds ==");
    const exp = await adminContext.request.get("/admin/export", { timeout: 120000 });
    check(exp.status() === 200, `the export downloads (${exp.status()})`);
    const files = unzip(await exp.body());
    const names = Object.keys(files).filter((n) => n.startsWith("db/"));
    const everything = names.map((n) => files[n].toString("utf8")).join("\n");
    check(names.length > 10, `${names.length} tables read`);
    check(![token, bare, tokenB, bareB].some((t) => everything.includes(t)), "no personal code in it");
    check(![signinValue(token), signinValue(tokenB)].some((v) => everything.includes(v)), "no sign-in value either: only a hash of it");
    const users = JSON.parse(files["db/users.json"].toString("utf8")).results;
    const row = users.find((u) => u.id === account.id);
    check(!!row && /@token\.invalid$/.test(row.email) && row.name === account.name,
          "the account's row holds a made-up name and a placeholder e-mail");
    note(`account row keys with a value: ${Object.keys(row).filter((k) => row[k] !== null && row[k] !== "" && row[k] !== false).join(", ")}`);
    const tracking = JSON.parse(files["db/tracking.json"].toString("utf8")).results;
    const ips = [...new Set(tracking.map((t) => t.ip))];
    note(`tracking table: ${tracking.length} row(s), addresses ${ips.join(", ")} (core records the address of signed-in users; issue #79)`);
    note(`tables of the plugin: ${names.filter((n) => /workshop|door|cohort/.test(n)).join(", ")}`);
  } catch (e) {
    console.log(`  [FAIL] the pass stopped: ${e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e}`);
    fails.push("stopped: " + e);
  } finally {
    await cleanup();
    const left = [await codeRow(CODE_A), await codeRow(CODE_B)];
    console.log(`  [${left.every((r) => r === null) ? "ok" : "FAIL"}] the test codes and accounts are deleted`);
    if (!left.every((r) => r === null)) fails.push("cleanup");
    await browser.close();
  }
  console.log(fails.length ? `\nFAILURES: ${JSON.stringify(fails)}` : "\nALL GREEN");
  process.exit(fails.length ? 1 : 0);
})();
