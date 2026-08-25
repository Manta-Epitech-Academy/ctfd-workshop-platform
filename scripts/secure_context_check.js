/* Does a MiniASM completion token actually derive in the browser?
 *
 * This is the one check that the whole TLS decision rests on. MiniASM's
 * js/token.js derives its token with window.crypto.subtle, which does not
 * exist outside a secure browsing context. The claim is that serving the
 * workshop on a public plain-http hostname makes every `validation: token`
 * step unsolvable rather than merely insecure — and that localhost hides it,
 * because localhost IS a secure context.
 *
 * So: the same dist, the same hostname, once over https and once over plain
 * http, with the hostname resolved to this machine either way.
 *
 *     node scripts/secure_context_check.js <host> <http-port> <dist-path> \
 *          <token-secret> <expected-flag> [path-to-playwright]
 *
 * e.g., against a rehearsal instance on 8091 behind nginx:
 *
 *     node scripts/secure_context_check.js miniasm.example.org 8091 \
 *          /runtime/miniasm/7e18bae/ $SECRET 'asm{...}'
 *
 * <host> must resolve to the machine running this. Set MAP_TO=127.0.0.1 in the
 * environment to force it there instead, which is what a local rehearsal on a
 * hostname that does not really exist needs.
 *
 * <http-port> is the instance's plain-http port. In production it is bound to
 * loopback, so this half only works from the server itself; pass `-` to skip
 * it. Skipping loses the demonstration, not the acceptance criterion — what
 * has to hold is that the token derives over https.
 * The secret is the instance's `workshop_token_secret` config and the expected
 * flag is what tools/sync_subject.py computed for TOKEN_ID.
 *
 * Needs Playwright, so it is not part of scripts/phase2_validate.py — the
 * suite has to run anywhere. This one is a deploy-day check.
 */
const HOST = process.argv[2];
const PORT = process.argv[3];
const DIST = process.argv[4];
const SECRET = process.argv[5];
const EXPECTED = process.argv[6];
const PLAYWRIGHT = process.argv[7]
  || "/home/kc/.npm/_npx/e41f203b7505f1fb/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);
const TOKEN_ID = 7;

if (!HOST || !PORT || !DIST || !SECRET || !EXPECTED) {
  console.error("usage: secure_context_check.js <host> <http-port> <dist-path> "
                + "<token-secret> <expected-flag> [playwright-path]");
  process.exit(2);
}

async function probe(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  return page.evaluate(async (id) => {
    const out = {
      origin: location.origin,
      secureContext: window.isSecureContext,
      hasSubtle: !!(window.crypto && window.crypto.subtle),
      hasTokenLib: !!window.MiniASMToken,
      token: null,
    };
    if (window.MiniASMToken) {
      window.MiniASMTokenSecret = window.__secret;
      out.token = await window.MiniASMToken.tokenFor(id);
    }
    return out;
  }, TOKEN_ID);
}

(async () => {
  const mapTo = process.env.MAP_TO;
  const browser = await chromium.launch({
    args: mapTo ? [`--host-resolver-rules=MAP ${HOST} ${mapTo}`] : [],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  await ctx.addInitScript((s) => { window.__secret = s; }, SECRET);
  const page = await ctx.newPage();

  const fails = [];

  const https = await probe(page, `https://${HOST}${DIST}`);
  console.log("over https :", JSON.stringify(https));
  if (!https.secureContext) fails.push("https origin was not a secure context");
  if (!https.hasSubtle) fails.push("crypto.subtle missing over https");
  if (https.token !== EXPECTED) {
    fails.push(`token over https was ${https.token}, the synced flag is ${EXPECTED}`);
  }

  if (PORT === "-") {
    console.log("over http  : skipped");
  } else {
    const http = await probe(page, `http://${HOST}:${PORT}${DIST}`);
    console.log("over http  :", JSON.stringify(http));
    if (http.secureContext) fails.push("plain-http origin claimed to be secure");
    if (http.token !== null) {
      fails.push(`plain http still produced a token (${http.token}) — the premise `
                 + `that http breaks tokens would be wrong`);
    }
  }

  await browser.close();
  if (fails.length) {
    console.log("\nFAILED:");
    fails.forEach((f) => console.log("  - " + f));
    process.exit(1);
  }
  console.log(PORT === "-"
    ? "\nPASS: the token derives over https."
    : "\nPASS: the token derives over https and cannot over plain http.");
})();
