/* The token door in a real browser (PLAN.md §60).
 *
 *     node scripts/token_door_browser_check.js <base-url> <admin-pass> \
 *          [path-to-playwright] [screenshot-dir]
 *
 * scripts/token_door_check.py holds the server to its half. This holds the
 * page to the other half, which only a browser can show because the token
 * exists nowhere else:
 *
 *   - the one box tells a code from a token, and refuses a mistyped one
 *     without sending anything;
 *   - the box writes what is typed in groups of four, and a dash or a space
 *     the person types never lands anywhere else;
 *   - a code that is not open yet is answered with when it opens, written in
 *     the visitor's own time zone;
 *   - a good code shows the personal code once, with a file, a copy button
 *     and a QR code, and makes no account until the box is ticked;
 *   - the same token opens the same account again: typed, pasted with the
 *     whole file around it, chosen as a file, dropped on the page, carried in
 *     the address's fragment, and from a browser that never saw it;
 *   - **the token never leaves the browser**: every request this script's
 *     visitors make is recorded, and none carries it;
 *   - the encryption key waits in IndexedDB, cannot be exported, and is gone
 *     once the door is shown to nobody in particular;
 *   - without a secure context the page says so instead of doing nothing.
 *
 * Needs Playwright and an instance served on https or on localhost (the page
 * needs Web Crypto). It makes two codes and one account and deletes them, also
 * when a check throws.
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const ADMIN_PASS = process.argv[3];
if (!BASE || !ADMIN_PASS) {
  console.error("usage: node scripts/token_door_browser_check.js <base-url> <admin-pass> [path-to-playwright] [screenshot-dir]");
  process.exit(2);
}
const { chromium } = require(process.argv[4] || "playwright");
const SHOTS = process.argv[5] || "";

const RUN = Math.random().toString(36).slice(2, 8).toUpperCase().replace(/[OIL]/g, "X");
const CODE = `DOOR-${RUN}`;
const SOON = `SOON-${RUN}`;
const TOKEN = /^(?:[0-9A-HJKMNP-TV-Z]{4}-){6}[0-9A-HJKMNP-TV-Z]{4}$/;
const NAME = /^[A-ZÉ][a-zéèêï]+-[A-ZÉ][a-zéèê]+-\d{2,4}$/;
const DOOR = "/api/v1/workshop/door";
// The test vector scripts/token_door_check.py holds too: one token and the
// sign-in value it must derive. Three implementations (assets/door.js on Web
// Crypto, Node's below, Python's there), one answer. If door.js ever derives
// anything else, every token already handed out stops opening its account.
const KNOWN = ["000G-40R4-0M30-E209-185G-R38E-1WQS",
               "86a70e11a5424d57ac29a433b40674b79fbb86b0c7bfca2c8918d29acce4d414"];

// What door.js must send for a token, worked out here with Node's own HKDF.
function signinValue(token) {
  const A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  let bits = 0n;
  for (const c of token.replace(/-/g, "").slice(0, 26)) bits = (bits << 5n) | BigInt(A.indexOf(c));
  const raw = Buffer.from((bits >> 2n).toString(16).padStart(32, "0"), "hex");
  return Buffer.from(crypto.hkdfSync("sha256", raw, "ctfd-workshop-token-door-v1", "sign-in", 32))
    .toString("hex");
}

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
  async function codeRow(code = CODE) {
    const html = await (await adminContext.request.get("/admin/workshop/codes")).text();
    const body = html.split("<tbody>")[1].split("</tbody>")[0];
    for (const chunk of body.split("<tr>").slice(1)) {
      if (!chunk.includes(`<code>${code}</code>`)) continue;
      const id = chunk.match(/name="id" value="(\d+)"/);
      const count = chunk.match(/<summary>\s*(\d+)/);
      return { id: id && id[1], accounts: count ? Number(count[1]) : 0 };
    }
    return null;
  }

  const madeUsers = new Set();
  let cleaned = false;
  async function cleanup() {
    if (cleaned) return;
    cleaned = true;
    for (const id of madeUsers) {
      await adminContext.request.delete(`/api/v1/users/${id}`, {
        headers: { "CSRF-Token": adminNonce, "Content-Type": "application/json" },
      });
    }
    for (const code of [CODE, SOON]) {
      const row = await codeRow(code);
      if (row && row.id) await codesPage({ action: "delete", id: row.id });
    }
  }

  const created = await codesPage({ action: "create", code: CODE, campus: `Door ${RUN}` });
  if (created.status() !== 302) {
    console.error(`could not create ${CODE}: ${created.status()}`);
    await browser.close();
    process.exit(2);
  }

  // ---- a visitor: a browser, and a record of everything it sends ---------
  const sent = [];
  async function visitor(extra) {
    const context = await browser.newContext({ ...options, acceptDownloads: true, ...extra });
    context.on("request", (request) => {
      sent.push({ url: request.url(), body: request.postData() || "",
                  headers: JSON.stringify(request.headers()) });
    });
    return context;
  }
  const doorCalls = () => sent.filter((r) => r.url.includes(DOOR)).length;
  const text = (page, name) => page.getAttribute("[data-ws-door]", `data-${name}`);
  const error = async (page) => {
    const p = page.locator("#ws-door-error");
    return (await p.isVisible()) ? (await p.textContent()) : null;
  };
  const me = (page) => page.evaluate(() =>
    fetch("/api/v1/users/me", { credentials: "same-origin" })
      .then((r) => (r.redirected ? null : r.json()))
      .then((j) => (j && j.data ? j.data : null)).catch(() => null));
  const storedKey = (page) => page.evaluate(() => new Promise((resolve) => {
    const open = indexedDB.open("ws-door", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("keys");
    open.onerror = () => resolve({ error: true });
    open.onsuccess = () => {
      const get = open.result.transaction("keys").objectStore("keys").get("encryption");
      get.onsuccess = async () => {
        const key = get.result;
        open.result.close();
        if (!key) { resolve({ present: false }); return; }
        let exported = true;
        try { await crypto.subtle.exportKey("raw", key); } catch (e) { exported = false; }
        // The key works: what it encrypts, it decrypts.
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const secret = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode("code"));
        const back = new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, secret));
        resolve({ present: true, isKey: key instanceof CryptoKey, extractable: key.extractable,
                  name: key.algorithm.name, length: key.algorithm.length, exported, works: back === "code" });
      };
    };
  }));
  async function comeBack(page, how) {
    await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
    const who = await me(page);
    check(!!who && who.id === account.id, `${how}: signed in as the same account`);
  }
  async function signOut(page) {
    await page.goto("/logout");
    await page.goto("/login");
    await page.waitForSelector("[data-ws-door]");
  }

  let account = null, token = null, fileBody = null;
  try {
    const context = await visitor({ permissions: ["clipboard-read", "clipboard-write"] });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));

    console.log("== the door is the participant side of the login page ==");
    await page.goto("/login");
    const invalid = await text(page, "invalid");
    check(await page.locator("#ws-door-code").isVisible(), "one box");
    check((await page.locator(".epi-login-main input:visible").count()) === 1,
          "and no other field: no name, no e-mail, no password");
    check(await page.locator("[data-ws-door-handout]").isHidden(), "the personal code screen is not shown");
    check(!!invalid && invalid.length > 20, "the page carries its one refusal sentence");
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "door-entry.png") });

    console.log("== what is not a code is refused on the page, and nothing is sent ==");
    for (const [label, typed] of [
      ["an empty box", ""],
      ["three letters", "abc"],
      ["forty letters", "A".repeat(40)],
      ["a token's shape with a wrong checksum", "0000-0000-0000-0000-0000-0000-0000"],
      ["28 characters with no dash", "ABCDEFGHJKMNPQRSTVWXYZ012345"],
    ]) {
      const before = doorCalls();
      await page.fill("#ws-door-code", typed);
      await page.click("[data-ws-door-form] [type=submit]");
      await page.waitForFunction(() => !document.querySelector("[data-ws-door-form] [type=submit]").disabled);
      check((await error(page)) === invalid && doorCalls() === before, `${label}: the one sentence, no request`);
    }
    await page.type("#ws-door-code", "x");
    check((await error(page)) === null, "typing again takes the sentence away");

    console.log("== the box writes the dashes ==");
    const typed = async (keys, ...presses) => {
      await page.fill("#ws-door-code", "");
      await page.focus("#ws-door-code");
      await page.keyboard.type(keys);
      for (const key of presses) await page.keyboard.press(key);
      return page.inputValue("#ws-door-code");
    };
    check((await typed("abcdefghjk")) === "ABCD-EFGH-JK", "ten characters typed: groups of four, a dash between");
    check((await typed("abcd")) === "ABCD", "no dash waits after a full group");
    check((await typed("abcd-")) === "ABCD-" && (await typed("abcd ")) === "ABCD-",
          "a dash or a space typed where a group ends is the dash");
    check((await typed("abcd ef")) === "ABCD-EF" && (await typed("abcd--ef")) === "ABCD-EF",
          "and it is there once");
    check((await typed("-ab-c d- ef")) === "ABCD-EF", "typed anywhere else, it is dropped");
    check((await typed("abcdefghjkmnpqrstvwxyz012345-")) === "ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345",
          "28 characters: seven groups, and no dash after the last");
    const left = (n) => Array(n).fill("ArrowLeft");
    check((await typed("abcdefgh", ...left(4), "Backspace")) === "ABCE-FGH",
          "Backspace on a dash takes the character before it");
    check((await typed("abcdefgh", ...left(5), "Delete")) === "ABCD-FGH",
          "Delete on a dash takes the character after it");
    await typed("abcdefgh", ...left(6));
    await page.keyboard.type("x");
    check((await page.inputValue("#ws-door-code")) === "ABCX-DEFG-H"
          && (await page.evaluate(() => document.querySelector("#ws-door-code").selectionStart)) === 4,
          "a character typed in the middle moves the dashes, and the caret stays behind it");
    await page.fill("#ws-door-code", "Mon code : abcd-efgh");
    check((await page.inputValue("#ws-door-code")) === "Mon code : abcd-efgh",
          "a text that is more than a code is left as it came");
    await page.fill("#ws-door-code", "");

    console.log("== a wrong code is asked about, and gets the same sentence ==");
    let before = doorCalls();
    await page.fill("#ws-door-code", `NOPE-${RUN}`);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === invalid && doorCalls() === before + 1, "one request, the one sentence");
    check(await page.locator("[data-ws-door-handout]").isHidden(), "and no personal code");

    console.log("== a code that is not open yet says when it opens ==");
    const opens = new Date(Date.now() + 3600 * 1000);
    const later = await codesPage({ action: "create", code: SOON, valid_from: opens.toISOString() });
    check(later.status() === 302, `${SOON} is created, to open in an hour`);
    // A visitor on the other side of the world: the time they read is theirs.
    const far = await visitor({ timezoneId: "Pacific/Auckland" });
    const early = await far.newPage();
    await early.goto("/login");
    await early.fill("#ws-door-code", SOON);
    await early.click("[data-ws-door-form] [type=submit]");
    await early.waitForSelector("#ws-door-error:not([hidden])");
    const flat = (string) => String(string || "").replace(/\s+/g, " ");
    const written = opens.toLocaleString(await text(early, "locale"),
      { dateStyle: "long", timeStyle: "short", timeZone: "Pacific/Auckland" });
    const told = await error(early);
    check(flat(told) === flat((await text(early, "early")).replace("{when}", written)),
          `the sentence carries the opening time, in the visitor's zone: ${told}`);
    check(told !== invalid && !String(told).includes("{"), "and it is not the one refusal sentence");
    check(await early.locator("[data-ws-door-handout]").isHidden()
          && (await codeRow(SOON)).accounts === 0, "no personal code, and no account");
    if (SHOTS) await early.screenshot({ path: path.join(SHOTS, "door-early.png") });
    await far.close();

    console.log("== a good code shows the personal code, once ==");
    await page.fill("#ws-door-code", `  ${CODE.toLowerCase().replace("-", " ")} `);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("[data-ws-door-handout]:not([hidden])");
    token = await page.getAttribute("[data-ws-door-token]", "data-token");
    check(TOKEN.test(token || ""), "seven groups of four, in Crockford's alphabet");
    const shown = await page.locator("[data-ws-door-token] span").allTextContents();
    check(shown.length === 7 && shown.join("-") === token, "drawn as seven groups");
    check((await page.textContent("[data-ws-login-title]")).trim() === await text(page, "handout-title"),
          "the page's title says what this is");
    check(await page.locator("[data-ws-door-entry]").isHidden(), "the box is gone");
    check(await page.locator(".epi-login-switch").isHidden(), "and so is the way to the staff side");
    check(await page.locator("[data-ws-door-enter]").isDisabled(), "Continue waits for the tick");
    check((await codeRow()).accounts === 0, "no account exists yet");
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "door-handout.png"), fullPage: true });

    console.log("== three ways to keep it ==");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.click("[data-ws-door-download]"),
    ]);
    const saved = await download.path();
    fileBody = fs.readFileSync(saved, "utf8");
    check(download.suggestedFilename() === await text(page, "file-name"),
          `the file is called ${download.suggestedFilename()}`);
    check(fileBody.includes(token), "it holds the personal code");
    check(fileBody.includes(`${BASE}/login`), "and the address to come back to");
    check(fileBody.includes("\r\n") && !/[^\r]\n/.test(fileBody), "with Windows line ends");

    await page.click("[data-ws-door-copy]");
    await page.waitForFunction(() =>
      document.querySelector("[data-ws-door-copy-label]").textContent
        === document.querySelector("[data-ws-door]").dataset.copied);
    check((await page.evaluate(() => navigator.clipboard.readText())) === token,
          "the copy button puts the code on the clipboard, and says so");

    // The QR code, read back: the same library, run here, on the address the
    // code should hold. Same modules, same content.
    const drawn = await page.getAttribute("[data-ws-door-qr-code] svg path", "d");
    const wanted = await page.evaluate((content) => {
      const qr = window.qrcode(0, "M");
      qr.addData(content);
      qr.make();
      let d = "";
      const n = qr.getModuleCount();
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
        if (qr.isDark(r, c)) d += "M" + (c + 4) + " " + (r + 4) + "h1v1h-1z";
      }
      return d;
    }, `${BASE}/login#${token}`);
    check(await page.locator("[data-ws-door-qr]").isVisible(), "a QR code is drawn");
    check(!!drawn && drawn === wanted, "and it holds this page's address with the code as its fragment");
    check((await page.evaluate(() => getComputedStyle(
      document.querySelector("[data-ws-door-qr-code]")).backgroundColor)) === "rgb(255, 255, 255)",
          "on white");

    console.log("== the tick makes the account ==");
    await page.check("[data-ws-door-saved]");
    check(await page.locator("[data-ws-door-enter]").isEnabled(), "ticked: Continue is live");
    await page.uncheck("[data-ws-door-saved]");
    check(await page.locator("[data-ws-door-enter]").isDisabled(), "unticked: it is not");
    await page.check("[data-ws-door-saved]");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.startsWith("/login")),
      page.click("[data-ws-door-enter]"),
    ]);
    account = await me(page);
    if (account) madeUsers.add(account.id);
    check(!!account && NAME.test(account.name), `signed in as ${account && account.name}, a name nobody typed`);
    check((await codeRow()).accounts === 1, "the code has one account");
    let key = await storedKey(page);
    check(key.present && key.isKey && key.name === "AES-GCM" && key.length === 256,
          "an AES-GCM key waits in IndexedDB");
    check(key.present && key.extractable === false && key.exported === false, "it cannot be exported");
    check(key.present && key.works, "and it encrypts and decrypts");
    check((await page.locator('a[href$="/settings"]').count()) === 0, "the navbar offers no settings page");
    await page.goto("/settings");
    check(!new URL(page.url()).pathname.startsWith("/settings"), "and /settings leads back to the workshop");

    console.log("== signed in and back at the door: the key stays ==");
    await page.goto("/login");
    await page.waitForSelector("[data-ws-door]");
    await page.waitForTimeout(300);
    check((await storedKey(page)).present, "the key is still there");

    console.log("== signed out: the key goes ==");
    await signOut(page);
    await page.waitForFunction(() => new Promise((resolve) => {
      const open = indexedDB.open("ws-door", 1);
      open.onupgradeneeded = () => open.result.createObjectStore("keys");
      open.onsuccess = () => {
        const get = open.result.transaction("keys").objectStore("keys").get("encryption");
        get.onsuccess = () => { open.result.close(); resolve(!get.result); };
      };
    }), null, { timeout: 5000 }).catch(() => {});
    check((await storedKey(page)).present === false, "no key is left for the next person on this computer");
    check((await me(page)) === null, "and nobody is signed in");

    console.log("== coming back ==");
    await page.fill("#ws-door-code", token);
    await page.click("[data-ws-door-form] [type=submit]");
    await comeBack(page, "the code as shown");
    key = await storedKey(page);
    check(key.present && key.extractable === false, "the key is back, from the token alone");

    await signOut(page);
    await page.fill("#ws-door-code", ` ${token.toLowerCase().replace(/-/g, " ")} `);
    await page.keyboard.press("Enter");
    await comeBack(page, "in small letters with spaces, and Enter");

    await signOut(page);
    await page.fill("#ws-door-code", token.replace(/-/g, "").replace(/0/g, "O").replace(/1/g, "l"));
    await page.click("[data-ws-door-form] [type=submit]");
    await comeBack(page, "with no dashes, O for 0 and l for 1");

    await signOut(page);
    await page.fill("#ws-door-code", fileBody.replace(/\r\n/g, " "));
    await page.click("[data-ws-door-form] [type=submit]");
    await comeBack(page, "the whole file pasted in the box");

    await signOut(page);
    await page.setInputFiles("[data-ws-door-file]", saved);
    await comeBack(page, "the file, chosen");

    await signOut(page);
    await page.evaluate((body) => {
      const data = new DataTransfer();
      data.items.add(new File([body], "code.txt", { type: "text/plain" }));
      const fire = (type, where) => where.dispatchEvent(
        new DragEvent(type, { dataTransfer: data, bubbles: true, cancelable: true }));
      fire("dragover", document.body);
      fire("drop", document.querySelector(".epi-login-panel") || document.body);
    }, fileBody);
    await comeBack(page, "the file, dropped beside the box");

    await signOut(page);
    await page.goto("about:blank");
    await page.goto(`/login#${token}`);
    await comeBack(page, "the QR code's address");

    console.log("== a browser that never saw it ==");
    const other = await visitor();
    const phone = await other.newPage();
    await phone.goto(`/login#${token}`);
    await comeBack(phone, "another browser, by the address");
    check((await storedKey(phone)).present, "and it has the key too");
    await other.close();

    console.log("== what does not open it ==");
    await signOut(page);
    const flipped = token.slice(0, -1) + (token.endsWith("Z") ? "Y" : "Z");
    before = doorCalls();
    await page.fill("#ws-door-code", flipped);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === invalid && doorCalls() === before,
          "one wrong character: refused on the page, nothing sent");

    before = doorCalls();
    await page.setInputFiles("[data-ws-door-file]", {
      name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("rien ici\n"),
    });
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === await text(page, "no-code") && doorCalls() === before,
          "a file with no code in it: said so, nothing sent");

    before = doorCalls();
    await page.setInputFiles("[data-ws-door-file]", {
      name: "big.txt", mimeType: "text/plain", buffer: Buffer.alloc(200000, "a"),
    });
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === await text(page, "no-code") && doorCalls() === before,
          "a file too big to be one: the same");

    await page.goto("about:blank");
    before = doorCalls();
    await page.goto(`/login#${flipped}`);
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === invalid && doorCalls() === before
          && !page.url().includes("#"), "a wrong code in the address: refused, and taken out of the address bar");

    // A token nobody was given: right shape, right checksum, no account.
    const stranger = await page.evaluate(async () => {
      const A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      let out = "", acc = 0, bits = 0;
      for (const b of bytes) {
        acc = (acc << 8) | b; bits += 8;
        while (bits >= 5) { out += A[(acc >>> (bits - 5)) & 31]; bits -= 5; }
        acc &= (1 << bits) - 1;
      }
      out += A[(acc << (5 - bits)) & 31];
      const h = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      return out + A[h[0] >>> 3] + A[((h[0] & 7) << 2) | (h[1] >>> 6)];
    });
    before = doorCalls();
    await page.fill("#ws-door-code", stranger);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(page)) === invalid && doorCalls() === before + 1 && (await me(page)) === null,
          "a well-formed token with no account: asked, refused with the one sentence");

    console.log("== the derivation is the one written down ==");
    check(signinValue(KNOWN[0]) === KNOWN[1], "Node's HKDF gives the test vector's sign-in value");
    before = sent.length;
    await page.fill("#ws-door-code", KNOWN[0]);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("#ws-door-error:not([hidden])");
    const vector = sent.slice(before).filter((r) => r.url.endsWith(`${DOOR}/signin`));
    check(vector.length === 1 && vector[0].body === JSON.stringify({ signin: KNOWN[1] }),
          "the page derives the same value from the test vector's token");
    const mine = sent.filter((r) => r.url.endsWith(`${DOOR}/register`));
    check(mine.length === 1 && JSON.parse(mine[0].body).signin === signinValue(token),
          "and what it registered with is the value Node derives from the code it showed");
    const backs = sent.filter((r) => r.url.endsWith(`${DOOR}/signin`)
      && r.body === JSON.stringify({ signin: signinValue(token) }));
    check(backs.length >= 7, `every way of coming back sent that same value (${backs.length} times)`);

    console.log("== the token never left the browser ==");
    const bare = token.replace(/-/g, "");
    const leaks = sent.filter((r) => [token, bare, token.toLowerCase(), bare.toLowerCase()]
      .some((t) => r.url.includes(t) || r.body.includes(t) || r.headers.includes(t)));
    check(sent.length > 50 && leaks.length === 0,
          `${sent.length} requests recorded, none carries the token`);
    const signins = sent.filter((r) => r.url.endsWith(`${DOOR}/signin`) || r.url.endsWith(`${DOOR}/register`));
    check(signins.length > 0 && signins.every((r) => /"signin":"[0-9a-f]{64}"/.test(r.body)),
          "what is sent instead is 64 hex characters");
    check(pageErrors.length === 0, `no script error on the way (${pageErrors.join(" | ") || "none"})`);

    console.log("== without a secure context ==");
    const plain = await visitor();
    await plain.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => false });
    });
    const old = await plain.newPage();
    await old.goto("/login");
    await old.waitForSelector("#ws-door-error:not([hidden])");
    check((await error(old)) === await text(old, "insecure"), "the page says it needs https");
    check(await old.locator("#ws-door-code").isDisabled()
          && await old.locator("[data-ws-door-form] [type=submit]").isDisabled(),
          "and the box and the button are off");
    await plain.close();

    console.log("== on a phone ==");
    const small = await visitor({ viewport: { width: 360, height: 740 } });
    const narrow = await small.newPage();
    await narrow.goto("/login");
    await narrow.fill("#ws-door-code", CODE);
    await narrow.click("[data-ws-door-form] [type=submit]");
    await narrow.waitForSelector("[data-ws-door-handout]:not([hidden])");
    const overflow = await narrow.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 0, "the personal code screen fits a 360px screen");
    const wraps = await narrow.evaluate(() => Array.from(
      document.querySelectorAll("[data-ws-door-token] span")).every((s) => s.getClientRects().length === 1));
    check(wraps, "and no group of four is cut in two");
    if (SHOTS) await narrow.screenshot({ path: path.join(SHOTS, "door-handout-phone.png"), fullPage: true });
    await narrow.goto("/login");
    check((await codeRow()).accounts === 1, "leaving that screen made no account");
    await small.close();
  } catch (e) {
    check(false, `the script itself failed: ${e && e.stack ? e.stack.split("\n").slice(0, 3).join(" ") : e}`);
  } finally {
    await cleanup();
    await browser.close();
  }

  console.log(fails.length ? `\nFAILURES: ${JSON.stringify(fails, null, 1)}` : "\nALL GREEN");
  process.exit(fails.length ? 1 : 0);
})();
