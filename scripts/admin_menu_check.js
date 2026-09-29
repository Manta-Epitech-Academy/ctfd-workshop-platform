/* Does the admin bar fold this plugin's entries into one dropdown?
 *
 *   node scripts/admin_menu_check.js <base-url> <admin-pass>
 *
 * The fold happens in the browser (assets/admin-menu.js), so a fetch of the
 * HTML proves nothing: the nine entries are in the markup either way. This
 * drives a real browser, signs in, and reads the bar as rendered.
 *
 * It also checks the two cases the script is written to decline: one entry is
 * left alone rather than wrapped in a menu of one, and a link that is not ours
 * is never swallowed.
 */
const { chromium } = require("/home/kc/.npm/_npx/bbb8a2c4738e2b0c/node_modules/playwright");

const BASE = (process.argv[2] || "http://localhost:9091").replace(/\/$/, "");
const PASS = process.argv[3];
if (!PASS) { console.error("usage: node scripts/admin_menu_check.js <base-url> <admin-pass>"); process.exit(2); }

const fails = [];
function check(cond, label) {
  console.log((cond ? "  OK   " : "  FAIL ") + label);
  if (!cond) fails.push(label);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(BASE + "/login");
  await page.fill('input[name="name"]', "admin");
  await page.fill('input[name="password"]', PASS);
  // The Epitech login override renders WTForms' `submit`, which is an <input>,
  // not the <button> core uses. Match both rather than either.
  await Promise.all([
    page.waitForNavigation(),
    page.click('input[type="submit"], button[type="submit"]'),
  ]);
  // Not `networkidle`: the statistics page polls, so the network never goes
  // idle and the wait would always time out. The real signal is the fold
  // itself, which is what the next line waits for.
  await page.goto(BASE + "/admin/statistics", { waitUntil: "domcontentloaded" });
  await page.waitForSelector('a.dropdown-toggle:has-text("Workshop")', { timeout: 10000 })
    .catch(() => {});

  const bar = "#base-navbars .navbar-nav";

  const top = await page.$$eval(bar + " > li.nav-item > a.nav-link",
    (as) => as.map((a) => (a.textContent || "").trim()).filter(Boolean));
  console.log("== the bar, as rendered ==");
  console.log("   " + JSON.stringify(top));

  check(top.includes("Workshop"), "one entry called Workshop");
  check(top.includes("Config"),
    "CTFd's own Config is still on the bar, not pushed off by ours");

  const strays = await page.$$eval(bar + " > li.nav-item > a.nav-link",
    (as) => as.filter((a) => (a.getAttribute("href") || "").includes("/admin/workshop/"))
             .map((a) => a.getAttribute("href")));
  check(strays.length === 0, `no workshop link left at top level: ${JSON.stringify(strays)}`);

  const folded = await page.$$eval(bar + " li.nav-item.dropdown",
    (lis) => {
      const li = lis.find((l) => (l.querySelector("a.dropdown-toggle") || {}).textContent
        && l.querySelector("a.dropdown-toggle").textContent.trim() === "Workshop");
      if (!li) return null;
      return Array.from(li.querySelectorAll("a.dropdown-item"))
        .map((a) => [a.textContent.trim(), a.getAttribute("href")]);
    });
  check(folded !== null, "it is a dropdown");
  check(folded && folded.length === 9, `holding all nine: ${folded && folded.length}`);
  check(folded && folded.every(([, href]) => href.includes("/admin/workshop/")),
    "each item pointing into /admin/workshop/");
  if (folded) console.log("   " + JSON.stringify(folded.map(([t]) => t)));

  console.log("\n== it opens, and the links work ==");
  await page.click('a.dropdown-toggle:has-text("Workshop")');
  const shown = await page.isVisible('.dropdown-menu a.dropdown-item:has-text("External access")');
  check(shown, "clicking it reveals the items");
  await Promise.all([
    page.waitForNavigation(),
    page.click('.dropdown-menu a.dropdown-item:has-text("External access")'),
  ]);
  check(page.url().endsWith("/admin/workshop/external"),
    `and they navigate: ${page.url()}`);

  console.log("\n== it declines to fold a single entry ==");
  const one = await page.evaluate(() => {
    // Rebuild a bar with one workshop entry and re-run the script's logic by
    // re-inserting the script, which is how it behaves with the workshop off.
    document.querySelectorAll("#base-navbars .navbar-nav li.nav-item.dropdown")
      .forEach((li) => {
        const t = li.querySelector("a.dropdown-toggle");
        if (t && t.textContent.trim() === "Workshop") li.remove();
      });
    const ul = document.querySelector("#base-navbars .navbar-nav");
    const li = document.createElement("li");
    li.className = "nav-item";
    li.innerHTML = '<a class="nav-link" href="/admin/workshop/plugin">Workshop plugin</a>';
    ul.appendChild(li);
    return true;
  });
  check(one, "a bar with one workshop entry is set up");
  await page.addScriptTag({ url: "/plugins/workshop/assets/admin-menu.js" });
  const still = await page.$$eval("#base-navbars .navbar-nav > li.nav-item > a.nav-link",
    (as) => as.filter((a) => (a.getAttribute("href") || "").includes("/admin/workshop/"))
             .map((a) => a.textContent.trim()));
  check(still.length === 1 && still[0] === "Workshop plugin",
    `it is left as a plain link, not a menu of one: ${JSON.stringify(still)}`);

  await browser.close();
  console.log();
  if (fails.length) { console.log("FAILURES:", fails); process.exit(1); }
  console.log("ALL GREEN");
})().catch((e) => { console.error(e); process.exit(1); });
