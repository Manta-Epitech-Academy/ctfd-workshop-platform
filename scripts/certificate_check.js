/* The certificate, in a real browser (PLAN.md §62).
 *
 *     node scripts/certificate_check.js <base-url> <admin-pass> \
 *          [path-to-playwright] [out-dir]
 *
 * The server signs, the browser draws, so only a browser shows the whole of
 * it. This holds the two together:
 *
 *   - the public key is open to everybody, and is the key that signed;
 *   - an account the door did not make gets no button and no certificate;
 *   - a new account has the button, hidden, and is refused a certificate
 *     until a first step is done; the button shows when the counter moves,
 *     with no reload;
 *   - the file is a PDF a reader opens, plain ASCII, one page of A4 on its
 *     side, called after the account;
 *   - the signed text read back out of the file verifies with the public
 *     key, and says who, where, how many XP and how many steps, as the
 *     platform counts them;
 *   - changing the XP or the name in it breaks the signature;
 *   - downloading again works, and the later one says it is later;
 *   - with the brand file out of reach the certificate is still made;
 *   - nothing the participant gets names another platform.
 *
 * Needs Playwright. `gs` and `mutool` are used when they are installed. It
 * makes one code and one account and deletes them, also when a check throws.
 * With an out-dir, the two certificates are left there.
 */
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const ADMIN_PASS = process.argv[3];
if (!BASE || !ADMIN_PASS) {
  console.error("usage: node scripts/certificate_check.js <base-url> <admin-pass> [path-to-playwright] [out-dir]");
  process.exit(2);
}
const { chromium } = require(process.argv[4] || "playwright");
const OUT = process.argv[5] || fs.mkdtempSync(path.join(os.tmpdir(), "certificate-check-"));
fs.mkdirSync(OUT, { recursive: true });

const RUN = Math.random().toString(36).slice(2, 8).toUpperCase().replace(/[OIL]/g, "X");
const CODE = `CERT-${RUN}`;
const CAMPUS = `Ville${RUN}`;
const API = "/api/v1/workshop/certificate";
const SIGNED = /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}/g;

const fails = [];
function check(cond, label) {
  console.log(`  [${cond ? "ok" : "FAIL"}] ${label}`);
  if (!cond) fails.push(label);
}
const b64 = (buffer) => Buffer.from(buffer).toString("base64url");
const unb64 = (text) => Buffer.from(text, "base64url");

// What a consumer does with a certificate: find the signed text, check it.
function read(file) {
  const found = fs.readFileSync(file, "latin1").match(SIGNED) || [];
  if (found.length !== 1) return { found: found.length };
  const [head, body, signature] = found[0].split(".");
  return { found: 1, text: found[0], head, body, signature,
           header: JSON.parse(unb64(head).toString("utf8")),
           payload: JSON.parse(unb64(body).toString("utf8")) };
}
function verifies(key, head, body, signature) {
  return crypto.verify(null, Buffer.from(`${head}.${body}`), key, unb64(signature));
}
function tool(name, args) {
  const run = spawnSync(name, args, { encoding: "utf8" });
  return run.error ? null : run;
}

(async () => {
  const browser = await chromium.launch();
  const options = { ignoreHTTPSErrors: true, baseURL: BASE, acceptDownloads: true };

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
  const nonce = await admin.evaluate(() => window.init.csrfNonce);
  const json = { "CSRF-Token": nonce, "Content-Type": "application/json" };
  const codesPage = (form) => adminContext.request.post("/admin/workshop/codes", {
    form: { nonce, ...form }, maxRedirects: 0,
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
  let userId = null, cleaned = false;
  async function cleanup() {
    if (cleaned) return;
    cleaned = true;
    // Twice: the server may have closed the connection this one sat idle on.
    for (let i = 0; i < 2; i++) {
      try {
        if (userId) await adminContext.request.delete(`/api/v1/users/${userId}`, { headers: json });
        const id = await codeId();
        if (id) await codesPage({ action: "delete", id });
        return;
      } catch (error) { if (i) throw error; }
    }
  }

  try {
    const created = await codesPage({ action: "create", code: CODE, campus: `Epitech ${CAMPUS}` });
    if (created.status() !== 302) throw new Error(`could not create ${CODE}: ${created.status()}`);

    console.log("== the public key ==");
    const nobody = await browser.newContext(options);
    const keyAnswer = await nobody.request.get(`${API}/key`);
    const keys = keyAnswer.ok() ? (await keyAnswer.json()).keys : [];
    const jwk = keys[0] || {};
    check(keyAnswer.ok() && keys.length === 1, "open to somebody not signed in, and there is one");
    check(jwk.kty === "OKP" && jwk.crv === "Ed25519" && jwk.alg === "EdDSA" && !("d" in jwk),
          "an Ed25519 public key, and only the public half");
    const print = b64(crypto.createHash("sha256")
      .update(JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x })).digest());
    check(jwk.kid === print.slice(0, 16), "named after its own thumbprint");
    const key = crypto.createPublicKey({ key: { kty: jwk.kty, crv: jwk.crv, x: jwk.x }, format: "jwk" });
    const anonymous = await nobody.request.get(API, { maxRedirects: 0 });
    check([302, 401, 403].includes(anonymous.status()), `nobody signed in: no certificate (${anonymous.status()})`);

    console.log("== an account the door did not make ==");
    await admin.goto("/workshop");
    check(await admin.locator("[data-ws-cert]").count() === 0, "the admin sees no button");
    check((await adminContext.request.get(API)).status() === 403, "and is refused a certificate");

    console.log("== a new account ==");
    const context = await browser.newContext(options);
    const errors = [];
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto("/login");
    await page.fill("#ws-door-code", CODE);
    await page.click("[data-ws-door-form] [type=submit]");
    await page.waitForSelector("[data-ws-door-handout]:not([hidden])");
    await page.check("[data-ws-door-saved]");
    await Promise.all([
      page.waitForURL((u) => u.pathname.startsWith("/workshop")),
      page.click("[data-ws-door-enter]"),
    ]);
    const me = (await (await context.request.get("/api/v1/users/me")).json()).data;
    userId = me.id;
    const holder = page.locator("[data-ws-cert]");
    check(await holder.count() === 1 && await holder.isHidden(), `${me.name}: the button is there, hidden`);
    check((await context.request.get(API)).status() === 409, "and no certificate for no step");
    await page.evaluate(() => {
      const counter = document.querySelector(".ws-overall-num");
      counter.textContent = counter.textContent.replace(/^\s*\d+/, "1");
    });
    await page.waitForTimeout(200);
    check(await holder.isVisible(), "the counter moves: the button shows, with no reload");

    console.log("== one step done ==");
    async function solve(challenge) {
      const answer = await adminContext.request.post("/api/v1/submissions", { headers: json, data: {
        provided: "MARKED AS SOLVED BY ADMIN", user_id: userId, team_id: null,
        challenge_id: challenge.id, type: "correct" } });
      if (!answer.ok()) throw new Error(`could not mark ${challenge.id} solved`);
    }
    const challenges = (await (await adminContext.request.get("/api/v1/challenges?view=admin")).json()).data
      .filter((c) => c.state !== "hidden");
    const steps = challenges;
    await solve(steps[0]);
    await page.reload();
    check(await holder.isVisible(), "after a reload the button is shown");
    // The instance speaks French unless somebody set it otherwise (PLAN.md §42).
    const label = (await holder.locator("button").innerText()).trim();
    const french = label === "Télécharger mon certificat";
    check(french || label === "Download my certificate", `it says: ${label}`);
    async function download(name) {
      const [file] = await Promise.all([page.waitForEvent("download"), holder.locator("button").click()]);
      const to = path.join(OUT, name);
      await file.saveAs(to);
      return { to, called: file.suggestedFilename() };
    }
    const first = await download("first.pdf");
    check(first.called === (french ? "certificat-" : "certificate-") + me.name + ".pdf",
          `the file is called ${first.called}`);
    const bytes = fs.readFileSync(first.to);
    check(bytes.slice(0, 5).toString() === "%PDF-" && bytes.every((b) => b < 128), "a PDF, plain ASCII");
    check(/\/MediaBox \[0 0 842(\.\d+)? 595(\.\d+)?\]/.test(bytes.toString("latin1")), "A4 on its side");
    const gs = tool("gs", ["-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", first.to]);
    if (gs) check(gs.status === 0 && !(gs.stdout + gs.stderr).trim(), "Ghostscript reads it without a word");
    const mu = tool("mutool", ["info", first.to]);
    if (mu) {
      check(mu.status === 0 && /Pages: 1\b/.test(mu.stdout), "MuPDF reads it: one page");
      check(/Anton/.test(mu.stdout) && /IBMPlexSans-Light/.test(mu.stdout), "set in the platform's typefaces");
    }

    console.log("== what it says, signed ==");
    const one = read(first.to);
    check(one.found === 1, "the signed text is in the file, once");
    check(one.header.alg === "EdDSA" && one.header.kid === jwk.kid, "signed with the published key, by name");
    check(verifies(key, one.head, one.body, one.signature), "and the signature verifies");
    const p = one.payload, c = (p.contents || [])[0] || {};
    check(p.v === 1 && p.iss === new URL(BASE).origin, `from ${p.iss}`);
    check(p.sub === me.name, `for ${p.sub}, the name the platform chose`);
    check(Math.abs(p.iat - Date.now() / 1000) < 300 && /^[0-9A-F]{16}$/.test(p.jti), "dated now, with a serial");
    check(p.contents.length === 1 && c.done === 1 && c.total > 1 && c.total <= challenges.length,
          `${c.done} step out of ${c.total}`);
    check(c.xp === steps[0].value, `${c.xp} XP, what the step is worth`);
    check(p.event && p.event.campus === CAMPUS, "the campus of the code, without the word Epitech");
    check(Object.keys(p).sort().join() === "contents,event,iat,iss,jti,sub,v"
          && Object.keys(c).sort().join() === "done,slug,title,total,xp", "and nothing else");
    const data = (await (await context.request.get(API)).json()).data;
    if (french) {
      // The translation ties some words together with no-break spaces.
      const said = data.sentence.replace(/\u00a0/g, " ");
      check(said.includes("1 étape sur") && said.includes(`Epitech ${CAMPUS}`),
            `the sentence: ${data.sentence}`);
    }

    console.log("== a changed certificate ==");
    const more = b64(JSON.stringify({ ...p, contents: [{ ...c, xp: c.xp + 1000 }] }));
    check(!verifies(key, one.head, more, one.signature), "more XP: the signature fails");
    const other = b64(JSON.stringify({ ...p, sub: "Somebody-Else-1" }));
    check(!verifies(key, one.head, other, one.signature), "another name: the signature fails");
    const stranger = crypto.generateKeyPairSync("ed25519");
    const forged = b64(crypto.sign(null, Buffer.from(`${one.head}.${more}`), stranger.privateKey));
    check(!verifies(key, one.head, more, forged), "signed again with another key: it fails");

    console.log("== downloading again, later ==");
    await solve(steps[1]);
    await solve(steps[2]);
    await page.waitForTimeout(1100);
    await page.reload();
    await page.route("**/pdf-brand*.json", (route) => route.abort());
    const second = await download("second.pdf");
    const two = read(second.to);
    const c2 = two.found === 1 ? two.payload.contents[0] : {};
    check(two.found === 1 && verifies(key, two.head, two.body, two.signature), "a second certificate, signed too");
    check(two.payload.sub === p.sub && two.payload.iss === p.iss, "the same account at the same address");
    check(two.payload.iat > p.iat && two.payload.jti !== p.jti, "later, under another serial");
    check(c2.done === 3 && c2.xp === steps.slice(0, 3).reduce((sum, x) => sum + x.value, 0),
          `${c2.done} steps and ${c2.xp} XP`);
    const plain = fs.readFileSync(second.to, "latin1");
    check(/\/BaseFont \/Helvetica/.test(plain) && !/FontFile2/.test(plain),
          "with the brand file out of reach: made all the same, in the standard fonts");
    const gs2 = tool("gs", ["-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", second.to]);
    if (gs2) check(gs2.status === 0 && !(gs2.stdout + gs2.stderr).trim(), "which Ghostscript reads too");

    console.log("== what the participant gets names no other platform ==");
    const script = await (await nobody.request.get("/plugins/workshop/assets/certificate.js")).text();
    const html = await page.locator("[data-ws-cert]").evaluate((e) => e.outerHTML);
    check(!/jump/i.test(JSON.stringify(data)) && !/jump/i.test(html) && !/jump/i.test(script)
          && !/jump/i.test(unb64(two.body).toString("utf8")), "none of it does");
    check(errors.length === 0, `no script error on the way (${errors.join("; ") || "none"})`);
    console.log(`the certificates: ${OUT}`);
  } catch (error) {
    console.log(`  [FAIL] the script stopped: ${error.stack || error}`);
    fails.push("the script stopped");
  } finally {
    await cleanup();
    await browser.close();
  }
  console.log(fails.length ? `\n${fails.length} FAILED:\n  ${fails.join("\n  ")}` : "\nALL GREEN");
  process.exit(fails.length ? 1 : 0);
})();
